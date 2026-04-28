import os
import re
import json
from typing import List, Dict, Any, Tuple, Optional, TypedDict

from langchain_mistralai import ChatMistralAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

from bioseq_investigator.utils import get_first_fasta_entry, translate_dna_to_protein
from bioseq_investigator.embeddings import get_or_create_index
from bioseq_investigator.search import get_prottrans_embedder, search_top_k
from bioseq_investigator.data_fetcher import get_uniprot_records
from bioseq_investigator.reranking import LocalReranker

# --- State Definition ---

class GraphState(TypedDict):
    prompt: str
    sequence_or_path: Optional[str]
    input_type: Optional[str]  # 'SEQUENCE' or 'FILEPATH'
    context: Optional[str]
    sequence: Optional[str]
    sequence_type: Optional[str]  # 'DNA' or 'PROTEIN'
    protein_sequence: Optional[str]
    ranked_results: Optional[List[Dict[str, Any]]]
    final_results: Optional[List[Dict[str, Any]]]
    error: Optional[str]

# --- Node Functions ---

def extract_input_node(state: GraphState) -> Dict[str, Any]:
    """
    LLM extracts the sequence or filepath and the context from the prompt.
    """
    llm = ChatMistralAI(model="mistral-small-latest")
    
    system_prompt = (
        "You are a bioinformatics assistant. Your task is to extract a biological sequence (DNA or Protein) "
        "or a file path to a FASTA file from the user's prompt, along with any contextual information or questions "
        "provided about that sequence.\n"
        "Respond ONLY with a JSON object containing the keys: 'sequence_or_path', 'input_type' (either 'SEQUENCE' or 'FILEPATH'), and 'context'."
    )
    
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=state['prompt'])
    ])
    
    try:
        # Simple extraction of JSON if LLM adds markdown or fluff
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
            
        data = json.loads(content)
        return {
            "sequence_or_path": data.get("sequence_or_path"),
            "input_type": data.get("input_type"),
            "context": data.get("context")
        }
    except Exception as e:
        return {"error": f"Failed to extract input: {str(e)}"}

def resolve_sequence_node(state: GraphState) -> Dict[str, Any]:
    """
    If input is a filepath, read the sequence from the file.
    """
    if state.get('error'): return {}
    
    input_type = state.get('input_type')
    path_or_seq = state.get('sequence_or_path')
    
    if input_type == 'FILEPATH':
        try:
            fasta_entry = get_first_fasta_entry(path_or_seq)
            # Extract sequence from FASTA entry (ignoring header for now, but keeping it in state if needed)
            lines = fasta_entry.splitlines()
            sequence = "".join(lines[1:]) if len(lines) > 1 else ""
            return {"sequence": sequence}
        except Exception as e:
            return {"error": f"Failed to resolve filepath: {str(e)}"}
    else:
        # If it was already a sequence, we might need to clean it (e.g. remove header if present)
        seq = path_or_seq
        if seq.startswith(">"):
            lines = seq.splitlines()
            seq = "".join(lines[1:])
        return {"sequence": seq}

def classify_type_node(state: GraphState) -> Dict[str, Any]:
    """
    Classifies the sequence as DNA or PROTEIN using heuristics and LLM.
    """
    if state.get('error'): return {}
    
    prompt = state['prompt'].lower()
    sequence = state['sequence']
    path = state.get('sequence_or_path', '')
    
    # 1. Heuristics - Keywords
    if any(k in prompt for k in ['dna', 'chromosome', 'nucleotide', 'genome']):
        return {"sequence_type": "DNA"}
    if any(k in prompt for k in ['protein', 'peptide', 'amino acid', 'polypeptide']):
        return {"sequence_type": "PROTEIN"}
    
    # 2. Heuristics - FASTA Header (if we had it, but we can check if the original path/seq had it)
    header = ""
    if state['sequence_or_path'].startswith(">"):
        header = state['sequence_or_path'].splitlines()[0]
    
    if header:
        if any(p in header for p in ['NC_', 'NM_']):
            return {"sequence_type": "DNA"}
        if any(p in header for p in ['NP_', 'XP_', 'SP|', 'TR|']):
            return {"sequence_type": "PROTEIN"}
            
    # 3. Heuristics - File Extension
    if state['input_type'] == 'FILEPATH':
        ext = os.path.splitext(path)[1].lower()
        if ext in ['.fna', '.ffn']:
            return {"sequence_type": "DNA"}
        if ext in ['.faa']:
            return {"sequence_type": "PROTEIN"}
            
    # 4. LLM Guess
    llm = ChatMistralAI(model="mistral-small-latest")
    
    snippet = sequence[:100]
    chars = "".join(set(sequence.upper()))
    
    guess_prompt = (
        f"Classify the following biological sequence as 'DNA' or 'PROTEIN'.\n"
        f"Sequence snippet: {snippet}\n"
        f"Sequence length: {len(sequence)}\n"
        f"Characters present: {chars}\n"
        f"Provide your answer as a single word: either 'DNA' or 'PROTEIN'."
    )
    
    response = llm.invoke([HumanMessage(content=guess_prompt)])
    guess = response.content.strip().upper()
    
    if "DNA" in guess:
        return {"sequence_type": "DNA"}
    else:
        return {"sequence_type": "PROTEIN"}

def preprocess_sequence_node(state: GraphState) -> Dict[str, Any]:
    """
    Translates DNA to protein if necessary.
    """
    if state.get('error'): return {}
    
    sequence = state['sequence']
    seq_type = state['sequence_type']
    
    if seq_type == 'DNA':
        try:
            # Ensure length is multiple of 3 for simple translation
            # In a real scenario we might need to find ORF, but here we follow instructions
            # truncate to multiple of 3 if needed? Or just pass through and let it fail if user passed partial codon?
            # Instructions say: "If it is a DNA sequence, it calls the function translate_dna_to_protein from utils"
            protein_seq = translate_dna_to_protein(sequence)
            return {"protein_sequence": protein_seq}
        except Exception as e:
            # Try to handle common DNA translation issues (length % 3)
            if "divisible by 3" in str(e):
                truncated_seq = sequence[:(len(sequence) // 3) * 3]
                try:
                    protein_seq = translate_dna_to_protein(truncated_seq)
                    return {"protein_sequence": protein_seq}
                except:
                    pass
            return {"error": f"Failed to translate DNA: {str(e)}"}
    else:
        return {"protein_sequence": sequence}

def rank_node(state: GraphState) -> Dict[str, Any]:
    """
    Perform sequence similarity search (top 50).
    """
    if state.get('error'): return {}
    
    protein_sequence = state['protein_sequence']
    
    try:
        # Resource paths - could be configurable
        H5_PATH = "data/per-protein.h5"
        INDEX_PATH = "data/per-protein.index"
        CACHE_PATH = "data/per-protein.accessions.pkl"
        
        index, accessions = get_or_create_index(H5_PATH, INDEX_PATH, CACHE_PATH)
        embedder_tools = get_prottrans_embedder()
        
        print(f"Ranking: searching for top 50 sequences...")
        matches = search_top_k(protein_sequence, embedder_tools, index, accessions, k=50)
        
        accession_list = [m[0] for m in matches]
        records = get_uniprot_records(accession_list)
        
        return {"ranked_results": records}
    except Exception as e:
        return {"error": f"Ranking failed: {str(e)}"}

def rerank_node(state: GraphState) -> Dict[str, Any]:
    """
    Perform contextual reranking (top 5).
    """
    if state.get('error'): return {}
    
    ranked_results = state['ranked_results']
    context = state['context']
    
    try:
        print("Reranking by context...")
        reranker = LocalReranker()
        final_records = reranker.rerank_by_context(ranked_results, context, top_n=5)
        return {"final_results": final_records}
    except Exception as e:
        return {"error": f"Reranking failed: {str(e)}"}

# --- Graph Construction ---

def create_pipeline():
    workflow = StateGraph(GraphState)
    
    workflow.add_node("extract_input", extract_input_node)
    workflow.add_node("resolve_sequence", resolve_sequence_node)
    workflow.add_node("classify_type", classify_type_node)
    workflow.add_node("preprocess", preprocess_sequence_node)
    workflow.add_node("rank", rank_node)
    workflow.add_node("rerank", rerank_node)
    
    workflow.set_entry_point("extract_input")
    
    workflow.add_edge("extract_input", "resolve_sequence")
    workflow.add_edge("resolve_sequence", "classify_type")
    workflow.add_edge("classify_type", "preprocess")
    workflow.add_edge("preprocess", "rank")
    workflow.add_edge("rank", "rerank")
    workflow.add_edge("rerank", END)
    
    return workflow.compile()

def run_bioseq_pipeline(prompt: str):
    pipeline = create_pipeline()
    initial_state = {
        "prompt": prompt,
        "sequence_or_path": None,
        "input_type": None,
        "context": None,
        "sequence": None,
        "sequence_type": None,
        "protein_sequence": None,
        "ranked_results": None,
        "final_results": None,
        "error": None
    }
    
    final_state = pipeline.invoke(initial_state)
    return final_state

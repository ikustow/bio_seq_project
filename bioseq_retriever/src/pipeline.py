import os
import json
from typing import List, Dict, Any, Tuple, Optional, TypedDict, Literal
from pydantic import BaseModel, Field

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

from src.config import (
    DEFAULT_H5_PATH, 
    DEFAULT_INDEX_PATH, 
    DEFAULT_CACHE_PATH, 
    ALLOWED_DATA_DIR
)

# --- Helper Functions ---

def is_secure_path(path: str) -> bool:
    """Verifies if the path is within the allowed directory."""
    abs_allowed = os.path.abspath(ALLOWED_DATA_DIR)
    abs_path = os.path.abspath(path)
    return abs_path.startswith(abs_allowed)

# --- Node Functions ---

def extract_and_classify_node(state: GraphState) -> Dict[str, Any]:
    """
    Uses LLM with structured output to extract data and classify sequence type.
    """
    if state.get("error"): return {}
    llm = get_llm(temperature=0)
    structured_llm = llm.with_structured_output(InputExtraction)
    
    system_message = (
        "You are an expert bioinformatics analyst specializing in sequence identification and data extraction. "
        "Your mission is to parse user input to extract biological data and determine its molecular nature with high precision. "
        "You must follow this multi-step Chain-of-Thought process:\n\n"
        "### 1. EXTRACTION STRATEGY\n"
        "Your first priority is to separate the core data from the surrounding metadata.\n"
        "- Biological Sequence: Look for strings composed of single-letter codes. They may appear as raw text or within a FASTA format (starting with a '>' header line). DNA sequences usually consist of A, T, G, C, while Proteins use a wider 20-letter alphabet (M, A, L, etc.).\n"
        "- File Path: Identify strings that resemble filesystem paths (e.g., 'data/sample.fasta', 'C:\\Sequences\\test.faa', './output.txt'). These point to external files containing sequences.\n"
        "- Contextual Information: Everything else is context. This includes user instructions (e.g., 'Find the closest matches'), biological metadata ('This is from a mouse'), or specific queries.\n"
        "*Rule*: If both a sequence and a path are present, prioritize the sequence for the 'sequence_or_path' field and note the path in the reasoning.\n\n"
        "### 2. CLASSIFICATION REASONING\n"
        "This is the most complex task. You must classify the sequence as either DNA or PROTEIN by weighing multiple evidence tiers:\n\n"
        "- Tier A: Explicit Hints & Metadata: Search the prompt and FASTA headers for keywords.\n"
        "  * DNA/RNA Indicators: 'DNA', 'RNA', 'nucleotide', 'transcript', 'gene', 'genome'. RefSeq prefixes: 'NM_', 'NR_', 'XM_', 'XR_'.\n"
        "  * Protein Indicators: 'protein', 'peptide', 'amino acid', 'ORF', 'proteome'. RefSeq prefixes: 'NP_', 'XP_', 'YP_'.\n"
        "- Tier B: Structural Cues: Look at the file extension if a path is provided.\n"
        "  * DNA: .fna, .fasta, .fa, .nuc.\n"
        "  * Protein: .faa, .pro, .pep.\n"
        "- Tier C: Character Composition Analysis:\n"
        "  * DNA: Strictly limited to {A, C, G, T, U} and IUPAC ambiguity codes {N, R, Y, K, M, S, W, B, D, H, V}. If the sequence contains characters like {L, I, V, F, W, Y, P, Q, E, K, H, R}, it is almost certainly a PROTEIN.\n"
        "  * Protein: Uses a much broader character set. The presence of 'L' (Leucine), 'F' (Phenylalanine), or 'W' (Tryptophan) is a strong indicator of protein, as these are common in proteins but never found in DNA (except as errors).\n"
        "  * Ambiguity Check: A sequence like 'ACGT' is ambiguous but defaults to DNA. A sequence like 'MAPLR' is unambiguously PROTEIN.\n"
        "- Tier D: Length and Pattern: Consider the length. Very short sequences might require more weight on Tier A.\n\n"
        "### 3. CONFIDENCE ASSESSMENT\n"
        "Evaluate the internal consistency of the evidence.\n"
        "- High Confidence (is_confident = True): Evidence across Tiers A, B, and C is consistent (e.g., user says 'protein' and the sequence contains 'L' and 'W').\n"
        "- Low Confidence (is_confident = False): Evidence is contradictory (e.g., user says 'DNA' but the sequence contains 'P' and 'Q'), or the sequence is too short and lacks any Tier A/B markers (e.g., a raw 'AAAAAA' with no context).\n\n"
        "Deliver your findings in the requested structured format, ensuring the 'reasoning' field reflects this multi-tier analysis."
    )
    
    try:
        result = structured_llm.invoke([
            SystemMessage(content=system_message),
            HumanMessage(content=state['prompt'])
        ])
        
        return {
            "sequence_or_path": result.sequence_or_path,
            "input_type": result.input_type,
            "context": result.context,
            "sequence_type": result.sequence_type,
            "is_confident": result.is_confident
        }
    except Exception as e:
        return {"error": f"Extraction failed: {str(e)}"}

def resolve_filepath_node(state: GraphState) -> Dict[str, Any]:
    """Node to resolve sequence from a file path with security check."""
    if state.get("error"): return {}
    path = state['sequence_or_path']
    if not is_secure_path(path):
        return {"error": f"Security violation: path {path} is not in {ALLOWED_DATA_DIR}"}
        
    try:
        fasta_entry = get_first_fasta_entry(path)
        lines = fasta_entry.splitlines()
        sequence = "".join(lines[1:]) if len(lines) > 1 else ""
        return {"sequence": sequence}
    except Exception as e:
        return {"error": f"File resolution failed: {str(e)}"}

def use_raw_sequence_node(state: GraphState) -> Dict[str, Any]:
    """Node to handle raw sequence input."""
    if state.get("error"): return {}
    seq = state['sequence_or_path']
    if seq.startswith(">"):
        lines = seq.splitlines()
        seq = "".join(lines[1:])
    return {"sequence": seq}

def translate_dna_node(state: GraphState) -> Dict[str, Any]:
    """Node to translate DNA to protein."""
    if state.get("error"): return {}
    try:
        protein_seq = translate_dna_to_protein(state['sequence'])
        return {"protein_sequence": protein_seq}
    except Exception as e:
        return {"error": f"Translation failed: {str(e)}"}

def pass_protein_node(state: GraphState) -> Dict[str, Any]:
    """Node for when sequence is already protein."""
    if state.get("error"): return {}
    return {"protein_sequence": state['sequence']}

def rank_node(state: GraphState) -> Dict[str, Any]:
    """Performs sequence similarity search (Top 50)."""
    if state.get('error'): return {}
    try:
        os.makedirs(os.path.dirname(DEFAULT_INDEX_PATH) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(DEFAULT_CACHE_PATH) or ".", exist_ok=True)
        index, accessions = get_or_create_index(DEFAULT_H5_PATH, DEFAULT_INDEX_PATH, DEFAULT_CACHE_PATH)
        embedder_tools = get_prottrans_embedder()
        matches = search_top_k(state['protein_sequence'], embedder_tools, index, accessions, k=50)
        records = get_uniprot_records([m[0] for m in matches])
        return {"ranked_results": records}
    except Exception as e:
        return {"error": f"Ranking failed: {str(e)}"}

def rerank_node(state: GraphState) -> Dict[str, Any]:
    """Performs contextual reranking (Top 5)."""
    if state.get('error'): return {}
    try:
        reranker = LocalReranker()
        final_records = reranker.rerank_by_context(state['ranked_results'], state['context'], top_n=5)
        return {"final_results": final_records}
    except Exception as e:
        return {"error": f"Reranking failed: {str(e)}"}

# --- Conditional Routing Logic ---

def check_error(state: GraphState) -> Literal["error", "continue"]:
    """Conditional edge to check for errors and short-circuit to END."""
    return "error" if state.get("error") else "continue"

def should_resolve_filepath(state: GraphState) -> Literal["resolve", "raw", "error"]:
    if state.get('error'): return "error"
    return "resolve" if state['input_type'] == "FILEPATH" else "raw"

def should_translate(state: GraphState) -> Literal["translate", "skip", "error"]:
    if state.get('error'): return "error"
    return "translate" if state['sequence_type'] == "DNA" else "skip"

# --- Graph Construction ---

def create_pipeline():
    workflow = StateGraph(GraphState)
    
    workflow.add_node("extract", extract_and_classify_node)
    workflow.add_node("resolve_file", resolve_filepath_node)
    workflow.add_node("use_raw", use_raw_sequence_node)
    workflow.add_node("translate", translate_dna_node)
    workflow.add_node("pass_protein", pass_protein_node)
    workflow.add_node("rank", rank_node)
    workflow.add_node("rerank", rerank_node)
    
    workflow.set_entry_point("extract")
    
    workflow.add_conditional_edges(
        "extract",
        should_resolve_filepath,
        {
            "resolve": "resolve_file",
            "raw": "use_raw",
            "error": END
        }
    )
    
    workflow.add_conditional_edges(
        "resolve_file",
        should_translate,
        {
            "translate": "translate",
            "skip": "pass_protein",
            "error": END
        }
    )
    workflow.add_conditional_edges(
        "use_raw",
        should_translate,
        {
            "translate": "translate",
            "skip": "pass_protein",
            "error": END
        }
    )
    
    workflow.add_conditional_edges("translate", check_error, {"error": END, "continue": "rank"})
    workflow.add_conditional_edges("pass_protein", check_error, {"error": END, "continue": "rank"})
    workflow.add_conditional_edges("rank", check_error, {"error": END, "continue": "rerank"})
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
        "is_confident": None,
        "ranked_results": None,
        "final_results": None,
        "error": None
    }
    return pipeline.invoke(initial_state)

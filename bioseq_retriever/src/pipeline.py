import os
import json
from typing import List, Dict, Any, Tuple, Optional, TypedDict, Literal
from pydantic import BaseModel, Field

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

from .utils import (
    get_first_fasta_entry,
    translate_dna_to_protein,
    get_llm,
)
from .embeddings import get_or_create_index
from .search import get_prottrans_embedder, search_top_k
from .data_fetcher import get_uniprot_records
from .reranking import LocalReranker

# --- Structured Output Models ---

class InputExtraction(BaseModel):
    sequence_or_path: str = Field(description="The extracted raw biological sequence or the file path.")
    input_type: Literal["SEQUENCE", "FILEPATH"] = Field(description="Whether the input is a raw sequence or a file path.")
    context: str = Field(description="Any contextual information, questions, or hints provided by the user.")
    sequence_type: Literal["DNA", "PROTEIN"] = Field(description="The classified type of the biological sequence.")
    is_confident: bool = Field(description="True if the LLM is highly confident in the sequence type classification.")
    reasoning: str = Field(description="Brief chain-of-thought reasoning for the extraction and classification.")

# --- State Definition ---

class GraphState(TypedDict):
    prompt: str
    sequence_or_path: Optional[str]
    input_type: Optional[str]
    context: Optional[str]
    sequence: Optional[str]
    sequence_type: Optional[str]
    protein_sequence: Optional[str]
    is_confident: Optional[bool]
    ranked_results: Optional[List[Dict[str, Any]]]
    final_results: Optional[List[Dict[str, Any]]]
    error: Optional[str]

# --- Node Functions ---

def extract_and_classify_node(state: GraphState) -> Dict[str, Any]:
    """
    Uses LLM with structured output to extract data and classify sequence type.
    """
    llm = get_llm(temperature=0)
    structured_llm = llm.with_structured_output(InputExtraction)
    
    system_message = (
        "You are an expert bioinformatics analyst. Your task is to extract input data and classify the biological sequence type.\n"
        "Guidelines:\n"
        "1. Extraction: Find the sequence (e.g., MALW...) or the file path (e.g., data/seq.faa). Extract everything else as context.\n"
        "2. Reasoning: Analyze hints ('DNA', 'peptide'), headers ('NP_', 'NM_'), and character composition.\n"
        "3. Confidence: Be confident if there are clear markers; mark False if the input is ambiguous or contradictory."
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
    """Node to resolve sequence from a file path."""
    try:
        fasta_entry = get_first_fasta_entry(state['sequence_or_path'])
        lines = fasta_entry.splitlines()
        sequence = "".join(lines[1:]) if len(lines) > 1 else ""
        return {"sequence": sequence}
    except Exception as e:
        return {"error": f"File resolution failed: {str(e)}"}

def use_raw_sequence_node(state: GraphState) -> Dict[str, Any]:
    """Node to handle raw sequence input."""
    seq = state['sequence_or_path']
    if seq.startswith(">"):
        lines = seq.splitlines()
        seq = "".join(lines[1:])
    return {"sequence": seq}

def translate_dna_node(state: GraphState) -> Dict[str, Any]:
    """Node to translate DNA to protein."""
    try:
        protein_seq = translate_dna_to_protein(state['sequence'])
        return {"protein_sequence": protein_seq}
    except Exception as e:
        return {"error": f"Translation failed: {str(e)}"}

def pass_protein_node(state: GraphState) -> Dict[str, Any]:
    """Node for when sequence is already protein."""
    return {"protein_sequence": state['sequence']}

def rank_node(state: GraphState) -> Dict[str, Any]:
    """Performs sequence similarity search (Top 50)."""
    if state.get('error'): return {}
    try:
        H5_PATH, INDEX_PATH, CACHE_PATH = "data/per-protein.h5", "data/per-protein.index", "data/per-protein.accessions.pkl"
        index, accessions = get_or_create_index(H5_PATH, INDEX_PATH, CACHE_PATH)
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

def should_resolve_filepath(state: GraphState) -> Literal["resolve", "raw"]:
    if state.get('error'): return "raw"
    return "resolve" if state['input_type'] == "FILEPATH" else "raw"

def should_translate(state: GraphState) -> Literal["translate", "skip"]:
    if state.get('error'): return "skip"
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
            "raw": "use_raw"
        }
    )
    
    workflow.add_conditional_edges(
        "resolve_file",
        should_translate,
        {
            "translate": "translate",
            "skip": "pass_protein"
        }
    )
    workflow.add_conditional_edges(
        "use_raw",
        should_translate,
        {
            "translate": "translate",
            "skip": "pass_protein"
        }
    )
    
    workflow.add_edge("translate", "rank")
    workflow.add_edge("pass_protein", "rank")
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
        "is_confident": None,
        "ranked_results": None,
        "final_results": None,
        "error": None
    }
    return pipeline.invoke(initial_state)

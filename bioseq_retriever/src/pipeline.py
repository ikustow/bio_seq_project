from typing import List, Dict, Any, Optional, TypedDict, Literal
from pydantic import BaseModel, Field

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

from src.utils import get_llm, translate_dna_to_protein, get_first_fasta_entry, is_secure_path, clean_sequence
from src.data_fetcher import get_uniprot_records
from src.search import search_top_k
from src.reranking import LocalReranker

from src.config import ALLOWED_DATA_DIR

# --- State Definitions ---

class InputExtraction(BaseModel):
    sequence_or_path: str = Field(description="The extracted raw biological sequence or the file path.")
    input_type: Literal["SEQUENCE", "FILEPATH"] = Field(description="Whether the input is a raw sequence or a file path.")
    context: str = Field(description="Any contextual information, questions, or hints provided by the user.")
    sequence_type: Literal["DNA", "PROTEIN"] = Field(description="The classified type of the biological sequence.")
    is_confident: bool = Field(description="True if the LLM is highly confident in the sequence type classification.")
    reasoning: str = Field(description="Brief chain-of-thought reasoning for the extraction and classification.")

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
    if state.get("error"): return {}
    llm = get_llm(temperature=0)
    structured_llm = llm.with_structured_output(InputExtraction)
    
    system_message = (
        "You are an expert bioinformatics analyst specializing in sequence identification and data extraction. "
        "Your mission is to parse user input to extract biological data and determine its molecular nature with high precision. "
        "You must follow this multi-step Chain-of-Thought process:\n\n"
        "### 1. EXTRACTION STRATEGY\n"
        "Your first priority is to separate the core data from the surrounding metadata.\n"
        "- Biological Sequence: Look for strings composed of single-letter codes. They may appear as raw text or within a FASTA format (starting with a '>' header line).\n"
        "- File Path: Identify strings that resemble filesystem paths (e.g., 'data/sample.fasta').\n"
        "- Contextual Information: Everything else is context.\n"
        "*Rule*: If both a sequence and a path are present, prioritize the sequence.\n\n"
        "### 2. CLASSIFICATION REASONING\n"
        "Classify as DNA or PROTEIN based on character set and metadata.\n"
        "### 3. CONFIDENCE ASSESSMENT\n"
        "Deliver findings in structured format."
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
        header, sequence = get_first_fasta_entry(path)
        # Header is part of the context now
        new_context = f"{state.get('context') or ''}\nFASTA Header: {header}".strip()
        return {
            "sequence": sequence,
            "context": new_context
        }
    except Exception as e:
        return {"error": f"File resolution failed: {str(e)}"}

def use_raw_sequence_node(state: GraphState) -> Dict[str, Any]:
    """Node to handle raw sequence input with cleanup."""
    if state.get("error"): return {}
    seq = state['sequence_or_path']
    cleaned_seq = clean_sequence(seq)
    return {"sequence": cleaned_seq}

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
    """Performs sequence similarity search via service client."""
    if state.get('error'): return {}
    try:
        matches = search_top_k(state['protein_sequence'], k=50)
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
    
    workflow.add_conditional_edges("extract", should_resolve_filepath, {"resolve": "resolve_file", "raw": "use_raw", "error": END})
    workflow.add_conditional_edges("resolve_file", should_translate, {"translate": "translate", "skip": "pass_protein", "error": END})
    workflow.add_conditional_edges("use_raw", should_translate, {"translate": "translate", "skip": "pass_protein", "error": END})
    
    workflow.add_conditional_edges("translate", check_error, {"error": END, "continue": "rank"})
    workflow.add_conditional_edges("pass_protein", check_error, {"error": END, "continue": "rank"})
    workflow.add_conditional_edges("rank", check_error, {"error": END, "continue": "rerank"})
    workflow.add_edge("rerank", END)
    
    return workflow.compile()

async def run_bioseq_pipeline(prompt: str):
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
    # Using ainvoke as requested
    return await pipeline.ainvoke(initial_state)

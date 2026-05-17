from typing import List, Dict, Any, Optional, TypedDict, Literal, Union
from pydantic import BaseModel, Field

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

import socket
from urllib.parse import urlparse

from src.utils import get_llm, get_first_fasta_entry, is_secure_path, clean_sequence
from src.data_fetcher import get_uniprot_records
from src.search import search_top_k, search_dna_top_k, blast_search
from src.reranking import LocalReranker

from src.config import ALLOWED_DATA_DIR, SEARCH_SERVICE_URL


def _rerank_service_alive(url: str = SEARCH_SERVICE_URL, timeout: float = 0.5) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

# --- Schema-Guided Reasoning Router Definition ---

class SequenceAnalysis(BaseModel):
    """Guided reasoning for raw biological sequences."""
    kind: Literal["sequence"] = Field(description="Discriminator for sequence-based routing.")
    
    step_1_alphabet_and_molecular_signature: str = Field(description="Examine the unique characters. Search for 'M', 'W', 'Y', 'K' which strongly indicate Protein, vs high 'A,T,G,C' density which suggests DNA.")
    step_2_functional_clues_from_context: str = Field(description="Analyze the prompt for mentions of genes, enzymes, translation, or specific biological processes that clarify the sequence's nature.")
    step_3_certainty_validation: str = Field(description="Synthesize steps 1 & 2. Are you 100% certain? If any ambiguity exists (e.g. short AAAAAA without context), this classification must be considered failed.")
    
    sequence_type: Literal["DNA", "PROTEIN"] = Field(description="The finalized molecular classification based on the preceding reasoning.")
    
    step_4_query_expansion_and_enrichment: str = Field(description="Expand the user's biological intent into rich metadata (synonyms, GO terms, metabolic pathways) to improve search precision.")
    
    raw_sequence: str = Field(description="The extracted raw sequence string.")
    extracted_context: str = Field(description="The original user constraints and questions.")

class FilePathAnalysis(BaseModel):
    """Guided reasoning for filesystem paths."""
    kind: Literal["filepath"] = Field(description="Discriminator for path-based routing.")
    
    step_1_extension_integrity_check: str = Field(description="Evaluate the file extension. .faa/.pep implies Protein; .fna/.nuc implies DNA; .fasta/.fa is ambiguous.")
    step_2_contextual_verification: str = Field(description="Does the user refer to this path as a 'protein file', 'gene sequence', or 'FASTA'? Match extension to context.")
    step_3_certainty_validation: str = Field(description="Are the extension and context consistent and sufficient for a 100% certain classification? If extension is missing or ambiguous and context is thin, this is a failure.")
    
    sequence_type: Literal["DNA", "PROTEIN"] = Field(description="The finalized molecular classification based on the preceding reasoning.")
    
    step_4_query_expansion_and_enrichment: str = Field(description="Expand the user's biological intent into rich metadata (synonyms, GO terms, metabolic pathways) to improve search precision.")
    
    path: str = Field(description="The extracted filesystem path.")
    extracted_context: str = Field(description="The original user constraints and questions.")

class ExtractionError(BaseModel):
    """Guided reasoning for invalid or ambiguous inputs."""
    kind: Literal["error"] = Field(description="Discriminator for error routing.")
    
    step_1_failure_analysis: str = Field(description="Provide a detailed technical breakdown of why the input is invalid. Is data missing? Is the classification ambiguous? Is the file type unsupported?")
    error_message: str = Field(description="A clear, professional error message to be returned to the user.")

class PipelineRouter(BaseModel):
    """Main Schema-Guided Reasoning entry point."""
    step_1_data_extraction_and_intent_mapping: str = Field(description="First, identify any strings resembling sequences or paths and isolate the user's natural language instructions.")
    step_2_routing_logic: str = Field(description="Based on Step 1, decide which specialized analysis branch to follow (sequence, filepath, or error).")
    
    analysis: Union[SequenceAnalysis, FilePathAnalysis, ExtractionError] = Field(description="The detailed, branch-specific reasoning and final data extraction.")

class GraphState(TypedDict):
    prompt: str
    sequence_or_path: Optional[str]
    input_type: Optional[str]
    context: Optional[str]
    sequence: Optional[str]
    sequence_type: Optional[str]
    ranked_results: Optional[List[Dict[str, Any]]]
    final_results: Optional[List[Dict[str, Any]]]
    error: Optional[str]
    # "embeddings" (default, ProtT5+FAISS via search-service) or "blast" (EBI REST).
    search_algorithm: Optional[str]

# --- Node Functions ---

def extract_and_classify_node(state: GraphState) -> Dict[str, Any]:
    """
    Uses an advanced Schema-Guided Router Pattern to analyze user input. 
    The schema forces the LLM to reason through extraction, character analysis, 
    and extension checking before committing to a routing decision.
    """
    if state.get("error"): return {}
    llm = get_llm(temperature=0)
    structured_llm = llm.with_structured_output(PipelineRouter)
    
    system_message = (
        "You are an elite bioinformatics data architect and routing engine. Your mission is to process raw user prompts "
        "and route them into a high-precision biological analysis pipeline. You must operate with absolute "
        "biological accuracy and strictly follow the schema-guided reasoning process. Each field in your response "
        "represents a mandatory step in your analytical chain of thought.\n\n"
        
        "### ANALYSIS PROTOCOL:\n"
        "1. **EXTRACT**: Isolate biological sequences (IUPAC codes) or filesystem paths (e.g. data/seq.fasta).\n"
        "2. **ROUTE**: Select the branch based on the strongest evidence. \n"
        "   - Use `SequenceAnalysis` if a raw string is found.\n"
        "   - Use `FilePathAnalysis` if a valid path is found.\n"
        "   - Use `ExtractionError` if data is missing or classification is uncertain.\n"
        "3. **REASON**: Within your chosen branch, perform the specific checks (character set for sequences, extensions for paths) "
        "to determine the molecular nature (DNA or PROTEIN).\n"
        "4. **VALIDATE**: If you are not 100% certain of the sequence type or if the input is contradictory, you MUST route to ERROR.\n"
        "5. **EXPAND**: For successful routes, enrich the user's intent by expanding natural language terms into "
        "precise biological processes, synonyms, and GO categories to aid downstream reranking.\n\n"
        
        "Your responses must be elaborate, generous in detail, and demonstrate a profound understanding of molecular biology."
    )
    
    try:
        result = structured_llm.invoke([
            SystemMessage(content=system_message),
            HumanMessage(content=state['prompt'])
        ])
        
        analysis = result.analysis
        
        # Branch Handling
        if analysis.kind == "error":
            return {"error": f"Router Error: {analysis.error_message} (Analysis: {analysis.step_1_failure_analysis})"}
        
        # Success Handling (Sequence or Path)
        is_path = analysis.kind == "filepath"
        return {
            "sequence_or_path": analysis.path if is_path else analysis.raw_sequence,
            "input_type": "FILEPATH" if is_path else "SEQUENCE",
            "context": f"{analysis.extracted_context}\nEnrichment: {analysis.step_4_query_expansion_and_enrichment}",
            "sequence_type": analysis.sequence_type,
            "error": None
        }
        
    except Exception as e:
        return {"error": f"Extraction/Routing Pipeline Failure: {str(e)}"}

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

def rank_dna_node(state: GraphState) -> Dict[str, Any]:
    """Performs DNA sequence similarity search (Top 50) via DNA search service."""
    if state.get('error'): return {}
    try:
        # Uses raw DNA sequence for search
        matches = search_dna_top_k(state['sequence'], k=50)
        records = get_uniprot_records([m[0] for m in matches])
        return {"ranked_results": records}
    except Exception as e:
        return {"error": f"DNA Ranking failed: {str(e)}"}

def rank_node(state: GraphState) -> Dict[str, Any]:
    """Performs sequence similarity search via the selected backend."""
    if state.get('error'): return {}
    try:
        algorithm = (state.get("search_algorithm") or "embeddings").lower()
        if algorithm == "blast":
            # BLAST is slower than FAISS and returns at most ~10 hits anyway;
            # we ask for 10 to give rerank something to reorder if a context
            # query was supplied. Hardcoded to SwissProt for speed/quality.
            matches = blast_search(state['sequence'], k=10)
        else:
            matches = search_top_k(state['sequence'], k=50)
        records = get_uniprot_records([m[0] for m in matches])
        return {"ranked_results": records}
    except Exception as e:
        return {"error": f"Ranking failed: {str(e)}"}

def rerank_node(state: GraphState) -> Dict[str, Any]:
    """Performs contextual reranking (Top 5)."""
    if state.get('error'): return {}
    ranked = state.get('ranked_results') or []
    if not _rerank_service_alive():
        print(f"Rerank service unreachable at {SEARCH_SERVICE_URL}; using top-5 of ranked_results.")
        return {"final_results": ranked[:5]}
    try:
        reranker = LocalReranker()
        # Takes top 50 matches (DNA or Protein) and reranks them
        final_records = reranker.rerank_by_context(ranked, state['context'], top_n=5)
        return {"final_results": final_records}
    except Exception as e:
        print(f"Rerank skipped ({e}); falling back to top-5 of ranked_results.")
        return {"final_results": ranked[:5]}

# --- Conditional Routing Logic ---

def check_error(state: GraphState) -> Literal["error", "continue"]:
    return "error" if state.get("error") else "continue"

def should_resolve_filepath(state: GraphState) -> Literal["resolve", "raw", "error"]:
    if state.get('error'): return "error"
    return "resolve" if state['input_type'] == "FILEPATH" else "raw"

def should_rank(state: GraphState) -> Literal["rank_dna", "protein_path", "error"]:
    if state.get('error'): return "error"
    return "rank_dna" if state['sequence_type'] == "DNA" else "protein_path"

# --- Graph Construction ---

def create_pipeline():
    workflow = StateGraph(GraphState)
    
    workflow.add_node("extract", extract_and_classify_node)
    workflow.add_node("resolve_file", resolve_filepath_node)
    workflow.add_node("use_raw", use_raw_sequence_node)
    workflow.add_node("rank_dna", rank_dna_node)
    workflow.add_node("rank", rank_node)
    workflow.add_node("rerank", rerank_node)
    
    workflow.set_entry_point("extract")
    
    workflow.add_conditional_edges("extract", should_resolve_filepath, {"resolve": "resolve_file", "raw": "use_raw", "error": END})
    
    # After resolution/raw input, branch to DNA search or Protein search path
    workflow.add_conditional_edges("resolve_file", should_rank, {"rank_dna": "rank_dna", "protein_path": "rank", "error": END})
    workflow.add_conditional_edges("use_raw", should_rank, {"rank_dna": "rank_dna", "protein_path": "rank", "error": END})
    
    # Convergence points
    workflow.add_conditional_edges("rank_dna", check_error, {"error": END, "continue": "rerank"})
    workflow.add_conditional_edges("rank", check_error, {"error": END, "continue": "rerank"})
    
    workflow.add_edge("rerank", END)
    
    return workflow.compile()

async def run_bioseq_pipeline(prompt: str, search_algorithm: str = "embeddings"):
    pipeline = create_pipeline()
    initial_state = {
        "prompt": prompt,
        "sequence_or_path": None,
        "input_type": None,
        "context": None,
        "sequence": None,
        "sequence_type": None,
        "ranked_results": None,
        "final_results": None,
        "error": None,
        "search_algorithm": search_algorithm,
    }
    # Using ainvoke as requested
    return await pipeline.ainvoke(initial_state)

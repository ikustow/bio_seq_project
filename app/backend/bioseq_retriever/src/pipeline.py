from typing import List, Dict, Any, Optional, TypedDict, Literal, Union
from pydantic import BaseModel, Field

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

import re
import socket
from urllib.parse import urlparse

from src.utils import get_llm, get_first_fasta_entry, is_secure_path, clean_sequence
from src.data_fetcher import get_uniprot_records
from src.search import search_protein_top_k, search_dna_top_k, blast_search
from src.reranking import LocalReranker

from src.config import ALLOWED_DATA_DIR, SEARCH_SERVICE_URL, RETRIEVAL_TOP_K, RERANK_TOP_N


def _rerank_service_alive(url: str = SEARCH_SERVICE_URL, timeout: float = 0.5) -> bool:
    """Cheap liveness probe for the rerank gateway.

    The rerank node calls a network service; if it's down the LocalReranker
    raises and we'd lose the ranked top-50 entirely. A 0.5s TCP connect is
    enough to decide whether to attempt the call or short-circuit to the
    top-5 slice of what we already have.
    """
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# =============================================================================
# SCHEMA-GUIDED REASONING ROUTER DEFINITIONS
# =============================================================================

class AnalysisFailure(BaseModel):
    """Unified error state for all failure points in the analytical cascade."""
    kind: Literal["error"] = Field(description="Discriminator for error outcomes.")
    failure_stage: Literal["INITIAL_ROUTING", "SEQUENCE_VALIDATION", "PATH_VALIDATION"] = Field(description="The logical stage where the analysis failed.")
    error_message: str = Field(description="A clear, professional error message to be returned to the user.")
    technical_root_cause: str = Field(description="Elaborate technical explanation of the failure for debugging.")

# --- Success Outcome Schemas ---

class SequenceSuccess(BaseModel):
    """Final state for a successfully classified biological sequence with identified entities."""
    kind: Literal["sequence_success"]
    sequence_type: Literal["DNA", "PROTEIN"] = Field(description="The finalized molecular nature.")
    raw_sequence: str = Field(description="The extracted raw sequence string.")
    context: str = Field(description="Extract the broadest possible biologically relevant semantic context from the query, including biological entities, genes, proteins, domains, functions, pathways, processes, molecular interactions, structural features, localization, taxonomy, evolutionary relationships, diseases, phenotypes, experimental evidence, ontology terms, regulatory relationships, host-pathogen context, biochemical activities, cellular context, and inferred biological associations. Preserve broad contextual and relational information with high semantic recall, including weakly implied or partially related concepts, without aggressive filtering or compression, since downstream instruction-aware embedding will refine and prioritize the signal.")

class FilePathSuccess(BaseModel):
    """Final state for a successfully classified filesystem path with identified entities."""
    kind: Literal["filepath_success"]
    sequence_type: Literal["DNA", "PROTEIN"] = Field(description="The finalized molecular nature.")
    path: str = Field(description="The extracted filesystem path.")
    context: str = Field(description="Extract the broadest possible biologically relevant semantic context from the query, including biological entities, genes, proteins, domains, functions, pathways, processes, molecular interactions, structural features, localization, taxonomy, evolutionary relationships, diseases, phenotypes, experimental evidence, ontology terms, regulatory relationships, host-pathogen context, biochemical activities, cellular context, and inferred biological associations. Preserve broad contextual and relational information with high semantic recall, including weakly implied or partially related concepts, without aggressive filtering or compression, since downstream instruction-aware embedding will refine and prioritize the signal.")

# --- Reasoning Cascade Branches ---

class SequenceAnalysis(BaseModel):
    """Reasoning cascade for biological sequence strings. Implements Step-by-Step validation."""
    kind: Literal["sequence"] = Field(description="Discriminator for sequence-based routing.")
    
    step_1_alphabet_and_molecular_signature: str = Field(description="Examine the unique characters. Search for non-nucleic 'M,W,Y,K' vs high 'A,T,G,C' density.")
    step_2_functional_clues_from_context: str = Field(description="Analyze the prompt for functional mentions (e.g. 'coding gene', 'translation') to support the alphabet signals.")
    step_3_certainty_validation: str = Field(description="Synthesize steps 1 & 2. Are you 100% certain? If any ambiguity exists, route to 'error' in the final outcome.")
    
    final_outcome: Union[SequenceSuccess, AnalysisFailure] = Field(description="The terminal result of the sequence reasoning path.")

class FilePathAnalysis(BaseModel):
    """Reasoning cascade for filesystem paths. Implements Step-by-Step validation."""
    kind: Literal["filepath"] = Field(description="Discriminator for path-based routing.")
    
    step_1_extension_integrity_check: str = Field(description="Evaluate the file extension (.faa, .fna, .fasta). Determine if it is explicit or ambiguous.")
    step_2_contextual_verification: str = Field(description="Does the user refer to this path as a 'protein file', 'gene sequence', or 'FASTA'? Match extension to context.")
    step_3_certainty_validation: str = Field(description="Are the extension and context consistent and sufficient for a 100% certain classification? If not, route to 'error' in the final outcome.")
    
    final_outcome: Union[FilePathSuccess, AnalysisFailure] = Field(description="The terminal result of the path reasoning path.")

# --- Master Router Root ---

class PipelineRouter(BaseModel):
    """Master router and orchestrator for schema-guided biological analysis. Entry point of the cascade."""
    step_1_data_extraction_and_mapping: str = Field(description="Initial extraction of candidate sequences, paths, and raw intent metadata.")
    step_2_initial_routing_logic: str = Field(description="Decide which analytical path is supported by the data (sequence, filepath, or immediate failure).")
    
    route: Union[SequenceAnalysis, FilePathAnalysis, AnalysisFailure] = Field(description="The analytical path chosen by the router based on initial evidence.")

# =============================================================================
# GRAPH STATE DEFINITION
# =============================================================================

class GraphState(TypedDict):
    prompt: str
    sequence_or_path: Optional[str]
    input_type: Optional[str]
    context: Optional[str]
    sequence: Optional[str]
    sequence_type: Optional[str]
    # ``ranked_results`` holds the top-50 from ``rank_*_node`` before
    # contextual reranking; ``final_results`` is the top-5 the rerank
    # gateway returns. The downstream adapter (``retriever_pipeline.py``)
    # and its unit tests consume ``final_results``.
    ranked_results: Optional[List[Dict[str, Any]]]
    final_results: Optional[List[Dict[str, Any]]]
    error: Optional[str]
    # "embeddings" (default, ProtT5+FAISS via search-service) or "blast" (EBI REST).
    search_algorithm: Optional[str]
    # Set to the LLM exception text when ``extract_and_classify_node`` had
    # to fall back to the regex extractor (e.g. Mistral 429, missing API
    # key, network error). ``None`` means the LLM call succeeded.
    # Surfaced to the UI by ``_merge_runtime_state`` as a backend warning.
    extraction_used_fallback: Optional[str]

# =============================================================================
# NODE FUNCTIONS
# =============================================================================

# Alphabets for the deterministic fallback. Standard amino-acid alphabet
# minus the 4 DNA letters gives a "protein-only" set whose presence is a
# strong signal that the input is a protein, not DNA. Kept inline so the
# inner retriever stays free of upward dependencies on app_services.
_AA_EXTENDED = set("ACDEFGHIKLMNPQRSTVWYBJUOZX*")
_DNA_ALPHABET = set("ACGTUN")
_PROTEIN_ONLY = set("EFILPQ")
_MIN_SEQ_LEN = 30
_SEQ_RUN_RE = re.compile(r"[ACDEFGHIKLMNPQRSTVWYBJUOZX*\-\s\d]{30,}")


def _classify_sequence_type(normalized: str) -> str:
    """Best-effort DNA/PROTEIN classification from the residue alphabet."""
    letters = set(normalized)
    if letters & _PROTEIN_ONLY:
        return "PROTEIN"
    if letters <= _DNA_ALPHABET:
        return "DNA"
    return "PROTEIN"


def _deterministic_extract(prompt: str) -> Optional[Dict[str, Any]]:
    """Pattern-matching fallback for ``extract_and_classify_node``.

    Mirrors the LLM's output shape so callers can't tell which path
    produced the result. Returns ``None`` when no plausible sequence is
    visible in the prompt — in that case the caller should surface the
    original LLM error rather than fabricate an extraction.
    """
    if not prompt:
        return None

    # 1) FASTA — most reliable. Body is the run of sequence-shaped lines
    #    following a ``>header`` line.
    fasta_match = re.search(r"^>.*$", prompt, flags=re.MULTILINE)
    if fasta_match:
        lines = prompt[fasta_match.start():].splitlines()
        header = lines[0]
        body_lines: List[str] = []
        for line in lines[1:]:
            compact = "".join(line.split()).upper()
            if compact and set(compact) <= (_AA_EXTENDED | {"-"}):
                body_lines.append(line)
            else:
                break
        body = "".join(body_lines).strip()
        if body:
            normalized = re.sub(r"\s+", "", body).upper()
            seq_type = _classify_sequence_type(normalized)
            context_parts = (prompt[:fasta_match.start()] + " " + header).split()
            return {
                "sequence_or_path": header + "\n" + body,
                "input_type": "SEQUENCE",
                "context": " ".join(context_parts) or header,
                "sequence_type": seq_type,
                "error": None,
            }

    # 2) Bare sequence — longest contiguous sequence-shaped substring.
    best: Optional[tuple] = None  # (start, end, normalized_body)
    for match in _SEQ_RUN_RE.finditer(prompt):
        normalized = re.sub(r"[\s\d]", "", match.group(0)).upper()
        if len(normalized) < _MIN_SEQ_LEN:
            continue
        if not set(normalized) <= _AA_EXTENDED:
            continue
        if best is None or len(normalized) > len(best[2]):
            best = (match.start(), match.end(), normalized)
    if best is None:
        return None

    context = (prompt[:best[0]] + " " + prompt[best[1]:]).strip()
    return {
        "sequence_or_path": best[2],
        "input_type": "SEQUENCE",
        "context": " ".join(context.split()),
        "sequence_type": _classify_sequence_type(best[2]),
        "error": None,
    }


def extract_and_classify_node(state: GraphState) -> Dict[str, Any]:
    """
    Implements the Schema-Guided Reasoning Cascade.
    Forces the model through sequential logic gates before finalizing
    classification and performing query expansion.

    When the LLM provider is transiently unavailable (e.g. Mistral returning
    HTTP 429 ``service_tier_capacity_exceeded``), the ``except`` arm falls
    back to a deterministic FASTA / bare-sequence extractor before surfacing
    the error — so a single rate-limit response doesn't kill the whole turn
    when the prompt has a perfectly parseable sequence in it.
    """
    if state.get("error"): return {}

    system_message = (
        "You are an elite bioinformatics data architect and routing engine. Your mission is to process raw user prompts "
        "and route them into a high-precision biological analysis pipeline with absolute scientific accuracy. "
        "You MUST follow the schema-guided reasoning process. Each field in the schema represents a mandatory logical checkpoint.\n\n"
        
        "### YOUR ARCHITECTURAL PROTOCOL:\n"
        "1. **INITIAL SCAN**: Identify strings resembling biological sequences (IUPAC codes) or filesystem paths.\n"
        "2. **ROUTING**: Select the analytical branch based on the strongest initial evidence.\n"
        "   - Select `SequenceAnalysis` if a raw sequence is found.\n"
        "   - Select `FilePathAnalysis` if a path is found.\n"
        "   - Select `AnalysisFailure` if data is missing or extraction is immediately impossible.\n"
        "3. **CASCADING REASONING**: Within the chosen branch, perform mandatory logic steps:\n"
        "   - **FOR SEQUENCES**: Analyze character distributions (e.g. searching for 'M' or 'W' for proteins) and match with functional context.\n"
        "   - **FOR PATHS**: Analyze extensions (.faa, .fna, .fasta) and verify against user instructions.\n"
        "4. **VALIDATION & ERROR ROUTING**: If, during your reasoning steps, you find the classification uncertain or the data invalid, "
        "you MUST route the `final_outcome` field to an `AnalysisFailure` object. Uncertainty is unacceptable.\n\n"
        
        "Your reasoning must be generous, elaborate, and demonstrate a profound mastery of molecular biology signals."
    )

    try:
        # Construct the LLM inside the try so that missing API keys
        # (``MISTRAL_API_KEY`` / ``OPENAI_API_KEY``) take the fallback
        # path instead of crashing the pipeline — get_llm raises
        # ValueError before returning when no key is configured.
        llm = get_llm(temperature=0)
        structured_llm = llm.with_structured_output(PipelineRouter)
        result = structured_llm.invoke([
            SystemMessage(content=system_message),
            HumanMessage(content=state['prompt'])
        ])

        # Traverse the cascade to find the terminal state
        terminal = result.route
        if isinstance(terminal, (SequenceAnalysis, FilePathAnalysis)):
            terminal = terminal.final_outcome

        # Handle the Unified Error Branch (AnalysisFailure)
        if terminal.kind == "error":
            return {"error": f"[{terminal.failure_stage}] {terminal.error_message} (Root Cause: {terminal.technical_root_cause})"}

        # Handle Success States
        if terminal.kind == "sequence_success":
            return {
                "sequence_or_path": terminal.raw_sequence,
                "input_type": "SEQUENCE",
                "context": terminal.context,
                "sequence_type": terminal.sequence_type,
                "error": None,
                "extraction_used_fallback": None,
            }
        else: # filepath_success
            return {
                "sequence_or_path": terminal.path,
                "input_type": "FILEPATH",
                "context": terminal.context,
                "sequence_type": terminal.sequence_type,
                "error": None,
                "extraction_used_fallback": None,
            }

    except Exception as e:
        # LLM step failed (e.g. Mistral 429, missing MISTRAL_API_KEY,
        # network error). Try the deterministic FASTA / bare-sequence
        # extractor before giving up so a transient provider hiccup
        # doesn't kill the whole turn. The exception text is propagated
        # in ``extraction_used_fallback`` so the frontend can show the
        # user that the LLM step was skipped this turn.
        exc_text = f"{type(e).__name__}: {e}"
        fallback = _deterministic_extract(state.get('prompt') or "")
        if fallback is not None:
            print(
                f"extract_and_classify_node: LLM failed ({exc_text}); "
                "using deterministic fallback."
            )
            fallback["extraction_used_fallback"] = exc_text
            return fallback
        return {"error": f"Guided Extraction Pipeline Failure: {exc_text}"}

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
        matches = search_dna_top_k(state['sequence'], k=RETRIEVAL_TOP_K)
        # matches is List[Tuple[accession, score]]
        records = get_uniprot_records([m[0] for m in matches])
        
        # Inject search scores into records for future reranking logic
        score_map = {m[0]: m[1] for m in matches}
        for rec in records:
            rec["_search_score"] = score_map.get(rec.get("primaryAccession"))

        return {"ranked_results": records}
    except Exception as e:
        return {"error": f"DNA Ranking failed: {str(e)}"}

def rank_protein_node(state: GraphState) -> Dict[str, Any]:
    """Performs protein sequence similarity search via the selected backend."""
    if state.get('error'): return {}
    try:
        algorithm = (state.get("search_algorithm") or "embeddings").lower()
        if algorithm == "blast":
            # BLAST is slower than FAISS and returns at most ~10 hits anyway;
            # we ask for 10 to give rerank something to reorder if a context
            # query was supplied. Hardcoded to SwissProt for speed/quality.
            matches = blast_search(state['sequence'], k=10)
        else:
            matches = search_protein_top_k(state['sequence'], k=RETRIEVAL_TOP_K)
            
        records = get_uniprot_records([m[0] for m in matches])

        # Stamp two score fields onto each record:
        # - ``_search_score`` feeds the rerank gateway, which sorts by it.
        # - ``_bioseq_embedding_score`` is what the UI (uniprot_record_to_candidate)
        #   reads to fill the EMB tile. Same value for both at this stage —
        #   cosine for embeddings, percent identity for BLAST.
        score_map = {m[0]: m[1] for m in matches}
        for rec in records:
            score = score_map.get(rec.get("primaryAccession"))
            rec["_search_score"] = score
            if score is not None:
                rec["_bioseq_embedding_score"] = score

        return {"ranked_results": records}
    except Exception as e:
        return {"error": f"Protein Ranking failed: {str(e)}"}

def rerank_node(state: GraphState) -> Dict[str, Any]:
    """Performs contextual reranking."""
    if state.get('error'): return {}
    ranked = state.get('ranked_results') or []
    algorithm = (state.get("search_algorithm") or "embeddings").lower()
    if algorithm == "blast":
        # BLAST is already ranked by percent identity; the rerank gateway
        # would overwrite ``_search_score`` with a contextual score and
        # hide the identity number we just stamped on for the UI. Skip it.
        return {"final_results": ranked[:5]}
    if not _rerank_service_alive():
        print(f"Rerank service unreachable at {SEARCH_SERVICE_URL}; using top-5 of ranked results.")
        return {"final_results": ranked[:5]}

    try:
        reranker = LocalReranker()
        # Takes matches and reranks them
        final_records = reranker.rerank_by_context(ranked, state['context'], top_n=RERANK_TOP_N)
        return {"final_results": final_records}
    except Exception as e:
        print(f"Rerank skipped ({e}); falling back to top-5 of initial results.")
        return {"final_results": ranked[:5]}

# --- Conditional Routing Logic ---

def check_error(state: GraphState) -> Literal["error", "continue"]:
    return "error" if state.get("error") else "continue"

def should_resolve_filepath(state: GraphState) -> Literal["resolve", "raw", "error"]:
    if state.get('error'): return "error"
    return "resolve" if state['input_type'] == "FILEPATH" else "raw"

def should_rank(state: GraphState) -> Literal["rank_dna", "rank_protein", "error"]:
    if state.get('error'): return "error"
    return "rank_dna" if state['sequence_type'] == "DNA" else "rank_protein"

# --- Graph Construction ---

def create_pipeline():
    workflow = StateGraph(GraphState)
    
    workflow.add_node("extract", extract_and_classify_node)
    workflow.add_node("resolve_file", resolve_filepath_node)
    workflow.add_node("use_raw", use_raw_sequence_node)
    workflow.add_node("rank_dna", rank_dna_node)
    workflow.add_node("rank_protein", rank_protein_node)
    workflow.add_node("rerank", rerank_node)
    
    workflow.set_entry_point("extract")
    
    workflow.add_conditional_edges("extract", should_resolve_filepath, {"resolve": "resolve_file", "raw": "use_raw", "error": END})
    
    # After resolution/raw input, branch to DNA search or Protein search path
    workflow.add_conditional_edges("resolve_file", should_rank, {"rank_dna": "rank_dna", "rank_protein": "rank_protein", "error": END})
    workflow.add_conditional_edges("use_raw", should_rank, {"rank_dna": "rank_dna", "rank_protein": "rank_protein", "error": END})
    
    # Convergence points
    workflow.add_conditional_edges("rank_dna", check_error, {"error": END, "continue": "rerank"})
    workflow.add_conditional_edges("rank_protein", check_error, {"error": END, "continue": "rerank"})
    
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

"""Legacy embeddings retrieval path (ProtT5 + FAISS + UniProt).

Wraps :mod:`bioseq_retriever.src.pipeline` so it can be selected from the
Streamlit UI as an alternative to the Neo4j graph retriever. The legacy
pipeline does not write to ``public.chat_sessions`` itself — this adapter
is the sole DB writer for the embeddings backend, mirroring how the graph
path works (see ``chat_pipeline.run_turn``).

Heavy dependencies (``torch``, ``transformers``, ``faiss``, ``h5py``,
``sentence-transformers``, ``pyfaidx``) are *optional*. We import them
lazily inside the cached factory so users on the graph backend never pay
the import cost. If anything is missing — packages, the H5 file, or the
LLM key needed by the contextual reranker — we surface a clear instruction
in the chat reply rather than crashing the page.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import streamlit as st

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _PROJECT_ROOT / "app"
_FRONTEND_ROOT = Path(__file__).resolve().parent
_RETRIEVER_ROOT = _PROJECT_ROOT / "bioseq_retriever"
for _path in (_FRONTEND_ROOT, _APP_ROOT, _PROJECT_ROOT, _RETRIEVER_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import session_db_adapter  # noqa: E402
from mock.protein_loader import Candidate, from_dict  # noqa: E402

# bioseq_retriever now defaults BIOSEQ_USE_SERVICES=true and routes embed/search
# through HTTP microservices on localhost:8001/8002. We're using the local-mode
# (in-process ProtT5+FAISS), so force services off before any retriever module
# imports config.py — it's a module-level constant that's read once at import.
os.environ.setdefault("BIOSEQ_USE_SERVICES", "false")


# ---------------------------------------------------------------------------
# Cached pipeline factory
# ---------------------------------------------------------------------------


REQUIRED_PACKAGES = ("torch", "transformers", "faiss", "h5py")


def _ensure_data_paths() -> tuple[Path, Path, Path]:
    """Resolve the H5 / FAISS index / accessions cache paths.

    Reads ``BIOSEQ_H5_PATH`` / ``BIOSEQ_INDEX_PATH`` / ``BIOSEQ_ACCESSIONS_CACHE_PATH``
    from env (the legacy pipeline does the same), with sensible defaults
    pointing under the legacy ``bioseq_retriever/data/`` folder. Returns the
    resolved absolute paths and raises ``FileNotFoundError`` with a clear
    message when the H5 file is missing.
    """
    h5_path_env = os.getenv("BIOSEQ_H5_PATH") or str(_RETRIEVER_ROOT / "data" / "per-protein.h5")
    h5_path = Path(h5_path_env).expanduser().resolve()

    base = h5_path.with_suffix("")
    index_default = f"{base}.index"
    # bioseq_retriever switched accessions cache from pickle to JSON
    # (embeddings.get_or_create_index now does json.load); reading a stale
    # ``.pkl`` produces "Expecting value: line 1 column 1 (char 0)". Default
    # to ``.json`` so the cache extension matches the loader.
    cache_default = f"{base}.accessions.json"

    index_path = Path(os.getenv("BIOSEQ_INDEX_PATH", index_default)).expanduser().resolve()
    cache_path = Path(os.getenv("BIOSEQ_ACCESSIONS_CACHE_PATH", cache_default)).expanduser().resolve()

    if not h5_path.exists():
        raise FileNotFoundError(
            f"Embeddings H5 file not found at {h5_path}. "
            f"Set BIOSEQ_H5_PATH in your .env to the actual location, "
            f"or place the file at the default path."
        )

    return h5_path, index_path, cache_path


def _missing_packages() -> list[str]:
    missing: list[str] = []
    for name in REQUIRED_PACKAGES:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    return missing


def _has_llm_credentials() -> tuple[bool, str]:
    if os.getenv("MISTRAL_API_KEY"):
        return True, "mistral"
    if os.getenv("OPENAI_API_KEY"):
        return True, "openai"
    return False, ""


@st.cache_resource(show_spinner="Loading ProtT5 + FAISS index (one-time)…")
def _build_pipeline_resources():
    """Build the legacy LangGraph + reranker once per Streamlit process.

    Cached because:
    - ProtT5 weights load is ~3-5s warm / ~60s cold;
    - FAISS index build from H5 can take tens of seconds;
    - the LangChain LLM client opens connections we'd rather reuse.

    Calls ``bootstrap.ensure_data()`` first — same hook the hf-spaces deploy
    uses inside ``rank_node``. That makes the embeddings backend self-bootstrap
    on the powerful laptop / HF Spaces: if ``BIOSEQ_DATA_SOURCE=hf:OWNER/REPO``
    is set and the H5 file is missing, it pulls from the configured HF Dataset
    (idempotent — skips when files are already on disk). Falls back to UniProt
    FTP otherwise.
    """
    # Bootstrap the data files before resolving paths, so the H5/index land
    # in the expected location if they were missing.
    from bioseq_retriever.src.bootstrap import ensure_data
    ensure_data()

    # Make env-derived data paths visible to the legacy pipeline before import.
    h5_path, index_path, cache_path = _ensure_data_paths()
    os.environ["BIOSEQ_H5_PATH"] = str(h5_path)
    os.environ["BIOSEQ_INDEX_PATH"] = str(index_path)
    os.environ["BIOSEQ_ACCESSIONS_CACHE_PATH"] = str(cache_path)

    from bioseq_retriever.src.embeddings import get_or_create_index
    from bioseq_retriever.src.search import get_prottrans_embedder
    from bioseq_retriever.src.reranking import LocalReranker
    from bioseq_retriever.src.pipeline import create_pipeline

    embedder_tools = get_prottrans_embedder()
    index, accessions = get_or_create_index(str(h5_path), str(index_path), str(cache_path))
    reranker = LocalReranker()
    graph = create_pipeline()

    return {
        "graph": graph,
        "embedder_tools": embedder_tools,
        "index": index,
        "accessions": accessions,
        "reranker": reranker,
    }


# ---------------------------------------------------------------------------
# Public turn handler
# ---------------------------------------------------------------------------


def run_turn_embeddings(prompt: str) -> dict[str, Any]:
    """Run one user turn through the legacy ProtT5+FAISS pipeline.

    Returns the same dict shape as ``chat_pipeline.run_turn`` so the caller
    can render either backend's result identically.
    """
    user_id = st.session_state.get("user_id") or "anonymous"
    session_id = st.session_state.get("session_id")
    if not session_id:
        raise RuntimeError(
            "session_id is missing from st.session_state; bootstrap_identity() must run first."
        )

    context = session_db_adapter.make_context(
        user_id=user_id,
        session_id=session_id,
        workspace_id=st.session_state.get("workspace_id"),
        user_role=st.session_state.get("user_role"),
    )
    warnings: list[str] = list(session_db_adapter.get_warnings())

    # Preflight: dependencies, data file, LLM key. Fail-fast with friendly UI
    # message before importing the heavy modules.
    preflight_error = _preflight_check()
    if preflight_error:
        warnings.append(preflight_error)
        _safe_save_turn(context, prompt, preflight_error, [], set(), warnings)
        return {
            "reply": preflight_error,
            "candidates": [],
            "candidates_raw": [],
            "reveals": set(),
            "warnings": warnings,
            "result": {"error": preflight_error},
            "persisted": session_db_adapter.is_persistent(),
        }

    try:
        resources = _build_pipeline_resources()
    except Exception as exc:
        msg = f"**Could not initialize embeddings backend:** {exc}"
        warnings.append(str(exc))
        _safe_save_turn(context, prompt, msg, [], set(), warnings)
        return {
            "reply": msg,
            "candidates": [],
            "candidates_raw": [],
            "reveals": set(),
            "warnings": warnings,
            "result": {"error": str(exc)},
            "persisted": session_db_adapter.is_persistent(),
        }

    # The legacy create_pipeline() builds a fresh-state LangGraph. Feed it the
    # cached embedder/index/reranker via injection on rank/rerank nodes is
    # awkward — easiest and correct path is to call the steps directly here,
    # since rank_node and rerank_node are pure functions of state + tools.
    try:
        result = _run_legacy_pipeline(prompt, resources)
    except Exception as exc:
        msg = f"**Embeddings pipeline error:** {exc}"
        warnings.append(str(exc))
        _safe_save_turn(context, prompt, msg, [], set(), warnings)
        return {
            "reply": msg,
            "candidates": [],
            "candidates_raw": [],
            "reveals": set(),
            "warnings": warnings,
            "result": {"error": str(exc)},
            "persisted": session_db_adapter.is_persistent(),
        }

    raw_candidates = list(result.get("final_results") or [])
    ui_candidates = [_candidate_from_legacy(record) for record in raw_candidates]
    reply = _assistant_message(result)
    query_protein_sequence = result.get("protein_sequence") or ""
    reveals = _revealed_sections(ui_candidates, query_protein_sequence)

    _safe_save_turn(context, prompt, reply, raw_candidates, reveals, warnings, query_protein_sequence)

    return {
        "reply": reply,
        "candidates": ui_candidates,
        "candidates_raw": raw_candidates,
        "reveals": reveals,
        "warnings": warnings,
        "result": result,
        "persisted": session_db_adapter.is_persistent(),
        "query_protein_sequence": query_protein_sequence,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _preflight_check() -> str | None:
    missing = _missing_packages()
    if missing:
        return (
            "**Embeddings backend dependencies are not installed.**\n\n"
            f"Missing: `{', '.join(missing)}`\n\n"
            "Install the embeddings extras section of `requirements.txt` "
            "(torch / transformers / faiss-cpu / h5py / sentencepiece / protobuf / "
            "sentence-transformers / pyfaidx / huggingface_hub). Total download is "
            "~2 GB; the ProtT5 weights are pulled lazily on first inference."
        )
    has_key, _provider = _has_llm_credentials()
    if not has_key:
        return (
            "**No LLM credentials available for the contextual reranker.**\n\n"
            "Set `MISTRAL_API_KEY` (preferred — what the hf-spaces deploy uses) "
            "or `OPENAI_API_KEY` in your .env, then restart Streamlit."
        )
    # Note: we deliberately do NOT pre-check H5 file existence here. The
    # bootstrap step inside ``_build_pipeline_resources`` will download the
    # H5 from BIOSEQ_DATA_SOURCE (HF Dataset or UniProt FTP) on first run.
    # If that fails, the error surfaces from there with a precise reason.
    return None


def _run_legacy_pipeline(prompt: str, resources: dict[str, Any]) -> dict[str, Any]:
    """Drive the legacy LangGraph with our cached resources.

    Calling ``run_bioseq_pipeline(prompt)`` directly would re-create the
    embedder/index/reranker on every turn. We instead invoke the same nodes
    in sequence using the cached instances, replicating the graph's
    extract → translate/pass → rank → rerank flow.
    """
    from bioseq_retriever.src.data_fetcher import get_uniprot_records
    from bioseq_retriever.src.pipeline import (
        extract_and_classify_node,
        pass_protein_node,
        resolve_filepath_node,
        translate_dna_node,
        use_raw_sequence_node,
    )
    from bioseq_retriever.src.search import search_top_k

    state: dict[str, Any] = {
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
        "error": None,
    }
    state.update(extract_and_classify_node(state))
    if state.get("error"):
        return state

    if state.get("input_type") == "FILEPATH":
        state.update(resolve_filepath_node(state))
    else:
        state.update(use_raw_sequence_node(state))
    if state.get("error"):
        return state

    if state.get("sequence_type") == "DNA":
        state.update(translate_dna_node(state))
    else:
        state.update(pass_protein_node(state))
    if state.get("error"):
        return state

    # rank_node — direct FAISS search on the cached index
    try:
        matches = search_top_k(
            state["protein_sequence"],
            resources["embedder_tools"],
            resources["index"],
            resources["accessions"],
            k=50,
        )
        embedding_scores = {accession: score for accession, score in matches}
        records = get_uniprot_records([accession for accession, _score in matches])
        for record in records:
            accession = record.get("primaryAccession")
            if accession in embedding_scores:
                record["_bioseq_embedding_score"] = embedding_scores[accession]
        state["ranked_results"] = records
        state["embedding_scores"] = embedding_scores
    except Exception as exc:
        state["error"] = f"Ranking failed: {exc}"
        return state

    # rerank_node — semantic reranking using the cached reranker
    try:
        final = resources["reranker"].rerank_by_context(
            state["ranked_results"],
            state.get("context") or "",
            top_n=5,
        )
        state["final_results"] = final
    except Exception as exc:
        state["error"] = f"Reranking failed: {exc}"

    return state


def _candidate_from_legacy(record: dict[str, Any]) -> Candidate:
    """UniProt JSON record → UI Candidate.

    The FAISS stage returns cosine similarity from normalized protein
    embeddings. We attach that score to the UniProt record before reranking so
    the final top-5 buttons can show it.
    """
    return Candidate(
        protein=from_dict(record),
        match_score=_score_as_percent(record.get("_bioseq_embedding_score")),
    )


def _assistant_message(state: dict[str, Any]) -> str:
    if state.get("error"):
        return f"**Embeddings pipeline error:** {state['error']}"

    final_results = state.get("final_results") or []
    if not final_results:
        return "Embeddings pipeline completed without final matches."

    lines = [
        "**Embeddings retriever result:**",
        "",
        f"- Detected type: `{state.get('sequence_type') or 'unknown'}`",
        f"- Classification confidence: `{state.get('is_confident')}`",
        f"- Final matches: `{len(final_results)}`",
        "",
        "**Top matches:**",
    ]
    for index, record in enumerate(final_results[:5], 1):
        accession = record.get("primaryAccession") or "unknown"
        name = (
            record.get("proteinDescription", {})
            .get("recommendedName", {})
            .get("fullName", {})
            .get("value")
            or "Unknown protein"
        )
        genes = record.get("genes") or []
        gene = genes[0].get("geneName", {}).get("value", "") if genes else ""
        suffix = f" ({gene})" if gene else ""
        lines.append(f"{index}. `{accession}` — {name}{suffix}")
    return "\n".join(lines)


def _revealed_sections(candidates: list[Candidate], query_protein_sequence: str | None = None) -> set[str]:
    if not candidates:
        return set()
    protein = candidates[0]["protein"]
    sections = {"header", "keyfacts", "structure"}
    if protein.get("function_text"):
        sections.add("function")
    if protein.get("tissue_specificity") or protein.get("subcellular_locations"):
        sections.add("expression")
    if protein.get("subunit_text") or protein.get("interactions"):
        sections.add("interactions")
    if protein.get("domains"):
        sections.add("domains")
    if protein.get("ptm_texts") or protein.get("functional_features") or protein.get("isoforms"):
        sections.add("regulation")
    if protein.get("variants"):
        sections.add("variants")
    if (
        protein.get("keywords")
        or protein.get("go_terms")
        or protein.get("go_terms_by_category")
        or protein.get("pathways")
    ):
        sections.add("pathways")
    if protein.get("disease"):
        sections.add("disease")
    if protein.get("pubmed_ids") or protein.get("xrefs"):
        sections.add("references")
    if query_protein_sequence and protein.get("sequence"):
        sections.add("alignment")
    return sections


def _score_as_percent(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score <= 0:
        return 0.0
    if score <= 1:
        return score * 100
    return min(score, 100.0)


def _safe_save_turn(
    context,
    prompt: str,
    reply: str,
    raw_candidates: list[dict[str, Any]],
    reveals: set[str],
    warnings: list[str],
    query_protein_sequence: str | None = None,
) -> None:
    """Persist the turn even on errors, tagging it as embeddings-mode."""
    try:
        # Rebuild candidates in the shape ``session_db_adapter`` expects (it
        # reads ``protein.accession``, ``protein.gene``, etc.). Legacy
        # records have UniProt-shaped fields, so we normalize via from_dict
        # before persisting so the sidebar history is consistent across
        # backends.
        normalized: list[dict[str, Any]] = []
        for record in raw_candidates or []:
            try:
                view = from_dict(record)
            except Exception:
                continue
            normalized.append({
                "protein": dict(view),
                "match_score": _score_as_percent(record.get("_bioseq_embedding_score")),
                "rank": len(normalized),
                "similarity_score": None,
                "context_score": None,
                "evidence": [],
            })
        session_db_adapter.save_turn(
            context,
            user_message=prompt,
            assistant_message=reply,
            candidates=normalized,
            revealed_sections=reveals,
            current_mode="embeddings_retriever",
            query_protein_sequence=query_protein_sequence,
        )
    except Exception as exc:
        warnings.append(f"Could not save session turn: {exc}")

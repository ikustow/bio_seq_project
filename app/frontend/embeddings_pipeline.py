"""Streamlit adapter for the embeddings retriever backend.

The actual retrieval pipeline lives in ``app/backend/bioseq_retriever`` and
talks to the search/rerank gateway over HTTP. This module keeps the old
Streamlit result shape and session persistence behavior while routing through
the supported backend entry point.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import streamlit as st

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _PROJECT_ROOT / "app"
_BACKEND_ROOT = _APP_ROOT / "backend"
_BACKEND_RETRIEVER_ROOT = _BACKEND_ROOT / "bioseq_retriever"
_FRONTEND_ROOT = Path(__file__).resolve().parent
for _path in (_FRONTEND_ROOT, _APP_ROOT, _PROJECT_ROOT, _BACKEND_ROOT, _BACKEND_RETRIEVER_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import session_db_adapter  # noqa: E402
from mock.protein_loader import Candidate, from_dict  # noqa: E402


def _has_llm_credentials() -> tuple[bool, str]:
    if os.getenv("MISTRAL_API_KEY"):
        return True, "mistral"
    if os.getenv("OPENAI_API_KEY"):
        return True, "openai"
    return False, ""


# ---------------------------------------------------------------------------
# Public turn handler
# ---------------------------------------------------------------------------


def run_turn_embeddings(prompt: str) -> dict[str, Any]:
    """Run one user turn through the embeddings retriever pipeline.

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

    # Preflight only covers credentials needed by pipeline_interface. The
    # heavyweight FAISS/ProtT5 dependencies live behind the backend gateway.
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

    # ``search_algorithm`` picks the rank-step backend; selectable from the
    # sidebar dropdown. Default is the embeddings (ProtT5+FAISS) path.
    algorithm = st.session_state.get("search_algorithm", "embeddings")
    try:
        from bioseq_retriever.pipeline_interface import run_pipeline_interface
        result = run_pipeline_interface(prompt, search_algorithm=algorithm)
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

    if result.get("error"):
        msg = f"**Embeddings pipeline error:** {result['error']}"
        warnings.append(str(result["error"]))
        _safe_save_turn(context, prompt, msg, [], set(), warnings)
        return {
            "reply": msg,
            "candidates": [],
            "candidates_raw": [],
            "reveals": set(),
            "warnings": warnings,
            "result": result,
            "persisted": session_db_adapter.is_persistent(),
        }

    raw_candidates = list(result.get("final_results") or [])
    ui_candidates = [_candidate_from_record(record) for record in raw_candidates]
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
    has_key, _provider = _has_llm_credentials()
    if not has_key:
        return (
            "**No LLM credentials available for the contextual reranker.**\n\n"
            "Set `MISTRAL_API_KEY` (preferred — what the hf-spaces deploy uses) "
            "or `OPENAI_API_KEY` in your .env, then restart Streamlit."
        )
    return None


def _candidate_from_record(record: dict[str, Any]) -> Candidate:
    """UniProt JSON record → UI Candidate.

    The backend stamps ``_bioseq_embedding_score`` before contextual reranking
    so the final top-5 buttons can show the original retrieval score.
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

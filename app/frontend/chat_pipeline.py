"""End-to-end turn pipeline for the Streamlit frontend.

Turn dispatch:

- **First user turn** in the session goes to the embeddings retriever
  (``embeddings_pipeline.run_turn_embeddings``). "First" is decided by
  reading ``working_memory.turn_count`` from ``public.chat_sessions`` so
  the rule survives page reloads, sidebar session-switches, and multi-tab
  use.
- **Subsequent turns** go to the chat-LLM module via
  ``chat_llm_pipeline.run_turn_chat_llm``.

The embeddings module is imported lazily so the heavy ML deps (~2 GB)
don't load until the user actually triggers a search. The retriever does
not write to ``public.chat_sessions`` itself — ``session_db_adapter`` is
the sole writer for both turn kinds, so sidebar history is uniform.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import streamlit as st

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _PROJECT_ROOT / "app"
_FRONTEND_ROOT = Path(__file__).resolve().parent
for _path in (_FRONTEND_ROOT, _APP_ROOT, _PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import backend_choice  # noqa: E402
import session_db_adapter  # noqa: E402
from mock.protein_loader import Candidate, DiseaseInfo, DomainFeature, ProteinView  # noqa: E402


def run_turn(prompt: str) -> dict[str, Any]:
    """Dispatch one user turn.

    Returns a dict with:
        reply: str               — assistant text to stream
        candidates: list[Candidate] — UI-shaped cards
        candidates_raw: list[dict] — full backend candidate dicts (for DB)
        reveals: set[str]        — protein-card sections to unlock
        warnings: list[str]      — surfaced from agent + persistence
        result: dict             — raw backend state (for debug)
        persisted: bool          — True iff session was written to DB
        backend: str             — which backend produced the result
        update_card: bool        — caller should replace the protein card
                                   when True (False on follow-up turns to
                                   keep the existing selection stable).
    """
    if not _is_first_turn_in_session():
        # Follow-up turn → chat-LLM module. Lazy import keeps stub-only path
        # decoupled from retriever code.
        import chat_llm_pipeline  # noqa: WPS433

        return chat_llm_pipeline.run_turn_chat_llm(prompt)

    # First turn → embeddings retriever. Lazy import so the heavy ML deps
    # (~2 GB) only load on first use.
    import embeddings_pipeline  # noqa: WPS433

    outcome = embeddings_pipeline.run_turn_embeddings(prompt)
    outcome["backend"] = backend_choice.BACKEND_EMBEDDINGS
    outcome.setdefault("update_card", True)
    return outcome


def _is_first_turn_in_session() -> bool:
    """Return True if this is the first user turn in the active session.

    Reads ``working_memory.turn_count`` from ``public.chat_sessions``. If the
    session row doesn't exist or the counter is 0, this is the first turn —
    route to a retriever. Any positive count means there's already history,
    so route to the chat-LLM module.

    Falls back to ``True`` (route to retriever) when DB persistence is off
    or the session id is missing — that way the UI degrades gracefully
    without history rather than dropping into the chat-LLM stub forever.
    """
    session_id = st.session_state.get("session_id")
    if not session_id:
        return True
    if not session_db_adapter.is_persistent():
        return True
    row = session_db_adapter.load_session(session_id)
    if not row:
        return True
    working_memory = row.get("working_memory") or {}
    if isinstance(working_memory, str):
        # Some psycopg / driver combinations may surface jsonb as text.
        try:
            import json
            working_memory = json.loads(working_memory)
        except Exception:
            working_memory = {}
    if not isinstance(working_memory, dict):
        return True
    try:
        turn_count = int(working_memory.get("turn_count") or 0)
    except (TypeError, ValueError):
        turn_count = 0
    return turn_count == 0


def restore_session_state(session_id: str) -> dict[str, Any]:
    """Load a previous session into the UI state and return a status dict."""
    row = session_db_adapter.load_session(session_id)
    if not row:
        return {"loaded": False, "candidates": [], "messages": []}

    raw_candidates = session_db_adapter.extract_candidates(row)
    ui_candidates = [_candidate_from_backend(item) for item in raw_candidates]
    messages = session_db_adapter.extract_messages(row)
    working_memory = row.get("working_memory") or {}
    query_protein_sequence = ""
    if isinstance(working_memory, dict):
        query_protein_sequence = str(working_memory.get("last_query_protein_sequence") or "")
    reveals = _revealed_sections(ui_candidates, query_protein_sequence)

    st.session_state.candidates = ui_candidates
    st.session_state.selected_candidate_idx = 0
    st.session_state.card_sections_revealed = set(reveals)
    st.session_state.query_protein_sequence = query_protein_sequence or None
    if messages:
        st.session_state.messages = [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]

    return {
        "loaded": True,
        "candidates": ui_candidates,
        "messages": messages,
        "row": row,
    }


def auto_restore_if_fresh_load(session_id: str) -> bool:
    """Restore session state on Streamlit cold-start (e.g. browser reload).

    Only fires when the chat history still looks like the default welcome
    state — that way it doesn't clobber an in-progress conversation when the
    user clicks around the sidebar mid-session.
    """
    if not session_id:
        return False
    if not session_db_adapter.is_persistent():
        return False

    if st.session_state.get("_auto_restore_attempted") == session_id:
        return False
    st.session_state["_auto_restore_attempted"] = session_id

    messages = st.session_state.get("messages") or []
    looks_fresh = (
        len(messages) <= 1
        and (not messages or messages[0].get("role") == "assistant")
        and not st.session_state.get("candidates")
    )
    if not looks_fresh:
        return False

    outcome = restore_session_state(session_id)
    return bool(outcome.get("loaded"))


# ---------------------------------------------------------------------------
# Backend → UI shape adapters
#
# After the schema unification (app_contracts.DomainFeature/DiseaseInfo now
# carry the same UI-friendly fields the protein_card expects) the adapter is
# a thin shape normalizer rather than a per-field translator. It also acts
# as a backward-compat shim for older persisted rows that pre-date the
# unification: missing keys get safe defaults so render() never KeyErrors.
# ---------------------------------------------------------------------------


_PROTEIN_DEFAULTS: dict[str, Any] = {
    "accession": "",
    "name": "Unknown protein",
    "alt_names": [],
    "gene_synonyms": [],
    "gene": "",
    "organism_scientific": "",
    "organism_common": "",
    "taxon_id": 0,
    "annotation_score": 0.0,
    "reviewed": False,
    "existence": "",
    "length": 0,
    "mol_weight": 0,
    "subcellular_locations": [],
    "function_text": "",
    "tissue_specificity": "",
    "subunit_text": "",
    "interactions": [],
    "ptm_texts": [],
    "isoforms": [],
    "functional_features": [],
    "variants": [],
    "pathways": [],
    "protein_family": "",
    "disease": None,
    "domains": [],
    "keywords": [],
    "go_terms": [],
    "go_terms_by_category": {},
    "pubmed_ids": [],
    "xrefs": {},
    "alphafold_accession": "",
    "sequence": "",
}

_DISEASE_DEFAULTS: dict[str, Any] = {
    "name": "",
    "acronym": "",
    "mim_id": "",
    "description": "",
    "variants": [],
}


def _assistant_message(state: dict[str, Any]) -> str:
    if state.get("error"):
        return f"**Pipeline error:** {state['error']}"

    final_results = state.get("final_results") or []
    if not final_results:
        return "Retriever pipeline completed without final matches."

    lines = [
        "**Retriever result:**",
        "",
        f"- Detected type: `{state.get('sequence_type') or 'unknown'}`",
        f"- Classification confidence: `{state.get('is_confident')}`",
        f"- Final matches: `{len(final_results)}`",
        "",
        "**Top matches:**",
    ]
    for index, record in enumerate(final_results[:5], 1):
        protein = record.get("protein") or {}
        accession = protein.get("accession") or "unknown"
        name = protein.get("name") or protein.get("protein_name") or "Unknown protein"
        gene = protein.get("gene") or protein.get("gene_primary") or ""
        suffix = f" ({gene})" if gene else ""
        lines.append(f"{index}. `{accession}` — {name}{suffix}")
    return "\n".join(lines)


def _candidate_from_backend(record: dict[str, Any]) -> Candidate:
    return Candidate(
        protein=_ensure_protein_shape(record.get("protein") or {}),
        match_score=_score_as_percent(record.get("match_score")),
    )


def _ensure_protein_shape(raw: dict[str, Any]) -> ProteinView:
    out: dict[str, Any] = {**_PROTEIN_DEFAULTS, **raw}
    out["disease"] = _ensure_disease_shape(out.get("disease"))
    out["domains"] = _ensure_domains_shape(out.get("domains"))
    for key in (
        "gene_synonyms",
        "interactions",
        "ptm_texts",
        "isoforms",
        "functional_features",
        "variants",
        "pathways",
        "keywords",
        "go_terms",
        "pubmed_ids",
        "subcellular_locations",
        "alt_names",
    ):
        if not isinstance(out.get(key), list):
            out[key] = []
    if not isinstance(out.get("go_terms_by_category"), dict):
        out["go_terms_by_category"] = {}
    if not out.get("alphafold_accession"):
        out["alphafold_accession"] = out.get("accession") or ""
    out["xrefs"] = {
        str(k): str(v)
        for k, v in (out.get("xrefs") or {}).items()
        if v not in (None, "")
    }
    return out  # type: ignore[return-value]


def _ensure_disease_shape(raw: Any) -> DiseaseInfo | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {**_DISEASE_DEFAULTS, **raw}
    # Backward compat with the original DiseaseInfo (names/count/xrefs):
    if not out["name"]:
        names = raw.get("names") or []
        if names:
            out["name"] = str(names[0])
    if not out["mim_id"]:
        xrefs = raw.get("xrefs") if isinstance(raw.get("xrefs"), dict) else {}
        out["mim_id"] = str(xrefs.get("MIM") or "")
    if not out["name"] and not raw.get("count"):
        return None
    return out  # type: ignore[return-value]


def _ensure_domains_shape(raw: Any) -> list[DomainFeature]:
    if not isinstance(raw, list):
        return []
    domains: list[DomainFeature] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("start") or 0)
            end = int(item.get("end") or 0)
        except (TypeError, ValueError):
            continue
        if start <= 0 or end <= 0:
            continue
        domains.append({  # type: ignore[typeddict-item]
            "type": str(item.get("type") or "Domain"),
            "name": str(item.get("name") or item.get("description") or "Domain"),
            "start": start,
            "end": end,
        })
    return domains


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
    """Backend score lives on a 0..1 scale; UI renders as percent."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score <= 0:
        return 0.0
    if score <= 1:
        return score * 100
    return score

"""End-to-end turn pipeline for the Streamlit frontend.

Every turn now goes to ``BioSeqChatService`` in the backend. The backend
classifies the input, decides between retriever and Chat-LLM follow-up, and
returns ``update_card`` so the UI knows whether to replace the protein card
or keep the current selection stable.
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
import session_objects  # noqa: E402
from backend.app_contracts import ChatTurnRequest  # noqa: E402
from backend.app_services.service_factory import create_bioseq_chat_service  # noqa: E402
from mock.protein_loader import Candidate, DiseaseInfo, DomainFeature, ProteinView  # noqa: E402


_CHAT_SERVICE = None


def _chat_service():
    global _CHAT_SERVICE
    if _CHAT_SERVICE is None:
        _CHAT_SERVICE = create_bioseq_chat_service()
    return _CHAT_SERVICE


def run_turn(prompt: str) -> dict[str, Any]:
    """Dispatch one user turn through the backend service.

    The backend owns routing (retriever vs Chat-LLM follow-up) and returns
    ``update_card`` so the UI knows whether to swap the protein card.

    Returns a dict with:
        reply: str                  — assistant text to stream
        candidates: list[Candidate] — UI-shaped cards (empty for follow-ups)
        candidates_raw: list[dict]  — full backend candidate dicts (for DB)
        reveals: set[str]           — protein-card sections to unlock
        warnings: list[str]         — surfaced from agent + persistence
        result: dict                — raw backend state (for debug)
        persisted: bool             — True iff session was written to DB
        backend: str                — backend tag (mode or runtime name)
        update_card: bool           — caller should replace the protein card
                                      when True (False on follow-up turns
                                      so the existing selection stays put).
    """
    outcome = _run_turn_backend(prompt)
    outcome.setdefault("backend", backend_choice.BACKEND_RUNTIME)
    outcome.setdefault("update_card", True)
    return outcome


def _run_turn_backend(prompt: str) -> dict[str, Any]:
    user_id = st.session_state.get("user_id") or "anonymous"
    session_id = st.session_state.get("session_id")
    if not session_id:
        raise RuntimeError("session_id is missing from st.session_state; bootstrap_identity() must run first.")

    context = session_db_adapter.make_context(
        user_id=user_id,
        session_id=session_id,
        workspace_id=st.session_state.get("workspace_id"),
        user_role=st.session_state.get("user_role"),
    )
    warnings: list[str] = list(session_db_adapter.get_warnings())

    ui_context = _build_ui_context(session_id)

    objects_payload = session_objects.serialize_for_request()
    selected_object_id = session_objects.get_selected_id()

    try:
        response = _chat_service().submit_turn(
            ChatTurnRequest(
                message=prompt,
                session_id=session_id,
                user_id=user_id,
                workspace_id=st.session_state.get("workspace_id"),
                user_role=st.session_state.get("user_role"),
                search_algorithm=st.session_state.get("search_algorithm"),
                think_mode=bool(st.session_state.get("think_mode_enabled")),
                ui_context=ui_context,
                objects=objects_payload,
                selected_object_id=selected_object_id,
            )
        )
    except Exception as exc:
        reply = f"**Backend runtime error:** {exc}"
        warnings.append(str(exc))
        _safe_save_turn(context, prompt, reply, [], set(), warnings)
        return {
            "reply": reply,
            "candidates": [],
            "candidates_raw": [],
            "reveals": set(),
            "warnings": warnings,
            "result": {"error": str(exc)},
            "persisted": session_db_adapter.is_persistent(),
            "update_card": True,
            "backend": backend_choice.BACKEND_RUNTIME,
        }

    warnings.extend(response.warnings)
    update_card = response.update_card
    reply = response.assistant_message
    secondary_reply = response.secondary_assistant_message or None
    suggested_questions = list(response.suggested_questions or [])
    suggested_questions_metadata = _suggested_questions_metadata(response.metadata)

    # Apply objects_patch to the local registry. This is independent of the
    # legacy ``update_card`` flag: both retriever turns and direct UniProt
    # lookups push patches now.
    try:
        patch = response.objects_patch.model_dump() if response.objects_patch else None
    except AttributeError:
        patch = response.objects_patch if isinstance(response.objects_patch, dict) else None
    if patch:
        session_objects.apply_objects_patch(patch)
    if response.selected_object_id:
        session_objects.set_selected(response.selected_object_id)

    if update_card:
        raw_candidates = [candidate.model_dump() for candidate in response.candidates]
        ui_candidates = [_candidate_from_backend(candidate) for candidate in raw_candidates]
        query_protein_sequence = response.pipeline.protein_sequence if response.pipeline else None
        raw_candidates, ui_candidates = _sort_by_alignment(
            query_protein_sequence, raw_candidates, ui_candidates
        )
        reveals = _revealed_sections(ui_candidates, query_protein_sequence)
        _safe_save_turn(
            context,
            prompt,
            reply,
            raw_candidates,
            reveals,
            warnings,
            query_protein_sequence,
            current_mode=response.current_mode or "bioseq_runtime_retriever",
            update_candidates=True,
            suggested_questions=suggested_questions,
            suggested_questions_metadata=suggested_questions_metadata,
            secondary_assistant_message=secondary_reply,
        )
    else:
        # Follow-up turn: keep the existing card untouched. We still surface
        # what the UI is rendering so callers that consume the dict (app.py
        # guards on update_card and skips overwriting state, but legacy
        # callers may expect candidates/reveals to be populated).
        raw_candidates = []
        ui_candidates = list(st.session_state.get("candidates") or [])
        reveals = set(st.session_state.get("card_sections_revealed") or set())
        query_protein_sequence = st.session_state.get("query_protein_sequence")
        _safe_save_turn(
            context,
            prompt,
            reply,
            [],
            None,
            warnings,
            None,
            current_mode=response.current_mode or "chat_llm",
            update_candidates=False,
            suggested_questions=suggested_questions,
            suggested_questions_metadata=suggested_questions_metadata,
        )

    return {
        "reply": reply,
        "secondary_reply": secondary_reply,
        "candidates": ui_candidates,
        "candidates_raw": raw_candidates,
        "reveals": reveals,
        "warnings": warnings,
        "result": response.model_dump(),
        "persisted": session_db_adapter.is_persistent(),
        "query_protein_sequence": query_protein_sequence,
        "update_card": update_card,
        "suggested_questions": suggested_questions,
        "backend": response.current_mode or backend_choice.BACKEND_RUNTIME,
    }


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
    workspace_snapshot: dict[str, Any] | None = None
    if isinstance(working_memory, dict):
        query_protein_sequence = str(working_memory.get("last_query_protein_sequence") or "")
        workspace_snapshot = working_memory.get("bioseq_workspace")
        if not isinstance(workspace_snapshot, dict):
            workspace_snapshot = None
    reveals = _revealed_sections(ui_candidates, query_protein_sequence)

    st.session_state.candidates = ui_candidates
    st.session_state.selected_candidate_idx = 0
    st.session_state.card_sections_revealed = set(reveals)
    st.session_state.query_protein_sequence = query_protein_sequence or None
    if workspace_snapshot:
        session_objects.apply_persisted(workspace_snapshot)
    if messages:
        st.session_state.messages = [_message_for_session_state(m) for m in messages]

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


def _safe_save_turn(
    context,
    prompt: str,
    reply: str,
    raw_candidates: list[dict[str, Any]],
    reveals: set[str] | None,
    warnings: list[str],
    query_protein_sequence: str | None = None,
    *,
    current_mode: str = "bioseq_runtime_retriever",
    update_candidates: bool = True,
    suggested_questions: list[str] | None = None,
    suggested_questions_metadata: dict[str, Any] | None = None,
    secondary_assistant_message: str | None = None,
) -> None:
    try:
        session_db_adapter.save_turn(
            context,
            user_message=prompt,
            assistant_message=reply,
            candidates=raw_candidates,
            revealed_sections=reveals,
            current_mode=current_mode,
            update_candidates=update_candidates,
            query_protein_sequence=query_protein_sequence,
            workspace_snapshot=session_objects.serialize_for_persistence(),
            suggested_questions=suggested_questions,
            think_mode=bool(st.session_state.get("think_mode_enabled")),
            suggested_questions_metadata=suggested_questions_metadata,
            secondary_assistant_message=secondary_assistant_message,
        )
    except Exception as exc:
        warnings.append(f"Could not save session turn: {exc}")


def _build_ui_context(session_id: str) -> dict[str, Any]:
    """Assemble the ui_context payload the backend needs for routing.

    Carries:
        turn_count            — int from working_memory; 0 routes to retriever
        messages              — recent chat transcript for Chat-LLM context
        selected_candidate    — dict shape of the currently focused card
        selected_candidate_index
    """
    ctx: dict[str, Any] = {}
    ctx["turn_count"] = _read_turn_count(session_id)
    ctx["messages"] = [
        {"role": m.get("role"), "content": m.get("content")}
        for m in (st.session_state.get("messages") or [])
        if isinstance(m, dict)
    ]
    selected_idx = st.session_state.get("selected_candidate_idx", 0)
    try:
        selected_idx_int = int(selected_idx)
    except (TypeError, ValueError):
        selected_idx_int = 0
    ctx["selected_candidate_index"] = selected_idx_int

    candidates = st.session_state.get("candidates") or []
    if candidates and 0 <= selected_idx_int < len(candidates):
        candidate = candidates[selected_idx_int]
        if isinstance(candidate, dict):
            ctx["selected_candidate"] = candidate
        elif hasattr(candidate, "model_dump"):
            try:
                ctx["selected_candidate"] = candidate.model_dump()
            except Exception:
                pass
    return ctx


def _suggested_questions_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    return {
        key: metadata[key]
        for key in (
            "suggested_questions_provider",
            "suggested_questions_model",
            "suggested_questions_raw",
        )
        if key in metadata
    }


def _message_for_session_state(message: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {
        "role": message["role"],
        "content": message["content"],
    }
    questions = message.get("suggested_questions")
    if isinstance(questions, list):
        item["suggested_questions"] = [str(q) for q in questions if q]
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        item["metadata"] = metadata
    return item


def _read_turn_count(session_id: str) -> int:
    if not session_id or not session_db_adapter.is_persistent():
        return 0
    row = session_db_adapter.load_session(session_id)
    if not row:
        return 0
    working_memory = row.get("working_memory") or {}
    if isinstance(working_memory, str):
        try:
            import json
            working_memory = json.loads(working_memory)
        except Exception:
            working_memory = {}
    if not isinstance(working_memory, dict):
        return 0
    try:
        return int(working_memory.get("turn_count") or 0)
    except (TypeError, ValueError):
        return 0


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
    "entry_name": "",
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


def _sort_by_alignment(
    query_protein_sequence: str | None,
    raw_candidates: list[dict[str, Any]],
    ui_candidates: list[Candidate],
) -> tuple[list[dict[str, Any]], list[Candidate]]:
    """Re-order candidates by local pairwise alignment % vs the query sequence.

    Falls back to the original order when there is no query sequence, no
    candidate has its own sequence, or alignment scoring fails for every
    candidate. Keeps the two lists in sync.
    """
    if not ui_candidates:
        return raw_candidates, ui_candidates
    # If we have neither a global query nor any per-candidate translations,
    # there's nothing to align against.
    has_per_candidate = any(c.get("query_translation") for c in ui_candidates)
    if not query_protein_sequence and not has_per_candidate:
        return raw_candidates, ui_candidates
    from components import alignment_viewer  # lazy: pulls Bio + streamlit cache

    scored: list[tuple[float, int]] = []
    any_scored = False
    for index, cand in enumerate(ui_candidates):
        candidate_sequence = cand["protein"].get("sequence")
        # Per-candidate translation (BLAST-DNA) wins over the session-global
        # query — different frames give different protein sequences.
        effective_query = cand.get("query_translation") or query_protein_sequence
        score: float | None = None
        if candidate_sequence and effective_query:
            score = alignment_viewer.alignment_match_percent(
                effective_query, candidate_sequence
            )
        if score is None:
            scored.append((float("-inf"), index))
        else:
            any_scored = True
            scored.append((score, index))

    if not any_scored:
        return raw_candidates, ui_candidates

    scored.sort(key=lambda item: (-item[0], item[1]))
    order = [idx for _, idx in scored]
    return [raw_candidates[i] for i in order], [ui_candidates[i] for i in order]


def _candidate_from_backend(record: dict[str, Any]) -> Candidate:
    return Candidate(
        protein=_ensure_protein_shape(record.get("protein") or {}),
        match_score=_score_as_percent(record.get("match_score")),
        query_translation=(record.get("query_translation") or None),
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

"""Bridge from the Streamlit UI to ``public.chat_sessions``.

Responsibilities:
- expose a process-cached ``PostgresSessionRepository`` (or fallback);
- read a session row and unpack ``working_memory.last_candidates`` back into
  the protein cards the UI renders;
- after each turn perform a read-merge-write upsert that adds UI-specific
  fields *on top of* whatever the retriever_agent already wrote (top-1
  protein, sequence, summaries). The merge preserves agent fields, extends
  ``proteins`` with the full top-5, and stores full candidate cards in
  ``working_memory.last_candidates``.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import streamlit as st

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _PROJECT_ROOT / "app"
for _path in (_APP_ROOT, _PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from backend.agents_core.shared.config import DEFAULT_ENV_PATH, load_env_file  # noqa: E402
from backend.agents_core.shared.models import AppContext  # noqa: E402
from backend.agents_core.shared.services.persistence import (  # noqa: E402
    NullSessionRepository,
    PostgresSessionRepository,
)


MAX_TRACKED_CARDS = 20


@st.cache_resource(show_spinner=False)
def _build_repository() -> tuple[Any, list[str]]:
    """Build (repository, warnings) once per Streamlit process."""
    load_env_file(DEFAULT_ENV_PATH)
    db_url = os.getenv("SUPABASE_DB_URL")
    warnings: list[str] = []
    if not db_url:
        warnings.append("SUPABASE_DB_URL is not set; session history is disabled.")
        return NullSessionRepository(), warnings
    try:
        return PostgresSessionRepository(db_url), warnings
    except Exception as exc:
        warnings.append(f"Could not connect to Supabase: {exc}. Session history is disabled.")
        return NullSessionRepository(), warnings


def get_repository():
    repo, _ = _build_repository()
    return repo


def get_warnings() -> list[str]:
    _repo, warnings = _build_repository()
    return list(warnings)


def is_persistent() -> bool:
    return not isinstance(get_repository(), NullSessionRepository)


def make_context(user_id: str, session_id: str, *, workspace_id: str | None = None, user_role: str | None = None) -> AppContext:
    return AppContext(
        user_id=user_id,
        session_id=session_id,
        workspace_id=workspace_id,
        user_role=user_role,
    )


def load_session(session_id: str) -> dict[str, Any] | None:
    """Return the raw row dict, or None if not found."""
    return get_repository().get_session(session_id)


def list_user_sessions(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return get_repository().list_sessions(user_id, limit=limit)


def extract_candidates(row: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Recover the last full candidate cards from ``working_memory.last_candidates``."""
    if not row:
        return []
    working_memory = row.get("working_memory") or {}
    if not isinstance(working_memory, dict):
        return []
    cards = working_memory.get("last_candidates")
    if not isinstance(cards, list):
        return []
    return [card for card in cards if isinstance(card, dict)]


def extract_messages(row: dict[str, Any] | None) -> list[dict[str, str]]:
    """Recover the chat transcript from ``working_memory.messages``."""
    if not row:
        return []
    working_memory = row.get("working_memory") or {}
    if not isinstance(working_memory, dict):
        return []
    msgs = working_memory.get("messages")
    if not isinstance(msgs, list):
        return []
    return [
        {"role": str(m.get("role", "")), "content": str(m.get("content", ""))}
        for m in msgs
        if isinstance(m, dict) and m.get("role") and m.get("content") is not None
    ]


def save_turn(
    context: AppContext,
    *,
    user_message: str,
    assistant_message: str,
    candidates: list[dict[str, Any]],
    revealed_sections: set[str] | list[str] | None = None,
    current_mode: str | None = None,
    update_candidates: bool = True,
) -> dict[str, Any] | None:
    """Append the current turn to ``public.chat_sessions``.

    Read-merge-write: pulls whatever retriever_agent already wrote during
    ``agent.invoke()``, extends it with UI-specific fields, then upserts. This
    preserves agent-side data (session_summary, active_accession, sequences)
    while ensuring the UI owns the full candidate cards and the message log.

    When ``update_candidates=False`` (chat-LLM follow-up turns that don't
    re-run retrieval), the saved ``proteins``, ``last_candidates``,
    ``last_revealed_sections``, ``working_set_ids`` and ``active_accession``
    are preserved untouched, and only the message log + turn counter are
    advanced. This keeps the protein card stable across follow-up Q&A.
    """
    repo = get_repository()
    if isinstance(repo, NullSessionRepository):
        return None

    saved = repo.get_session(context.session_id) or {}

    # Compose the merged state passed into ``upsert_session``. Field-by-field:
    # - agent already wrote ``proteins`` (top-1). We extend it with all 5 cards.
    # - ``working_memory`` is a free-form jsonb: append the message log,
    #   keep the latest 5 cards, bump turn counters.
    saved_wm = saved.get("working_memory") or {}
    if not isinstance(saved_wm, dict):
        saved_wm = {}

    if update_candidates:
        proteins = _merge_protein_records(
            saved.get("proteins") or [],
            candidates,
        )
        new_last_candidates = candidates[:MAX_TRACKED_CARDS]
        new_working_set = _merge_working_set(
            saved.get("working_set_ids") or [],
            [c.get("protein", {}).get("accession") for c in candidates if isinstance(c, dict)],
        )
        revealed_list = sorted(set(revealed_sections or []))
    else:
        # Follow-up turn (chat-LLM stub or future LLM module): keep the cards
        # the user already sees. Reveals come from the saved row so the
        # protein-card section state matches what was previously unlocked.
        proteins = list(saved.get("proteins") or [])
        new_last_candidates = list(saved_wm.get("last_candidates") or [])
        new_working_set = list(saved.get("working_set_ids") or [])
        if revealed_sections is not None:
            revealed_list = sorted(set(revealed_sections))
        else:
            revealed_list = list(saved_wm.get("last_revealed_sections") or [])

    sequences = list(saved.get("sequences") or [])

    messages = list(saved_wm.get("messages") or [])
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    if user_message:
        messages.append({"role": "user", "content": user_message, "ts": now_iso})
    if assistant_message:
        messages.append({"role": "assistant", "content": assistant_message, "ts": now_iso})

    turn_count = int(saved_wm.get("turn_count") or 0) + 1

    working_memory = {
        **saved_wm,
        "messages": messages[-200:],  # keep recent tail; full history is in LangGraph checkpoints
        "last_candidates": new_last_candidates,
        "last_user_message": user_message,
        "last_assistant_message": assistant_message,
        "last_revealed_sections": revealed_list,
        "last_turn_at": now_iso,
        "turn_count": turn_count,
        "ui_writer": "streamlit_frontend",
    }

    if update_candidates:
        top_protein = candidates[0].get("protein") if candidates and isinstance(candidates[0], dict) else None
        top_accession = top_protein.get("accession") if isinstance(top_protein, dict) else None
        active_accession = top_accession or saved.get("active_accession")
        last_tool_summary = (
            f"Returned {len(candidates)} candidate(s)"
            + (f"; top: {top_accession}" if top_accession else ".")
        )
    else:
        active_accession = saved.get("active_accession")
        last_tool_summary = saved.get("last_tool_results_summary") or "Follow-up turn; no new retrieval."

    state = {
        "session_summary": saved.get("session_summary") or _short_summary(user_message, assistant_message),
        "proteins": proteins,
        "sequences": sequences,
        "working_memory": working_memory,
        "active_sequence_id": saved.get("active_sequence_id"),
        "active_accession": active_accession,
        "last_analysis_summary": _short_summary(user_message, assistant_message, limit=400),
        "working_set_ids": new_working_set,
        # Tagging order: explicit override (per-turn from the dispatching
        # backend) > what the agent already wrote > UI fallback.
        "current_mode": current_mode or saved.get("current_mode") or "streamlit_ui",
        "last_tool_results_summary": last_tool_summary,
    }

    repo.upsert_session(context, state)
    # Return the freshly written row for callers that want to reflect it in UI state.
    return repo.get_session(context.session_id)


def _short_summary(user_message: str, assistant_message: str, limit: int = 240) -> str:
    head = (user_message or "").strip().replace("\n", " ")
    body = (assistant_message or "").strip().replace("\n", " ")
    text = f"Q: {head[:120]} | A: {body[:200]}" if head or body else ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _merge_protein_records(existing: list[Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add candidates to the tracked ``proteins`` list, dedup by accession.

    Each candidate is a full ``CandidateView``-like dict; we keep only the
    compact ``ProteinRecord`` shape the schema expects.
    """
    by_accession: dict[str, dict[str, Any]] = {}
    for item in existing or []:
        if isinstance(item, dict) and item.get("accession"):
            by_accession[item["accession"]] = dict(item)

    for cand in candidates or []:
        if not isinstance(cand, dict):
            continue
        protein = cand.get("protein") or {}
        if not isinstance(protein, dict):
            continue
        accession = protein.get("accession")
        if not accession:
            continue
        compact = {
            "accession": accession,
            "gene_name": protein.get("gene") or protein.get("gene_primary"),
            "protein_name": protein.get("name") or protein.get("protein_name"),
            "source": "ui_candidate",
            "status": "candidate",
            "notes": protein.get("organism_scientific") or protein.get("organism_name"),
        }
        previous = by_accession.get(accession, {})
        by_accession[accession] = {
            **previous,
            **{k: v for k, v in compact.items() if v is not None},
        }
    return list(by_accession.values())


def _merge_working_set(existing: list[Any], incoming: list[Any], limit: int = 40) -> list[str]:
    seen: list[str] = []
    for item in [*existing, *incoming]:
        if not item:
            continue
        text = str(item)
        if text not in seen:
            seen.append(text)
    return seen[-limit:]

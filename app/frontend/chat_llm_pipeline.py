"""Chat-LLM follow-up turn adapter.

Thin Streamlit-facing wrapper around ``backend.app_services.chat_llm``.
This module's only jobs are:

* Read the bits of ``st.session_state`` the backend needs (selected
  candidate, recent messages).
* Call the backend ``ChatLLMService`` which owns provider selection,
  prompt building and the actual HTTP/SDK call.
* Persist the follow-up turn through ``session_db_adapter`` without
  overwriting the saved candidate state, so the protein card on the
  right stays untouched.

All provider keys, env-var lookups and prompt strings live in
``app/backend/app_services/chat_llm.py``. Removing this wrapper is the
last step of the migration once the frontend stops handling persistence.
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

import session_db_adapter  # noqa: E402
from backend.app_services.chat_llm import ChatLLMRequest, ChatLLMService  # noqa: E402


_CHAT_LLM_SERVICE: ChatLLMService | None = None


def _chat_llm_service() -> ChatLLMService:
    global _CHAT_LLM_SERVICE
    if _CHAT_LLM_SERVICE is None:
        _CHAT_LLM_SERVICE = ChatLLMService()
    return _CHAT_LLM_SERVICE


def run_turn_chat_llm(prompt: str) -> dict[str, Any]:
    """Run a follow-up chat turn through the backend Chat-LLM service."""
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

    request = ChatLLMRequest(
        prompt=prompt,
        history=_message_history(),
        selected_candidate=_selected_candidate(),
    )

    current_mode = "chat_llm"
    result: dict[str, Any]
    try:
        response = _chat_llm_service().generate(request)
        reply = response.reply
        result = dict(response.raw)
        current_mode = str(result.get("mode") or "chat_llm")
    except Exception as exc:
        reply = f"**Chat LLM error:** {exc}"
        result = {"mode": "chat_llm", "error": str(exc)}
        current_mode = "chat_llm_error"
        warnings.append(str(exc))

    # Persist the turn but do not touch the saved candidate state. The user is
    # still looking at the protein card from the previous retriever turn.
    try:
        session_db_adapter.save_turn(
            context,
            user_message=prompt,
            assistant_message=reply,
            candidates=[],
            revealed_sections=None,
            current_mode=current_mode,
            update_candidates=False,
        )
    except Exception as exc:
        warnings.append(f"Could not save session turn: {exc}")

    return {
        "reply": reply,
        "candidates": st.session_state.get("candidates") or [],
        "candidates_raw": [],
        "reveals": set(st.session_state.get("card_sections_revealed") or set()),
        "warnings": warnings,
        "result": result,
        "persisted": session_db_adapter.is_persistent(),
        "backend": current_mode,
        "update_card": False,
    }


def _message_history() -> list[dict[str, Any]]:
    history = st.session_state.get("messages") or []
    return [message for message in history if isinstance(message, dict)]


def _selected_candidate() -> dict[str, Any] | None:
    candidates = st.session_state.get("candidates") or []
    if not candidates:
        return None
    selected_idx = st.session_state.get("selected_candidate_idx", 0)
    try:
        selected_idx = int(selected_idx)
    except (TypeError, ValueError):
        selected_idx = 0
    if selected_idx < 0 or selected_idx >= len(candidates):
        return None
    candidate = candidates[selected_idx]
    if isinstance(candidate, dict):
        return candidate
    # UI-shaped Candidate objects (mock.protein_loader.Candidate) — best effort.
    if hasattr(candidate, "model_dump"):
        try:
            return candidate.model_dump()
        except Exception:
            return None
    if hasattr(candidate, "__dict__"):
        return dict(candidate.__dict__)
    return None

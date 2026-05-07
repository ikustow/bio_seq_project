"""Chat-LLM follow-up turn handler.

For follow-up questions in an existing session (after the first retriever
turn), the UI should call the dedicated chat-LLM module — written by a
teammate, not yet integrated. Until that lands, this module is a **stub**
that returns a friendly "module is baking" reply, persists the turn to
``public.chat_sessions`` (so it shows up in the sidebar history), and
preserves the protein card untouched.

When the real chat-LLM module ships:
1. Replace ``run_turn_chat_llm`` with a real call into that module.
2. Keep the same return contract (``reply``, ``candidates``, ``reveals``,
   ``warnings``, ``result``, ``persisted``, ``backend``, ``update_card``).
3. Keep ``current_mode='chat_llm'`` (drop the ``_stub`` suffix) so saved
   sessions carry a clean tag.
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


STUB_MESSAGE = (
    "⏳ **Chat agent module is still baking — a couple of days more.**\n\n"
    "I can't continue this conversation yet because the LLM follow-up "
    "module isn't wired in. The protein card on the right still reflects "
    "your previous search.\n\n"
    "Options:\n"
    "- Click **➕ New chat** in the sidebar to start a fresh retriever search.\n"
    "- Wait for the chat agent to come online and your follow-ups will work here."
)


def run_turn_chat_llm(prompt: str) -> dict[str, Any]:
    """Stub follow-up handler — returns the standard outcome shape.

    Preserves the cards/reveals from the prior retriever turn so the right
    column doesn't blink to empty when the user types a follow-up.
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

    # Persist the turn but DON'T touch the saved candidate state — the user
    # is still looking at the protein card from the previous retriever turn.
    try:
        session_db_adapter.save_turn(
            context,
            user_message=prompt,
            assistant_message=STUB_MESSAGE,
            candidates=[],
            revealed_sections=None,
            current_mode="chat_llm_stub",
            update_candidates=False,
        )
    except Exception as exc:
        warnings.append(f"Could not save session turn: {exc}")

    # Return whatever the UI is currently rendering on the right so the
    # caller can keep the card stable. ``update_card=False`` tells app.py
    # not to clobber st.session_state.candidates / selected_candidate_idx.
    return {
        "reply": STUB_MESSAGE,
        "candidates": st.session_state.get("candidates") or [],
        "candidates_raw": [],
        "reveals": set(st.session_state.get("card_sections_revealed") or set()),
        "warnings": warnings,
        "result": {"mode": "chat_llm_stub"},
        "persisted": session_db_adapter.is_persistent(),
        "backend": "chat_llm_stub",
        "update_card": False,
    }

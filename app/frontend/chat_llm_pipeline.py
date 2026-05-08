"""Chat-LLM follow-up turn handler.

Follow-up questions in an existing session are routed here after the first
retriever turn. This module calls a configured Gemini proxy and preserves the
currently rendered protein card, so a chat answer does not wipe the right
column.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import requests
import streamlit as st

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _PROJECT_ROOT / "app"
_FRONTEND_ROOT = Path(__file__).resolve().parent
for _path in (_FRONTEND_ROOT, _APP_ROOT, _PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import session_db_adapter  # noqa: E402


PROXY_URL_ENV = "BIOSEQ_LLM_PROXY_URL"
PROXY_TOKEN_ENV = "BIOSEQ_LLM_PROXY_TOKEN"
REQUEST_TIMEOUT_SECONDS = 45


def run_turn_chat_llm(prompt: str) -> dict[str, Any]:
    """Run a follow-up chat turn through the configured Gemini proxy."""
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

    current_mode = "chat_llm"
    try:
        reply, result = _call_gemini_proxy(prompt)
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


def _call_gemini_proxy(prompt: str) -> tuple[str, dict[str, Any]]:
    proxy_url = (os.getenv(PROXY_URL_ENV) or "").strip()
    proxy_token = (os.getenv(PROXY_TOKEN_ENV) or "").strip()
    if not proxy_url:
        raise RuntimeError(f"{PROXY_URL_ENV} is not set.")
    if not proxy_token:
        raise RuntimeError(f"{PROXY_TOKEN_ENV} is not set.")

    payload = {
        "contents": _build_gemini_contents(prompt),
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 900,
        },
        "systemInstruction": {
            "parts": [
                {
                    "text": (
                        "You are BioSeq Investigator's follow-up chat assistant. "
                        "Answer concisely, use the conversation context, and do "
                        "not claim that a new database search was run."
                    )
                }
            ]
        },
    }

    response = requests.post(
        proxy_url,
        json=payload,
        headers={"X-BioSeq-Token": proxy_token},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    return _extract_gemini_text(data), {"mode": "chat_llm", "gemini": data}


def _build_gemini_contents(prompt: str) -> list[dict[str, Any]]:
    messages = st.session_state.get("messages") or []
    contents: list[dict[str, Any]] = []
    seen_current_prompt = False

    for message in messages[-20:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            seen_current_prompt = seen_current_prompt or content == prompt
            contents.append({"role": "user", "parts": [{"text": content}]})
        elif role == "assistant" and contents:
            contents.append({"role": "model", "parts": [{"text": content}]})

    if not seen_current_prompt:
        contents.append({"role": "user", "parts": [{"text": prompt}]})
    return contents


def _extract_gemini_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no candidates.")
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    text = "\n".join(str(part.get("text") or "") for part in parts if part.get("text")).strip()
    if not text:
        raise RuntimeError("Gemini returned an empty text response.")
    return text

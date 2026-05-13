"""Adapter from the Streamlit UI to the backend BioSeq chat service."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

_FRONTEND_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _PROJECT_ROOT / "app"

for path in (_FRONTEND_ROOT, _APP_ROOT, _PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("BIOSEQ_BACKEND", "runtime")

from backend.app_contracts import ChatTurnRequest  # noqa: E402
from backend.app_services.service_factory import create_bioseq_chat_service  # noqa: E402
from mock.protein_loader import Candidate  # noqa: E402


_SERVICE = None


def _service():
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = create_bioseq_chat_service()
    return _SERVICE


def run_search(prompt: str) -> list[Candidate]:
    """Run the backend runtime retriever and return UI-ready candidates."""
    request = ChatTurnRequest(
        message=prompt,
        session_id=str(st.session_state.get("session_id") or "frontend_runtime"),
        user_id=str(st.session_state.get("user_id") or "anonymous"),
        workspace_id=st.session_state.get("workspace_id"),
        user_role=st.session_state.get("user_role"),
    )
    result = _service().submit_turn(request)
    if result.pipeline and result.pipeline.error:
        raise RuntimeError(result.pipeline.error)
    return [_candidate_for_ui(candidate.model_dump()) for candidate in result.candidates]


def _candidate_for_ui(candidate: dict) -> Candidate:
    output = dict(candidate)
    match_score = output.get("match_score")
    if isinstance(match_score, (int, float)) and 0 < match_score <= 1:
        output["match_score"] = float(match_score) * 100.0
    return Candidate(**output)

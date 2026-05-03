from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.app_contracts import ChatTurnRequest, ChatTurnResult  # noqa: E402
from backend.app_services.service_factory import create_bioseq_chat_service  # noqa: E402


@st.cache_resource
def get_backend_service():
    return create_bioseq_chat_service()


def submit_turn(
    message: str,
    session_id: str,
    user_id: str = "anonymous",
    selected_accession: str | None = None,
    selected_candidate_index: int | None = None,
) -> ChatTurnResult:
    return get_backend_service().submit_turn(
        ChatTurnRequest(
            message=message,
            session_id=session_id,
            user_id=user_id,
            selected_accession=selected_accession,
            selected_candidate_index=selected_candidate_index,
        )
    )

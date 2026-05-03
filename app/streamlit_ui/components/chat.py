from __future__ import annotations

import streamlit as st

from backend.app_contracts import ChatTurnResult
from streamlit_ui.backend_adapter import submit_turn


def init_chat_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("candidates", [])
    st.session_state.setdefault("revealed_sections", set())
    st.session_state.setdefault("warnings", [])
    st.session_state.setdefault("selected_candidate_index", 0)


def render_chat(session_id: str, user_id: str) -> None:
    st.subheader("Investigation chat")
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Ask about an accession, gene, protein name, or paste a prepared protein sequence")
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Querying BioSeq backend"):
            result = submit_turn(
                prompt,
                session_id=session_id,
                user_id=user_id,
                selected_candidate_index=st.session_state.selected_candidate_index,
            )
        _apply_turn_result(result)
        st.markdown(result.assistant_message)


def _apply_turn_result(result: ChatTurnResult) -> None:
    st.session_state.messages.append({"role": "assistant", "content": result.assistant_message})
    st.session_state.candidates = result.candidates
    st.session_state.revealed_sections = result.revealed_sections
    st.session_state.warnings = result.warnings
    st.session_state.selected_candidate_index = result.selected_candidate_index

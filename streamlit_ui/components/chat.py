"""Chat column: history, input box, and streamed assistant replies."""

from __future__ import annotations

import time
from typing import Iterable

import streamlit as st

from mock import conversation


def _stream_tokens(text: str, delay: float = 0.012) -> Iterable[str]:
    for word in text.split(" "):
        yield word + " "
        time.sleep(delay)


def _render_user_message(content: str) -> None:
    with st.chat_message("user"):
        if conversation.fasta_detected(content):
            st.markdown(
                ":blue-badge[FASTA detected] "
                f":gray-badge[{sum(c.isalpha() for c in content)} aa]"
            )
            st.markdown(f"<div class='seq-block'>{content}</div>", unsafe_allow_html=True)
        else:
            st.markdown(content)


def _handle_submission(text: str) -> None:
    """Append user message, compute assistant reply, update session state."""
    if not text.strip():
        return
    st.session_state.messages.append({"role": "user", "content": text})
    reply, reveals = conversation.route(text, st.session_state.conv_state)
    st.session_state.card_sections_revealed.update(reveals)
    if (
        st.session_state.conv_state.step >= 1
        and st.session_state.protein is None
        and st.session_state.on_first_search is not None
    ):
        st.session_state.on_first_search()
    st.session_state.pending_assistant = reply


def render(on_first_search) -> None:
    """Render the chat column.

    `on_first_search` is a callable the chat invokes the first time the user
    triggers a search — it should load the protein into `session_state`.
    """
    st.session_state.on_first_search = on_first_search

    st.markdown("#### Conversation")
    chat_area = st.container(height=620, border=False)

    with chat_area:
        for msg in st.session_state.messages:
            if msg["role"] == "user":
                _render_user_message(msg["content"])
            else:
                with st.chat_message("assistant"):
                    st.markdown(msg["content"])

        # If there is a pending assistant reply, stream it inside this container.
        pending = st.session_state.pop("pending_assistant", None)
        if pending:
            with st.chat_message("assistant"):
                st.write_stream(_stream_tokens(pending))
            st.session_state.messages.append({"role": "assistant", "content": pending})

    # Controls under the chat area.
    ctrl_cols = st.columns([1, 1, 4])
    if ctrl_cols[0].button("Try example", use_container_width=True, type="primary"):
        _handle_submission(conversation.example_first_message())
        st.rerun()
    if ctrl_cols[1].button("Reset", use_container_width=True):
        for k in (
            "messages",
            "conv_state",
            "protein",
            "card_sections_revealed",
            "pending_assistant",
        ):
            st.session_state.pop(k, None)
        st.rerun()

    user_input = st.chat_input("Paste a FASTA sequence or ask a question…")
    if user_input:
        _handle_submission(user_input)
        st.rerun()

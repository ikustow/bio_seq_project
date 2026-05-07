"""Chat column: history, input box, and streamed assistant replies."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

import streamlit as st

from mock import conversation

SubmitHandler = Callable[[str], tuple[str, Iterable[str]]]


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


def _handle_submission(text: str, on_submit: SubmitHandler | None) -> None:
    """Append user message, compute assistant reply, update session state."""
    if not text.strip():
        return
    st.session_state.messages.append({"role": "user", "content": text})

    if on_submit is None:
        reply, reveals = conversation.route(text, st.session_state.conv_state)
    else:
        reply, reveals = on_submit(text)

    st.session_state.card_sections_revealed.update(reveals)
    if on_submit is None and (
        st.session_state.conv_state.step >= 1
        and st.session_state.candidates is None
        and st.session_state.on_first_search is not None
    ):
        st.session_state.on_first_search()
    st.session_state.pending_assistant = reply


def _reset_conversation() -> None:
    """Reset = start a brand-new session.

    We deliberately keep ``user_id`` (cookie identity) so the sidebar history
    survives. The previous ``session_id`` row in ``public.chat_sessions`` is
    left intact as part of the user's history; we just mint a new one.
    """
    import session_identity  # noqa: WPS433  (avoid circular import at module load)

    for k in (
        "messages",
        "conv_state",
        "candidates",
        "selected_candidate_idx",
        "card_sections_revealed",
        "pending_assistant",
        "vector_db_result",
    ):
        st.session_state.pop(k, None)
    session_identity.start_new_session(reason="chat_reset_button")


def render(on_first_search, on_submit: SubmitHandler | None = None) -> None:
    """Render the chat column.

    `on_first_search` is a callable the chat invokes the first time the user
    triggers a search — it should load the protein into `session_state`.
    """
    st.session_state.on_first_search = on_first_search

    # Toolbar: title on the left, Reset button on the right — keeps controls
    # close to the section header so the chat area can flow underneath without
    # an awkward vertical gap.
    head_col, reset_col = st.columns([5, 1], vertical_alignment="center")
    with head_col:
        st.markdown("<div class='chat-title'>Conversation</div>", unsafe_allow_html=True)
    with reset_col:
        if st.button(
            "Reset",
            width="stretch",
            help="Clear the conversation and start over",
            key="chat_reset_btn",
        ):
            _reset_conversation()
            st.rerun()

    # While the chat is fresh (only the welcome message), let the container
    # size to its content so the suggestion chip and input field stay visible
    # without scrolling. Once the user has sent something, switch to a fixed
    # scrollable area so growing history doesn't push the input off-screen.
    has_user_message = any(m["role"] == "user" for m in st.session_state.messages)
    if has_user_message:
        chat_area = st.container(height=540, border=False)
    else:
        chat_area = st.container(border=False)

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

    # Suggestion chip — only shown while the conversation is fresh so it
    # behaves like a starter prompt and disappears once the user is engaged.
    if not has_user_message:
        chip_cols = st.columns([1, 4, 1])
        with chip_cols[1]:
            if st.button(
                "✨  Try the demo sequence — UNC5C (Human)",
                width="stretch",
                key="try_example_chip",
            ):
                _handle_submission(conversation.example_first_message(), on_submit)
                st.rerun()

    user_input = st.chat_input("Paste a FASTA sequence or ask a question…")
    if user_input:
        _handle_submission(user_input, on_submit)
        st.rerun()

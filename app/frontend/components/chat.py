"""Chat column: history, input box, and streamed assistant replies."""

from __future__ import annotations

import html
import time
from collections.abc import Callable, Iterable
from typing import Any

import streamlit as st

from mock import conversation

SubmitHandler = Callable[[str], tuple[str, Iterable[str], list[str]]]


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


def _render_assistant_message(
    message: dict[str, Any],
    message_index: int,
) -> None:
    with st.chat_message("assistant"):
        st.markdown(str(message.get("content") or ""))
        _render_suggested_questions(
            _message_suggested_questions(message),
            message_index,
            _language_context_for_message(message_index, str(message.get("content") or "")),
        )


def _render_suggested_questions(
    questions: list[str],
    message_index: int,
    language_context: str,
) -> None:
    if not questions:
        return
    heading = (
        "Может вам будет интересно"
        if _looks_like_russian(language_context)
        else "You might also be interested"
    )
    with st.container(key=f"suggested_questions_{message_index}"):
        st.markdown(
            "<div class='suggested-questions'>"
            f"<div class='suggested-questions-heading'>{html.escape(heading)}</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        for question_index, question in enumerate(questions[:3]):
            with st.container(
                key=f"suggested_question_row_{message_index}_{question_index}"
            ):
                st.markdown(
                    f"<span class='suggested-question-text'>{html.escape(question)}</span>",
                    unsafe_allow_html=True,
                )
                if st.button(
                    "↪",
                    key=f"suggested_question_insert_{message_index}_{question_index}",
                    help="Insert this question into the chat input",
                ):
                    _prefill_chat_input(question)
                    st.rerun()


def _message_suggested_questions(message: dict[str, Any]) -> list[str]:
    questions = message.get("suggested_questions")
    if not isinstance(questions, list):
        metadata = message.get("metadata")
        questions = metadata.get("suggested_questions") if isinstance(metadata, dict) else []
    return [str(question) for question in (questions or []) if question]


def _language_context_for_message(message_index: int, assistant_content: str) -> str:
    previous_messages = st.session_state.get("messages", [])[:message_index]
    previous_user_messages = [
        str(message.get("content") or "")
        for message in previous_messages
        if message.get("role") == "user"
    ]
    latest_user_message = previous_user_messages[-1] if previous_user_messages else ""
    return f"{latest_user_message}\n{assistant_content}"


def _language_context_for_pending(assistant_content: str) -> str:
    previous_user_messages = [
        str(message.get("content") or "")
        for message in st.session_state.get("messages", [])
        if message.get("role") == "user"
    ]
    latest_user_message = previous_user_messages[-1] if previous_user_messages else ""
    return f"{latest_user_message}\n{assistant_content}"


def _looks_like_russian(text: str) -> bool:
    cyrillic_count = sum("\u0400" <= char <= "\u04ff" for char in text)
    return cyrillic_count >= 3


def _prefill_chat_input(question: str) -> None:
    draft = str(question).strip()
    if not draft:
        return
    current = str(st.session_state.get("chat_prompt") or "").strip()
    st.session_state["chat_prompt"] = f"{current} {draft}".strip() if current else draft


def _handle_submission(text: str, on_submit: SubmitHandler | None) -> None:
    """Append user message, compute assistant reply, update session state."""
    if not text.strip():
        return
    st.session_state.messages.append({"role": "user", "content": text})

    if on_submit is None:
        reply, reveals = conversation.route(text, st.session_state.conv_state)
        suggested_questions: list[str] = []
    else:
        reply, reveals, suggested_questions = on_submit(text)

    st.session_state.card_sections_revealed.update(reveals)
    if on_submit is None and (
        st.session_state.conv_state.step >= 1
        and st.session_state.candidates is None
        and st.session_state.on_first_search is not None
    ):
        st.session_state.on_first_search()
    st.session_state.pending_assistant = {
        "content": reply,
        "suggested_questions": suggested_questions,
    }


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
        "query_protein_sequence",
    ):
        st.session_state.pop(k, None)
    session_identity.start_new_session(reason="chat_reset_button")


def render(on_first_search, on_submit: SubmitHandler | None = None) -> None:
    """Render the chat column.

    `on_first_search` is a callable the chat invokes the first time the user
    triggers a search — it should load the protein into `session_state`.
    """
    st.session_state.on_first_search = on_first_search

    # Toolbar: title on the left, Reset button on the right. We render the
    # whole row inside a keyed container so CSS can right-align the button
    # at a fixed width regardless of how the chat column resizes.
    with st.container(key="chat_toolbar"):
        head_col, reset_col = st.columns([5, 1], vertical_alignment="center")
        with head_col:
            st.markdown("<div class='chat-title'>Conversation</div>", unsafe_allow_html=True)
        with reset_col:
            if st.button(
                "Reset",
                help="Clear the conversation and start over",
                key="chat_reset_btn",
            ):
                _reset_conversation()
                st.rerun()

    # We always render the message history into a keyed container; CSS
    # then flexes it to fill all remaining vertical space in the left
    # column so the chat input docks at the bottom (ChatGPT-style).
    # The height value below is a sensible fallback for environments
    # where the calc-based CSS rule doesn't apply.
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
        for message_index, msg in enumerate(st.session_state.messages):
            if msg["role"] == "user":
                _render_user_message(msg["content"])
            else:
                _render_assistant_message(
                    msg,
                    message_index,
                )

        # If there is a pending assistant reply, stream it inside this container.
        pending = st.session_state.pop("pending_assistant", None)
        if pending:
            if isinstance(pending, dict):
                pending_content = str(pending.get("content") or "")
                pending_questions = [
                    str(question)
                    for question in (pending.get("suggested_questions") or [])
                    if question
                ]
            else:
                pending_content = str(pending)
                pending_questions = []
            with st.chat_message("assistant"):
                st.write_stream(_stream_tokens(pending_content))
                _render_suggested_questions(
                    pending_questions,
                    len(st.session_state.messages),
                    _language_context_for_pending(pending_content),
                )
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": pending_content,
                    "suggested_questions": pending_questions,
                }
            )

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

    user_input = st.chat_input(
        "Paste a FASTA sequence or ask a question…",
        key="chat_prompt",
    )
    if user_input:
        _handle_submission(user_input, on_submit)
        st.rerun()

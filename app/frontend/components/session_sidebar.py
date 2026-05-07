"""Sidebar: anonymous identity + session history + 'New chat' button."""

from __future__ import annotations

import datetime as _dt
from typing import Any

import streamlit as st

import backend_choice
import chat_pipeline
import session_db_adapter
import session_identity


def render() -> None:
    user_id = st.session_state.get("user_id")
    session_id = st.session_state.get("session_id")
    if not user_id or not session_id:
        return

    with st.sidebar:
        st.markdown("### 🧬 BioSeq Investigator")
        if st.button("➕ New chat", width="stretch", key="sidebar_new_chat"):
            _start_fresh_session()
            st.rerun()

        _render_backend_selector()

        persistent = session_db_adapter.is_persistent()
        if not persistent:
            st.warning(
                "Session history is not persisted. Set ``SUPABASE_DB_URL`` in your "
                ".env to enable cross-session memory.",
                icon="⚠️",
            )

        st.markdown("#### My sessions")
        if persistent:
            try:
                sessions = session_db_adapter.list_user_sessions(user_id, limit=20)
            except Exception as exc:
                sessions = []
                st.caption(f"Could not load history: {exc}")
            _render_session_list(sessions, current_id=session_id)
        else:
            st.caption("Only this session is available without DB persistence.")

        st.markdown("---")
        with st.expander("Debug ids", expanded=False):
            st.markdown(
                f"- user: `{session_identity.short_id(user_id)}`\n"
                f"- session: `{session_identity.short_id(session_id)}`"
            )
            snapshot = session_identity.cookie_state_snapshot()
            st.markdown("**Cookie state**")
            st.markdown(
                f"- controller loaded: `{snapshot['controller_loaded']}`\n"
                f"- hydration: `{snapshot['state']}`\n"
                f"- cookie user_id: `{session_identity.short_id(snapshot['cookie_user_id'] or '—')}`\n"
                f"- cookie session_id: `{session_identity.short_id(snapshot['cookie_session_id'] or '—')}`\n"
                f"- pending promotion: user=`{snapshot['user_pending_promotion']}` session=`{snapshot['session_pending_promotion']}`"
            )
            if st.session_state.get("backend_warnings"):
                st.markdown("**Warnings**")
                for warning in st.session_state.backend_warnings:
                    st.markdown(f"- {warning}")


def _render_backend_selector() -> None:
    current = backend_choice.get_backend()
    options = list(backend_choice.ALL_BACKENDS)
    try:
        index = options.index(current)
    except ValueError:
        index = 0
    selected = st.radio(
        "Search engine",
        options=options,
        index=index,
        format_func=backend_choice.label_for,
        key="sidebar_backend_radio",
        help=backend_choice.description_for(current),
    )
    if selected != current:
        backend_choice.set_backend(selected)
        st.rerun()
    st.caption(backend_choice.description_for(selected))


def _render_session_list(sessions: list[dict[str, Any]], *, current_id: str) -> None:
    if not sessions:
        st.caption("No previous sessions yet.")
        return

    for row in sessions:
        sid = str(row.get("session_id"))
        is_current = sid == current_id
        label = _format_session_label(row, is_current=is_current)
        if is_current:
            st.markdown(f"**▶ {label}**")
            continue
        if st.button(label, key=f"sidebar_session_{sid}", width="stretch"):
            _switch_to_session(sid)
            st.rerun()


def _format_session_label(row: dict[str, Any], *, is_current: bool) -> str:
    summary = (row.get("session_summary") or row.get("last_analysis_summary") or "").strip()
    if not summary:
        accession = row.get("active_accession")
        summary = f"Session on {accession}" if accession else "(no summary yet)"
    when = _format_when(row.get("updated_at"))
    head = summary[:60] + ("…" if len(summary) > 60 else "")
    return f"{head}  ·  {when}"


def _format_when(value: Any) -> str:
    if isinstance(value, _dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, str):
        try:
            return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value
    return ""


def _start_fresh_session() -> None:
    """Mint a new session_id and wipe per-conversation UI state."""
    session_identity.start_new_session(reason="sidebar_new_chat")
    _clear_conversation_state()


def _switch_to_session(session_id: str) -> None:
    """Switch to an existing session and rehydrate UI state from the DB row."""
    session_identity.switch_session(session_id)
    _clear_conversation_state()
    chat_pipeline.restore_session_state(session_id)


def _clear_conversation_state() -> None:
    keys_to_clear = (
        "messages",
        "conv_state",
        "candidates",
        "selected_candidate_idx",
        "card_sections_revealed",
        "pending_assistant",
        "vector_db_result",
        "on_first_search",
    )
    for key in keys_to_clear:
        st.session_state.pop(key, None)

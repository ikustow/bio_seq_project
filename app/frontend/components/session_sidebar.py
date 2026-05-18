"""Sidebar: anonymous identity + session history + 'New chat' button."""

from __future__ import annotations

import base64
import datetime as _dt
import html
from pathlib import Path
from typing import Any

import streamlit as st

import chat_pipeline
import session_db_adapter
import session_identity

_PLUS_ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "Plus.png"


@st.cache_data(show_spinner=False)
def _new_chat_button_css(icon_path: str) -> str:
    """Replace the default emoji icon on the 'New chat' button with Plus.png.

    Streamlit's ``st.button`` only accepts emoji / Material icons, so we
    target the keyed wrapper (``.st-key-sidebar_new_chat``) and inject a
    ``::before`` pseudo-element carrying the PNG as a base64 data URI.
    """
    path = Path(icon_path)
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        "<style>"
        ".st-key-sidebar_new_chat div.stButton > button > div {"
        " transform: translateX(-0.55rem); }"
        ".st-key-sidebar_new_chat div.stButton > button > div p,"
        ".st-key-sidebar_new_chat div.stButton > button > div {"
        " font-weight: 700 !important; }"
        ".st-key-sidebar_new_chat div.stButton > button > div::before {"
        f" content: ''; display: inline-block; width: 18px; height: 18px;"
        f" margin-right: 0.5rem; vertical-align: -3px;"
        f" background-image: url('data:image/png;base64,{encoded}');"
        " background-size: contain; background-repeat: no-repeat;"
        " background-position: center; }"
        "</style>"
    )


def render() -> None:
    user_id = st.session_state.get("user_id")
    session_id = st.session_state.get("session_id")
    if not user_id or not session_id:
        return

    with st.sidebar:
        # Search-algorithm picker. Only affects the first turn of a session
        # (subsequent turns are chat-LLM follow-ups regardless of choice).
        # Persisted in session_state; survives sidebar reruns.
        _ALGO_LABELS = {
            "embeddings": "Embeddings (ProtT5 + FAISS)",
            "blast": "BLAST (EBI / SwissProt)",
        }
        _algo_keys = list(_ALGO_LABELS.keys())
        current_algo = st.session_state.get("search_algorithm", "embeddings")
        if current_algo not in _ALGO_LABELS:
            current_algo = "embeddings"
        picked = st.selectbox(
            "Search algorithm",
            options=_algo_keys,
            index=_algo_keys.index(current_algo),
            format_func=lambda key: _ALGO_LABELS[key],
            key="sidebar_search_algorithm",
        )
        st.session_state["search_algorithm"] = picked

        st.toggle(
            "Assistant Mode 🧠",
            key="think_mode_enabled",
            help="Suggest three follow-up questions after each answer",
        )

        css = _new_chat_button_css(str(_PLUS_ICON_PATH))
        if css:
            st.markdown(css, unsafe_allow_html=True)
        button_label = "New chat" if css else "➕ New chat"
        if st.button(button_label, width="stretch", key="sidebar_new_chat"):
            _start_fresh_session()
            st.rerun()

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


def _render_session_list(sessions: list[dict[str, Any]], *, current_id: str) -> None:
    if not sessions:
        st.caption("No previous sessions yet.")
        return

    with st.container(key="sidebar_session_list"):
        for row in sessions:
            sid = str(row.get("session_id"))
            is_current = sid == current_id
            title = _format_session_title(row)
            when = _format_when(row.get("updated_at"))
            if is_current:
                _render_current_session_item(title, when)
                continue
            if st.button(title, key=f"sidebar_session_{sid}", width="stretch"):
                _switch_to_session(sid)
                st.rerun()
            _render_session_date(when)


def _render_current_session_item(title: str, when: str) -> None:
    date_html = (
        f"<div class='sidebar-session-current-date'>{html.escape(when)}</div>"
        if when
        else ""
    )
    st.markdown(
        "<div class='sidebar-session-current'>"
        f"<div class='sidebar-session-current-title'>{html.escape(title)}</div>"
        f"{date_html}"
        "</div>",
        unsafe_allow_html=True,
    )


def _render_session_date(when: str) -> None:
    if not when:
        return
    st.markdown(
        f"<div class='sidebar-session-date'>{html.escape(when)}</div>",
        unsafe_allow_html=True,
    )


def _format_session_title(row: dict[str, Any]) -> str:
    summary = (row.get("session_summary") or row.get("last_analysis_summary") or "").strip()
    if not summary:
        accession = row.get("active_accession")
        summary = f"Session on {accession}" if accession else "(no summary yet)"
    head = summary[:60] + ("…" if len(summary) > 60 else "")
    return head


def _format_when(value: Any) -> str:
    if isinstance(value, _dt.datetime):
        return value.strftime("%d.%m %H:%M")
    if isinstance(value, str):
        try:
            return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d.%m %H:%M")
        except ValueError:
            if len(value) >= 16 and value[4:5] == "-":
                return value[5:16].replace("-", ".").replace("T", " ")
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
        "query_protein_sequence",
        "on_first_search",
        # Session-scoped object registry — must reset together with the
        # conversation so a brand-new session starts with an empty
        # Session Objects bar.
        "objects",
        "object_order",
        "selected_object_id",
        "_seq_label_counter",
    )
    for key in keys_to_clear:
        st.session_state.pop(key, None)

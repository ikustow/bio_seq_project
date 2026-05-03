from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import streamlit as st

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from streamlit_ui.components.chat import init_chat_state, render_chat  # noqa: E402
from streamlit_ui.components.protein_card import render_protein_card  # noqa: E402


def main() -> None:
    st.set_page_config(page_title="BioSeq Investigator", page_icon="B", layout="wide")
    _style()
    init_chat_state()
    st.session_state.setdefault("session_id", f"session_{uuid.uuid4().hex[:10]}")
    st.session_state.setdefault("user_id", os.getenv("APP_USER_ID", "anonymous"))

    with st.sidebar:
        st.title("BioSeq Investigator")
        st.caption(f"Backend: {os.getenv('BIOSEQ_BACKEND', 'mock')}")
        st.text_input("User ID", key="user_id")
        st.code(st.session_state.session_id, language=None)
        if st.button("New conversation", use_container_width=True):
            _reset_conversation()

        warnings = st.session_state.get("warnings") or []
        if warnings:
            st.divider()
            st.caption("Warnings")
            for warning in warnings:
                st.warning(warning)

    chat_col, card_col = st.columns([0.58, 0.42], gap="large")
    with chat_col:
        render_chat(st.session_state.session_id, st.session_state.user_id)
    with card_col:
        render_protein_card(st.session_state.get("candidates", []))


def _reset_conversation() -> None:
    st.session_state.session_id = f"session_{uuid.uuid4().hex[:10]}"
    st.session_state.messages = []
    st.session_state.candidates = []
    st.session_state.revealed_sections = set()
    st.session_state.warnings = []
    st.session_state.selected_candidate_index = 0
    st.rerun()


def _style() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.5rem; max-width: 1360px; }
        [data-testid="stSidebar"] { border-right: 1px solid #e5e7eb; }
        div[data-testid="stMetric"] {
          border: 1px solid #e5e7eb;
          border-radius: 6px;
          padding: 0.65rem 0.75rem;
          background: #ffffff;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()

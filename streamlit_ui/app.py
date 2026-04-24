"""BioSeq Investigator — Streamlit mock UI entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Ensure this folder is on sys.path so `from mock...` / `from components...`
# work when Streamlit launches the file directly.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from components import chat, protein_card  # noqa: E402
from mock import conversation, protein_loader  # noqa: E402

PROTEIN_JSON = _HERE.parent / "test_data_from_database" / "O95185.json"
STYLE_PATH = _HERE / "assets" / "style.css"


def _inject_styles() -> None:
    if STYLE_PATH.exists():
        st.markdown(f"<style>{STYLE_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def _configured_password() -> str | None:
    """Return the shared password if one is configured, else None (auth disabled)."""
    try:
        pw = st.secrets.get("app_password")
    except Exception:
        return None
    return pw or None


def _require_password() -> None:
    """Simple single-password gate. No-op if no password is configured."""
    expected = _configured_password()
    if expected is None or st.session_state.get("auth_ok"):
        return

    st.markdown("### 🔒 BioSeq Investigator")
    st.caption("Enter the access password to continue.")
    with st.form("login", clear_on_submit=True):
        pw = st.text_input("Password", type="password", label_visibility="collapsed")
        submitted = st.form_submit_button("Enter", type="primary")
        if submitted:
            if pw == expected:
                st.session_state.auth_ok = True
                st.rerun()
            else:
                st.error("Wrong password.")
    st.stop()


def _bootstrap_session() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": conversation.welcome()}
        ]
    if "conv_state" not in st.session_state:
        st.session_state.conv_state = conversation.ConversationState()
    if "protein" not in st.session_state:
        st.session_state.protein = None
    if "card_sections_revealed" not in st.session_state:
        st.session_state.card_sections_revealed = set()
    if "pending_assistant" not in st.session_state:
        st.session_state.pending_assistant = None
    if "on_first_search" not in st.session_state:
        st.session_state.on_first_search = None


def _load_protein() -> None:
    """Invoked by the chat column the first time a search is triggered."""
    if st.session_state.protein is None:
        st.session_state.protein = protein_loader.load(PROTEIN_JSON)


def main() -> None:
    st.set_page_config(
        page_title="BioSeq Investigator — mock UI",
        page_icon=":dna:",
        layout="wide",
    )
    _inject_styles()
    _require_password()
    _bootstrap_session()

    header_cols = st.columns([8, 2])
    with header_cols[0]:
        st.title("🧬 BioSeq Investigator")
        st.caption(
            "Paste a biological sequence, ask a question, and get an "
            "evidence-grounded answer backed by public bioinformatics databases."
        )
    with header_cols[1]:
        st.markdown("")
        st.markdown("")
        st.markdown(":orange-badge[Demo mode · scripted responses]")

    st.divider()

    left, right = st.columns([5, 7], gap="large")
    with left:
        chat.render(on_first_search=_load_protein)
    with right:
        protein_card.render(
            st.session_state.protein,
            st.session_state.card_sections_revealed,
        )


if __name__ == "__main__":
    main()

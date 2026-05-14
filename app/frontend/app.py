"""BioSeq Investigator Streamlit frontend entry point."""

from __future__ import annotations

import os

# Windows OpenMP collision workaround. Two OpenMP runtimes (Intel ``libiomp5md``
# from numpy/MKL and LLVM ``libomp140`` from faiss/torch) get loaded into the
# same process and abort it with ``OMP: Error #15`` mid-request. Setting this
# before any heavy import lets them coexist. The same workaround is applied
# in the eval harness (see commit d42484d). Must be set before ``import faiss``
# anywhere in the dependency chain, so it lives at the very top of the entry
# point.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

# Ensure this folder is on sys.path so `from mock...` / `from components...`
# work when Streamlit launches the file directly.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import config  # noqa: E402
import session_identity  # noqa: E402
from components import chat, debug_panel, protein_card, session_sidebar  # noqa: E402
from mock import conversation, protein_loader  # noqa: E402

BACKEND_MODE = os.getenv(
    "BIOSEQ_FRONTEND_BACKEND",
    os.getenv("BIOSEQ_BACKEND", "mock"),
).strip().lower()

PROTEIN_DATA_DIR = _HERE / "test_data_from_database"

# 5 best matches from the (mocked) rank/re-rank pipeline, ordered best → worst.
# Each tuple is (UniProt accession, match-confidence percent).
CANDIDATE_SPECS: list[tuple[str, float]] = [
    ("O95185", 98.7),       # Human (UNC5C) — top match
    ("Q761X5", 92.4),       # Rat (UNC5C)
    ("F7HIS3", 86.1),       # Rhesus macaque
    ("A0A8C8XS57", 78.3),   # Lion
    ("A0A6P5M6C5", 71.5),   # Koala
]

STYLE_PATH = _HERE / "assets" / "style.css"


def _inject_styles() -> None:
    if STYLE_PATH.exists():
        st.markdown(f"<style>{STYLE_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


# JS that adds a drag handle to the left edge of the right column and
# persists its width to localStorage. Runs inside a 0-height components
# iframe and reaches into ``window.parent.document`` to attach the
# handle; a MutationObserver re-attaches after each Streamlit rerun.
_RIGHT_PANEL_RESIZER_JS = """
<script>
(function () {
    const doc = window.parent.document;
    const root = doc.documentElement;
    const STORAGE_KEY = "bioseq_right_panel_width";
    const MIN_WIDTH = 320;
    const MAX_WIDTH = 1400;
    const DEFAULT_WIDTH = 440;

    function readSaved() {
        const raw = parseInt(window.parent.localStorage.getItem(STORAGE_KEY) || "", 10);
        if (Number.isFinite(raw)) {
            return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, raw));
        }
        return DEFAULT_WIDTH;
    }

    function applyWidth(px) {
        const clamped = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, px));
        root.style.setProperty("--right-panel-width", clamped + "px");
        return clamped;
    }

    applyWidth(readSaved());

    // Find the *current* right column and make sure exactly one handle
    // lives on it. Streamlit's reruns can replace the column DOM, so we
    // re-create the handle on every mutation — but the pointer event
    // handlers are installed once via delegation below, so a freshly
    // re-attached handle works without rebinding listeners.
    function syncHandle() {
        const markers = doc.querySelectorAll(".st-key-main_layout .st-key-main_right");
        if (!markers.length) return;
        const marker = markers[markers.length - 1];
        const rightCol = marker.closest('[data-testid="stColumn"]');
        if (!rightCol) return;
        // Remove any stale handles that ended up in detached / sibling
        // columns after a rerender.
        doc.querySelectorAll(".right-resizer").forEach(function (h) {
            if (h.parentElement !== rightCol) h.remove();
        });
        if (rightCol.querySelector(".right-resizer")) return;
        const handle = doc.createElement("div");
        handle.className = "right-resizer";
        handle.title = "Drag to resize the protein-card panel";
        rightCol.appendChild(handle);
    }

    // Install the pointer handlers exactly once. We use document-level
    // delegation so the same listeners keep working even after Streamlit
    // tears down and recreates the handle DOM. Before this rewrite the
    // listeners were bound directly to the handle element, so a rerender
    // mid-session left a "live looking" handle that no longer reacted
    // to drags.
    if (!doc.__bioseqResizerHandlersInstalled) {
        doc.__bioseqResizerHandlersInstalled = true;
        let dragging = false;
        let startX = 0;
        let startWidth = 0;
        let activeHandle = null;
        let activePointerId = null;

        doc.addEventListener("pointerdown", function (event) {
            const handle = event.target.closest(".right-resizer");
            if (!handle) return;
            const rightCol = handle.parentElement;
            if (!rightCol) return;
            event.preventDefault();
            dragging = true;
            startX = event.clientX;
            startWidth = rightCol.getBoundingClientRect().width;
            activeHandle = handle;
            activePointerId = event.pointerId;
            handle.classList.add("is-dragging");
            root.classList.add("is-resizing-right");
            try { handle.setPointerCapture(event.pointerId); } catch (e) {}
        }, true);

        doc.addEventListener("pointermove", function (event) {
            if (!dragging) return;
            event.preventDefault();
            const delta = startX - event.clientX;
            applyWidth(startWidth + delta);
        }, true);

        function endDrag(event) {
            if (!dragging) return;
            dragging = false;
            root.classList.remove("is-resizing-right");
            if (activeHandle) {
                activeHandle.classList.remove("is-dragging");
                try {
                    if (activePointerId !== null) {
                        activeHandle.releasePointerCapture(activePointerId);
                    }
                } catch (e) {}
            }
            activeHandle = null;
            activePointerId = null;
            const cur = parseInt(root.style.getPropertyValue("--right-panel-width"), 10);
            if (Number.isFinite(cur)) {
                window.parent.localStorage.setItem(STORAGE_KEY, String(cur));
            }
        }
        doc.addEventListener("pointerup", endDrag, true);
        doc.addEventListener("pointercancel", endDrag, true);
    }

    syncHandle();
    const observer = new MutationObserver(function () { syncHandle(); });
    observer.observe(doc.body, { childList: true, subtree: true });
})();
</script>
"""


def _inject_right_panel_resizer() -> None:
    components.html(_RIGHT_PANEL_RESIZER_JS, height=0, width=0)


# Each candidate cell ("Top 5 matches" row) is visually one card, but
# only the top accession button is clickable in Streamlit. This script
# forwards clicks from anywhere inside the cell (EMB / SEQ tiles, the
# separator, padding) to that hidden button — so the whole card behaves
# as a single button, matching the visual.
_CANDIDATE_CLICK_FORWARDER_JS = """
<script>
(function () {
    const doc = window.parent.document;
    if (doc.__bioseqCandidateForwarderInstalled) return;
    doc.__bioseqCandidateForwarderInstalled = true;
    doc.addEventListener("click", function (event) {
        const cell = event.target.closest('[class*="st-key-candidate_cell_"]');
        if (!cell) return;
        // If the user already clicked the real button (or a child of it),
        // let Streamlit handle it directly — no need to re-dispatch.
        if (event.target.closest('button')) return;
        const btn = cell.querySelector('button[data-testid^="stBaseButton-"]');
        if (btn) btn.click();
    }, true);
})();
</script>
"""


def _inject_candidate_click_forwarder() -> None:
    components.html(_CANDIDATE_CLICK_FORWARDER_JS, height=0, width=0)


def _configured_password() -> str | None:
    """Return the shared password if one is configured, else None (auth disabled).

    Checks the ``APP_PASSWORD`` env var first (HF Spaces and similar hosts
    expose Secrets as env vars, not via Streamlit's secrets file), then
    falls back to ``st.secrets["app_password"]`` for local dev with
    ``.streamlit/secrets.toml``.
    """
    pw = (os.getenv("APP_PASSWORD") or "").strip()
    if not pw:
        try:
            pw = (st.secrets.get("app_password") or "").strip()
        except Exception:
            pw = ""
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
    # Identity bootstrap: cookie-based user_id + per-tab session_id. Must run
    # before any backend call so AppContext can carry stable ids per browser.
    _user_id, session_id = session_identity.bootstrap_identity()

    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": conversation.welcome()}
        ]
    if "conv_state" not in st.session_state:
        st.session_state.conv_state = conversation.ConversationState()
    if "candidates" not in st.session_state:
        st.session_state.candidates = None
    if "selected_candidate_idx" not in st.session_state:
        st.session_state.selected_candidate_idx = 0
    if "card_sections_revealed" not in st.session_state:
        st.session_state.card_sections_revealed = set()
    if "pending_assistant" not in st.session_state:
        st.session_state.pending_assistant = None
    if "on_first_search" not in st.session_state:
        st.session_state.on_first_search = None
    if "backend_warnings" not in st.session_state:
        st.session_state.backend_warnings = []
    if "query_protein_sequence" not in st.session_state:
        st.session_state.query_protein_sequence = None
    debug_panel.init_state()

    # If the session_id came from a cookie (i.e. browser reload of an existing
    # conversation), try to rehydrate the chat from the DB. Lazy import keeps
    # the first paint snappy; the cached service does the heavy lifting once.
    if session_id and config.USE_VECTOR_DB_MODE:
        try:
            import chat_pipeline  # noqa: WPS433

            chat_pipeline.auto_restore_if_fresh_load(session_id)
        except Exception as exc:
            st.session_state.backend_warnings.append(
                f"Could not auto-restore session {session_id}: {exc}"
            )


def _first_user_message() -> str:
    for m in st.session_state.messages:
        if m["role"] == "user":
            return m["content"]
    return ""


def _load_protein() -> None:
    """Invoked by the chat column the first time a search is triggered."""
    if st.session_state.candidates is not None:
        return

    if BACKEND_MODE == "real":
        # Lazy import — heavy deps (torch, faiss, transformers) only load when
        # the real backend is actually selected.
        prompt = _first_user_message()
        try:
            import backend_adapter  # noqa: WPS433

            with st.spinner("Searching databases — this can take 30–90 seconds…"):
                candidates = backend_adapter.run_search(prompt)
        except (ImportError, RuntimeError) as exc:
            st.error(f"Backend error: {exc}")
            return
        except ValueError as exc:
            # e.g. MISTRAL_API_KEY missing from setup_environment
            st.error(str(exc))
            return

        st.session_state.candidates = candidates
        st.session_state.selected_candidate_idx = 0
    else:
        st.session_state.candidates = protein_loader.load_candidates(
            PROTEIN_DATA_DIR, CANDIDATE_SPECS
        )
        st.session_state.selected_candidate_idx = 0


def _handle_vector_db_submission(text: str) -> tuple[str, set[str]]:
    """Run one user turn through the active backend and persist it.

    On retriever turns (``update_card=True``) we replace the protein card
    with the new candidates and reset the candidate switcher to position 0.
    On chat-LLM follow-up turns (``update_card=False``) we leave the card
    state alone so the user's selected candidate doesn't jump.
    """
    import chat_pipeline  # noqa: WPS433  (lazy import; heavy backend deps)

    with st.spinner("Working…"):
        outcome = chat_pipeline.run_turn(text)

    if outcome.get("update_card", True):
        st.session_state.candidates = outcome["candidates"]
        st.session_state.selected_candidate_idx = 0
        st.session_state.card_sections_revealed = set(outcome["reveals"])
        st.session_state.query_protein_sequence = outcome.get("query_protein_sequence")
    st.session_state.vector_db_result = outcome["result"]
    st.session_state.backend_warnings = outcome["warnings"]
    debug_panel.capture(text, outcome)
    return outcome["reply"], outcome["reveals"]


def main() -> None:
    st.set_page_config(
        page_title="BioSeq Investigator",
        page_icon=":dna:",
        layout="wide",
    )
    _inject_styles()
    _require_password()
    _bootstrap_session()
    session_sidebar.render()

    st.title("🧬 BioSeq Investigator")
    st.caption(
        "Paste a biological sequence, ask a question, and get an "
        "evidence-grounded answer backed by public bioinformatics databases."
    )

    st.divider()

    with st.container(key="main_layout"):
        left, right = st.columns([5, 7], gap="large")
        with left:
            with st.container(key="main_left"):
                chat.render(
                    on_first_search=_load_protein,
                    on_submit=_handle_vector_db_submission if config.USE_VECTOR_DB_MODE else None,
                )
        with right:
            with st.container(key="main_right"):
                protein_card.render(
                    st.session_state.candidates,
                    st.session_state.card_sections_revealed,
                    query_sequence=st.session_state.query_protein_sequence,
                )

    _inject_right_panel_resizer()
    _inject_candidate_click_forwarder()
    debug_panel.render()


if __name__ == "__main__":
    main()

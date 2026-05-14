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

import json
import sys
import time
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
from components import chat, protein_card, session_sidebar  # noqa: E402
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

    function attach() {
        const marker = doc.querySelector(".st-key-main_layout .st-key-main_right");
        if (!marker) return;
        const rightCol = marker.closest('[data-testid="stColumn"]');
        if (!rightCol || rightCol.querySelector(".right-resizer")) return;

        const handle = doc.createElement("div");
        handle.className = "right-resizer";
        handle.title = "Drag to resize the protein-card panel";
        rightCol.appendChild(handle);

        let dragging = false;
        let startX = 0;
        let startWidth = 0;

        handle.addEventListener("pointerdown", function (event) {
            event.preventDefault();
            dragging = true;
            startX = event.clientX;
            startWidth = rightCol.getBoundingClientRect().width;
            handle.classList.add("is-dragging");
            // Flag the whole document so CSS can disable selection
            // everywhere for the duration of the drag — Streamlit/BaseWeb
            // override body-level user-select, so we set it on <html>.
            root.classList.add("is-resizing-right");
            try { handle.setPointerCapture(event.pointerId); } catch (e) {}
        });

        handle.addEventListener("pointermove", function (event) {
            if (!dragging) return;
            event.preventDefault();
            const delta = startX - event.clientX;
            applyWidth(startWidth + delta);
        });

        function endDrag(event) {
            if (!dragging) return;
            dragging = false;
            handle.classList.remove("is-dragging");
            root.classList.remove("is-resizing-right");
            try { handle.releasePointerCapture(event.pointerId); } catch (e) {}
            const cur = parseInt(root.style.getPropertyValue("--right-panel-width"), 10);
            if (Number.isFinite(cur)) {
                window.parent.localStorage.setItem(STORAGE_KEY, String(cur));
            }
        }
        handle.addEventListener("pointerup", endDrag);
        handle.addEventListener("pointercancel", endDrag);
    }

    attach();
    const observer = new MutationObserver(function () { attach(); });
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


# Floating, resizable debug panel that shows the full payload of the most
# recent chat-LLM request (URL, headers, JSON body, system prompt). Lives at
# document level via st.markdown so it can float over the layout regardless
# of Streamlit's column structure. Resize: both + min/max in CSS lets the
# user scale from tiny corner box to nearly fullscreen by dragging the
# top-left grip.
_LLM_DEBUG_PANEL_JS = """
<script>
(function () {
    const doc = window.parent.document;
    const PANEL_ID = "bioseq-llm-debug-panel";
    const panel = doc.getElementById(PANEL_ID);
    if (!panel) return;
    if (panel.__bioseqWired) return;
    panel.__bioseqWired = true;

    // Re-parent under <body> so no Streamlit container's stacking context
    // (the sidebar in particular sits on a higher one than st.markdown
    // output) can pin the panel under the sidebar.
    if (panel.parentNode !== doc.body) {
        doc.body.appendChild(panel);
    }

    // Drop any persisted state from previous builds — this version always
    // restarts the panel at its default corner + minimum size on reload.
    try {
        window.parent.localStorage.removeItem("bioseq_llm_debug_panel_state");
        window.parent.localStorage.removeItem("bioseq_llm_debug_panel_state_v2");
    } catch (e) { /* no-op */ }

    function clampToViewport() {
        const win = window.parent;
        const rect = panel.getBoundingClientRect();
        const maxLeft = Math.max(0, win.innerWidth  - rect.width);
        const maxTop  = Math.max(0, win.innerHeight - rect.height);
        const left = Math.min(Math.max(0, rect.left), maxLeft);
        const top  = Math.min(Math.max(0, rect.top),  maxTop);
        panel.style.left = left + "px";
        panel.style.top  = top  + "px";
        panel.style.right  = "auto";
        panel.style.bottom = "auto";
    }

    const selector  = panel.querySelector("[data-role='selector']");
    const bodyEl    = panel.querySelector("[data-role='body']");
    const copyBtn   = panel.querySelector("[data-role='copy']");
    const header    = panel.querySelector(".lldp-header");

    if (copyBtn && bodyEl) {
        copyBtn.addEventListener("click", function (event) {
            event.stopPropagation();
            const text = bodyEl.innerText || "";
            const done = function () {
                const original = copyBtn.textContent;
                copyBtn.textContent = "✓";
                setTimeout(function () { copyBtn.textContent = original; }, 900);
            };
            try {
                window.parent.navigator.clipboard.writeText(text).then(done, done);
            } catch (e) {
                // Fallback: synthesize a temporary textarea inside the iframe parent.
                const ta = doc.createElement("textarea");
                ta.value = text;
                ta.style.position = "fixed";
                ta.style.left = "-9999px";
                doc.body.appendChild(ta);
                ta.select();
                try { doc.execCommand("copy"); } catch (err) {}
                doc.body.removeChild(ta);
                done();
            }
        });
    }

    // Drag the panel by its header. Ignore drags that start on interactive
    // elements (buttons, the selector) so they still work normally.
    let dragging = false;
    let dragStartX = 0, dragStartY = 0;
    let panelStartLeft = 0, panelStartTop = 0;
    if (header) {
        header.addEventListener("pointerdown", function (event) {
            if (event.target.closest("select, option")) return;
            event.preventDefault();
            dragging = true;
            const rect = panel.getBoundingClientRect();
            panelStartLeft = rect.left;
            panelStartTop  = rect.top;
            dragStartX = event.clientX;
            dragStartY = event.clientY;
            panel.classList.add("is-dragging");
            try { header.setPointerCapture(event.pointerId); } catch (e) {}
        });
        header.addEventListener("pointermove", function (event) {
            if (!dragging) return;
            event.preventDefault();
            const dx = event.clientX - dragStartX;
            const dy = event.clientY - dragStartY;
            panel.style.left = (panelStartLeft + dx) + "px";
            panel.style.top  = (panelStartTop  + dy) + "px";
            panel.style.right  = "auto";
            panel.style.bottom = "auto";
        });
        function endDrag(event) {
            if (!dragging) return;
            dragging = false;
            panel.classList.remove("is-dragging");
            try { header.releasePointerCapture(event.pointerId); } catch (e) {}
            clampToViewport();
        }
        header.addEventListener("pointerup", endDrag);
        header.addEventListener("pointercancel", endDrag);
    }

    // Keep the panel on-screen when the user resizes the browser.
    window.parent.addEventListener("resize", clampToViewport);

    if (selector) {
        const entries = JSON.parse(panel.dataset.entries || "[]");
        selector.addEventListener("change", function () {
            const idx = parseInt(selector.value, 10);
            if (!Number.isFinite(idx) || idx < 0 || idx >= entries.length) return;
            if (bodyEl) bodyEl.textContent = entries[idx].text;
        });
        // The <select> opens a native dropdown on pointerdown — make sure
        // that doesn't also start a panel drag.
        selector.addEventListener("pointerdown", function (event) {
            event.stopPropagation();
        });
    }
})();
</script>
"""


def _format_debug_entry(entry: dict) -> str:
    """Render one debug log entry as a single block of human-readable text:
    a curl command (when applicable), then a pretty-printed JSON dump of the
    full request payload, then the assistant reply for cross-reference."""
    request = entry.get("request") or {}
    provider = request.get("provider") or entry.get("provider") or "unknown"
    parts: list[str] = []

    parts.append(f"# Chat LLM debug — provider: {provider}")
    parts.append(f"# User prompt:\n{entry.get('prompt', '')}")
    parts.append("")

    if provider == "gemini_proxy":
        url = request.get("url") or ""
        headers = request.get("headers") or {}
        payload = request.get("payload") or {}
        curl_lines = [f"curl -X POST '{url}' \\"]
        for key, value in headers.items():
            curl_lines.append(f"  -H '{key}: {value}' \\")
        body_json = json.dumps(payload, indent=2, ensure_ascii=False)
        curl_lines.append(f"  -d '{body_json}'")
        parts.append("# Reproducer (curl):")
        parts.append("\n".join(curl_lines))
        parts.append("")

    parts.append("# Full request object:")
    parts.append(json.dumps(request, indent=2, ensure_ascii=False, default=str))
    parts.append("")
    parts.append("# Assistant reply:")
    parts.append(str(entry.get("reply", "")))
    return "\n".join(parts)


def _render_llm_debug_panel() -> None:
    log: list[dict] = list(st.session_state.get("llm_debug_log") or [])
    indexed = list(enumerate(log))
    indexed.reverse()  # newest first

    def _label(idx: int, entry: dict) -> str:
        prompt = (entry.get("prompt") or "").strip().replace("\n", " ")
        if len(prompt) > 60:
            prompt = prompt[:57] + "…"
        return f"#{idx + 1} · {prompt or '(empty prompt)'}"

    if indexed:
        js_entries = [
            {"label": _label(idx, entry), "text": _format_debug_entry(entry)}
            for idx, entry in indexed
        ]
        initial_text = js_entries[0]["text"]
        initial_label = js_entries[0]["label"]
        options_html = "".join(
            f'<option value="{i}">{_html_escape(item["label"])}</option>'
            for i, item in enumerate(js_entries)
        )
        selector_html = (
            f'<select data-role="selector" class="lldp-selector" '
            f'title="Pick a turn">{options_html}</select>'
        )
    else:
        js_entries = []
        initial_text = (
            "No chat-LLM requests captured yet. Send a follow-up message to "
            "the chat once a protein card is loaded and the full Gemini "
            "payload will show up here."
        )
        initial_label = "(no requests yet)"
        selector_html = ""

    entries_json = _html_escape(json.dumps(js_entries, ensure_ascii=False))

    panel_html = (
        f'<div id="bioseq-llm-debug-panel" class="bioseq-llm-debug-panel" '
        f'data-entries="{entries_json}">'
        f'<div class="lldp-header" title="Drag to move">'
        f'<span class="lldp-dot"></span>'
        f'<strong>Debug</strong>'
        f'<span class="lldp-spacer"></span>'
        f'{selector_html}'
        f'<span data-role="copy" class="lldp-copy" title="Copy debug text">⧉</span>'
        f'</div>'
        f'<div data-role="body" class="lldp-body">{_html_escape(initial_text)}</div>'
        f'</div>'
    )
    st.markdown(panel_html, unsafe_allow_html=True)
    components.html(_LLM_DEBUG_PANEL_JS, height=0, width=0)


def _html_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


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
    if "llm_debug_log" not in st.session_state:
        st.session_state.llm_debug_log = []

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
    _capture_llm_debug(text, outcome)
    return outcome["reply"], outcome["reveals"]


def _capture_llm_debug(prompt: str, outcome: dict) -> None:
    """Pull the full request payload sent to the chat LLM out of the backend
    result and stash it in session_state so the floating debug panel can show
    exactly what was sent. Retriever-only turns have no LLM request and are
    skipped."""
    result = outcome.get("result") or {}
    metadata = result.get("metadata") or {}
    debug_request = metadata.get("debug_request")
    if not debug_request:
        return
    log = st.session_state.get("llm_debug_log") or []
    log.append(
        {
            "ts": time.time(),
            "prompt": prompt,
            "reply": outcome.get("reply", ""),
            "backend": outcome.get("backend"),
            "provider": metadata.get("provider"),
            "request": debug_request,
        }
    )
    st.session_state.llm_debug_log = log[-50:]


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
    _render_llm_debug_panel()


if __name__ == "__main__":
    main()

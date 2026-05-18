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

import base64
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
import session_objects  # noqa: E402
from components import chat, debug_panel, object_bar, object_inspector, protein_card, session_sidebar  # noqa: E402
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
LOGO_PATH = _HERE / "assets" / "Logo.png"
FAVICON_PATH = _HERE / "assets" / "Icon_Small_processed.png"


def _load_favicon():
    """Open the favicon as a PIL.Image so Streamlit's set_page_config gets
    an unambiguous image object — passing a raw Windows path string had
    been flaky. Falls back to the DNA emoji if the file or PIL is
    unavailable so a missing asset can never block the app from starting."""
    if not FAVICON_PATH.exists():
        return ":dna:"
    try:
        from PIL import Image

        return Image.open(FAVICON_PATH)
    except Exception:
        return ":dna:"


def _inject_styles() -> None:
    if STYLE_PATH.exists():
        st.markdown(f"<style>{STYLE_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def _render_topbar() -> None:
    """Fixed full-width app header. Sits above the sidebar; the sidebar and
    main block-container are pushed down by 90px in style.css to clear it."""
    if LOGO_PATH.exists():
        logo_b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
        logo_src = f"data:image/png;base64,{logo_b64}"
        # Inline ``height``/``width`` attributes + style so the browser sizes
        # the logo correctly during the brief window before style.css is
        # applied (otherwise the raw PNG flashes at its natural ~1000px size
        # on first paint and the whole topbar reflows). Same reason the
        # topbar wrapper carries inline layout below.
        logo_html = (
            f'<img src="{logo_src}" class="bioseq-topbar-logo" alt="BioSeq logo" '
            f'height="46" style="height:46px;width:auto;display:block;">'
        )
    else:
        logo_html = ""
    st.markdown(
        f"""
        <div class="bioseq-topbar" style="height:68px;display:flex;align-items:center;gap:1.75rem;padding:0 1.5rem;box-sizing:border-box;overflow:hidden;">
          <div class="bioseq-topbar-brand" style="display:flex;align-items:center;gap:0.8rem;flex-shrink:0;">
            {logo_html}
            <span class="bioseq-topbar-title">BioSeq Investigator</span>
          </div>
          <div class="bioseq-topbar-tagline">
            Paste a biological sequence, ask a question, and get an<br>
            evidence-grounded answer backed by public bioinformatics databases.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# JS that adds a drag handle to the left edge of the right column and
# persists its width to localStorage. Runs inside a 0-height components
# iframe and reaches into ``window.parent.document`` to attach the
# handle.
#
# The handle is appended to ``document.body`` (NOT to the React-managed
# column) and positioned with ``position: fixed`` from the column's
# bounding rect. Previously we appended it as a child of the
# ``[data-testid="stColumn"]`` div, which caused React to crash with
# ``Failed to execute 'removeChild' on 'Node'`` whenever Streamlit
# reconciled that column (most reliably on session switch, when the
# whole right pane rebuilds). Living at body level keeps the handle
# completely outside React's tracked DOM, so reconciliation can't trip
# over it.
_RIGHT_PANEL_RESIZER_JS = """
<script>
(function () {
    const doc = window.parent.document;
    const win = window.parent;
    const root = doc.documentElement;
    const STORAGE_KEY = "bioseq_right_panel_width";
    const MIN_WIDTH = 320;
    const MAX_WIDTH = 1400;
    const DEFAULT_WIDTH = 440;

    function readSaved() {
        const raw = parseInt(win.localStorage.getItem(STORAGE_KEY) || "", 10);
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

    function findRightColumn() {
        const markers = doc.querySelectorAll(".st-key-main_layout .st-key-main_right");
        if (!markers.length) return null;
        const marker = markers[markers.length - 1];
        return marker.closest('[data-testid="stColumn"]');
    }

    // Singleton handle that lives in ``document.body`` — outside the
    // React tree, so reconciliation never tries to walk over or remove
    // it. Idempotent: if the handle already exists we reuse it.
    function ensureHandle() {
        // Drop any leftover handles from older builds that injected the
        // node into the column itself. After the first run only the
        // body-level handle survives.
        doc.querySelectorAll(".right-resizer").forEach(function (h) {
            if (h.parentElement !== doc.body) h.remove();
        });
        let handle = doc.body.querySelector(":scope > .right-resizer");
        if (handle) return handle;
        handle = doc.createElement("div");
        handle.className = "right-resizer";
        handle.title = "Drag to resize the protein-card panel";
        doc.body.appendChild(handle);
        return handle;
    }

    function positionHandle() {
        const handle = ensureHandle();
        const rightCol = findRightColumn();
        if (!rightCol) {
            handle.style.display = "none";
            return;
        }
        const rect = rightCol.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) {
            handle.style.display = "none";
            return;
        }
        handle.style.display = "";
        handle.style.top = rect.top + "px";
        handle.style.left = (rect.left - 4) + "px";
        handle.style.height = rect.height + "px";
    }

    // Re-install pointer handlers on every iframe load. The previous
    // "install exactly once" pattern broke after any Streamlit rerun
    // that re-mounted this components.html iframe: the handler defined
    // inside the dead iframe stayed registered on the parent document,
    // but its closure was detached, so pointerdown silently no-ops
    // (hover still works because that's pure CSS — matches the
    // reported "handle hoverable but drag does nothing" symptom).
    // Storing previous handler refs on ``doc`` lets us swap them out
    // each time for fresh ones defined in THIS iframe's live context.
    let dragging = false;
    let startX = 0;
    let startWidth = 0;
    let activeHandle = null;
    let activePointerId = null;

    const onPointerDown = function (event) {
        const handle = event.target.closest(".right-resizer");
        if (!handle) return;
        const rightCol = findRightColumn();
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
    };

    const onPointerMove = function (event) {
        if (!dragging) return;
        event.preventDefault();
        const delta = startX - event.clientX;
        applyWidth(startWidth + delta);
    };

    const onPointerEnd = function (event) {
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
            win.localStorage.setItem(STORAGE_KEY, String(cur));
        }
    };

    if (doc.__bioseqResizerPointerDown) {
        try { doc.removeEventListener("pointerdown", doc.__bioseqResizerPointerDown, true); } catch (e) {}
    }
    if (doc.__bioseqResizerPointerMove) {
        try { doc.removeEventListener("pointermove", doc.__bioseqResizerPointerMove, true); } catch (e) {}
    }
    if (doc.__bioseqResizerPointerEnd) {
        try { doc.removeEventListener("pointerup", doc.__bioseqResizerPointerEnd, true); } catch (e) {}
        try { doc.removeEventListener("pointercancel", doc.__bioseqResizerPointerEnd, true); } catch (e) {}
    }
    doc.__bioseqResizerPointerDown = onPointerDown;
    doc.__bioseqResizerPointerMove = onPointerMove;
    doc.__bioseqResizerPointerEnd = onPointerEnd;
    doc.addEventListener("pointerdown", onPointerDown, true);
    doc.addEventListener("pointermove", onPointerMove, true);
    doc.addEventListener("pointerup", onPointerEnd, true);
    doc.addEventListener("pointercancel", onPointerEnd, true);

    // Position once now, then keep the handle aligned with the column.
    // Replace any prior rAF loop / MutationObserver from an earlier
    // iframe load so we don't pile up handlers across reruns.
    positionHandle();

    if (win.__bioseqResizerRafId) {
        try { win.cancelAnimationFrame(win.__bioseqResizerRafId); } catch (e) {}
    }
    function tick() {
        positionHandle();
        win.__bioseqResizerRafId = win.requestAnimationFrame(tick);
    }
    win.__bioseqResizerRafId = win.requestAnimationFrame(tick);

    if (win.__bioseqResizerResizeHandler) {
        win.removeEventListener("resize", win.__bioseqResizerResizeHandler);
    }
    win.__bioseqResizerResizeHandler = positionHandle;
    win.addEventListener("resize", win.__bioseqResizerResizeHandler);
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
    // Re-install on every iframe load — see _MENTION_BRIDGE_JS in
    // chat.py for the full explanation of why the "install once"
    // pattern silently breaks after Streamlit re-mounts this
    // components.html iframe.
    const handler = function (event) {
        const cell = event.target.closest('[class*="st-key-candidate_cell_"]');
        if (!cell) return;
        // If the user already clicked the real button (or a child of it),
        // let Streamlit handle it directly — no need to re-dispatch.
        if (event.target.closest('button')) return;
        const btn = cell.querySelector('button[data-testid^="stBaseButton-"]');
        if (btn) btn.click();
    };
    if (doc.__bioseqCandidateForwarderHandler) {
        try { doc.removeEventListener("click", doc.__bioseqCandidateForwarderHandler, true); } catch (e) {}
    }
    doc.__bioseqCandidateForwarderHandler = handler;
    doc.addEventListener("click", handler, true);
})();
</script>
"""


def _inject_candidate_click_forwarder() -> None:
    components.html(_CANDIDATE_CLICK_FORWARDER_JS, height=0, width=0)


# Makes the (CSS-fixed) topbar visually scroll up and out of view as the
# user scrolls down — without actually changing its position from `fixed`
# (which we tried earlier with `absolute` and it broke Streamlit's
# layout). Pure compositor transform on each scroll event: when scrollY
# is in [0, TOPBAR_HEIGHT] the bar is translated up by that amount;
# beyond TOPBAR_HEIGHT it stays fully off-screen. As a bonus, once the
# bar has scrolled off, the page scrollbar (which the fixed bar would
# otherwise cover at the top) is fully visible again.
_TOPBAR_SCROLL_JS = """
<script>
(function () {
    const doc = window.parent.document;
    const win = window.parent;

    // Kept in sync with --topbar-height in assets/style.css. Effectively
    // inert under the current "no global page scroll" layout (scrollY
    // stays at 0), but updated for correctness in case the global
    // overflow rule is ever relaxed.
    const HEIGHT = 68;

    function topbar() { return doc.querySelector('.bioseq-topbar'); }

    function currentScrollY() {
        // Streamlit historically scrolled the window itself, but newer
        // versions sometimes attach the scroll to [data-testid="stMain"]
        // (or stAppViewContainer). Probe both and take the larger value;
        // whichever container is actually scrolling will report a
        // nonzero scrollTop while the other stays at 0.
        let y = win.scrollY || win.pageYOffset || 0;
        const candidates = [
            doc.querySelector('[data-testid="stMain"]'),
            doc.querySelector('[data-testid="stAppViewContainer"]'),
            doc.scrollingElement,
        ];
        for (const el of candidates) {
            if (el && el.scrollTop > y) y = el.scrollTop;
        }
        return y;
    }

    let pending = false;
    function update() {
        pending = false;
        const bar = topbar();
        if (!bar) return;
        const offset = Math.min(Math.max(currentScrollY(), 0), HEIGHT);
        // translate3d to force the compositor and avoid a layout pass.
        bar.style.transform = `translate3d(0, ${-offset}px, 0)`;
        // The sidebar is position: fixed in Streamlit's layout — it
        // doesn't scroll with the page. Without this its padding-top
        // would stay at 90px even after the topbar has slid out of view,
        // leaving an obvious empty rectangle at the top-left. Shrinking
        // --topbar-offset in lockstep with the bar's translation pulls
        // the sidebar's first item up to the viewport edge.
        doc.documentElement.style.setProperty(
            '--topbar-offset', `${HEIGHT - offset}px`
        );
    }
    function schedule() {
        if (pending) return;
        pending = true;
        win.requestAnimationFrame(update);
    }

    // Listen at capture-phase on the window so we pick up scrolls
    // regardless of which descendant container actually receives the
    // event. Wheel events too — some Streamlit scroll containers consume
    // the scroll event before it bubbles. Re-install per iframe load
    // (see _MENTION_BRIDGE_JS in chat.py) so a re-mounted iframe doesn't
    // leave a zombie scroll handler from the old (detached) JS context.
    if (win.__bioseqTopbarScrollHandler) {
        ['scroll', 'wheel'].forEach(function (evt) {
            try { win.removeEventListener(evt, win.__bioseqTopbarScrollHandler, { capture: true }); } catch (e) {}
        });
    }
    win.__bioseqTopbarScrollHandler = schedule;
    ['scroll', 'wheel'].forEach(function (evt) {
        win.addEventListener(evt, schedule, { passive: true, capture: true });
    });

    update();
})();
</script>
"""


def _inject_topbar_scroll() -> None:
    components.html(_TOPBAR_SCROLL_JS, height=0, width=0)


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
    session_objects.init_state()
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


_PROGRESS_STAGES: tuple[tuple[float, str], ...] = (
    (0.0,  "Classifying sequence…"),
    (3.0,  "Embedding & searching index…"),
    (15.0, "Re-ranking top candidates…"),
    (35.0, "Fetching UniProt details…"),
    (55.0, "Finalizing results…"),
)


def _render_progress(placeholder, label: str) -> None:
    # Multi-sequence batches push a ``@Seq_B (2/5)`` prefix into session
    # state from ``chat._run_pending`` so each search in the batch tells the
    # user which one is in flight — without it the spinner just cycles the
    # same stage labels and the four still-``searching`` chips on the
    # Session Objects bar look stuck.
    prefix = st.session_state.get("batch_progress_label") or ""
    full_label = f"{prefix} — {label}" if prefix else label
    placeholder.markdown(
        f'<div class="bioseq-progress">'
        f'<span class="bioseq-progress-spinner"></span>'
        f'<span class="bioseq-progress-label">{full_label}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )


def _start_progress_ticker(placeholder, stop_event):
    """Advance the progress label on a fixed timetable until stopped.

    The retriever runs in the main Streamlit script thread and blocks
    until it returns, so we can't drive the label from per-stage callbacks
    without plumbing one through several layers of the backend. A timed
    ticker on a background thread gives the user a sense of which stage
    we are likely in (matched to empirical timings of the pipeline) and
    is good enough until proper callbacks land.
    """
    import threading
    import time
    from streamlit.runtime.scriptrunner import add_script_run_ctx

    def _tick() -> None:
        start = time.time()
        for delay, label in _PROGRESS_STAGES[1:]:
            wait = delay - (time.time() - start)
            if wait > 0 and stop_event.wait(wait):
                return
            if stop_event.is_set():
                return
            try:
                _render_progress(placeholder, label)
            except Exception:
                return

    thread = threading.Thread(target=_tick, daemon=True)
    # Streamlit elements require the script-run context to be attached
    # to any thread that calls into them, otherwise the writes from this
    # thread silently no-op (or warn) when targeting the parent script.
    add_script_run_ctx(thread)
    thread.start()
    return thread


def _handle_vector_db_submission(text: str) -> tuple[str, set[str], list[str]]:
    """Run one user turn through the active backend and persist it.

    On retriever turns (``update_card=True``) we replace the protein card
    with the new candidates and reset the candidate switcher to position 0.
    On chat-LLM follow-up turns (``update_card=False``) we leave the card
    state alone so the user's selected candidate doesn't jump.
    """
    import chat_pipeline  # noqa: WPS433  (lazy import; heavy backend deps)
    import threading

    stop_event = threading.Event()
    placeholder = st.empty()
    _render_progress(placeholder, _PROGRESS_STAGES[0][1])
    ticker = _start_progress_ticker(placeholder, stop_event)
    try:
        outcome = chat_pipeline.run_turn(text)
    finally:
        stop_event.set()
        ticker.join(timeout=1)
        placeholder.empty()

    if outcome.get("update_card", True):
        st.session_state.candidates = outcome["candidates"]
        st.session_state.selected_candidate_idx = 0
        st.session_state.card_sections_revealed = set(outcome["reveals"])
        st.session_state.query_protein_sequence = outcome.get("query_protein_sequence")
    st.session_state.vector_db_result = outcome["result"]
    st.session_state.backend_warnings = outcome["warnings"]
    debug_panel.capture(text, outcome)
    return outcome["reply"], outcome["reveals"], list(outcome.get("suggested_questions") or [])


def main() -> None:
    st.set_page_config(
        page_title="BioSeq Investigator",
        page_icon=_load_favicon(),
        layout="wide",
    )
    _inject_styles()
    _render_topbar()
    _require_password()
    _bootstrap_session()
    session_sidebar.render()

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
                object_bar.render()
                object_inspector.render()
                # Legacy protein card stays available as a fallback when the
                # new registry has no selection but the old flow produced
                # candidates (e.g. mock backend, demo chip).
                if (
                    not session_objects.list_objects()
                    and st.session_state.candidates is not None
                ):
                    protein_card.render(
                        st.session_state.candidates,
                        st.session_state.card_sections_revealed,
                        query_sequence=st.session_state.query_protein_sequence,
                    )

    _inject_right_panel_resizer()
    _inject_candidate_click_forwarder()
    _inject_topbar_scroll()
    debug_panel.render()

    # Deferred rerun trigger. ``chat._run_pending`` is invoked from inside
    # the left column, well before ``object_bar.render()`` gets a chance to
    # commit the freshly-updated Session Objects chip statuses to the
    # browser. If we rerun from there, every intermediate state is
    # discarded and the user sees all sequences flip to ``ready`` in one
    # batch at the very end. Instead, ``_run_pending`` parks either a
    # follow-up ``pending_run`` (next sequence in a multi-paste batch) or
    # a ``_chat_force_rerun`` flag (post-batch chip refresh) in session
    # state and lets the script finish so the right column commits. Only
    # *then* do we rerun — so each sequence's transition to ``ready``
    # actually paints before the next search starts.
    force_rerun = st.session_state.pop("_chat_force_rerun", False)
    if st.session_state.get("pending_run") or force_rerun:
        st.rerun()


if __name__ == "__main__":
    main()

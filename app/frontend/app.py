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
import gateway_supervisor  # noqa: E402
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
            <div class="bioseq-topbar-brand-text" style="display:flex;flex-direction:column;justify-content:center;line-height:1.1;">
              <span class="bioseq-topbar-title">BioSeq Investigator</span>
              <span class="bioseq-topbar-attribution" title="Built on protein data from UniProt. &#169; UniProt Consortium, licensed under CC BY 4.0. Data adapted for presentation. BioSeq Investigator is an independent project, not affiliated with or endorsed by UniProt.">Built on <a href="https://www.uniprot.org" target="_blank" rel="noopener noreferrer">UniProt</a> data &#183; &#169; UniProt Consortium &#183; <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener noreferrer">CC BY 4.0</a></span>
            </div>
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


# Streamlit/React reconciliation sometimes fails to remove a previous
# render's ``.st-key-main_layout`` subtree — most reliably when the
# user sends their first message and several structural changes hit
# the DOM in the same commit (mention bridge appears, demo chip
# disappears, chat_area gains its first user message). The previous
# rendered ``main_layout`` is left in the DOM marked entirely stale,
# but still occupying flex space — pushing the live chat narrow on
# the left and the right panel down into the bottom corner.
#
# This sweeper runs on every rerun and removes any ``main_layout``
# whose ``stElementContainer`` descendants are all ``data-stale="true"``.
# Same defensive-cleanup pattern as the right-resizer / debug panel
# scripts use for their own orphaned nodes from older builds.
_STALE_LAYOUT_SWEEPER_JS = """
<script>
(function () {
    const doc = window.parent.document;
    const win = window.parent;

    function sweep() {
        const layouts = doc.querySelectorAll(".st-key-main_layout");
        if (layouts.length === 0) return;
        const fresh = [];
        const stale = [];
        layouts.forEach(function (node) {
            const containers = node.querySelectorAll(
                '[data-testid="stElementContainer"]'
            );
            if (!containers.length) { fresh.push(node); return; }
            let anyFresh = false;
            for (let i = 0; i < containers.length; i++) {
                if (containers[i].getAttribute("data-stale") !== "true") {
                    anyFresh = true;
                    break;
                }
            }
            (anyFresh ? fresh : stale).push(node);
        });
        // ALWAYS unhide fresh layouts first, regardless of how many
        // total layouts exist. A previously-hidden layout (because it
        // was stale in a prior 2-layout race) can become the live
        // one in a subsequent rerun — if we don't restore display
        // here, it stays hidden forever and the chat input + content
        // inside it look like they vanished.
        //
        // Hide via inline ``display: none`` instead of ``node.remove()``.
        // Removing the node fights React for ownership of a DOM subtree
        // it still expects to manage — on the next reconciliation pass
        // React can re-attach the orphan, undoing our sweep. React
        // doesn't compete over inline styles, so display:none sticks.
        fresh.forEach(function (node) {
            if (node.style.display === "none") node.style.display = "";
        });
        // Hide stale layouts only when at least one fresh exists, so
        // we never strip the page bare during a transient state where
        // every layout happens to be marked stale for one frame.
        if (!fresh.length) return;
        stale.forEach(function (node) {
            if (node.style.display !== "none") node.style.display = "none";
        });
    }

    // Initial sweeps — once on the next frame so we're past the initial
    // commit, and once after a short delay to catch late reconciliations
    // (Streamlit's autosize / our own JS injections occasionally land a
    // few frames after the script-run notification).
    win.requestAnimationFrame(function () {
        sweep();
        win.setTimeout(sweep, 80);
    });

    // Persistent watcher. The two-shot pattern above misses the case
    // where the user submits a chat message and the retriever blocks
    // the script thread for several seconds: Streamlit marks the
    // previous ``main_layout``'s containers ``data-stale="true"`` long
    // after the rerun started, so by the time the marks land the
    // sweeper has already finished. The observer below catches those
    // late mutations and re-sweeps. Reference is stashed on ``win`` so
    // a re-mounted iframe disconnects the old observer before
    // installing its own — same zombie-closure protection used
    // elsewhere in this file (resizer, debug panel, etc.). */
    if (win.__bioseqStaleSweepObserver) {
        try { win.__bioseqStaleSweepObserver.disconnect(); } catch (e) {}
    }
    let pending = false;
    function schedule() {
        if (pending) return;
        pending = true;
        win.requestAnimationFrame(function () {
            pending = false;
            sweep();
        });
    }
    const observer = new win.MutationObserver(function (mutations) {
        for (let i = 0; i < mutations.length; i++) {
            const m = mutations[i];
            if (m.type === "attributes") { schedule(); return; }
            if (m.addedNodes && m.addedNodes.length) { schedule(); return; }
        }
    });
    observer.observe(doc.body, {
        subtree: true,
        attributes: true,
        attributeFilter: ["data-stale"],
        childList: true,
    });
    win.__bioseqStaleSweepObserver = observer;
})();
</script>
"""


def _inject_stale_layout_sweeper() -> None:
    components.html(_STALE_LAYOUT_SWEEPER_JS, height=0, width=0)


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
            if (!el || el.scrollTop === 0) continue;
            // Style sets overflow:hidden on these containers to disable
            // page-level scroll, but the browser still programmatically
            // scrolls them on focus/caret-in-view when the chat input
            // textarea autoresizes past the viewport. That phantom scroll
            // never reverses on its own and would leave the topbar
            // permanently shifted up. Snap scrollTop back to 0 and don't
            // count it toward the topbar offset.
            if (getComputedStyle(el).overflowY === 'hidden') {
                el.scrollTop = 0;
                continue;
            }
            if (el.scrollTop > y) y = el.scrollTop;
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


# While ``.bioseq-click-shield`` is in the DOM it captures every pointer
# event so widget clicks can't trigger a rerun mid-search (see the CSS
# comment in style.css). That also kills wheel scroll, so this script
# manually forwards the wheel delta to the topmost scrollable ancestor
# under the cursor. The listener gates on the shield's presence each
# time, and otherwise stays a no-op so it never affects normal
# interactions.
#
# Re-install on every iframe load (same zombie-closure protection
# used by the mention bridge, topbar scroll, and right-panel resizer
# — see their comments for the full explanation). The previous "guard
# with __bioseqShieldWheelInstalled" pattern left a dead handler
# pointing into a destroyed iframe context after the first rerun, and
# wheel events stopped scrolling silently from that point on.
_CLICK_SHIELD_WHEEL_FORWARDER_JS = """
<script>
(function () {
    const doc = window.parent.document;

    function shield() {
        return doc.querySelector('.bioseq-click-shield');
    }

    function findScrollable(x, y, sh) {
        // Temporarily disable the shield's pointer capture so
        // elementFromPoint returns the element underneath, then put it
        // back. The DOM mutation here is synchronous and invisible.
        const prev = sh.style.pointerEvents;
        sh.style.pointerEvents = 'none';
        const el = doc.elementFromPoint(x, y);
        sh.style.pointerEvents = prev;
        let node = el;
        while (node && node !== doc.body) {
            const cs = doc.defaultView.getComputedStyle(node);
            const overflowY = cs.overflowY;
            if ((overflowY === 'auto' || overflowY === 'scroll')
                    && node.scrollHeight > node.clientHeight) {
                return node;
            }
            node = node.parentElement;
        }
        return doc.scrollingElement || doc.documentElement;
    }

    const handler = function (e) {
        const sh = shield();
        if (!sh) return;
        e.preventDefault();
        e.stopPropagation();
        const target = findScrollable(e.clientX, e.clientY, sh);
        if (target && typeof target.scrollBy === 'function') {
            target.scrollBy({
                left: e.deltaX,
                top: e.deltaY,
                behavior: 'auto',
            });
        }
    };

    if (doc.__bioseqShieldWheelHandler) {
        try {
            doc.removeEventListener(
                'wheel', doc.__bioseqShieldWheelHandler, { capture: true }
            );
        } catch (e) {}
    }
    doc.__bioseqShieldWheelHandler = handler;
    doc.addEventListener('wheel', handler, { capture: true, passive: false });
})();
</script>
"""


def _inject_click_shield_wheel_forwarder() -> None:
    components.html(_CLICK_SHIELD_WHEEL_FORWARDER_JS, height=0, width=0)


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

# Display labels for the search-algorithm picker. Kept in sync with the
# sidebar (components/session_sidebar.py) — both modules want to print the
# same human-readable name (status footer here, dropdown there).
_ALGO_LABELS = {
    "embeddings": "Embeddings (ProtT5 + FAISS)",
    "blast": "BLAST (EBI / SwissProt)",
}


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as ``m:ss`` for the live progress ticker."""
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _render_progress(placeholder, label: str, elapsed_seconds: float = 0.0) -> None:
    # Multi-sequence batches push a ``@Seq_B (2/5)`` prefix into session
    # state from ``chat._run_pending`` so each search in the batch tells the
    # user which one is in flight — without it the spinner just cycles the
    # same stage labels and the four still-``searching`` chips on the
    # Session Objects bar look stuck.
    prefix = st.session_state.get("batch_progress_label") or ""
    full_label = f"{prefix} — {label}" if prefix else label
    timer = _format_elapsed(elapsed_seconds)
    # The click-shield is a full-viewport transparent overlay that absorbs
    # pointer events while the retriever runs synchronously in this script
    # run. Without it, any click on a Streamlit widget triggers a rerun and
    # aborts the in-flight search. Both the shield and the inline progress
    # row live inside the same placeholder, so ``placeholder.empty()`` in
    # ``_handle_vector_db_submission``'s ``finally`` block removes them
    # together when the turn completes (or errors).
    placeholder.markdown(
        f'<div class="bioseq-click-shield" aria-hidden="true"></div>'
        f'<div class="bioseq-progress">'
        f'<span class="bioseq-progress-spinner"></span>'
        f'<span class="bioseq-progress-label">{full_label}</span>'
        f'<span class="bioseq-progress-timer">{timer}</span>'
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
    is good enough until proper callbacks land. The same ticker also
    re-renders every ~0.5s so the live elapsed-time counter next to the
    label updates smoothly without a per-stage trigger.
    """
    import threading
    import time
    from streamlit.runtime.scriptrunner import add_script_run_ctx

    TICK_INTERVAL = 1.0

    def _tick() -> None:
        start = time.monotonic()
        stage_index = 1  # next stage threshold to cross
        current_label = _PROGRESS_STAGES[0][1]
        while not stop_event.is_set():
            elapsed = time.monotonic() - start
            # Advance the stage label across any thresholds we've crossed
            # since the last tick.
            while (
                stage_index < len(_PROGRESS_STAGES)
                and elapsed >= _PROGRESS_STAGES[stage_index][0]
            ):
                current_label = _PROGRESS_STAGES[stage_index][1]
                stage_index += 1
            try:
                _render_progress(placeholder, current_label, elapsed)
            except Exception:
                return
            if stop_event.wait(TICK_INTERVAL):
                return

    thread = threading.Thread(target=_tick, daemon=True)
    # Streamlit elements require the script-run context to be attached
    # to any thread that calls into them, otherwise the writes from this
    # thread silently no-op (or warn) when targeting the parent script.
    add_script_run_ctx(thread)
    thread.start()
    return thread


# Retry tuning for transient upstream failures (e.g. the Gemini proxy
# returning 503 Service Unavailable when its worker is cold/overloaded).
_SERVER_BUSY_RETRIES = 1
_SERVER_BUSY_RETRY_DELAY_SECONDS = 5.0
_SERVER_BUSY_NOTICE = "Server is busy, let us wait for a couple of seconds…"


def _run_turn_with_progress(text: str, placeholder) -> tuple[dict, float]:
    """Run one backend turn while driving the live progress indicator.

    Returns ``(outcome, elapsed_seconds)``. The progress ticker is torn
    down before returning so the caller can render its own status (busy
    notice, "Finalizing…") into the same placeholder without a background
    thread overwriting it.
    """
    import chat_pipeline  # noqa: WPS433  (lazy import; heavy backend deps)
    import threading
    import time

    stop_event = threading.Event()
    _render_progress(placeholder, _PROGRESS_STAGES[0][1], 0.0)
    ticker = _start_progress_ticker(placeholder, stop_event)
    start = time.monotonic()
    try:
        outcome = chat_pipeline.run_turn(text)
    finally:
        elapsed = time.monotonic() - start
        stop_event.set()
        ticker.join(timeout=1)
    return outcome, elapsed


def _render_busy_notice(placeholder, message: str) -> None:
    """Render a transient "server busy" status while we wait to retry.

    Reuses the progress row's look — including the click-shield so a stray
    click doesn't trigger a rerun and abort the pending retry — but swaps
    the spinner copy for the wait notice and drops the elapsed timer.
    """
    placeholder.markdown(
        f'<div class="bioseq-click-shield" aria-hidden="true"></div>'
        f'<div class="bioseq-progress">'
        f'<span class="bioseq-progress-spinner"></span>'
        f'<span class="bioseq-progress-label">{message}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )


def _handle_vector_db_submission(
    text: str,
) -> tuple[str, set[str], list[str], str | None]:
    """Run one user turn through the active backend and persist it.

    On retriever turns (``update_card=True``) we replace the protein card
    with the new candidates and reset the candidate switcher to position 0.
    On chat-LLM follow-up turns (``update_card=False``) we leave the card
    state alone so the user's selected candidate doesn't jump.

    Returns ``(reply, reveals, suggested_questions, secondary_reply)``.
    ``secondary_reply`` is the optional Chat-LLM follow-up text emitted
    right after a retriever hit; ``None`` on every other path.
    """
    import time

    placeholder = st.empty()
    outcome, elapsed_seconds = _run_turn_with_progress(text, placeholder)

    # Transient upstream failure (e.g. the Gemini proxy returning 503 while
    # its worker is cold/overloaded): tell the user we're waiting, pause,
    # then retry. The backend tags these turns as ``server_busy`` and skips
    # persisting them, so a successful retry leaves a single clean turn in
    # history instead of an error bubble followed by the real answer.
    attempts = 0
    while outcome.get("server_busy") and attempts < _SERVER_BUSY_RETRIES:
        attempts += 1
        _render_busy_notice(placeholder, _SERVER_BUSY_NOTICE)
        time.sleep(_SERVER_BUSY_RETRY_DELAY_SECONDS)
        retry_outcome, retry_elapsed = _run_turn_with_progress(text, placeholder)
        outcome = retry_outcome
        elapsed_seconds += retry_elapsed + _SERVER_BUSY_RETRY_DELAY_SECONDS

    # Don't blank the slot here — there's still ~1–3s of work below
    # (footer, session-state, debug_panel, then Streamlit rerun + chat
    # history re-render). If we ``empty()`` now, that interval shows as
    # a blank gap between the live progress and the final reply. Hold a
    # "Finalizing…" label until the very end of the function instead.
    _render_progress(placeholder, "Finalizing…", elapsed_seconds)

    # Only retriever turns get the timing/algorithm footer — Chat-LLM
    # follow-up turns don't actually hit a database, so the elapsed
    # number would mostly be Gemini latency and the algorithm label
    # would be misleading.
    if outcome.get("update_card", True):
        algo_key = st.session_state.get("search_algorithm") or "embeddings"
        algo_label = _ALGO_LABELS.get(algo_key, algo_key)
        footer = (
            f"\n\n---\n"
            f"*Search completed in {elapsed_seconds:.1f}s · "
            f"Algorithm: {algo_label}*"
        )
        outcome["reply"] = f"{outcome['reply']}{footer}"

    if outcome.get("update_card", True):
        st.session_state.candidates = outcome["candidates"]
        st.session_state.selected_candidate_idx = 0
        st.session_state.card_sections_revealed = set(outcome["reveals"])
        st.session_state.query_protein_sequence = outcome.get("query_protein_sequence")
    st.session_state.vector_db_result = outcome["result"]
    st.session_state.backend_warnings = outcome["warnings"]
    debug_panel.capture(text, outcome)
    secondary_reply = outcome.get("secondary_reply") or None
    # All post-processing done; safe to tear down the progress slot. The
    # caller renders the actual reply right after this returns, so the
    # transition from "Finalizing…" to the final message is now seamless.
    placeholder.empty()
    return (
        outcome["reply"],
        outcome["reveals"],
        list(outcome.get("suggested_questions") or []),
        secondary_reply,
    )


@st.cache_resource(show_spinner=False)
def _ensure_gateway_once() -> dict:
    """Bring up the heavy search/rerank gateway exactly once per server
    process. Opt-in via ``BIOSEQ_SPAWN_GATEWAY`` (set it on single-container
    deploys such as a HF Streamlit Space, where there is no second terminal to
    run ``services/search_service.py`` in). ``st.cache_resource`` makes this
    run once and survive Streamlit's per-interaction script reruns. No-op /
    harmless when the flag is unset or a gateway is already listening."""
    return gateway_supervisor.ensure_gateway()


def main() -> None:
    st.set_page_config(
        page_title="BioSeq Investigator",
        page_icon=_load_favicon(),
        layout="wide",
    )
    # Kick off the gateway before anything heavy renders, so its multi-minute
    # warmup overlaps with the user reading the page. Fire-and-forget; the
    # retriever's own probe handles the not-ready-yet window.
    _ensure_gateway_once()
    _inject_styles()
    _render_topbar()
    # Install the wheel forwarder early too — the click shield it
    # complements gets rendered inside chat.render() (above the
    # spinner) at the moment the retriever starts, so the wheel
    # listener has to already be live by then or scroll stays dead
    # for the entire blocking call.
    _inject_click_shield_wheel_forwarder()
    # Install the stale-layout sweeper as early as possible so its
    # MutationObserver is alive BEFORE chat.render() blocks on
    # ``run_turn``. If we leave the inject at the bottom of main()
    # (where it used to live), the observer only comes online after
    # the retrieval has already finished — meaning Streamlit's late
    # ``data-stale="true"`` marks land while no observer is watching,
    # the orphaned previous layout stays in the DOM, and the chat
    # gets squeezed narrow while the right panel falls below it.
    _inject_stale_layout_sweeper()
    _require_password()
    _bootstrap_session()
    session_sidebar.render()

    with st.container(key="main_layout"):
        # Ratio chosen so the INITIAL paint already matches what the CSS
        # :has() override later forces (left fills, right pinned to
        # ~440px). With ratio [5, 7] the left column would briefly render
        # at ~42% width — visible as the chat "snapping wider" ~1s into
        # page load, once the override resolves. ~3:1 is close enough to
        # the final ~73/27 split on a typical viewport that the eye
        # doesn't catch the adjustment.
        left, right = st.columns([3, 1], gap="large")
        # Render the right column BEFORE the left. ``chat.render()`` in
        # the left column blocks the script thread on the synchronous
        # retriever call inside ``run_turn``, so anything declared
        # AFTER chat.render only paints once the search returns. With
        # the right rendered first, the Session Objects bar and Object
        # Inspector stay visible (and even reflect the in-flight
        # ``searching`` chip status) throughout the entire search.
        #
        # The visual order doesn't change — ``st.columns`` controls
        # left/right placement via CSS flex, not by Python render
        # order. Streamlit's submit flow (chat_input → stage → rerun →
        # block → rerun) means the new object's "searching" status is
        # already in session_state by the time the right column
        # re-renders for the blocking pass, so this swap actually
        # improves the in-flight UX rather than showing stale state.
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
        with left:
            with st.container(key="main_left"):
                chat.render(
                    on_first_search=_load_protein,
                    on_submit=_handle_vector_db_submission if config.USE_VECTOR_DB_MODE else None,
                )

    _inject_right_panel_resizer()
    _inject_candidate_click_forwarder()
    _inject_topbar_scroll()
    debug_panel.render(visible=config.SHOW_DEBUG_PANELS)

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

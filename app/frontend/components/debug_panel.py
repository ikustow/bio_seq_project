"""Floating chat-LLM debug panel.

Self-contained Streamlit component that captures the full payload sent to
the chat LLM (Gemini proxy / OpenAI) per turn and renders it in a small,
draggable, resizable panel pinned to the document. Lives at ``<body>``
level so it floats above the rest of the layout regardless of column or
sidebar stacking contexts.

Public surface:
    capture(prompt, outcome)
        Call after each chat turn (from ``app._handle_vector_db_submission``
        or equivalent). Pulls ``debug_request`` out of the backend
        ``ChatTurnResult.metadata`` and appends an entry to
        ``st.session_state.llm_debug_log`` (capped at 50).

    render()
        Call once near the end of ``main()``. Emits the panel markup + the
        JS that wires drag/resize/copy/reset/selector behavior.

The panel keeps its on-screen position and size across Streamlit reruns
within a single page load by re-parenting itself to ``<body>`` on first
mount and merging fresh state from subsequent reruns. It does NOT persist
state to ``localStorage`` — every page reload starts from the default
corner-tucked minimum size.
"""

from __future__ import annotations

import json
import time
from typing import Any

import streamlit as st
import streamlit.components.v1 as components


_PANEL_ID = "bioseq-llm-debug-panel"
_LOG_KEY = "llm_debug_log"
_LOG_LIMIT = 50


def init_state() -> None:
    """Initialize session state slot. Safe to call repeatedly."""
    if _LOG_KEY not in st.session_state:
        st.session_state[_LOG_KEY] = []


def capture(prompt: str, outcome: dict[str, Any]) -> None:
    """Pull the full request payload sent to the chat LLM out of a turn's
    backend ``outcome`` and stash it in session_state. Retriever-only turns
    have no LLM request and are skipped silently.

    ``reply`` in the captured entry is the Chat-LLM reply itself: on a
    plain follow-up that's ``outcome["reply"]``, on a retriever turn
    that's now ``outcome["secondary_reply"]`` (the chained Gemini answer
    rendered as the second assistant bubble). The retriever's canned
    "I classified..." string is not a Chat-LLM artefact and shouldn't
    surface here as the model's response.
    """
    result = outcome.get("result") or {}
    metadata = result.get("metadata") or {}
    debug_request = metadata.get("debug_request")
    if not debug_request:
        return
    secondary_reply = outcome.get("secondary_reply") or None
    primary_reply = outcome.get("reply", "")
    chat_llm_reply = secondary_reply if secondary_reply is not None else primary_reply
    log = st.session_state.get(_LOG_KEY) or []
    entry: dict[str, Any] = {
        "ts": time.time(),
        "prompt": prompt,
        "reply": chat_llm_reply,
        "backend": outcome.get("backend"),
        "provider": metadata.get("provider"),
        "request": debug_request,
    }
    # For chained retriever → Gemini turns surface the canonical pipeline
    # message too so the panel shows what the user actually saw in the
    # first assistant bubble. Plain follow-up turns leave this absent.
    if secondary_reply is not None:
        entry["primary_reply"] = primary_reply
    log.append(entry)
    st.session_state[_LOG_KEY] = log[-_LOG_LIMIT:]


def render() -> None:
    """Emit the panel markup and wire its behavior.

    The panel HTML is NOT emitted via ``st.markdown`` — doing so used to
    crash React with ``Failed to execute 'removeChild' on 'Node'`` because
    the JS below re-parents the panel to ``document.body``, and on the
    next rerun Streamlit emits a fresh copy while React still thinks the
    original lives inside its container. Instead the panel data is
    serialized into the JS payload and the JS creates/updates the panel
    DOM entirely inside ``<body>``, completely outside React's tree.
    """
    log: list[dict[str, Any]] = list(st.session_state.get(_LOG_KEY) or [])
    indexed = list(enumerate(log))
    indexed.reverse()  # newest first

    if indexed:
        js_entries = [
            {"label": _entry_label(idx, entry), "text": _format_entry(entry)}
            for idx, entry in indexed
        ]
    else:
        js_entries = []

    payload = {
        "panelId": _PANEL_ID,
        "entries": js_entries,
        "emptyText": (
            "No chat-LLM requests captured yet. Send a follow-up message "
            "once a protein card is loaded and the full Gemini payload "
            "will show up here."
        ),
    }
    # Escape any ``</`` so a captured prompt or reply that contains
    # ``</script>`` can't break out of the <script> block when the JS
    # payload is interpolated below.
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    components.html(
        _PANEL_JS.replace("__BIOSEQ_DEBUG_PAYLOAD__", payload_json),
        height=0,
        width=0,
    )


def _entry_label(idx: int, entry: dict[str, Any]) -> str:
    prompt = (entry.get("prompt") or "").strip().replace("\n", " ")
    if len(prompt) > 60:
        prompt = prompt[:57] + "…"
    return f"#{idx + 1} · {prompt or '(empty prompt)'}"


def _format_entry(entry: dict[str, Any]) -> str:
    """Render one log entry as a human-readable block: curl reproducer
    (when applicable), the full request as pretty JSON, and the assistant
    reply for cross-reference."""
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
    primary_reply = entry.get("primary_reply")
    if primary_reply:
        parts.append("# Retriever (primary) reply shown before the LLM bubble:")
        parts.append(str(primary_reply))
        parts.append("")
        parts.append("# Chat-LLM (secondary) reply:")
    else:
        parts.append("# Assistant reply:")
    parts.append(str(entry.get("reply", "")))
    return "\n".join(parts)


def _html_escape(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


_PANEL_JS = """
<script>
(function () {
    const doc = window.parent.document;
    const win = window.parent;
    const PAYLOAD = __BIOSEQ_DEBUG_PAYLOAD__;
    const PANEL_ID = PAYLOAD.panelId;

    // Drop any state saved by older builds — this version always starts
    // at the default corner + minimum size on each page load.
    try {
        win.localStorage.removeItem("bioseq_llm_debug_panel_state");
        win.localStorage.removeItem("bioseq_llm_debug_panel_state_v2");
    } catch (e) { /* no-op */ }

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // Sweep up any leftover panels that older builds emitted via
    // ``st.markdown`` into Streamlit's container. Leaving them there
    // would let React try to reconcile around our body-level panel and
    // crash with ``removeChild`` on the next rerun.
    doc.querySelectorAll('#' + PANEL_ID).forEach(function (node) {
        if (node.parentElement !== doc.body) node.remove();
    });

    function buildPanelMarkup(hasEntries) {
        const selectorHtml = hasEntries
            ? '<select data-role="selector" class="lldp-selector" title="Pick a turn"></select>'
            : '';
        return (
            '<div class="lldp-header" title="Drag to move">'
            + '<span class="lldp-dot"></span>'
            + '<strong>Debug</strong>'
            + '<span class="lldp-spacer"></span>'
            + selectorHtml
            + '<span data-role="copy" class="lldp-copy" title="Copy debug text">⧉</span>'
            + '<span data-role="reset" class="lldp-reset" title="Reset to default size and position">⤓</span>'
            + '</div>'
            + '<div data-role="body" class="lldp-body"></div>'
            + '<span data-role="resize" class="lldp-resize" title="Drag to resize"></span>'
        );
    }

    function applyEntries(panel, entries, emptyText) {
        panel.dataset.entries = JSON.stringify(entries);
        const header = panel.querySelector('.lldp-header');
        let sel = panel.querySelector('[data-role="selector"]');
        const body = panel.querySelector('[data-role="body"]');
        const copyBtn = panel.querySelector('[data-role="copy"]');

        if (entries.length) {
            if (!sel) {
                sel = doc.createElement('select');
                sel.setAttribute('data-role', 'selector');
                sel.className = 'lldp-selector';
                sel.setAttribute('title', 'Pick a turn');
                header.insertBefore(sel, copyBtn);
                wireSelector(panel, sel);
            }
            const prev = sel.value;
            sel.innerHTML = entries.map(function (item, i) {
                return '<option value="' + i + '">' + escapeHtml(item.label) + '</option>';
            }).join('');
            const keep = !!sel.querySelector('option[value="' + prev + '"]');
            sel.value = keep ? prev : '0';
            const idx = parseInt(sel.value, 10) || 0;
            if (body && entries[idx]) body.textContent = entries[idx].text;
        } else {
            if (sel) sel.remove();
            if (body) body.textContent = emptyText;
        }
    }

    // wireSelector used to attach the selector's change/pointerdown
    // listeners here, but those closures died together with the iframe
    // that created them (Chromium silently no-ops listeners whose
    // realm has been torn down). All selector wiring now happens in
    // the per-rerun rewire block below via the ``on()`` helper so it
    // gets torn down + recreated on every script run.
    function wireSelector(panel, sel) { /* no-op; kept for call-site compat */ }

    function clampToViewport(panel) {
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

    function resetPanel(panel) {
        // Clearing the inline styles lets the panel fall back to the CSS
        // defaults: right/bottom 8px corner anchor + minimum width/height.
        panel.style.left   = "";
        panel.style.top    = "";
        panel.style.right  = "";
        panel.style.bottom = "";
        panel.style.width  = "";
        panel.style.height = "";
    }

    // The panel lives in document.body — created here on first call,
    // reused on subsequent calls. Streamlit never sees it.
    let panel = doc.body.querySelector(':scope > #' + PANEL_ID);
    if (!panel) {
        panel = doc.createElement('div');
        panel.id = PANEL_ID;
        panel.className = 'bioseq-llm-debug-panel';
        panel.innerHTML = buildPanelMarkup(PAYLOAD.entries.length > 0);
        doc.body.appendChild(panel);
    }

    applyEntries(panel, PAYLOAD.entries, PAYLOAD.emptyText);

    // Defensive cleanup on every iframe load: clear any stale drag/resize
    // state classes and force interactivity. If a Streamlit rerun
    // happened mid-drag, the corresponding ``pointerup`` may have been
    // lost on the dying iframe and the panel was left with a stuck
    // ``is-dragging`` / ``is-resizing`` class or — worse — an unrelased
    // pointer capture (the symptom: panel becomes completely
    // unclickable until a full page reload). Force everything back to a
    // sane state here so the panel can never get permanently wedged.
    panel.classList.remove("is-dragging", "is-resizing");
    panel.style.pointerEvents = "auto";
    try {
        if (win.__bioseqDebugPointerId != null) {
            panel.releasePointerCapture(win.__bioseqDebugPointerId);
        }
    } catch (e) { /* no-op */ }
    win.__bioseqDebugPointerId = null;

    // -- Always re-wire handlers on every iframe load. --
    //
    // The panel lives at parent-document body level, but every Streamlit
    // rerun produces a fresh iframe that runs this script. Handlers
    // defined inside the iframe close over local state (``dragging``,
    // ``dragStartX``, ...). When the iframe is torn down on the next
    // rerun, those closures point at a destroyed JS realm — in Chromium
    // the handlers silently no-op and the panel goes completely
    // unclickable. The previous build only wired handlers when
    // ``isNew`` was true (i.e. the very first script run), so the bug
    // surfaced reliably after the first rerun that mutated the panel
    // (e.g. an LLM response landing and ``applyEntries`` running).
    //
    // The fix is to remove the old handlers before each rewire so the
    // panel always carries listeners that close over *this* iframe's
    // (alive) scope. We stash the teardown callback on the panel so the
    // next iframe load can reach it.
    if (panel.__bioseqTeardown) {
        try { panel.__bioseqTeardown(); } catch (e) { /* no-op */ }
    }
    const cleanups = [];
    const on = function (target, evt, fn, opts) {
        target.addEventListener(evt, fn, opts);
        cleanups.push(function () {
            try { target.removeEventListener(evt, fn, opts); } catch (e) {}
        });
    };
    panel.__bioseqTeardown = function () {
        for (let i = 0; i < cleanups.length; i++) {
            try { cleanups[i](); } catch (e) {}
        }
    };

    const bodyEl   = panel.querySelector("[data-role='body']");
    const copyBtn  = panel.querySelector("[data-role='copy']");
    const resetBtn = panel.querySelector("[data-role='reset']");
    const header   = panel.querySelector(".lldp-header");
    const grip     = panel.querySelector("[data-role='resize']");
    const sel      = panel.querySelector("[data-role='selector']");
    if (sel && bodyEl) {
        on(sel, "change", function () {
            const entries = JSON.parse(panel.dataset.entries || '[]');
            const idx = parseInt(sel.value, 10);
            if (!Number.isFinite(idx) || idx < 0 || idx >= entries.length) return;
            bodyEl.textContent = entries[idx].text;
        });
        // Keep the dropdown click from bubbling into our panel-wide drag
        // listener. ``isInteractiveTarget`` already covers ``select``,
        // but stopPropagation here is cheap belt-and-braces in case the
        // browser fires pointerdown on an internal option element whose
        // tagName isn't ``SELECT``.
        on(sel, "pointerdown", function (event) { event.stopPropagation(); });
    }

    if (copyBtn && bodyEl) {
        const doCopy = function (event) {
            event.stopPropagation();
            event.preventDefault();
            const text = bodyEl.innerText || "";
            const flash = function () {
                const original = copyBtn.textContent;
                copyBtn.textContent = "✓";
                setTimeout(function () { copyBtn.textContent = original; }, 900);
            };
            const fallback = function () {
                try {
                    const ta = doc.createElement("textarea");
                    ta.value = text;
                    ta.style.position = "fixed";
                    ta.style.left = "-9999px";
                    doc.body.appendChild(ta);
                    ta.select();
                    doc.execCommand("copy");
                    doc.body.removeChild(ta);
                    flash();
                } catch (err) { /* no-op */ }
            };
            const clip = (window.parent.navigator && window.parent.navigator.clipboard) || null;
            if (clip && clip.writeText) {
                clip.writeText(text).then(flash, fallback);
            } else {
                fallback();
            }
        };
        on(copyBtn, "pointerdown", doCopy);
        on(copyBtn, "click", doCopy);
    }

    if (resetBtn) {
        const doReset = function (event) {
            event.stopPropagation();
            event.preventDefault();
            resetPanel(panel);
            const original = resetBtn.textContent;
            resetBtn.textContent = "✓";
            setTimeout(function () { resetBtn.textContent = original; }, 600);
        };
        on(resetBtn, "pointerdown", doReset);
        on(resetBtn, "click", doReset);
    }

    // Drag from anywhere on the panel chrome that isn't body text or an
    // interactive control. The body's JSON stays selectable for copy.
    let dragging = false;
    let dragStartX = 0, dragStartY = 0;
    let panelStartLeft = 0, panelStartTop = 0;
    const isInteractiveTarget = function (target) {
        if (!target || !target.closest) return false;
        return !!target.closest(
            "[data-role='copy'], [data-role='reset'], [data-role='selector'], "
            + "[data-role='resize'], [data-role='body'], select, option, button, "
            + "input, textarea, a"
        );
    };
    on(panel, "pointerdown", function (event) {
        if (event.button !== 0) return;
        if (isInteractiveTarget(event.target)) return;
        event.preventDefault();
        dragging = true;
        const rect = panel.getBoundingClientRect();
        panelStartLeft = rect.left;
        panelStartTop  = rect.top;
        dragStartX = event.clientX;
        dragStartY = event.clientY;
        panel.classList.add("is-dragging");
    });
    on(doc, "pointermove", function (event) {
        if (!dragging) return;
        event.preventDefault();
        const dx = event.clientX - dragStartX;
        const dy = event.clientY - dragStartY;
        panel.style.left   = (panelStartLeft + dx) + "px";
        panel.style.top    = (panelStartTop  + dy) + "px";
        panel.style.right  = "auto";
        panel.style.bottom = "auto";
    });
    const endDrag = function () {
        if (!dragging) return;
        dragging = false;
        panel.classList.remove("is-dragging");
        clampToViewport(panel);
    };
    on(doc, "pointerup", endDrag);
    on(doc, "pointercancel", endDrag);
    on(win, "blur", endDrag);

    // Custom resize grip in the bottom-right corner.
    let resizing = false;
    let resizeStartX = 0, resizeStartY = 0;
    let panelStartW = 0, panelStartH = 0;
    if (grip) {
        on(grip, "pointerdown", function (event) {
            event.preventDefault();
            event.stopPropagation();
            resizing = true;
            const rect = panel.getBoundingClientRect();
            panelStartW = rect.width;
            panelStartH = rect.height;
            resizeStartX = event.clientX;
            resizeStartY = event.clientY;
            panel.classList.add("is-resizing");
        });
        on(doc, "pointermove", function (event) {
            if (!resizing) return;
            event.preventDefault();
            const dx = event.clientX - resizeStartX;
            const dy = event.clientY - resizeStartY;
            panel.style.width  = Math.max(140, panelStartW + dx) + "px";
            panel.style.height = Math.max(30, panelStartH + dy) + "px";
        });
        const endResize = function () {
            if (!resizing) return;
            resizing = false;
            panel.classList.remove("is-resizing");
            clampToViewport(panel);
        };
        on(doc, "pointerup", endResize);
        on(doc, "pointercancel", endResize);
        on(win, "blur", endResize);
    }

    on(win, "resize", function () { clampToViewport(panel); });
})();
</script>
"""

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
    have no LLM request and are skipped silently."""
    result = outcome.get("result") or {}
    metadata = result.get("metadata") or {}
    debug_request = metadata.get("debug_request")
    if not debug_request:
        return
    log = st.session_state.get(_LOG_KEY) or []
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

    function wireSelector(panel, sel) {
        if (sel.__bioseqWired) return;
        sel.__bioseqWired = true;
        const body = panel.querySelector('[data-role="body"]');
        sel.addEventListener('change', function () {
            const entries = JSON.parse(panel.dataset.entries || '[]');
            const idx = parseInt(sel.value, 10);
            if (!Number.isFinite(idx) || idx < 0 || idx >= entries.length) return;
            if (body) body.textContent = entries[idx].text;
        });
        sel.addEventListener('pointerdown', function (event) { event.stopPropagation(); });
        sel.addEventListener('click',       function (event) { event.stopPropagation(); });
    }

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
    const isNew = !panel;
    if (!panel) {
        panel = doc.createElement('div');
        panel.id = PANEL_ID;
        panel.className = 'bioseq-llm-debug-panel';
        panel.innerHTML = buildPanelMarkup(PAYLOAD.entries.length > 0);
        doc.body.appendChild(panel);
    }

    applyEntries(panel, PAYLOAD.entries, PAYLOAD.emptyText);

    if (!isNew) return;  // handlers already wired on the existing panel.
    panel.__bioseqWired = true;

    const bodyEl   = panel.querySelector("[data-role='body']");
    const copyBtn  = panel.querySelector("[data-role='copy']");
    const resetBtn = panel.querySelector("[data-role='reset']");
    const header   = panel.querySelector(".lldp-header");
    const grip     = panel.querySelector("[data-role='resize']");
    const initialSel = panel.querySelector("[data-role='selector']");
    if (initialSel) wireSelector(panel, initialSel);

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
        copyBtn.addEventListener("pointerdown", doCopy);
        copyBtn.addEventListener("click", doCopy);
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
        resetBtn.addEventListener("pointerdown", doReset);
        resetBtn.addEventListener("click", doReset);
    }

    // Drag the panel by its header. Ignore drags that start on the
    // interactive children so they keep working normally.
    let dragging = false;
    let dragStartX = 0, dragStartY = 0;
    let panelStartLeft = 0, panelStartTop = 0;
    if (header) {
        header.addEventListener("pointerdown", function (event) {
            if (event.target.closest(
                "[data-role='copy'], [data-role='reset'], [data-role='selector'], "
                + "[data-role='resize'], select, option, button"
            )) return;
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
            panel.style.left   = (panelStartLeft + dx) + "px";
            panel.style.top    = (panelStartTop  + dy) + "px";
            panel.style.right  = "auto";
            panel.style.bottom = "auto";
        });
        const endDrag = function (event) {
            if (!dragging) return;
            dragging = false;
            panel.classList.remove("is-dragging");
            try { header.releasePointerCapture(event.pointerId); } catch (e) {}
            clampToViewport(panel);
        };
        header.addEventListener("pointerup", endDrag);
        header.addEventListener("pointercancel", endDrag);
    }

    // Custom resize grip in the bottom-right corner. Sits above the
    // body's scrollbar so grabbing the corner can never be hijacked by
    // an accidental scroll-thumb drag.
    let resizing = false;
    let resizeStartX = 0, resizeStartY = 0;
    let panelStartW = 0, panelStartH = 0;
    if (grip) {
        grip.addEventListener("pointerdown", function (event) {
            event.preventDefault();
            event.stopPropagation();
            resizing = true;
            const rect = panel.getBoundingClientRect();
            panelStartW = rect.width;
            panelStartH = rect.height;
            resizeStartX = event.clientX;
            resizeStartY = event.clientY;
            panel.classList.add("is-resizing");
            try { grip.setPointerCapture(event.pointerId); } catch (e) {}
        });
        grip.addEventListener("pointermove", function (event) {
            if (!resizing) return;
            event.preventDefault();
            const dx = event.clientX - resizeStartX;
            const dy = event.clientY - resizeStartY;
            panel.style.width  = Math.max(90, panelStartW + dx) + "px";
            panel.style.height = Math.max(22, panelStartH + dy) + "px";
        });
        const endResize = function (event) {
            if (!resizing) return;
            resizing = false;
            panel.classList.remove("is-resizing");
            try { grip.releasePointerCapture(event.pointerId); } catch (e) {}
            clampToViewport(panel);
        };
        grip.addEventListener("pointerup", endResize);
        grip.addEventListener("pointercancel", endResize);
    }

    win.addEventListener("resize", function () { clampToViewport(panel); });
})();
</script>
"""

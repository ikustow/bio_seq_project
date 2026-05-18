"""Session-scoped Object Bar — compact two-line chips for the registry.

Each chip is a single styled card containing label, type/length, best
match (if any) and a status pill. Clicks are routed through the
mention bridge (``.st-key-mention_btn_<id>`` hidden Streamlit buttons +
shared JS handler) so a chip click ends up calling
``session_objects.set_selected(<id>)`` via the normal event loop.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path

import streamlit as st

import session_objects

_PASTE_ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "paste.png"


@st.cache_data(show_spinner=False)
def _chip_insert_icon_css(icon_path: str) -> str:
    """Inject the paste.png as a mask-image on the chip's insert button.

    ``mask-image`` keeps the source PNG monochromatic so we can recolor
    it via ``background-color`` (blue by default, white when the chip is
    selected) without shipping two artwork variants.
    """
    path = Path(icon_path)
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    url = f"url('data:image/png;base64,{encoded}')"
    return (
        "<style>"
        ".bioseq-chip-insert {"
        f" -webkit-mask-image: {url};"
        f" mask-image: {url}; }}"
        "</style>"
    )


_STATUS_LABEL: dict[str, str] = {
    "draft": "draft",
    "queued": "queued",
    "classifying": "classifying",
    "searching": "searching…",
    "ready": "ready",
    "not_searched": "not searched",
    "search_failed": "search failed",
    "error": "error",
}

CHIPS_PER_ROW = 2  # wider chips: two per row keeps the bar compact


def _best_match_summary(seq: dict) -> str:
    matches = seq.get("matches") or []
    if not matches:
        return ""
    chosen_idx = int(seq.get("selected_match_index") or 0)
    if chosen_idx >= len(matches):
        chosen_idx = 0
    match = matches[chosen_idx]
    accession = match.get("accession") or (match.get("protein") or {}).get("accession") or ""
    gene = (match.get("protein") or {}).get("gene") or ""
    if accession and gene:
        return f"{accession} · {gene}"
    return accession or gene or ""


def _sequence_caption(seq: dict) -> str:
    seq_type = (seq.get("sequence_type") or "").lower() or "unknown"
    length = seq.get("length") or 0
    parts = [seq_type]
    if length:
        parts.append(f"{length} aa")
    best = _best_match_summary(seq)
    if best:
        parts.append(f"→ {best}")
    return " · ".join(parts)


def _protein_caption(obj: dict) -> str:
    gene = obj.get("gene") or ""
    organism = obj.get("organism") or ""
    parts = [bit for bit in (gene, organism) if bit]
    return " · ".join(parts) if parts else "UniProt entry"


def _chip_html(obj: dict, is_selected: bool) -> str:
    label = html.escape(obj.get("label") or obj.get("id") or "?")
    object_id = obj.get("id") or ""
    kind = obj.get("kind") or "object"

    if kind == "sequence":
        caption = _sequence_caption(obj)
        status = obj.get("status") or "draft"
        status_label = _STATUS_LABEL.get(status, status)
        status_html = (
            f'<span class="bioseq-chip-status bioseq-chip-status-{status}">'
            f"{html.escape(status_label)}</span>"
        )
    else:
        caption = _protein_caption(obj)
        status_html = '<span class="bioseq-chip-status bioseq-chip-status-protein">UniProt</span>'

    selected_class = " is-selected" if is_selected else ""
    insert_btn = (
        f'<button type="button" class="bioseq-chip-insert" '
        f'data-label="{label}" '
        f'aria-label="Insert @{label} into the chat input" '
        f'title="Insert @{label} into the chat input">'
        f"</button>"
    )
    return (
        f'<a href="#chip-{html.escape(object_id)}" '
        f'class="bioseq-chip{selected_class}" '
        f'draggable="true" '
        f'data-object-id="{html.escape(object_id)}" '
        f'data-label="{label}" '
        f'data-kind="{html.escape(kind)}">'
        f'<span class="bioseq-chip-header">'
        f'<span class="bioseq-chip-label">{label}</span>'
        f"{insert_btn}"
        f"</span>"
        f'<span class="bioseq-chip-caption">{html.escape(caption)}</span>'
        f'<span class="bioseq-chip-status-row">{status_html}</span>'
        f"</a>"
    )


def render() -> None:
    """Render the Session Objects bar above the Inspector."""
    session_objects.init_state()
    objects = session_objects.list_objects()
    selected_id = session_objects.get_selected_id()

    with st.container(border=True, key="object_bar"):
        st.markdown("#### Session objects")
        if not objects:
            st.caption(
                "No objects yet. Paste a sequence, attach a FASTA file, "
                "or type a UniProt accession (e.g. `O95185`) on the left."
            )
            return

        st.caption(f"{len(objects)} object(s) · click a chip to inspect it.")

        icon_css = _chip_insert_icon_css(str(_PASTE_ICON_PATH))
        if icon_css:
            st.markdown(icon_css, unsafe_allow_html=True)

        # Render the chips as a grid of HTML cards. Streamlit-side this is
        # a single ``st.markdown`` so we don't pay rerun cost per chip;
        # the click-to-select interaction is handled by the JS bridge.
        chips_html: list[str] = ['<div class="bioseq-chip-grid">']
        for obj in objects:
            chips_html.append(_chip_html(obj, is_selected=(obj["id"] == selected_id)))
        chips_html.append("</div>")
        st.markdown("\n".join(chips_html), unsafe_allow_html=True)

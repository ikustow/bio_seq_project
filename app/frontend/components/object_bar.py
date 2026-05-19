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
        ".bioseq-chip-insert::before {"
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


def _sequence_caption_html(seq: dict) -> str:
    """Build the caption HTML for a sequence chip.

    Two-row layout:
      - Row 1: ``<len> aa`` (left) ........... ``<accession>`` pill (right)
      - Row 2: ``<strong>Full protein name</strong>`` on its own line
    """
    length = seq.get("length") or 0
    matches = seq.get("matches") or []
    chosen_idx = int(seq.get("selected_match_index") or 0)
    if chosen_idx >= len(matches):
        chosen_idx = 0

    accession = ""
    name = ""
    if matches:
        match = matches[chosen_idx] or {}
        protein = match.get("protein") or {}
        accession = match.get("accession") or protein.get("accession") or ""
        name = protein.get("name") or ""

    blocks: list[str] = []
    if length or accession:
        len_html = (
            f'<span class="bioseq-chip-len">{html.escape(f"{length} aa")}</span>'
            if length
            else '<span class="bioseq-chip-len"></span>'
        )
        acc_html = (
            f'<span class="bioseq-chip-acc">{html.escape(accession)}</span>'
            if accession
            else ""
        )
        blocks.append(f'<span class="bioseq-chip-meta">{len_html}{acc_html}</span>')
    if name:
        blocks.append(
            f'<strong class="bioseq-chip-name">{html.escape(name)}</strong>'
        )
    return "".join(blocks)


def _protein_caption(obj: dict) -> str:
    gene = obj.get("gene") or ""
    organism = obj.get("organism") or ""
    parts = [bit for bit in (gene, organism) if bit]
    return " · ".join(parts) if parts else "UniProt entry"


def _chip_html(obj: dict, is_selected: bool) -> str:
    # ``label`` is the raw identifier (Seq_A / accession). ``visible_label``
    # is what the user sees and what gets pasted via the insert button —
    # for matched sequences it's the UniProt entry name (e.g. HBE1_HUMAN)
    # so the chip carries real biology, not an internal handle.
    raw_label = obj.get("label") or obj.get("id") or "?"
    visible_label = session_objects.display_label(obj) or raw_label
    label = html.escape(visible_label)
    object_id = obj.get("id") or ""
    kind = obj.get("kind") or "object"

    if kind == "sequence":
        caption_html = _sequence_caption_html(obj)
        status = obj.get("status") or "draft"
        status_label = _STATUS_LABEL.get(status, status)
        status_html = (
            f'<span class="bioseq-chip-status bioseq-chip-status-{status}">'
            f"{html.escape(status_label)}</span>"
        )
    else:
        caption_html = html.escape(_protein_caption(obj))
        status_html = '<span class="bioseq-chip-status bioseq-chip-status-protein">UniProt</span>'

    selected_class = " is-selected" if is_selected else ""
    insert_btn = (
        f'<button type="button" class="bioseq-chip-insert" '
        f'data-label="{label}" '
        f'aria-label="Insert @{label} into the chat input" '
        f'title="Insert @{label} into the chat input">'
        f"</button>"
    )
    # Carry the human-readable protein name on the chip so JS in the chat
    # composer can read it (via ``data-tooltip``) and surface it as a
    # tooltip on the autocomplete dropdown.
    tooltip_text = session_objects.protein_tooltip(obj)
    tooltip_attr = (
        f' data-tooltip="{html.escape(tooltip_text)}"' if tooltip_text else ""
    )
    return (
        f'<a href="#chip-{html.escape(object_id)}" '
        f'class="bioseq-chip{selected_class}" '
        f'draggable="true" '
        f'data-object-id="{html.escape(object_id)}" '
        f'data-label="{label}" '
        f'data-kind="{html.escape(kind)}"{tooltip_attr}>'
        f'<span class="bioseq-chip-header">'
        f'<span class="bioseq-chip-label">{label}</span>'
        f"{status_html}"
        f"{insert_btn}"
        f"</span>"
        f'<span class="bioseq-chip-caption">{caption_html}</span>'
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

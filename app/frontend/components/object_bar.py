"""Session-scoped Object Bar — compact two-line chips for the registry.

Each chip is a single styled card containing label, type/length, best
match (if any) and a status pill. Clicks are routed through the
mention bridge (``.st-key-mention_btn_<id>`` hidden Streamlit buttons +
shared JS handler) so a chip click ends up calling
``session_objects.set_selected(<id>)`` via the normal event loop.

The bar also surfaces *ghost* chips for spawn suggestions — when the
user previews a non-anchored match in a card's Top-5 switcher we offer
to create a new card for it without losing the original. Ghost-chip
``Create`` buttons reuse the mention bridge by carrying a synthetic
``data-object-id`` with the ``__spawn__`` prefix; the bridge callback
(``components/chat.py:_mention_click``) dispatches on the prefix and
calls :func:`session_objects.fork_sequence_with_match`.
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

    Pinned to the **anchored** match: previewing alternatives in the
    Top-5 switcher must NOT change what the bar shows for this card.
    """
    length = seq.get("length") or 0
    matches = seq.get("matches") or []
    # Identity is anchored_match_index; selected_match_index is preview only.
    chosen_idx = seq.get("anchored_match_index")
    if chosen_idx is None:
        chosen_idx = seq.get("selected_match_index") or 0
    try:
        chosen_idx = int(chosen_idx)
    except (TypeError, ValueError):
        chosen_idx = 0
    if chosen_idx >= len(matches) or chosen_idx < 0:
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


def _ghost_chip_html(
    parent_obj: dict,
    match_index: int,
    entry: str,
    accession: str,
) -> str:
    """Render a ghost spawn suggestion as a regular-sized chip.

    Same outer ``.bioseq-chip`` shape as real chips so the grid layout
    stays uniform — a ``.bioseq-chip-ghost`` modifier applies the
    dashed-border / muted-fill styling. The chip body is informational
    (clicks on it are intentionally a no-op so the user can't trigger a
    spawn by accident); the actual create affordance is the circular
    ``.bioseq-chip-create-btn`` floating at the bottom-center, mirroring
    the position of the insert button on real chips.

    The Create button carries ``data-object-id="__spawn__…"`` so the
    existing mention bridge routes the click into a hidden Streamlit
    button whose callback dispatches on the SPAWN_PREFIX and calls
    :func:`session_objects.fork_sequence_with_match`.
    """
    parent_id = str(parent_obj.get("id") or "")
    parent_label = (
        session_objects.display_label(parent_obj)
        or parent_obj.get("label")
        or "?"
    )
    token = session_objects.make_spawn_token(parent_id, match_index)
    entry_safe = html.escape(entry)
    accession_safe = html.escape(accession)
    tooltip = (
        f"Create a new card anchored on @{entry} (forked from @{parent_label})."
    )
    accession_html = (
        f'<span class="bioseq-chip-acc">{accession_safe}</span>'
        if accession
        else ""
    )
    return (
        f'<div class="bioseq-chip bioseq-chip-ghost" '
        f'data-spawn-fork="{html.escape(token)}" '
        f'title="{html.escape(tooltip)}">'
        f'<span class="bioseq-chip-header">'
        f'<span class="bioseq-chip-label">{entry_safe}</span>'
        f'<span class="bioseq-chip-status bioseq-chip-status-ghost">new</span>'
        f"</span>"
        f'<span class="bioseq-chip-caption">'
        f'<span class="bioseq-chip-meta">'
        f"{accession_html}"
        f"</span>"
        f'<strong class="bioseq-chip-name">Spawn as own card</strong>'
        f"</span>"
        f'<a href="#spawn-{html.escape(token)}" '
        f'class="bioseq-chip-create-btn" '
        f'data-object-id="{html.escape(token)}" '
        f'aria-label="{html.escape(tooltip)}" '
        f'title="{html.escape(tooltip)}">'
        f"Create"
        f"</a>"
        f"</div>"
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

        suggestions = session_objects.compute_spawn_suggestions()
        if suggestions:
            st.caption(
                f"{len(objects)} object(s) · dashed chip = spawn the previewed match."
            )
        else:
            st.caption(f"{len(objects)} object(s) · click a chip to inspect it.")

        icon_css = _chip_insert_icon_css(str(_PASTE_ICON_PATH))
        if icon_css:
            st.markdown(icon_css, unsafe_allow_html=True)

        # Render real chips + ghost spawn chips in one grid so they all
        # share dimensions. Streamlit-side this is a single ``st.markdown``
        # so we don't pay rerun cost per chip; the click interactions are
        # handled by the shared mention bridge in ``components/chat.py``.
        chips_html: list[str] = ['<div class="bioseq-chip-grid">']
        for obj in objects:
            chips_html.append(_chip_html(obj, is_selected=(obj["id"] == selected_id)))
        for parent_obj, match_index, entry, accession in suggestions:
            chips_html.append(
                _ghost_chip_html(parent_obj, match_index, entry, accession)
            )
        chips_html.append("</div>")
        st.markdown("\n".join(chips_html), unsafe_allow_html=True)

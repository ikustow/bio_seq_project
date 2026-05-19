"""Inspector — detailed view of the currently selected workspace object.

Two display modes:

- selected Sequence  ->  sequence summary, top-5 matches, alignment for
                         the locally-selected candidate.
- selected Protein   ->  one UniProt protein card with alignment vs the
                         Sequence the user came from (if any).
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

import session_objects
from components import alignment_viewer, protein_card


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


def _set_selected_match(sequence_id: str, index: int) -> None:
    session_objects.set_sequence_selected_match(sequence_id, index)


def _open_protein(accession: str, sequence_id: str | None) -> None:
    """Open a Protein card as the active inspector view."""
    obj = session_objects.get_object(session_objects.make_protein_id(accession))
    if obj is None:
        # Backend created the Protein during the same turn; if absent here we
        # do nothing and the user can click again after the next rerun.
        return
    if sequence_id:
        session_objects.set_protein_last_origin(accession, sequence_id)
    session_objects.set_selected(session_objects.make_protein_id(accession))


def render() -> None:
    """Render the inspector for the currently selected object."""
    selected_id = session_objects.get_selected_id()
    if selected_id is None:
        _render_empty()
        return

    obj = session_objects.get_object(selected_id)
    if obj is None:
        _render_empty()
        return

    if obj["kind"] == "sequence":
        _render_sequence(obj)
    elif obj["kind"] == "protein":
        _render_protein(obj)
    else:
        _render_empty()


def _render_empty() -> None:
    with st.container(border=True):
        st.markdown("### Object inspector")
        st.markdown(
            "<div class='card-locked'>Select a workspace object to inspect it.</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Sequence inspector
# ---------------------------------------------------------------------------


def _render_sequence(seq: dict) -> None:
    label = session_objects.display_label(seq) or seq.get("label") or seq.get("id")
    seq_type = seq.get("sequence_type") or "UNKNOWN"
    length = seq.get("length") or 0
    status = seq.get("status") or "draft"
    fasta_header = seq.get("fasta_header") or ""
    src = seq.get("source") or {}
    src_kind = src.get("type") or "pasted_text"
    source_label = (
        f"file <code>{html.escape(str(src.get('file_name') or 'uploaded'))}</code>"
        if src_kind == "file"
        else "pasted into chat"
    )

    header_parts: list[str] = []
    header_parts.append(
        f'<div class="bioseq-seq-header__title">{html.escape(str(label))}</div>'
    )
    badge_html = (
        f'<span class="bioseq-seq-header__badge bioseq-seq-header__badge--type">'
        f'{html.escape(seq_type)}</span>'
        f'<span class="bioseq-seq-header__badge bioseq-seq-header__badge--len">'
        f'{length} aa</span>'
        f'<span class="bioseq-seq-header__badge bioseq-seq-header__badge--status '
        f'bioseq-seq-header__status-{html.escape(status)}">'
        f'{html.escape(_STATUS_LABEL.get(status, status))}</span>'
    )
    header_parts.append(f'<div class="bioseq-seq-header__badges">{badge_html}</div>')

    facts: list[str] = []
    facts.append(f'<span class="bioseq-seq-header__fact-label">Source:</span> {source_label}')
    if seq.get("confidence") is not None:
        facts.append(
            f'<span class="bioseq-seq-header__fact-label">Confidence:</span> '
            f'<code>{seq["confidence"]:.2f}</code>'
        )
    if fasta_header:
        facts.append(
            f'<span class="bioseq-seq-header__fact-label">FASTA:</span> '
            f'<code>{html.escape(fasta_header[:120])}</code>'
        )
    reason = seq.get("classification_reason")
    if reason:
        facts.append(f'<span class="bioseq-seq-header__reason">{html.escape(reason)}</span>')

    header_parts.append(
        '<div class="bioseq-seq-header__facts">'
        + "<br>".join(facts)
        + "</div>"
    )

    st.markdown(
        '<div class="bioseq-seq-header">' + "".join(header_parts) + "</div>",
        unsafe_allow_html=True,
    )
    for warning in seq.get("warnings") or []:
        st.warning(warning)

    # DNA placeholder — translation handled elsewhere (see spec).
    if seq_type == "DNA" and not seq.get("protein_sequence"):
        st.info("DNA → protein translation is not implemented yet for this object.")

    raw_or_protein = seq.get("protein_sequence") or seq.get("normalized_sequence") or ""
    if raw_or_protein:
        with st.expander("Sequence body", expanded=False):
            st.code(_wrap_sequence(raw_or_protein), language="text")

    matches = seq.get("matches") or []
    chosen_idx = int(seq.get("selected_match_index") or 0)

    # A search counts as "in progress" only while there's still a pending
    # backend call. When the backend returned but produced no patch (e.g.
    # pipeline.error path in BioSeqChatService), the sequence is left at
    # status="searching" with no matches — surface that as a recoverable
    # failure instead of an indefinite spinner.
    pending_run = bool(st.session_state.get("pending_run"))
    active_states = {"searching", "queued", "classifying"}
    search_in_progress = pending_run and status in active_states
    stalled = (
        not matches
        and status in active_states
        and not pending_run
    )

    if search_in_progress:
        st.info("Searching for similar proteins…")
        return
    if status in {"search_failed", "error"} or stalled:
        st.error(
            "The search did not return any matches — the backend may have "
            "been unreachable or returned an error."
        )
        _render_retry_search_button(seq)
        return
    if status == "not_searched":
        st.info(
            "Automatic search was skipped for this sequence. Use Search "
            "again to run the retriever."
        )
        _render_retry_search_button(seq)
        return
    if not matches:
        st.caption("No matches yet for this sequence.")
        _render_retry_search_button(seq)
        return

    query_seq = seq.get("protein_sequence") or seq.get("normalized_sequence") or ""

    # Render the original Top-5 switcher (EMB/SEQ colored tiles) + the full
    # UniProt card of the currently-selected match below it. The switcher
    # owns its selection per-Sequence; we plumb the index in and a callback
    # back instead of touching ``st.session_state.selected_candidate_idx``.
    sequence_id = seq["id"]

    def _select_match(index: int, _sid: str = sequence_id) -> None:
        session_objects.set_sequence_selected_match(_sid, index)

    revealed = _revealed_sections((matches[chosen_idx] or {}).get("protein") or {})
    protein_card.render(
        matches,
        revealed,
        query_sequence=query_seq or None,
        selected_index=chosen_idx,
        on_select_index=_select_match,
        key_suffix=sequence_id,
    )


def _render_retry_search_button(seq: dict) -> None:
    """Re-run the retriever for a Sequence whose first search produced nothing.

    Goes through the same ``_stage_submission`` path the chat-input field
    uses (so there is only one submission code path to maintain). The
    sequence is flipped back to ``queued`` first so the backend's
    ``_retriever_input_from_request`` finds it as a pending sequence and
    feeds its body to the pipeline — same as the original submission.
    """
    label = seq.get("label") or ""
    if not label:
        return
    if not st.button(
        "🔄 Search again",
        key=f"retry_search_{seq['id']}",
        help="Re-run the retriever for this sequence.",
        type="primary",
    ):
        return
    session_objects.set_sequence_status(seq["id"], "queued")
    # Local import — chat imports from object_inspector indirectly via
    # the components package, so a top-level import here would cycle.
    from components.chat import _stage_submission

    if _stage_submission(f"@{label}", []):
        st.rerun()


# ---------------------------------------------------------------------------
# Protein inspector
# ---------------------------------------------------------------------------


def _render_protein(obj: dict) -> None:
    accession = obj.get("accession") or obj.get("label")
    display_name = obj.get("display_name") or accession
    card = obj.get("card") or {}

    with st.container(border=True):
        st.markdown(f"### {display_name}")
        st.caption(f"UniProt `{accession}` · gene {obj.get('gene') or '—'} · {obj.get('organism') or '—'}")
        linked = obj.get("linked_sequence_ids") or []
        if linked:
            st.markdown(":blue-badge[Linked sequences: " + ", ".join(linked) + "]")

    if not card:
        st.info(
            "This Protein was opened directly. The full UniProt card is "
            "still loading or unavailable."
        )
        _render_protein_quick_facts(obj)
        return

    revealed = _revealed_sections(card)
    query_sequence = _alignment_query_sequence(obj)
    _render_protein_sections(card, revealed, query_sequence)


def _render_protein_sections(
    card: dict,
    revealed: set[str],
    query_sequence: str | None,
) -> None:
    """Render the per-section UniProt card without the candidate switcher.

    The spec requires that the Protein Inspector does not show the
    top-5 buttons — those live only in the Sequence Inspector.
    """
    match_score = float(card.get("match_score") or 1.0) if isinstance(card.get("match_score"), (int, float)) else 1.0
    if match_score <= 0:
        confidence_badge = ":gray-badge[match-confidence unavailable]"
    elif match_score >= 90:
        confidence_badge = f":green-badge[{match_score:.1f}%]"
    elif match_score >= 50:
        confidence_badge = f":orange-badge[{match_score:.1f}%]"
    else:
        confidence_badge = f":red-badge[{match_score:.1f}%]"
    if match_score < 100 and (isinstance(card.get("match_score"), float) or "match_score" in card):
        st.markdown(f"**Match confidence:** {confidence_badge}")

    for key in protein_card._ALL_SECTIONS:
        title = protein_card._SECTION_LABELS[key]
        is_revealed = key in revealed
        container = protein_card._section(title, is_revealed, protein_card._LOCKED_HINTS[key])
        if not is_revealed:
            continue
        with container:
            if key == "alignment":
                protein_card._render_alignment(card, query_sequence)
            else:
                protein_card._RENDERERS[key](card)


def _render_protein_quick_facts(obj: dict) -> None:
    rows = [
        ("Accession", obj.get("accession") or "—"),
        ("UniProt ID", obj.get("uniprot_id") or "—"),
        ("Gene", obj.get("gene") or "—"),
        ("Organism", obj.get("organism") or "—"),
    ]
    df = pd.DataFrame(rows, columns=["Field", "Value"])
    st.dataframe(df, hide_index=True, width="stretch")


def _alignment_query_sequence(protein_obj: dict) -> str | None:
    origin_id = protein_obj.get("last_origin_sequence_id")
    if not origin_id:
        linked = protein_obj.get("linked_sequence_ids") or []
        if linked:
            origin_id = linked[-1]
    if not origin_id:
        return None
    origin = session_objects.get_object(origin_id)
    if not origin:
        return None
    return origin.get("protein_sequence") or origin.get("normalized_sequence") or None


def _revealed_sections(protein: dict) -> set[str]:
    if not protein:
        return set()
    sections = {"header", "keyfacts", "structure"}
    if protein.get("function_text"):
        sections.add("function")
    if protein.get("tissue_specificity") or protein.get("subcellular_locations"):
        sections.add("expression")
    if protein.get("subunit_text") or protein.get("interactions"):
        sections.add("interactions")
    if protein.get("domains"):
        sections.add("domains")
    if protein.get("ptm_texts") or protein.get("functional_features") or protein.get("isoforms"):
        sections.add("regulation")
    if protein.get("variants"):
        sections.add("variants")
    if (
        protein.get("keywords")
        or protein.get("go_terms")
        or protein.get("go_terms_by_category")
        or protein.get("pathways")
    ):
        sections.add("pathways")
    if protein.get("disease"):
        sections.add("disease")
    if protein.get("pubmed_ids") or protein.get("xrefs"):
        sections.add("references")
    if protein.get("sequence"):
        sections.add("alignment")
    return sections


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _status_badge(status: str) -> str:
    mapping = {
        "draft": ":gray-badge[draft]",
        "queued": ":gray-badge[queued]",
        "classifying": ":blue-badge[classifying]",
        "searching": ":blue-badge[searching...]",
        "ready": ":green-badge[ready]",
        "not_searched": ":gray-badge[not searched]",
        "search_failed": ":red-badge[search failed]",
        "error": ":red-badge[error]",
    }
    return mapping.get(status, f":gray-badge[{status}]")


def _wrap_sequence(seq: str, width: int = 60) -> str:
    return "\n".join(seq[i : i + width] for i in range(0, len(seq), width))

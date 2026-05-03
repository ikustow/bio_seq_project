from __future__ import annotations

import streamlit as st

from backend.app_contracts import CandidateView, ProteinView


def render_protein_card(candidates: list[CandidateView]) -> None:
    st.subheader("Protein card")
    if not candidates:
        st.info("No protein candidate selected yet.")
        return

    labels = [_candidate_label(candidate) for candidate in candidates]
    selected = st.radio(
        "Candidates",
        options=list(range(len(candidates))),
        format_func=lambda index: labels[index],
        horizontal=False,
        key="selected_candidate_index",
    )
    protein = candidates[selected].protein
    _render_overview(protein, candidates[selected])
    _render_sections(protein)


def _candidate_label(candidate: CandidateView) -> str:
    protein = candidate.protein
    score = candidate.similarity_score if candidate.similarity_score is not None else candidate.match_score
    return f"{candidate.rank + 1}. {protein.accession} {protein.gene or protein.name} ({score:.3f})"


def _render_overview(protein: ProteinView, candidate: CandidateView) -> None:
    st.markdown(f"### {protein.accession}")
    st.markdown(f"**{protein.name}**")
    cols = st.columns(4)
    cols[0].metric("Gene", protein.gene or "-")
    cols[1].metric("Length", protein.length or 0)
    cols[2].metric("Score", f"{candidate.match_score:.3f}")
    cols[3].metric("Reviewed", "yes" if protein.reviewed else "no")
    st.caption(protein.organism_scientific or "Organism unavailable")


def _render_sections(protein: ProteinView) -> None:
    tabs = st.tabs(["Function", "Features", "References"])
    with tabs[0]:
        st.write(protein.function_text or "No function annotation loaded for this protein.")
        if protein.disease:
            st.write("Disease annotations:", ", ".join(protein.disease.names) or protein.disease.count)
    with tabs[1]:
        if protein.domains:
            st.table([domain.model_dump() for domain in protein.domains])
        else:
            st.write("No domain annotations loaded.")
        if protein.keywords:
            st.write("Keywords:", ", ".join(protein.keywords))
        if protein.go_terms:
            st.write("GO terms:", ", ".join(protein.go_terms[:12]))
    with tabs[2]:
        if protein.pubmed_ids:
            st.write("PubMed:", ", ".join(protein.pubmed_ids[:12]))
        if protein.xrefs:
            st.json(protein.xrefs)
        if protein.alphafold_accession:
            st.write("AlphaFold:", protein.alphafold_accession)

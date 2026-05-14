"""Right-side protein card: progressively-revealed sections over a `ProteinView`."""

from __future__ import annotations

import html
import re
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from components import alignment_viewer
from components.domain_diagram import build_figure
from mock.protein_loader import Candidate, ProteinView

_ALL_SECTIONS: tuple[str, ...] = (
    "header",
    "alignment",
    "keyfacts",
    "function",
    "expression",
    "interactions",
    "domains",
    "regulation",
    "variants",
    "structure",
    "pathways",
    "disease",
    "references",
)

_SECTION_LABELS: dict[str, str] = {
    "header": "Identification",
    "keyfacts": "Key facts",
    "function": "Function",
    "expression": "Expression & location",
    "interactions": "Interactions",
    "domains": "Domain architecture",
    "regulation": "Regulation & isoforms",
    "variants": "Known variants",
    "structure": "3D structure (AlphaFold)",
    "pathways": "Pathways & GO terms",
    "disease": "Disease association",
    "references": "References & external links",
    "alignment": "Alignment",
}

_CITATION_RE = re.compile(r"\[?PubMed:(\d+)\]?")


def _linkify_citations(text: str) -> str:
    return _CITATION_RE.sub(
        lambda m: f"[PubMed:{m.group(1)}](https://pubmed.ncbi.nlm.nih.gov/{m.group(1)})",
        text,
    )


def _section(title: str, revealed: bool, locked_hint: str) -> "st.delta_generator.DeltaGenerator":
    container = st.container(border=True)
    with container:
        if revealed:
            st.markdown(f"#### {title}")
        else:
            st.markdown(
                f"<div class='card-locked'><b>{title}</b><br>"
                f"<span class='card-locked-hint'>{locked_hint}</span></div>",
                unsafe_allow_html=True,
            )
    return container


def _render_header(p: ProteinView) -> None:
    score_stars = "★" * int(round(p["annotation_score"])) + "☆" * (5 - int(round(p["annotation_score"])))
    reviewed_badge = ":green-badge[✓ Reviewed]" if p["reviewed"] else ":orange-badge[Unreviewed]"
    st.markdown(f"### {p['name']}")
    if p["alt_names"]:
        st.caption(" · ".join(p["alt_names"][:3]))

    if p.get("gene_synonyms"):
        st.caption("Gene synonyms: " + ", ".join(p["gene_synonyms"][:6]))

    meta_cols = st.columns(4)
    meta_cols[0].markdown(
        f"**UniProt**  \n[{p['accession']}](https://www.uniprot.org/uniprotkb/{p['accession']})"
    )
    meta_cols[1].markdown(f"**Gene**  \n`{p['gene']}`")
    meta_cols[2].markdown(
        f"**Organism**  \n{p['organism_scientific']} ({p['organism_common']})"
    )
    meta_cols[3].markdown(f"**Annotation**  \n{score_stars}  \n{reviewed_badge}")


def _render_keyfacts(p: ProteinView) -> None:
    rows = [
        ("Length", f"{p['length']:,} aa"),
        ("Molecular weight", f"{p['mol_weight']:,} Da"),
        ("Existence", p["existence"]),
        ("Subcellular location", ", ".join(p["subcellular_locations"]) or "—"),
        ("Alt. names", "; ".join(p["alt_names"]) or "—"),
        ("Protein family", p.get("protein_family") or "-"),
        ("Taxon ID", str(p["taxon_id"])),
    ]
    df = pd.DataFrame(rows, columns=["Field", "Value"])
    st.dataframe(df, hide_index=True, width="stretch")


def _render_function(p: ProteinView) -> None:
    st.markdown(_linkify_citations(p["function_text"]))


def _render_expression(p: ProteinView) -> None:
    tissue_specificity = p.get("tissue_specificity") or ""
    if tissue_specificity:
        st.markdown("**Tissue specificity**")
        st.markdown(_linkify_citations(tissue_specificity))
    if p["subcellular_locations"]:
        st.markdown("**Observed locations**")
        st.markdown(" ".join(f":gray-badge[{loc}]" for loc in p["subcellular_locations"][:12]))
    if not tissue_specificity and not p["subcellular_locations"]:
        st.info("No expression or localization notes available.")


def _render_interactions(p: ProteinView) -> None:
    subunit_text = p.get("subunit_text") or ""
    interactions = p.get("interactions") or []
    if subunit_text:
        st.markdown(_linkify_citations(subunit_text))
    if interactions:
        rows = []
        for item in interactions[:12]:
            partner = item.get("gene") or item.get("accession") or "Interaction partner"
            rows.append({
                "Partner": partner,
                "UniProt": item.get("accession") or "-",
                "IntAct": item.get("int_act_id") or "-",
                "Experiments": item.get("experiments") or 0,
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if not subunit_text and not interactions:
        st.info("No interaction notes available.")


def _render_domains(p: ProteinView) -> None:
    if not p["domains"]:
        st.info("No annotated domains to display.")
        return
    st.plotly_chart(
        build_figure(p["length"], p["domains"]),
        width="stretch",
        config={"displayModeBar": False},
    )
    st.caption(f"Architecture over {p['length']:,} residues · hover for details.")


def _render_regulation(p: ProteinView) -> None:
    ptm_texts = p.get("ptm_texts") or []
    features = p.get("functional_features") or []
    isoforms = p.get("isoforms") or []

    if ptm_texts:
        st.markdown("**Post-translational regulation**")
        for text in ptm_texts[:5]:
            st.markdown(f"- {_linkify_citations(text)}")

    if features:
        st.markdown("**Functional regions and sites**")
        rows = [
            {
                "Type": item.get("type") or "",
                "Region": f"{item.get('start')}-{item.get('end')}",
                "Description": item.get("description") or item.get("name") or "",
            }
            for item in features[:14]
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if isoforms:
        st.markdown("**Isoforms**")
        rows = [
            {
                "Name": item.get("name") or "-",
                "IDs": ", ".join(item.get("ids") or []),
                "Status": item.get("status") or "-",
            }
            for item in isoforms[:8]
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if not ptm_texts and not features and not isoforms:
        st.info("No regulation, feature-site, or isoform notes available.")


def _render_variants(p: ProteinView) -> None:
    variants = p.get("variants") or []
    if not variants:
        st.info("No curated natural variants available.")
        return

    ordered = sorted(variants, key=lambda item: not item.get("disease_related"))
    rows = []
    for item in ordered[:10]:
        rows.append({
            "Variant": item.get("label") or "Variant",
            "Position": item.get("position") or "",
            "dbSNP": item.get("dbsnp_id") or "-",
            "Note": item.get("description") or "-",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _pdb_cache_path(accession: str) -> Path:
    return Path(__file__).parent.parent / "assets" / f"AF-{accession}-F1-model_v4.pdb"


def _fetch_pdb(accession: str) -> str | None:
    cache = _pdb_cache_path(accession)
    if cache.exists():
        try:
            return cache.read_text(encoding="utf-8")
        except OSError:
            pass
    url = f"https://alphafold.ebi.ac.uk/files/AF-{accession}-F1-model_v4.pdb"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(resp.text, encoding="utf-8")
        return resp.text
    except Exception:
        return None


def _render_structure(p: ProteinView) -> None:
    try:
        import py3Dmol  # type: ignore
    except Exception:
        st.info(
            "3D viewer dependency (py3Dmol) missing. "
            f"See the model on [AlphaFold DB](https://alphafold.ebi.ac.uk/entry/{p['alphafold_accession']})."
        )
        return

    pdb = _fetch_pdb(p["alphafold_accession"])
    if not pdb:
        st.info(
            "3D structure unavailable in this environment. "
            f"Open on [AlphaFold DB](https://alphafold.ebi.ac.uk/entry/{p['alphafold_accession']})."
        )
        return

    view = py3Dmol.view(width=560, height=380)
    view.addModel(pdb, "pdb")
    view.setStyle({"cartoon": {"color": "spectrum"}})
    view.zoomTo()
    st.components.v1.html(view._make_html(), height=400, scrolling=False)
    st.caption(
        f"AlphaFold predicted structure · "
        f"[AF-{p['alphafold_accession']}-F1](https://alphafold.ebi.ac.uk/entry/{p['alphafold_accession']})"
    )


def _render_keywords(p: ProteinView) -> None:
    if p["keywords"]:
        st.markdown("**Keywords**")
        st.markdown(" ".join(f":blue-badge[{k}]" for k in p["keywords"][:14]))


def _render_pathways(p: ProteinView) -> None:
    pathways = p.get("pathways") or []
    go_terms_by_category = p.get("go_terms_by_category") or {}
    _render_keywords(p)

    if pathways:
        st.markdown("**Pathways**")
        rows = [
            {
                "Database": item.get("database") or "",
                "ID": item.get("id") or "",
                "Pathway": item.get("name") or "-",
            }
            for item in pathways[:10]
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if go_terms_by_category:
        st.markdown("**GO terms**")
        for category, terms in go_terms_by_category.items():
            if terms:
                st.markdown(f"**{category}**")
                st.markdown(" ".join(f":gray-badge[{term}]" for term in terms[:8]))
    elif p["go_terms"]:
        st.markdown("**GO terms**")
        st.markdown(" ".join(f":gray-badge[{g}]" for g in p["go_terms"][:8]))

    if not pathways and not go_terms_by_category and not p["keywords"] and not p["go_terms"]:
        st.info("No pathway or GO annotations available.")


def _render_disease(p: ProteinView) -> None:
    d = p["disease"]
    if not d:
        st.info("No disease association on record.")
        return
    title = f"**{d['name']}**"
    if d["acronym"]:
        title += f" ({d['acronym']})"
    if d["mim_id"]:
        title += f" · [MIM:{d['mim_id']}](https://omim.org/entry/{d['mim_id']})"
    st.markdown(title)
    st.markdown(_linkify_citations(d["description"]))
    if d["variants"]:
        st.markdown("**Associated variants:**")
        for v in d["variants"]:
            st.markdown(f"- `{v}`")


def _render_references(p: ProteinView) -> None:
    if p["pubmed_ids"]:
        st.markdown("**PubMed references**")
        pm_links = [
            f"[{pid}](https://pubmed.ncbi.nlm.nih.gov/{pid})" for pid in p["pubmed_ids"][:8]
        ]
        st.markdown(" · ".join(pm_links))

    if p["xrefs"]:
        st.markdown("**Cross-references**")
        rows = list(p["xrefs"].items())
        df = pd.DataFrame(rows, columns=["Database", "ID"])
        st.dataframe(df, hide_index=True, width="stretch")


def _render_alignment(p: ProteinView, query_sequence: str | None) -> None:
    if not query_sequence:
        st.info("Alignment will appear after a protein sequence search.")
        return
    if not p.get("sequence"):
        st.info("The selected UniProt candidate does not include a sequence for alignment.")
        return
    candidate_label = p["accession"]
    if p.get("gene"):
        candidate_label = f"{candidate_label} ({p['gene']})"
    alignment_viewer.render_alignment(
        query_sequence,
        p["sequence"],
        query_name="Query sequence",
        candidate_name=candidate_label,
    )


_RENDERERS = {
    "header": _render_header,
    "keyfacts": _render_keyfacts,
    "function": _render_function,
    "expression": _render_expression,
    "interactions": _render_interactions,
    "domains": _render_domains,
    "regulation": _render_regulation,
    "variants": _render_variants,
    "structure": _render_structure,
    "pathways": _render_pathways,
    "disease": _render_disease,
    "references": _render_references,
}

_LOCKED_HINTS = {
    "header": "Submit a sequence to identify the protein.",
    "keyfacts": "Submit a sequence to see its core record.",
    "function": "Submit a sequence to see the biological function.",
    "expression": "Submit a sequence to see tissue and cell-location notes.",
    "interactions": "Submit a sequence to see known binding partners.",
    "domains": "Ask for a simpler explanation to unlock the domain map.",
    "regulation": "Submit a sequence to see PTMs, sites, and isoforms.",
    "variants": "Submit a sequence to see known natural variants.",
    "structure": "Submit a sequence to load the 3D model.",
    "pathways": "Submit a sequence to see pathways and GO annotations.",
    "disease": "Ask about diseases to reveal disease associations.",
    "references": "Request the disease details to unlock references.",
    "alignment": "Submit a sequence to compare it with the selected match.",
}


def _match_tone(score: float) -> str:
    if score >= 90:
        return "green"
    if score >= 70:
        return "yellow"
    if score >= 50:
        return "orange"
    return "red"


_SCORE_TOOLTIPS: dict[str, str] = {
    "EMB": (
        "Embedding similarity — how close this candidate's protein-language-model "
        "vector is to your query in the retrieval index. Higher means the model "
        "considered them more semantically similar before re-ranking."
    ),
    "SEQ": (
        "Sequence-alignment match — pairwise BLOSUM62 alignment of your query "
        "against this candidate, weighted by mutual coverage. Higher means more "
        "of the two sequences line up with identical residues."
    ),
}


def _score_tile(label: str, score: float | None) -> str:
    if score is None or score <= 0:
        tone = "gray"
        value = "--"
    else:
        tone = _match_tone(score)
        value = f"{int(round(score))}%"
    tooltip = _SCORE_TOOLTIPS.get(label, label)
    return (
        f"<span class='candidate-score candidate-score-{tone}' "
        f"title=\"{html.escape(tooltip)}\">"
        f"<span class='candidate-score-label'>{html.escape(label)}</span>"
        f"<span class='candidate-score-value'>{html.escape(value)}</span>"
        "</span>"
    )


def _badge_tone(score: float) -> str:
    if score >= 90:
        return "green"
    if score >= 50:
        return "orange"
    return "red"


def _select_candidate(index: int) -> None:
    st.session_state.selected_candidate_idx = index


def _alignment_score_for_candidate(candidate: Candidate, query_sequence: str | None) -> float | None:
    if not query_sequence:
        return None
    protein = candidate["protein"]
    candidate_sequence = protein.get("sequence")
    if not candidate_sequence:
        return None
    return alignment_viewer.alignment_match_percent(query_sequence, candidate_sequence)


def _render_switcher(candidates: list[Candidate], query_sequence: str | None) -> int:
    """Render the candidate switcher and return the chosen index.

    Uses `selected_candidate_idx` in session_state as both the initial value
    and the persisted selection across reruns.
    """
    chosen = int(st.session_state.get("selected_candidate_idx", 0) or 0)
    if chosen < 0 or chosen >= len(candidates):
        chosen = 0
    st.session_state.selected_candidate_idx = chosen

    with st.container(border=True, key="candidate_switcher"):
        st.markdown("#### Top 5 matches")
        st.caption(
            "Ranked & re-ranked by the retrieval pipeline. "
            "Pick a candidate to view its full record."
        )
        # Only the tile background colours live inline — all layout
        # rules (sizes, grid structure, font sizing) live in style.css
        # so there's a single source of truth.
        st.markdown(
            """
            <style>
              .candidate-score-green { background: #bbf7d0; }
              .candidate-score-yellow { background: #fef08a; }
              .candidate-score-orange { background: #fed7aa; }
              .candidate-score-red { background: #fecaca; }
              .candidate-score-gray { background: #e5e7eb; color: #4b5563; }
            </style>
            """,
            unsafe_allow_html=True,
        )
        columns = st.columns(len(candidates))
        for index, candidate in enumerate(candidates):
            protein = candidate["protein"]
            accession = protein.get("accession") or ""
            alignment_score = _alignment_score_for_candidate(candidate, query_sequence)
            with columns[index]:
                with st.container(key=f"candidate_cell_{index}"):
                    st.button(
                        accession,
                        key=f"candidate_button_{index}_{accession}",
                        help=protein.get("name") or accession,
                        use_container_width=True,
                        type="primary" if index == chosen else "secondary",
                        on_click=_select_candidate,
                        args=(index,),
                    )
                    active_metrics_class = " candidate-metrics-active" if index == chosen else ""
                    st.markdown(
                        f"<div class='candidate-metrics{active_metrics_class}'>"
                        "<div class='candidate-scores'>"
                        f"{_score_tile('EMB', candidate['match_score'])}"
                        f"{_score_tile('SEQ', alignment_score)}"
                        "</div>"
                        "</div>",
                        unsafe_allow_html=True,
                    )
        st.markdown("<div style='height: 6px;'></div>", unsafe_allow_html=True)
    return chosen


def render(
    candidates: list[Candidate] | None,
    revealed: set[str],
    query_sequence: str | None = None,
) -> None:
    if candidates is None:
        with st.container(border=True):
            st.markdown("### Protein card")
            st.markdown(
                "<div class='card-locked'>The protein card will appear here "
                "once you submit a sequence on the left.</div>",
                unsafe_allow_html=True,
            )
        return

    if not candidates:
        with st.container(border=True):
            st.markdown("### Protein card")
            st.warning(
                "The retrieval pipeline returned no candidates for this query. "
                "Try rephrasing or pasting a different sequence."
            )
        return

    chosen = _render_switcher(candidates, query_sequence)
    selected = candidates[chosen]
    protein = selected["protein"]
    score = selected["match_score"]

    if score <= 0:
        confidence_badge = ":gray-badge[match-confidence unavailable]"
    else:
        tone = _badge_tone(score)
        confidence_badge = f":{tone}-badge[{score:.1f}%]"

    st.markdown(
        f"**Match confidence:** {confidence_badge}  "
        f":gray-badge[rank #{chosen + 1} of {len(candidates)}]"
    )

    for key in _ALL_SECTIONS:
        title = _SECTION_LABELS[key]
        is_revealed = key in revealed
        container = _section(title, is_revealed, _LOCKED_HINTS[key])
        if is_revealed:
            with container:
                if key == "alignment":
                    _render_alignment(protein, query_sequence)
                else:
                    _RENDERERS[key](protein)

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


@st.cache_data(show_spinner=False)
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


@st.cache_data(show_spinner=False)
def _build_py3dmol_html(pdb: str) -> str | None:
    try:
        import py3Dmol  # type: ignore
    except Exception:
        return None
    view = py3Dmol.view(width=560, height=380)
    view.addModel(pdb, "pdb")
    view.setStyle({"cartoon": {"color": "spectrum"}})
    view.zoomTo()
    return view._make_html()


def _render_structure(p: ProteinView) -> None:
    try:
        import py3Dmol  # type: ignore  # noqa: F401
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

    viewer_html = _build_py3dmol_html(pdb)
    if not viewer_html:
        st.info(
            "3D viewer could not be initialised. "
            f"Open on [AlphaFold DB](https://alphafold.ebi.ac.uk/entry/{p['alphafold_accession']})."
        )
        return
    st.components.v1.html(viewer_html, height=400, scrolling=False)
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


# EMB/SEQ score gradient — non-linear on purpose. Real candidates
# cluster at the high end (80–100% is the working range), so most of
# the perceptual contrast lives there: 80→90→95→100 each step bumps
# saturation up and lightness down so 100% reads as visibly "best"
# rather than a slightly-greener 90%. Sub-50 scores are already a bad
# outcome and don't need fine resolution, so the red→yellow half is
# spaced more loosely. Each stop is
# ``(score_fraction, (hue, saturation%, lightness%))`` and bracketing
# stops are linearly interpolated in HSL space.
_SCORE_GRADIENT_STOPS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.00, (0,   72, 80)),
    (0.25, (20,  78, 76)),
    (0.50, (45,  88, 74)),
    (0.70, (62,  82, 72)),
    (0.80, (85,  62, 70)),
    (0.90, (118, 55, 66)),
    (0.95, (135, 62, 58)),
    (1.00, (142, 70, 48)),
)


def _score_background(score: float) -> str:
    """Return an ``hsl(...)`` string for the tile background.

    ``score`` is expected on a 0..100 scale. Out-of-range values are
    clamped so an unexpected backend value can't produce a NaN colour.
    """
    t = max(0.0, min(1.0, score / 100.0))
    stops = _SCORE_GRADIENT_STOPS
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            h = c0[0] + (c1[0] - c0[0]) * f
            s = c0[1] + (c1[1] - c0[1]) * f
            l = c0[2] + (c1[2] - c0[2]) * f
            return f"hsl({h:.0f}, {s:.0f}%, {l:.0f}%)"
    h, s, l = stops[-1][1]
    return f"hsl({h}, {s}%, {l}%)"


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
        # Missing-score tiles keep the neutral grey class so users
        # don't mistake them for a real low-confidence reading.
        extra_class = " candidate-score-missing"
        style_attr = ""
        value = "--"
    else:
        extra_class = ""
        style_attr = f" style=\"background-color: {_score_background(score)}\""
        value = f"{int(round(score))}%"
    tooltip = _SCORE_TOOLTIPS.get(label, label)
    return (
        f"<span class='candidate-score{extra_class}'"
        f"{style_attr} title=\"{html.escape(tooltip)}\">"
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


def _select_candidate_via_callback(index: int, callback) -> None:
    callback(index)


def _normalize_match_score(value: object) -> float | None:
    """Render-time guard: backend `matches` arrive on a 0..1 scale, while the
    legacy `st.session_state.candidates` path already pre-multiplies to %.
    Treat values in (0, 1] as 0..1 and rescale; leave everything else alone.
    """
    try:
        score = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if score <= 0:
        return 0.0
    if score <= 1:
        return score * 100.0
    return score


def _alignment_score_for_candidate(candidate: Candidate, query_sequence: str | None) -> float | None:
    if not query_sequence:
        return None
    protein = candidate["protein"]
    candidate_sequence = protein.get("sequence")
    if not candidate_sequence:
        return None
    return alignment_viewer.alignment_match_percent(query_sequence, candidate_sequence)


def _render_switcher(
    candidates: list[Candidate],
    query_sequence: str | None,
    *,
    selected_index: int | None = None,
    on_select_index=None,
    key_suffix: str = "",
) -> int:
    """Render the candidate switcher and return the chosen index.

    Two modes:

    - Legacy global mode (``selected_index`` / ``on_select_index`` are
      ``None``): the switcher uses ``st.session_state.selected_candidate_idx``
      as both the initial value and the persisted selection across reruns.
      This keeps the demo-chip flow and any callers that still rely on the
      pre-registry behaviour working.

    - Registry mode: the caller (Sequence Inspector) owns the selection state
      inside ``sequence.selected_match_index``. It passes the current index
      in and a callback that updates the Sequence in ``session_objects``.
      Button keys are suffixed with ``key_suffix`` so multiple switchers
      can coexist on the same page (one per Sequence).
    """
    use_registry = on_select_index is not None
    if use_registry:
        chosen = int(selected_index or 0)
    else:
        chosen = int(st.session_state.get("selected_candidate_idx", 0) or 0)
    if chosen < 0 or chosen >= len(candidates):
        chosen = 0
    if not use_registry:
        st.session_state.selected_candidate_idx = chosen

    container_key = f"candidate_switcher_{key_suffix}" if key_suffix else "candidate_switcher"
    with st.container(border=True, key=container_key):
        st.markdown("#### Top 5 matches")
        st.caption(
            "Ranked & re-ranked by the retrieval pipeline. "
            "Pick a candidate to view its full record."
        )
        # Tile colours are computed inline from the score
        # (`_score_background`); layout + the neutral "missing score"
        # style live in style.css.
        columns = st.columns(len(candidates))
        for index, candidate in enumerate(candidates):
            protein = candidate["protein"]
            accession = protein.get("accession") or ""
            alignment_score = _alignment_score_for_candidate(candidate, query_sequence)
            match_score = _normalize_match_score(candidate.get("match_score"))
            cell_key = f"candidate_cell_{key_suffix}_{index}" if key_suffix else f"candidate_cell_{index}"
            btn_key = (
                f"candidate_button_{key_suffix}_{index}_{accession}"
                if key_suffix
                else f"candidate_button_{index}_{accession}"
            )
            click_handler = (
                (lambda i=index: on_select_index(i)) if use_registry else None
            )
            with columns[index]:
                with st.container(key=cell_key):
                    if use_registry:
                        st.button(
                            accession,
                            key=btn_key,
                            help=protein.get("name") or accession,
                            use_container_width=True,
                            type="primary" if index == chosen else "secondary",
                            on_click=click_handler,
                        )
                    else:
                        st.button(
                            accession,
                            key=btn_key,
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
                        f"{_score_tile('EMB', match_score)}"
                        f"{_score_tile('SEQ', alignment_score)}"
                        "</div>"
                        "</div>",
                        unsafe_allow_html=True,
                    )
    return chosen


def render(
    candidates: list[Candidate] | None,
    revealed: set[str],
    query_sequence: str | None = None,
    *,
    selected_index: int | None = None,
    on_select_index=None,
    key_suffix: str = "",
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

    chosen = _render_switcher(
        candidates,
        query_sequence,
        selected_index=selected_index,
        on_select_index=on_select_index,
        key_suffix=key_suffix,
    )
    selected = candidates[chosen]
    protein = selected["protein"]
    score = _normalize_match_score(selected.get("match_score")) or 0.0

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

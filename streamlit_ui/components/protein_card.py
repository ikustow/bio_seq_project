"""Right-side protein card: progressively-revealed sections over a `ProteinView`."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from components.domain_diagram import build_figure
from mock.protein_loader import ProteinView

_ALL_SECTIONS: tuple[str, ...] = (
    "header",
    "keyfacts",
    "function",
    "domains",
    "structure",
    "keywords",
    "disease",
    "references",
)

_SECTION_LABELS: dict[str, str] = {
    "header": "Identification",
    "keyfacts": "Key facts",
    "function": "Function",
    "domains": "Domain architecture",
    "structure": "3D structure (AlphaFold)",
    "keywords": "Keywords & GO terms",
    "disease": "Disease association",
    "references": "References & external links",
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
        ("Taxon ID", str(p["taxon_id"])),
    ]
    df = pd.DataFrame(rows, columns=["Field", "Value"])
    st.dataframe(df, hide_index=True, use_container_width=True)


def _render_function(p: ProteinView) -> None:
    st.markdown(_linkify_citations(p["function_text"]))


def _render_domains(p: ProteinView) -> None:
    if not p["domains"]:
        st.info("No annotated domains to display.")
        return
    st.plotly_chart(
        build_figure(p["length"], p["domains"]),
        use_container_width=True,
        config={"displayModeBar": False},
    )
    st.caption(f"Architecture over {p['length']:,} residues · hover for details.")


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
    if p["go_terms"]:
        st.markdown("**GO terms**")
        st.markdown(" ".join(f":gray-badge[{g}]" for g in p["go_terms"][:8]))


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
        st.dataframe(df, hide_index=True, use_container_width=True)


_RENDERERS = {
    "header": _render_header,
    "keyfacts": _render_keyfacts,
    "function": _render_function,
    "domains": _render_domains,
    "structure": _render_structure,
    "keywords": _render_keywords,
    "disease": _render_disease,
    "references": _render_references,
}

_LOCKED_HINTS = {
    "header": "Submit a sequence to identify the protein.",
    "keyfacts": "Submit a sequence to see its core record.",
    "function": "Submit a sequence to see the biological function.",
    "domains": "Ask for a simpler explanation to unlock the domain map.",
    "structure": "Submit a sequence to load the 3D model.",
    "keywords": "Submit a sequence to see UniProt keywords.",
    "disease": "Ask about diseases to reveal disease associations.",
    "references": "Request the disease details to unlock references.",
}


def render(protein: ProteinView | None, revealed: set[str]) -> None:
    if protein is None:
        with st.container(border=True):
            st.markdown("### Protein card")
            st.markdown(
                "<div class='card-locked'>The protein card will appear here "
                "once you submit a sequence on the left.</div>",
                unsafe_allow_html=True,
            )
        return

    for key in _ALL_SECTIONS:
        title = _SECTION_LABELS[key]
        is_revealed = key in revealed
        container = _section(title, is_revealed, _LOCKED_HINTS[key])
        if is_revealed:
            with container:
                _RENDERERS[key](protein)

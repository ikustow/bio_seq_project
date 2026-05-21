"""Markdown export helpers for the selected protein card."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from mock.protein_loader import Candidate, ProteinView

_CITATION_RE = re.compile(r"\[?PubMed:(\d+)\]?")
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def markdown_filename(protein: ProteinView) -> str:
    """Return a stable, browser-friendly default filename."""
    accession = _clean_inline(protein.get("accession") or "protein")
    gene = _clean_inline(protein.get("gene") or "")
    parts = [accession]
    if gene:
        parts.append(gene)
    base = "_".join(parts)
    safe = _FILENAME_SAFE_RE.sub("_", base).strip("._-") or "protein"
    return f"{safe}_protein_card.md"


def build_protein_markdown(
    *,
    selected: Candidate,
    candidates: list[Candidate],
    selected_index: int,
    revealed: set[str],
    query_sequence: str | None = None,
) -> str:
    """Build a readable Markdown report matching the visible protein card."""
    protein = selected["protein"]
    accession = protein.get("accession") or "Unknown accession"
    name = protein.get("name") or "Unknown protein"
    gene = protein.get("gene") or "-"
    rank = selected_index + 1
    total = len(candidates)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"# {name}",
        "",
        f"**UniProt:** [{accession}](https://www.uniprot.org/uniprotkb/{accession})  ",
        f"**Gene:** `{gene}`  ",
        f"**Match confidence:** {_percent(selected.get('match_score'))}  ",
        f"**Rank:** #{rank} of {total}  ",
        f"**Generated:** {generated_at}",
        "",
    ]

    if candidates:
        lines.extend(_top_matches(candidates, selected_index))

    section_builders = {
        "header": _identification,
        # Prefer the BLAST-DNA per-hit translation when present; otherwise
        # use the session-global protein query.
        "alignment": lambda p: _alignment(
            p, selected.get("query_translation") or query_sequence
        ),
        "keyfacts": _key_facts,
        "function": _function,
        "expression": _expression,
        "interactions": _interactions,
        "domains": _domains,
        "regulation": _regulation,
        "variants": _variants,
        "structure": _structure,
        "pathways": _pathways,
        "disease": _disease,
        "references": _references,
    }

    for key, builder in section_builders.items():
        if key not in revealed:
            continue
        section = builder(protein)
        if section:
            lines.extend(section)

    return _normalize_blank_lines(lines)


def _top_matches(candidates: list[Candidate], selected_index: int) -> list[str]:
    rows = []
    for index, candidate in enumerate(candidates[:5]):
        protein = candidate["protein"]
        accession = protein.get("accession") or "-"
        label = f"[{accession}](https://www.uniprot.org/uniprotkb/{accession})"
        if index == selected_index:
            label = f"**{label}**"
        rows.append([
            str(index + 1),
            label,
            _clean_inline(protein.get("gene") or "-"),
            _clean_inline(protein.get("name") or "-"),
            _percent(candidate.get("match_score")),
        ])
    return [
        "## Top Matches",
        "",
        *_markdown_table(
            ["Rank", "UniProt", "Gene", "Protein", "Match"],
            rows,
        ),
        "",
    ]


def _identification(p: ProteinView) -> list[str]:
    rows = [
        ["Protein name", _clean_inline(p.get("name") or "-")],
        ["Alternative names", _join(p.get("alt_names"))],
        ["Gene", f"`{_clean_inline(p.get('gene') or '-')}`"],
        ["Gene synonyms", _join(p.get("gene_synonyms"))],
        ["Organism", _organism(p)],
        ["Taxon ID", str(p.get("taxon_id") or "-")],
        ["Reviewed", "Yes" if p.get("reviewed") else "No"],
        ["Annotation score", _annotation_score(p.get("annotation_score"))],
    ]
    return _section("Identification", _markdown_table(["Field", "Value"], rows))


def _key_facts(p: ProteinView) -> list[str]:
    rows = [
        ["Length", f"{_number(p.get('length'))} aa"],
        ["Molecular weight", f"{_number(p.get('mol_weight'))} Da"],
        ["Existence", _clean_inline(p.get("existence") or "-")],
        ["Subcellular location", _join(p.get("subcellular_locations"))],
        ["Protein family", _clean_inline(p.get("protein_family") or "-")],
    ]
    return _section("Key Facts", _markdown_table(["Field", "Value"], rows))


def _function(p: ProteinView) -> list[str]:
    text = _linkify_citations(p.get("function_text") or "")
    if not text:
        return []
    return _section("Function", [text])


def _expression(p: ProteinView) -> list[str]:
    lines: list[str] = []
    tissue = _linkify_citations(p.get("tissue_specificity") or "")
    if tissue:
        lines.extend(["**Tissue specificity**", "", tissue, ""])
    locations = p.get("subcellular_locations") or []
    if locations:
        lines.extend(["**Observed locations**", "", _bullets(locations)])
    return _section("Expression & Location", lines) if lines else []


def _interactions(p: ProteinView) -> list[str]:
    lines: list[str] = []
    subunit = _linkify_citations(p.get("subunit_text") or "")
    if subunit:
        lines.extend([subunit, ""])

    interactions = p.get("interactions") or []
    if interactions:
        rows = [
            [
                _clean_inline(item.get("gene") or item.get("accession") or "Interaction partner"),
                _clean_inline(item.get("accession") or "-"),
                _clean_inline(item.get("int_act_id") or "-"),
                str(item.get("experiments") or 0),
            ]
            for item in interactions[:12]
        ]
        lines.extend(_markdown_table(["Partner", "UniProt", "IntAct", "Experiments"], rows))
    return _section("Interactions", lines) if lines else []


def _domains(p: ProteinView) -> list[str]:
    domains = p.get("domains") or []
    if not domains:
        return []
    rows = [
        [
            _clean_inline(item.get("type") or "Domain"),
            _clean_inline(item.get("name") or item.get("description") or "Domain"),
            _range(item.get("start"), item.get("end")),
        ]
        for item in domains
    ]
    return _section("Domain Architecture", _markdown_table(["Type", "Name", "Residues"], rows))


def _regulation(p: ProteinView) -> list[str]:
    lines: list[str] = []
    ptm_texts = p.get("ptm_texts") or []
    features = p.get("functional_features") or []
    isoforms = p.get("isoforms") or []

    if ptm_texts:
        lines.extend(["**Post-translational regulation**", "", _bullets(_linkify_citations(t) for t in ptm_texts[:5]), ""])

    if features:
        rows = [
            [
                _clean_inline(item.get("type") or "-"),
                _range(item.get("start"), item.get("end")),
                _clean_inline(item.get("description") or item.get("name") or "-"),
            ]
            for item in features[:14]
        ]
        lines.extend(["**Functional regions and sites**", "", *_markdown_table(["Type", "Region", "Description"], rows), ""])

    if isoforms:
        rows = [
            [
                _clean_inline(item.get("name") or "-"),
                _join(item.get("ids")),
                _clean_inline(item.get("status") or "-"),
            ]
            for item in isoforms[:8]
        ]
        lines.extend(["**Isoforms**", "", *_markdown_table(["Name", "IDs", "Status"], rows)])

    return _section("Regulation & Isoforms", lines) if lines else []


def _variants(p: ProteinView) -> list[str]:
    variants = p.get("variants") or []
    if not variants:
        return []
    ordered = sorted(variants, key=lambda item: not item.get("disease_related"))
    rows = [
        [
            _clean_inline(item.get("label") or "Variant"),
            str(item.get("position") or "-"),
            _clean_inline(item.get("dbsnp_id") or "-"),
            _clean_inline(item.get("description") or "-"),
        ]
        for item in ordered[:10]
    ]
    return _section("Known Variants", _markdown_table(["Variant", "Position", "dbSNP", "Note"], rows))


def _structure(p: ProteinView) -> list[str]:
    accession = p.get("alphafold_accession") or p.get("accession") or ""
    if not accession:
        return []
    lines = [
        f"- AlphaFold DB: [AF-{accession}-F1](https://alphafold.ebi.ac.uk/entry/{accession})",
        f"- UniProt: [{p.get('accession') or accession}](https://www.uniprot.org/uniprotkb/{p.get('accession') or accession})",
    ]
    return _section("3D Structure", lines)


def _pathways(p: ProteinView) -> list[str]:
    lines: list[str] = []
    keywords = p.get("keywords") or []
    pathways = p.get("pathways") or []
    go_terms_by_category = p.get("go_terms_by_category") or {}
    go_terms = p.get("go_terms") or []

    if keywords:
        lines.extend(["**Keywords**", "", _bullets(keywords[:14]), ""])

    if pathways:
        rows = [
            [
                _clean_inline(item.get("database") or "-"),
                _clean_inline(item.get("id") or "-"),
                _clean_inline(item.get("name") or "-"),
            ]
            for item in pathways[:10]
        ]
        lines.extend(["**Pathways**", "", *_markdown_table(["Database", "ID", "Pathway"], rows), ""])

    if go_terms_by_category:
        lines.extend(["**GO terms**", ""])
        for category, terms in go_terms_by_category.items():
            if terms:
                lines.extend([f"**{_clean_inline(category)}**", "", _bullets(terms[:8]), ""])
    elif go_terms:
        lines.extend(["**GO terms**", "", _bullets(go_terms[:8])])

    return _section("Pathways & GO Terms", lines) if lines else []


def _disease(p: ProteinView) -> list[str]:
    disease = p.get("disease")
    if not disease:
        return []

    title = _clean_inline(disease.get("name") or "Disease association")
    acronym = disease.get("acronym")
    if acronym:
        title = f"{title} ({_clean_inline(acronym)})"

    lines = [f"**{title}**"]
    mim_id = disease.get("mim_id")
    if mim_id:
        lines.append(f"MIM: [{mim_id}](https://omim.org/entry/{mim_id})")
    description = _linkify_citations(disease.get("description") or "")
    if description:
        lines.extend(["", description])
    variants = disease.get("variants") or []
    if variants:
        lines.extend(["", "**Associated variants**", "", _bullets(f"`{_clean_inline(v)}`" for v in variants)])
    return _section("Disease Association", lines)


def _references(p: ProteinView) -> list[str]:
    lines: list[str] = []
    pubmed_ids = p.get("pubmed_ids") or []
    if pubmed_ids:
        refs = [f"[{pid}](https://pubmed.ncbi.nlm.nih.gov/{pid})" for pid in pubmed_ids[:8]]
        lines.extend(["**PubMed references**", "", ", ".join(refs), ""])

    xrefs = p.get("xrefs") or {}
    if xrefs:
        rows = [[_clean_inline(k), _clean_inline(v)] for k, v in xrefs.items()]
        lines.extend(["**Cross-references**", "", *_markdown_table(["Database", "ID"], rows)])

    return _section("References & External Links", lines) if lines else []


def _alignment(p: ProteinView, query_sequence: str | None) -> list[str]:
    sequence = p.get("sequence") or ""
    if not query_sequence or not sequence:
        return []
    lines = [
        f"- Query sequence length: {_number(len(query_sequence))} aa",
        f"- Candidate sequence length: {_number(len(sequence))} aa",
        "",
        "<details>",
        "<summary>Candidate sequence</summary>",
        "",
        "```text",
        _wrap_sequence(sequence),
        "```",
        "</details>",
    ]
    return _section("Alignment", lines)


def _section(title: str, body: list[str]) -> list[str]:
    if not body:
        return []
    return [f"## {title}", "", *body, ""]


def _markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    clean_headers = [_escape_table_cell(h) for h in headers]
    lines = [
        "| " + " | ".join(clean_headers) + " |",
        "| " + " | ".join("---" for _ in clean_headers) + " |",
    ]
    for row in rows:
        padded = [*row, *([""] * (len(headers) - len(row)))]
        lines.append("| " + " | ".join(_escape_table_cell(cell) for cell in padded[: len(headers)]) + " |")
    return lines


def _bullets(items: Any) -> str:
    values = [_clean_inline(str(item)) for item in items if str(item).strip()]
    return "\n".join(f"- {item}" for item in values) or "-"


def _join(items: Any) -> str:
    if not items:
        return "-"
    return ", ".join(_clean_inline(str(item)) for item in items if str(item).strip()) or "-"


def _organism(p: ProteinView) -> str:
    scientific = _clean_inline(p.get("organism_scientific") or "")
    common = _clean_inline(p.get("organism_common") or "")
    if scientific and common:
        return f"{scientific} ({common})"
    return scientific or common or "-"


def _percent(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number <= 0:
        return "-"
    return f"{number:.1f}%"


def _annotation_score(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{number:.1f} / 5"


def _number(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "-"


def _range(start: Any, end: Any) -> str:
    if start in (None, "") or end in (None, ""):
        return "-"
    return f"{start}-{end}"


def _linkify_citations(text: str) -> str:
    return _CITATION_RE.sub(
        lambda match: f"[PubMed:{match.group(1)}](https://pubmed.ncbi.nlm.nih.gov/{match.group(1)})",
        text,
    )


def _clean_inline(value: str) -> str:
    return " ".join(str(value).replace("\n", " ").split())


def _escape_table_cell(value: Any) -> str:
    return _clean_inline(str(value)).replace("|", "\\|")


def _wrap_sequence(sequence: str, width: int = 80) -> str:
    compact = "".join(str(sequence).split())
    return "\n".join(compact[i : i + width] for i in range(0, len(compact), width))


def _normalize_blank_lines(lines: list[str]) -> str:
    out: list[str] = []
    blank = False
    for line in lines:
        cur = line.rstrip()
        if cur:
            out.append(cur)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip() + "\n"

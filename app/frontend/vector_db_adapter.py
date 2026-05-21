"""Adapter from the Streamlit UI to the graph/vector DB retriever interface."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _PROJECT_ROOT / "app"
for path in (_APP_ROOT, _PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend.agents_core.retriever_agent.pipeline_interface import run_pipeline_interface  # noqa: E402
from mock.protein_loader import Candidate, DiseaseInfo, DomainFeature, ProteinView  # noqa: E402


def run_prompt(prompt: str) -> tuple[str, list[Candidate], set[str], dict[str, Any]]:
    """Run the retriever interface and return UI-ready chat/card data."""
    result = run_pipeline_interface(prompt)
    candidates = [_candidate_from_backend(item) for item in result.get("final_results") or []]
    return _assistant_message(result), candidates, _revealed_sections(candidates), result


def _assistant_message(state: dict[str, Any]) -> str:
    if state.get("error"):
        return f"**Vector DB pipeline error:** {state['error']}"

    final_results = state.get("final_results") or []
    if not final_results:
        return "Vector DB pipeline completed without final matches."

    lines = [
        "**Vector DB pipeline result:**",
        "",
        f"- Detected type: `{state.get('sequence_type') or 'unknown'}`",
        f"- Classification confidence: `{state.get('is_confident')}`",
        f"- Final matches: `{len(final_results)}`",
        "",
        "**Top matches:**",
    ]
    for index, record in enumerate(final_results[:5], 1):
        protein = record.get("protein") or {}
        accession = protein.get("accession") or "unknown"
        name = protein.get("name") or protein.get("protein_name") or "Unknown protein"
        gene = protein.get("gene") or protein.get("gene_primary") or ""
        suffix = f" ({gene})" if gene else ""
        lines.append(f"{index}. `{accession}` — {name}{suffix}")
    return "\n".join(lines)


def _candidate_from_backend(record: dict[str, Any]) -> Candidate:
    protein = _protein_from_backend(record.get("protein") or {})
    return Candidate(
        protein=protein,
        match_score=_score_as_percent(record.get("match_score")),
        query_translation=(record.get("query_translation") or None),
    )


def _protein_from_backend(raw: dict[str, Any]) -> ProteinView:
    accession = str(_first(raw, "accession", default=""))
    disease = _disease_from_backend(raw.get("disease"))
    return ProteinView(
        accession=accession,
        name=str(_first(raw, "name", "protein_name", default=accession or "Unknown protein")),
        alt_names=_as_str_list(raw.get("alt_names")),
        gene_synonyms=_as_str_list(raw.get("gene_synonyms")),
        gene=str(_first(raw, "gene", "gene_primary", "gene_name", default="") or ""),
        organism_scientific=str(_first(raw, "organism_scientific", "organism_name", default="") or ""),
        organism_common=str(_first(raw, "organism_common", "organism_common_name", default="") or ""),
        taxon_id=_as_int(raw.get("taxon_id")),
        annotation_score=_as_float(raw.get("annotation_score")),
        reviewed=bool(raw.get("reviewed")),
        existence=str(_first(raw, "existence", "protein_existence", default="") or ""),
        length=_as_int(_first(raw, "length", "sequence_length", default=0)),
        mol_weight=_as_int(raw.get("mol_weight")),
        subcellular_locations=_as_str_list(raw.get("subcellular_locations")),
        function_text=str(raw.get("function_text") or ""),
        tissue_specificity=str(raw.get("tissue_specificity") or ""),
        subunit_text=str(raw.get("subunit_text") or ""),
        interactions=raw.get("interactions") if isinstance(raw.get("interactions"), list) else [],
        ptm_texts=_as_str_list(raw.get("ptm_texts")),
        isoforms=raw.get("isoforms") if isinstance(raw.get("isoforms"), list) else [],
        functional_features=raw.get("functional_features") if isinstance(raw.get("functional_features"), list) else [],
        variants=raw.get("variants") if isinstance(raw.get("variants"), list) else [],
        pathways=raw.get("pathways") if isinstance(raw.get("pathways"), list) else [],
        protein_family=str(raw.get("protein_family") or ""),
        disease=disease,
        domains=_domains_from_backend(raw.get("domains")),
        keywords=_as_str_list(raw.get("keywords")),
        go_terms=_as_str_list(raw.get("go_terms")),
        go_terms_by_category=raw.get("go_terms_by_category") if isinstance(raw.get("go_terms_by_category"), dict) else {},
        pubmed_ids=_as_str_list(raw.get("pubmed_ids")),
        xrefs={str(k): str(v) for k, v in (raw.get("xrefs") or {}).items() if v not in (None, "")},
        alphafold_accession=str(raw.get("alphafold_accession") or accession),
        sequence=str(_first(raw, "sequence", "protein_sequence", default="") or ""),
    )


def _disease_from_backend(value: Any) -> DiseaseInfo | None:
    if not isinstance(value, dict):
        return None
    names = _as_str_list(value.get("names"))
    xrefs = value.get("xrefs") if isinstance(value.get("xrefs"), dict) else {}
    name = value.get("name") or (names[0] if names else "")
    count = _as_int(value.get("count"), default=len(names))
    if not name and count == 0:
        return None
    return DiseaseInfo(
        name=str(name or "Disease association"),
        acronym=str(value.get("acronym") or ""),
        mim_id=str(value.get("mim_id") or xrefs.get("MIM") or ""),
        description=str(value.get("description") or ""),
        variants=_as_str_list(value.get("variants")),
    )


def _domains_from_backend(value: Any) -> list[DomainFeature]:
    if not isinstance(value, list):
        return []
    domains: list[DomainFeature] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        start = _as_int(item.get("start"), default=0)
        end = _as_int(item.get("end"), default=0)
        if start <= 0 or end <= 0:
            continue
        domains.append(
            DomainFeature(
                type=str(item.get("type") or "Domain"),
                name=str(item.get("name") or item.get("description") or "Domain"),
                start=start,
                end=end,
            )
        )
    return domains


def _revealed_sections(candidates: list[Candidate]) -> set[str]:
    if not candidates:
        return set()

    protein = candidates[0]["protein"]
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
    return sections


def _first(source: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None:
            return value
    return default


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _score_as_percent(value: Any) -> float:
    score = _as_float(value)
    if score <= 0:
        return 0.0
    if score <= 1:
        return score * 100
    return score

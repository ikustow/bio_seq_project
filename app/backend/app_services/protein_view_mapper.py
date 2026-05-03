from __future__ import annotations

import json
from typing import Any

from backend.app_contracts import CandidateView, DiseaseInfo, DomainFeature, EvidenceItem, ProteinView


def _first_present(record: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "reviewed", "swiss-prot"}
    return bool(value)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_value(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return value


def _as_list(value: Any) -> list[str]:
    parsed = _json_value(value)
    if parsed is None:
        return []
    if isinstance(parsed, list):
        output: list[str] = []
        for item in parsed:
            if isinstance(item, dict):
                label = item.get("name") or item.get("id") or item.get("term") or item.get("value")
                if label:
                    output.append(str(label))
            elif item not in (None, ""):
                output.append(str(item))
        return output
    if isinstance(parsed, dict):
        return [str(value) for value in parsed.values() if value not in (None, "")]
    return [part.strip() for part in str(parsed).replace(";", ",").split(",") if part.strip()]


def _as_dict(value: Any) -> dict[str, str]:
    parsed = _json_value(value)
    if isinstance(parsed, dict):
        return {str(key): str(value) for key, value in parsed.items() if value not in (None, "")}
    return {}


def _as_domains(value: Any) -> list[DomainFeature]:
    parsed = _json_value(value)
    if not isinstance(parsed, list):
        return []
    domains: list[DomainFeature] = []
    for item in parsed:
        if isinstance(item, dict):
            domains.append(
                DomainFeature(
                    name=str(item.get("name") or item.get("type") or item.get("description") or ""),
                    start=_optional_int(item.get("start") or item.get("begin")),
                    end=_optional_int(item.get("end")),
                    description=str(item.get("description") or item.get("note") or ""),
                )
            )
        elif item:
            domains.append(DomainFeature(name=str(item)))
    return domains


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _disease_info(record: dict[str, Any]) -> DiseaseInfo | None:
    names = _as_list(_first_present(record, "disease_names", "disease_names_json", default=[]))
    count = _as_int(record.get("disease_count"), default=len(names))
    if count == 0 and not names:
        return None
    return DiseaseInfo(names=names, count=count)


def protein_record_to_view(record: dict[str, Any]) -> ProteinView:
    accession = str(_first_present(record, "accession", "protein_accession", default=""))
    name = str(_first_present(record, "protein_name", "name", "entry_name", default=accession or "Unknown protein"))
    return ProteinView(
        accession=accession,
        name=name,
        alt_names=_as_list(_first_present(record, "alt_names_json", "alt_names", default=[])),
        gene=str(_first_present(record, "gene_primary", "gene", "gene_name", default="") or ""),
        organism_scientific=str(_first_present(record, "organism_name", "organism_scientific", default="") or ""),
        organism_common=str(_first_present(record, "organism_common", "organism_common_name", default="") or ""),
        taxon_id=_as_int(record.get("taxon_id")),
        annotation_score=_as_float(record.get("annotation_score")),
        reviewed=_as_bool(record.get("reviewed")),
        existence=str(_first_present(record, "protein_existence", "existence", default="") or ""),
        length=_as_int(_first_present(record, "sequence_length", "length", default=0)),
        mol_weight=_as_int(record.get("mol_weight")),
        subcellular_locations=_as_list(_first_present(record, "subcellular_locations_json", "subcellular_locations", default=[])),
        function_text=str(record.get("function_text") or ""),
        disease=_disease_info(record),
        domains=_as_domains(record.get("domains_json") or record.get("domains")),
        keywords=_as_list(record.get("keywords_json") or record.get("keywords")),
        go_terms=_as_list(record.get("go_terms_json") or record.get("go_terms")),
        pubmed_ids=_as_list(record.get("pubmed_ids_json") or record.get("pubmed_ids")),
        xrefs=_as_dict(record.get("xrefs_json") or record.get("xrefs")),
        alphafold_accession=str(record.get("alphafold_accession") or ""),
        sequence=str(record.get("protein_sequence") or record.get("sequence") or ""),
    )


def neighbor_record_to_candidate(record: dict[str, Any], rank: int) -> CandidateView:
    protein = protein_record_to_view(record)
    similarity = record.get("similarity_score") or record.get("cosine_sim")
    similarity_score = _as_float(similarity) if similarity is not None else None
    match_score = similarity_score if similarity_score is not None else (1.0 if rank == 0 else 0.0)
    evidence = [EvidenceItem(label="Accession", value=protein.accession)]
    if similarity_score is not None:
        evidence.append(EvidenceItem(label="Similarity", value=f"{similarity_score:.4f}"))
    return CandidateView(
        protein=protein,
        match_score=match_score,
        rank=rank,
        similarity_score=similarity_score,
        evidence=evidence,
    )

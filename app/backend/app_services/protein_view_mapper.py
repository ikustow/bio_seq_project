from __future__ import annotations

import json
from typing import Any

from backend.app_contracts import (
    CandidateView,
    DiseaseInfo,
    DomainFeature,
    EvidenceItem,
    FeatureInfo,
    InteractionInfo,
    IsoformInfo,
    PathwayRef,
    ProteinView,
    VariantInfo,
)


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
        # The frontend domain diagram does arithmetic on start/end and colors
        # by type. Anything without a valid (start, end) pair is dropped —
        # bare strings, dicts with missing/zero coordinates, etc. — so the
        # card never blows up on None or zero-length features.
        if not isinstance(item, dict):
            continue
        start = _optional_int(item.get("start") or item.get("begin"))
        end = _optional_int(item.get("end"))
        if start is None or end is None or start <= 0 or end <= 0:
            continue
        domains.append(
            DomainFeature(
                type=str(item.get("type") or item.get("category") or "Domain"),
                name=str(item.get("name") or item.get("description") or "Domain"),
                start=start,
                end=end,
                description=str(item.get("description") or item.get("note") or ""),
            )
        )
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
    # The Neo4j graph currently carries only ``disease_names`` / ``disease_count``;
    # ``acronym`` / ``mim_id`` / ``variants`` aren't propagated yet. We surface
    # the first name as the UI-friendly ``name`` so the protein card can
    # render even on graph-only data, and leave the richer fields empty —
    # the embeddings backend (UniProt JSON) populates them from upstream.
    return DiseaseInfo(
        name=names[0] if names else "",
        names=names,
        count=count,
    )


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
        # alphafold_accession defaults to the canonical accession when not
        # explicitly stored — every UniProt entry has a corresponding AlphaFold
        # model under the same id, and the protein-card 3D viewer keys off it.
        alphafold_accession=str(record.get("alphafold_accession") or accession),
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


_UNIPROT_DOMAIN_FEATURE_TYPES = {"Signal", "Domain", "Transmembrane"}
_UNIPROT_XREF_WHITELIST = ("RefSeq", "Ensembl", "KEGG", "CCDS", "HGNC", "MIM", "AlphaFoldDB")


def uniprot_record_to_candidate(record: dict[str, Any], rank: int) -> CandidateView:
    protein = uniprot_record_to_view(record)
    rerank_score = _optional_float(record.get("_rerank_score"))
    similarity_score = _optional_float(record.get("_bioseq_embedding_score") or record.get("score"))
    match_score = rerank_score if rerank_score is not None else similarity_score
    if match_score is None:
        match_score = 1.0 if rank == 0 else max(0.0, 1.0 - (rank * 0.1))

    evidence = [
        EvidenceItem(label="Accession", value=protein.accession, source="bioseq_retriever"),
        EvidenceItem(label="Rank", value=str(rank + 1), source="bioseq_retriever"),
    ]
    if rerank_score is not None:
        evidence.append(EvidenceItem(label="Rerank score", value=f"{rerank_score:.4f}", source="bioseq_retriever"))
    if record.get("_rerank_explanation"):
        evidence.append(
            EvidenceItem(
                label="Rerank explanation",
                value=str(record["_rerank_explanation"]),
                source="bioseq_retriever",
            )
        )

    return CandidateView(
        protein=protein,
        match_score=match_score,
        rank=rank,
        similarity_score=similarity_score,
        context_score=rerank_score,
        evidence=evidence,
    )


def uniprot_record_to_view(record: dict[str, Any]) -> ProteinView:
    comments: list[dict[str, Any]] = record.get("comments", []) or []
    features: list[dict[str, Any]] = record.get("features", []) or []
    cross_refs: list[dict[str, Any]] = record.get("uniProtKBCrossReferences", []) or []
    references: list[dict[str, Any]] = record.get("references", []) or []
    organism = record.get("organism", {}) or {}
    protein_desc = record.get("proteinDescription", {}) or {}
    recommended_name = protein_desc.get("recommendedName", {}).get("fullName", {}).get("value", "")
    accession = str(record.get("primaryAccession") or record.get("accession") or "")
    alt_names = [
        item.get("fullName", {}).get("value", "")
        for item in protein_desc.get("alternativeNames", []) or []
        if item.get("fullName", {}).get("value")
    ]
    genes = record.get("genes", []) or []
    gene = genes[0].get("geneName", {}).get("value", "") if genes else ""
    gene_synonyms = [
        item.get("value", "")
        for gene_record in genes
        for item in gene_record.get("synonyms", []) or []
        if item.get("value")
    ]
    sequence = record.get("sequence", {}) or {}
    disease = _uniprot_disease_info(comments)
    if disease:
        disease.variants = _uniprot_disease_variants(features, disease.acronym)

    return ProteinView(
        accession=accession,
        name=recommended_name or str(record.get("uniProtkbId") or accession or "Unknown protein"),
        alt_names=alt_names,
        gene_synonyms=gene_synonyms,
        gene=gene,
        organism_scientific=str(organism.get("scientificName") or ""),
        organism_common=str(organism.get("commonName") or ""),
        taxon_id=_as_int(organism.get("taxonId")),
        annotation_score=_as_float(record.get("annotationScore")),
        reviewed=str(record.get("entryType") or "").startswith("UniProtKB reviewed"),
        existence=str(record.get("proteinExistence") or ""),
        length=_as_int(sequence.get("length")),
        mol_weight=_as_int(sequence.get("molWeight")),
        subcellular_locations=_uniprot_subcellular_locations(comments),
        function_text=_uniprot_comment_text(comments, "FUNCTION"),
        tissue_specificity=_uniprot_comment_text(comments, "TISSUE SPECIFICITY"),
        subunit_text=_uniprot_comment_text(comments, "SUBUNIT"),
        interactions=_uniprot_interactions(comments),
        ptm_texts=_uniprot_comment_texts(comments, "PTM"),
        isoforms=_uniprot_isoforms(comments),
        functional_features=_uniprot_functional_features(features),
        variants=_uniprot_variants(features),
        pathways=_uniprot_pathways(cross_refs),
        protein_family=_uniprot_comment_text(comments, "SIMILARITY"),
        disease=disease,
        domains=_uniprot_domains(features),
        keywords=[item.get("name", "") for item in record.get("keywords", []) or [] if item.get("name")],
        go_terms=_uniprot_go_terms(cross_refs),
        go_terms_by_category=_uniprot_go_terms_by_category(cross_refs),
        pubmed_ids=_uniprot_pubmed_ids(references),
        xrefs=_uniprot_xrefs(cross_refs),
        alphafold_accession=_uniprot_alphafold_accession(cross_refs, accession),
        sequence=str(sequence.get("value") or ""),
    )


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _uniprot_first_comment(comments: list[dict[str, Any]], comment_type: str) -> dict[str, Any] | None:
    for comment in comments:
        if comment.get("commentType") == comment_type:
            return comment
    return None


def _uniprot_comment_texts(comments: list[dict[str, Any]], comment_type: str) -> list[str]:
    output: list[str] = []
    for comment in comments:
        if comment.get("commentType") != comment_type:
            continue
        for text in comment.get("texts", []) or []:
            value = text.get("value")
            if value:
                output.append(str(value))
    return output


def _uniprot_comment_text(comments: list[dict[str, Any]], comment_type: str) -> str:
    return " ".join(_uniprot_comment_texts(comments, comment_type))


def _uniprot_subcellular_locations(comments: list[dict[str, Any]]) -> list[str]:
    comment = _uniprot_first_comment(comments, "SUBCELLULAR LOCATION")
    if not comment:
        return []
    output: list[str] = []
    for location in comment.get("subcellularLocations", []) or []:
        value = location.get("location", {}).get("value")
        if value and value not in output:
            output.append(str(value))
    return output


def _uniprot_disease_info(comments: list[dict[str, Any]]) -> DiseaseInfo | None:
    comment = _uniprot_first_comment(comments, "DISEASE")
    if not comment:
        return None
    disease = comment.get("disease", {}) or {}
    xref = disease.get("diseaseCrossReference") or {}
    mim_id = xref.get("id", "") if xref.get("database") == "MIM" else ""
    return DiseaseInfo(
        name=str(disease.get("diseaseId") or ""),
        acronym=str(disease.get("acronym") or ""),
        mim_id=str(mim_id or ""),
        description=str(disease.get("description") or ""),
    )


def _uniprot_disease_variants(features: list[dict[str, Any]], disease_acronym: str) -> list[str]:
    variants: list[str] = []
    for feature in features:
        if feature.get("type") != "Natural variant":
            continue
        description = str(feature.get("description") or "")
        if disease_acronym and disease_acronym not in description:
            continue
        alt = feature.get("alternativeSequence") or {}
        original = alt.get("originalSequence", "")
        alternatives = alt.get("alternativeSequences") or []
        position = feature.get("location", {}).get("start", {}).get("value")
        alternative = alternatives[0] if alternatives else ""
        label = f"{original}{position}{alternative}" if original and position and alternative else description.split(";")[0]
        dbsnp = _uniprot_feature_xref(feature, "dbSNP")
        variants.append(f"{label} ({dbsnp})" if dbsnp else label)
    return [variant for variant in variants if variant]


def _uniprot_domains(features: list[dict[str, Any]]) -> list[DomainFeature]:
    output: list[DomainFeature] = []
    for feature in features:
        feature_type = feature.get("type")
        if feature_type not in _UNIPROT_DOMAIN_FEATURE_TYPES:
            continue
        start, end = _uniprot_feature_position(feature)
        if start is None or end is None:
            continue
        description = str(feature.get("description") or "")
        if feature_type == "Signal":
            name = "Signal peptide"
        elif feature_type == "Transmembrane":
            name = "Transmembrane"
        else:
            name = description or "Domain"
        output.append(DomainFeature(type=str(feature_type), name=name, start=start, end=end, description=description))
    return output


def _uniprot_feature_position(feature: dict[str, Any]) -> tuple[int | None, int | None]:
    location = feature.get("location", {}) or {}
    start = _optional_int(location.get("start", {}).get("value"))
    end = _optional_int(location.get("end", {}).get("value"))
    return start, end


def _uniprot_functional_features(features: list[dict[str, Any]]) -> list[FeatureInfo]:
    useful_types = {"Topological domain", "Region", "Site", "Modified residue", "Glycosylation"}
    output: list[FeatureInfo] = []
    for feature in features:
        feature_type = str(feature.get("type") or "")
        if feature_type not in useful_types:
            continue
        start, end = _uniprot_feature_position(feature)
        if start is None or end is None:
            continue
        description = str(feature.get("description") or "")
        output.append(
            FeatureInfo(
                type=feature_type,
                name=description or feature_type,
                start=start,
                end=end,
                description=description,
            )
        )
    return output


def _uniprot_variants(features: list[dict[str, Any]]) -> list[VariantInfo]:
    output: list[VariantInfo] = []
    for feature in features:
        if feature.get("type") != "Natural variant":
            continue
        start, _end = _uniprot_feature_position(feature)
        if start is None:
            continue
        alt = feature.get("alternativeSequence") or {}
        original = str(alt.get("originalSequence") or "")
        alternatives = alt.get("alternativeSequences") or []
        alternative = str(alternatives[0]) if alternatives else ""
        description = str(feature.get("description") or "")
        dbsnp_id = _uniprot_feature_xref(feature, "dbSNP")
        label = f"{original}{start}{alternative}" if original and alternative else f"Variant at {start}"
        disease_related = any(token in description.lower() for token in ("disease", "ad;", "alzheimer", "cancer"))
        output.append(
            VariantInfo(
                label=label,
                position=start,
                original=original,
                alternative=alternative,
                description=description,
                dbsnp_id=dbsnp_id,
                disease_related=disease_related,
            )
        )
    return output


def _uniprot_feature_xref(feature: dict[str, Any], database: str) -> str:
    for xref in feature.get("featureCrossReferences") or []:
        if xref.get("database") == database:
            return str(xref.get("id") or "")
    return ""


def _uniprot_interactions(comments: list[dict[str, Any]]) -> list[InteractionInfo]:
    output: list[InteractionInfo] = []
    for comment in comments:
        if comment.get("commentType") != "INTERACTION":
            continue
        for interaction in comment.get("interactions", []) or []:
            partner = interaction.get("interactantTwo") or {}
            accession = str(partner.get("uniProtKBAccession") or "")
            gene = str(partner.get("geneName") or "")
            int_act_id = str(partner.get("intActId") or "")
            if accession or gene or int_act_id:
                output.append(
                    InteractionInfo(
                        accession=accession,
                        gene=gene,
                        int_act_id=int_act_id,
                        experiments=_as_int(interaction.get("numberOfExperiments")),
                    )
                )
    return output


def _uniprot_isoforms(comments: list[dict[str, Any]]) -> list[IsoformInfo]:
    output: list[IsoformInfo] = []
    for comment in comments:
        if comment.get("commentType") != "ALTERNATIVE PRODUCTS":
            continue
        for isoform in comment.get("isoforms", []) or []:
            name = str(isoform.get("name", {}).get("value") or "")
            ids = [str(item) for item in isoform.get("isoformIds", []) or [] if item]
            status = str(isoform.get("isoformSequenceStatus") or "")
            if name or ids:
                output.append(IsoformInfo(name=name, ids=ids, status=status))
    return output


def _uniprot_pathways(cross_refs: list[dict[str, Any]]) -> list[PathwayRef]:
    output: list[PathwayRef] = []
    for xref in cross_refs:
        if xref.get("database") != "Reactome":
            continue
        props = {
            item.get("key"): item.get("value")
            for item in xref.get("properties", []) or []
            if item.get("key")
        }
        output.append(
            PathwayRef(
                database="Reactome",
                id=str(xref.get("id") or ""),
                name=str(props.get("PathwayName") or props.get("Description") or ""),
            )
        )
    return output


def _uniprot_xrefs(cross_refs: list[dict[str, Any]]) -> dict[str, str]:
    output: dict[str, str] = {}
    for xref in cross_refs:
        database = xref.get("database")
        if database not in _UNIPROT_XREF_WHITELIST or database in output:
            continue
        output[str(database)] = str(xref.get("id") or "")
    return output


def _uniprot_alphafold_accession(cross_refs: list[dict[str, Any]], fallback: str) -> str:
    for xref in cross_refs:
        if xref.get("database") == "AlphaFoldDB":
            return str(xref.get("id") or fallback)
    return fallback


def _uniprot_pubmed_ids(references: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for reference in references:
        for xref in reference.get("citation", {}).get("citationCrossReferences", []) or []:
            if xref.get("database") == "PubMed":
                pubmed_id = xref.get("id")
                if pubmed_id and pubmed_id not in ids:
                    ids.append(str(pubmed_id))
    return ids


def _uniprot_go_terms(cross_refs: list[dict[str, Any]], limit: int = 8) -> list[str]:
    output: list[str] = []
    for xref in cross_refs:
        if xref.get("database") != "GO":
            continue
        for prop in xref.get("properties", []) or []:
            if prop.get("key") != "GoTerm":
                continue
            value = str(prop.get("value") or "")
            if value.startswith(("P:", "F:", "C:")):
                value = value[2:]
            if value and value not in output:
                output.append(value)
            break
        if len(output) >= limit:
            break
    return output


def _uniprot_go_terms_by_category(cross_refs: list[dict[str, Any]], limit_per_category: int = 8) -> dict[str, list[str]]:
    categories = {
        "P": "Biological process",
        "F": "Molecular function",
        "C": "Cellular component",
    }
    output: dict[str, list[str]] = {
        "Biological process": [],
        "Molecular function": [],
        "Cellular component": [],
    }
    for xref in cross_refs:
        if xref.get("database") != "GO":
            continue
        value = ""
        for prop in xref.get("properties", []) or []:
            if prop.get("key") == "GoTerm":
                value = str(prop.get("value") or "")
                break
        if len(value) < 3 or value[1] != ":":
            continue
        category = categories.get(value[0])
        term = value[2:]
        if category and term and term not in output[category] and len(output[category]) < limit_per_category:
            output[category].append(term)
    return {category: values for category, values in output.items() if values}

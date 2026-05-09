"""Flatten a UniProt JSON record into a stable `ProteinView` dict for the UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional, TypedDict


class DomainFeature(TypedDict):
    type: str
    name: str
    start: int
    end: int


class DiseaseInfo(TypedDict):
    name: str
    acronym: str
    mim_id: str
    description: str
    variants: list[str]


class InteractionInfo(TypedDict):
    accession: str
    gene: str
    int_act_id: str
    experiments: int


class IsoformInfo(TypedDict):
    name: str
    ids: list[str]
    status: str


class FeatureInfo(TypedDict):
    type: str
    name: str
    start: int
    end: int
    description: str


class VariantInfo(TypedDict):
    label: str
    position: int
    original: str
    alternative: str
    description: str
    dbsnp_id: str
    disease_related: bool


class PathwayRef(TypedDict):
    database: str
    id: str
    name: str


class Candidate(TypedDict):
    protein: "ProteinView"
    match_score: float


class ProteinView(TypedDict):
    accession: str
    name: str
    alt_names: list[str]
    gene_synonyms: list[str]
    gene: str
    organism_scientific: str
    organism_common: str
    taxon_id: int
    annotation_score: float
    reviewed: bool
    existence: str
    length: int
    mol_weight: int
    subcellular_locations: list[str]
    function_text: str
    tissue_specificity: str
    subunit_text: str
    interactions: list[InteractionInfo]
    ptm_texts: list[str]
    isoforms: list[IsoformInfo]
    functional_features: list[FeatureInfo]
    variants: list[VariantInfo]
    pathways: list[PathwayRef]
    protein_family: str
    disease: Optional[DiseaseInfo]
    domains: list[DomainFeature]
    keywords: list[str]
    go_terms: list[str]
    go_terms_by_category: dict[str, list[str]]
    pubmed_ids: list[str]
    xrefs: dict[str, str]
    alphafold_accession: str
    sequence: str


_DOMAIN_FEATURE_TYPES = {"Signal", "Domain", "Transmembrane"}
_XREF_WHITELIST = ("RefSeq", "Ensembl", "KEGG", "CCDS", "HGNC", "MIM", "AlphaFoldDB")


def _first_comment(comments: list[dict], comment_type: str) -> Optional[dict]:
    for c in comments:
        if c.get("commentType") == comment_type:
            return c
    return None


def _comment_texts(comments: list[dict], comment_type: str) -> list[str]:
    out: list[str] = []
    for comment in comments:
        if comment.get("commentType") != comment_type:
            continue
        for text in comment.get("texts", []) or []:
            value = text.get("value")
            if value:
                out.append(value)
    return out


def _comment_text(comments: list[dict], comment_type: str) -> str:
    return " ".join(_comment_texts(comments, comment_type))


def _subcellular_locations(comments: list[dict]) -> list[str]:
    comment = _first_comment(comments, "SUBCELLULAR LOCATION")
    if not comment:
        return []
    out: list[str] = []
    for loc in comment.get("subcellularLocations", []):
        val = loc.get("location", {}).get("value")
        if val and val not in out:
            out.append(val)
    return out


def _function_text(comments: list[dict]) -> str:
    return _comment_text(comments, "FUNCTION")


def _disease_info(comments: list[dict]) -> Optional[DiseaseInfo]:
    comment = _first_comment(comments, "DISEASE")
    if not comment:
        return None
    d = comment.get("disease", {})
    mim_id = ""
    xref = d.get("diseaseCrossReference") or {}
    if xref.get("database") == "MIM":
        mim_id = xref.get("id", "")
    return DiseaseInfo(
        name=d.get("diseaseId", ""),
        acronym=d.get("acronym", ""),
        mim_id=mim_id,
        description=d.get("description", ""),
        variants=[],
    )


def _disease_variants(features: list[dict], disease_acronym: str) -> list[str]:
    variants: list[str] = []
    for f in features:
        if f.get("type") != "Natural variant":
            continue
        desc = f.get("description", "") or ""
        if disease_acronym and disease_acronym not in desc:
            continue
        alt = f.get("alternativeSequence") or {}
        orig = alt.get("originalSequence", "")
        alts = alt.get("alternativeSequences") or []
        pos = f.get("location", {}).get("start", {}).get("value")
        rs = ""
        for x in f.get("featureCrossReferences") or []:
            if x.get("database") == "dbSNP":
                rs = x.get("id", "")
                break
        alt_str = alts[0] if alts else ""
        label = f"{orig}{pos}{alt_str}" if (orig and pos and alt_str) else desc.split(";")[0]
        if rs:
            label = f"{label} ({rs})"
        variants.append(label)
    return variants


def _domains(features: list[dict]) -> list[DomainFeature]:
    out: list[DomainFeature] = []
    for f in features:
        ftype = f.get("type")
        if ftype not in _DOMAIN_FEATURE_TYPES:
            continue
        start = f.get("location", {}).get("start", {}).get("value")
        end = f.get("location", {}).get("end", {}).get("value")
        if start is None or end is None:
            continue
        desc = f.get("description") or ""
        if ftype == "Signal":
            name = "Signal peptide"
        elif ftype == "Transmembrane":
            name = "Transmembrane"
        else:
            name = desc or "Domain"
        out.append(DomainFeature(type=ftype, name=name, start=int(start), end=int(end)))
    return out


def _feature_position(feature: dict) -> tuple[int | None, int | None]:
    location = feature.get("location", {}) or {}
    start = location.get("start", {}).get("value")
    end = location.get("end", {}).get("value")
    if start is None or end is None:
        return None, None
    return int(start), int(end)


def _functional_features(features: list[dict]) -> list[FeatureInfo]:
    useful_types = {
        "Topological domain",
        "Region",
        "Site",
        "Modified residue",
        "Glycosylation",
    }
    out: list[FeatureInfo] = []
    for feature in features:
        ftype = feature.get("type") or ""
        if ftype not in useful_types:
            continue
        start, end = _feature_position(feature)
        if start is None or end is None:
            continue
        desc = feature.get("description") or ""
        name = desc or ftype
        out.append(FeatureInfo(
            type=ftype,
            name=name,
            start=start,
            end=end,
            description=desc,
        ))
    return out


def _variants(features: list[dict]) -> list[VariantInfo]:
    out: list[VariantInfo] = []
    for feature in features:
        if feature.get("type") != "Natural variant":
            continue
        start, _end = _feature_position(feature)
        if start is None:
            continue
        alt = feature.get("alternativeSequence") or {}
        original = alt.get("originalSequence") or ""
        alternatives = alt.get("alternativeSequences") or []
        alternative = alternatives[0] if alternatives else ""
        description = feature.get("description") or ""
        dbsnp_id = ""
        for xref in feature.get("featureCrossReferences") or []:
            if xref.get("database") == "dbSNP":
                dbsnp_id = xref.get("id", "")
                break
        label = f"{original}{start}{alternative}" if original and alternative else f"Variant at {start}"
        disease_related = any(token in description.lower() for token in ("disease", "ad;", "alzheimer", "cancer"))
        out.append(VariantInfo(
            label=label,
            position=start,
            original=original,
            alternative=alternative,
            description=description,
            dbsnp_id=dbsnp_id,
            disease_related=disease_related,
        ))
    return out


def _interactions(comments: list[dict]) -> list[InteractionInfo]:
    out: list[InteractionInfo] = []
    for comment in comments:
        if comment.get("commentType") != "INTERACTION":
            continue
        for interaction in comment.get("interactions", []) or []:
            partner = interaction.get("interactantTwo") or {}
            accession = partner.get("uniProtKBAccession") or ""
            gene = partner.get("geneName") or ""
            int_act_id = partner.get("intActId") or ""
            if not accession and not gene and not int_act_id:
                continue
            out.append(InteractionInfo(
                accession=accession,
                gene=gene,
                int_act_id=int_act_id,
                experiments=int(interaction.get("numberOfExperiments") or 0),
            ))
    return out


def _isoforms(comments: list[dict]) -> list[IsoformInfo]:
    out: list[IsoformInfo] = []
    for comment in comments:
        if comment.get("commentType") != "ALTERNATIVE PRODUCTS":
            continue
        for isoform in comment.get("isoforms", []) or []:
            name = isoform.get("name", {}).get("value") or ""
            ids = [str(item) for item in isoform.get("isoformIds", []) or [] if item]
            status = isoform.get("isoformSequenceStatus") or ""
            if name or ids:
                out.append(IsoformInfo(name=name, ids=ids, status=status))
    return out


def _pathways(cross_refs: list[dict]) -> list[PathwayRef]:
    out: list[PathwayRef] = []
    for xref in cross_refs:
        db = xref.get("database") or ""
        if db != "Reactome":
            continue
        props = {
            item.get("key"): item.get("value")
            for item in xref.get("properties", []) or []
            if item.get("key")
        }
        name = props.get("PathwayName") or props.get("Description") or ""
        out.append(PathwayRef(database=db, id=xref.get("id", ""), name=name))
    return out


def _xrefs(cross_refs: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for x in cross_refs:
        db = x.get("database")
        if db not in _XREF_WHITELIST or db in out:
            continue
        out[db] = x.get("id", "")
    return out


def _alphafold_accession(cross_refs: list[dict], fallback: str) -> str:
    for x in cross_refs:
        if x.get("database") == "AlphaFoldDB":
            return x.get("id", fallback)
    return fallback


def _pubmed_ids(references: list[dict]) -> list[str]:
    ids: list[str] = []
    for ref in references:
        for x in ref.get("citation", {}).get("citationCrossReferences", []) or []:
            if x.get("database") == "PubMed":
                pid = x.get("id")
                if pid and pid not in ids:
                    ids.append(pid)
    return ids


def _go_terms(cross_refs: list[dict], limit: int = 8) -> list[str]:
    out: list[str] = []
    for x in cross_refs:
        if x.get("database") != "GO":
            continue
        for p in x.get("properties", []) or []:
            if p.get("key") == "GoTerm":
                val = p.get("value", "")
                if val.startswith(("P:", "F:", "C:")):
                    val = val[2:]
                if val and val not in out:
                    out.append(val)
                break
        if len(out) >= limit:
            break
    return out


def _go_terms_by_category(cross_refs: list[dict], limit_per_category: int = 8) -> dict[str, list[str]]:
    categories = {
        "P": "Biological process",
        "F": "Molecular function",
        "C": "Cellular component",
    }
    out: dict[str, list[str]] = {
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
                value = prop.get("value", "")
                break
        if len(value) < 3 or value[1] != ":":
            continue
        category = categories.get(value[0])
        term = value[2:]
        if not category or not term:
            continue
        if term not in out[category] and len(out[category]) < limit_per_category:
            out[category].append(term)
    return {key: values for key, values in out.items() if values}


def from_dict(record: dict) -> ProteinView:
    raw = record
    comments: list[dict] = raw.get("comments", []) or []
    features: list[dict] = raw.get("features", []) or []
    cross_refs: list[dict] = raw.get("uniProtKBCrossReferences", []) or []
    references: list[dict] = raw.get("references", []) or []
    organism = raw.get("organism", {}) or {}
    protein_desc = raw.get("proteinDescription", {}) or {}
    rec_name = protein_desc.get("recommendedName", {}).get("fullName", {}).get("value", "")
    alt_names = [
        a.get("fullName", {}).get("value", "")
        for a in protein_desc.get("alternativeNames", []) or []
    ]
    alt_names = [a for a in alt_names if a]
    genes = raw.get("genes", []) or []
    gene = genes[0].get("geneName", {}).get("value", "") if genes else ""
    gene_synonyms = [
        item.get("value", "")
        for gene_record in genes
        for item in gene_record.get("synonyms", []) or []
    ]
    gene_synonyms = [item for item in gene_synonyms if item]
    sequence = raw.get("sequence", {}) or {}
    accession = raw.get("primaryAccession", "")

    disease = _disease_info(comments)
    if disease:
        disease["variants"] = _disease_variants(features, disease["acronym"])

    return ProteinView(
        accession=accession,
        name=rec_name,
        alt_names=alt_names,
        gene_synonyms=gene_synonyms,
        gene=gene,
        organism_scientific=organism.get("scientificName", ""),
        organism_common=organism.get("commonName", ""),
        taxon_id=int(organism.get("taxonId", 0) or 0),
        annotation_score=float(raw.get("annotationScore", 0) or 0),
        reviewed=(raw.get("entryType", "").startswith("UniProtKB reviewed")),
        existence=raw.get("proteinExistence", ""),
        length=int(sequence.get("length", 0) or 0),
        mol_weight=int(sequence.get("molWeight", 0) or 0),
        subcellular_locations=_subcellular_locations(comments),
        function_text=_function_text(comments),
        tissue_specificity=_comment_text(comments, "TISSUE SPECIFICITY"),
        subunit_text=_comment_text(comments, "SUBUNIT"),
        interactions=_interactions(comments),
        ptm_texts=_comment_texts(comments, "PTM"),
        isoforms=_isoforms(comments),
        functional_features=_functional_features(features),
        variants=_variants(features),
        pathways=_pathways(cross_refs),
        protein_family=_comment_text(comments, "SIMILARITY"),
        disease=disease,
        domains=_domains(features),
        keywords=[k.get("name", "") for k in raw.get("keywords", []) or [] if k.get("name")],
        go_terms=_go_terms(cross_refs),
        go_terms_by_category=_go_terms_by_category(cross_refs),
        pubmed_ids=_pubmed_ids(references),
        xrefs=_xrefs(cross_refs),
        alphafold_accession=_alphafold_accession(cross_refs, accession),
        sequence=sequence.get("value", ""),
    )


def load(path: str | Path) -> ProteinView:
    return from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def load_candidates(
    directory: str | Path,
    specs: Iterable[tuple[str, float]],
) -> list[Candidate]:
    """Load multiple `ProteinView`s with their associated mock match scores.

    `specs` is an iterable of `(accession, match_score_percent)` pairs — order
    is preserved so callers can pre-rank the list (best match first).
    """
    base = Path(directory)
    out: list[Candidate] = []
    for accession, score in specs:
        out.append(Candidate(
            protein=load(base / f"{accession}.json"),
            match_score=float(score),
        ))
    return out

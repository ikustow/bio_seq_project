from __future__ import annotations

from pydantic import BaseModel, Field


class DomainFeature(BaseModel):
    """UI-ready protein-feature record.

    ``type`` is required by the protein_card domain diagram for coloring
    (``"Domain"`` / ``"Signal"`` / ``"Transmembrane"``). Defaults to
    ``"Domain"`` so a backend that doesn't classify features still produces
    a valid view.
    """

    type: str = "Domain"
    name: str = ""
    start: int | None = None
    end: int | None = None
    description: str = ""


class DiseaseInfo(BaseModel):
    """UI-ready disease association.

    Carries both the legacy graph fields (``names``, ``count``, ``xrefs``)
    and the UI-friendly fields the protein card needs (``name``,
    ``acronym``, ``mim_id``, ``variants``). Either side can populate what it
    has; missing fields fall back to safe defaults.
    """

    name: str = ""
    acronym: str = ""
    mim_id: str = ""
    description: str = ""
    variants: list[str] = Field(default_factory=list)
    names: list[str] = Field(default_factory=list)
    count: int = 0
    xrefs: dict[str, str] = Field(default_factory=dict)


class EvidenceItem(BaseModel):
    label: str
    value: str
    source: str = "graph"


class ProteinView(BaseModel):
    accession: str
    name: str
    alt_names: list[str] = Field(default_factory=list)
    gene: str = ""
    organism_scientific: str = ""
    organism_common: str = ""
    taxon_id: int = 0
    annotation_score: float = 0
    reviewed: bool = False
    existence: str = ""
    length: int = 0
    mol_weight: int = 0
    subcellular_locations: list[str] = Field(default_factory=list)
    function_text: str = ""
    disease: DiseaseInfo | None = None
    domains: list[DomainFeature] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    go_terms: list[str] = Field(default_factory=list)
    pubmed_ids: list[str] = Field(default_factory=list)
    xrefs: dict[str, str] = Field(default_factory=dict)
    alphafold_accession: str = ""
    sequence: str = ""


class CandidateView(BaseModel):
    protein: ProteinView
    match_score: float
    rank: int
    similarity_score: float | None = None
    context_score: float | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)

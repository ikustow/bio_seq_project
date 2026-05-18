from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for path in (PROJECT_ROOT / "app", PROJECT_ROOT / "app" / "frontend", PROJECT_ROOT):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)


@pytest.fixture(autouse=True)
def clean_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "BIOSEQ_BACKEND",
        "BIOSEQ_CHAT_LLM_PROVIDER",
        "BIOSEQ_INPUT_EXTRACTOR",
        "BIOSEQ_LLM_PROXY_URL",
        "BIOSEQ_LLM_PROXY_TOKEN",
        "BIOSEQ_OPENAI_CHAT_MODEL",
        "BIOSEQ_THINK_MISTRAL_MODEL",
        "BIOSEQ_THINK_OPENAI_MODEL",
        "BIOSEQ_THINK_PROVIDER",
        "BIOSEQ_THINK_TIMEOUT_SECONDS",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "MISTRAL_API_KEY",
        "SUPABASE_DB_URL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def candidate_view():
    from backend.app_contracts.protein_view import CandidateView, DomainFeature, ProteinView

    protein = ProteinView(
        accession="O95185",
        name="Netrin receptor UNC5C",
        gene="UNC5C",
        organism_scientific="Homo sapiens",
        organism_common="human",
        length=931,
        mol_weight=101000,
        function_text="Receptor for netrin.",
        tissue_specificity="Expressed in brain.",
        subcellular_locations=["Cell membrane"],
        domains=[DomainFeature(type="Domain", name="Ig-like", start=62, end=159)],
        keywords=["Receptor"],
        go_terms=["GO:0005515"],
        pubmed_ids=["25419706"],
        alphafold_accession="O95185",
        sequence="MALWMRLLPLLALLALWGPGPGAG",
    )
    return CandidateView(protein=protein, match_score=0.987, rank=0)


@pytest.fixture
def candidate_dict(candidate_view):
    data = candidate_view.model_dump()
    data["match_score"] = 98.7
    return data

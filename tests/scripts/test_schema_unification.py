"""Unit tests for the schema unification adapters.

Verifies that ``chat_pipeline._ensure_protein_shape`` correctly normalizes
both the new (post-unification) backend shape AND the legacy persisted
shape (where DiseaseInfo had ``names/count/xrefs`` instead of
``name/acronym/mim_id/variants``, and DomainFeature lacked ``type``).

Also smoke-checks that the new app_contracts models accept the extended
fields without runtime errors.

Run:
    python tests/scripts/test_schema_unification.py
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "app"))


# ---------------------------------------------------------------------------
# 1. app_contracts: new shape works.
# ---------------------------------------------------------------------------


def test_backend_contracts_accept_extended_fields() -> None:
    from backend.app_contracts.protein_view import (
        CandidateView,
        DiseaseInfo,
        DomainFeature,
        ProteinView,
    )

    domain = DomainFeature(type="Signal", name="Signal peptide", start=1, end=24)
    assert domain.type == "Signal"
    assert domain.start == 1

    disease = DiseaseInfo(
        name="Alzheimer disease",
        acronym="AD",
        mim_id="104300",
        description="...",
        variants=["T835M (rs137875858)"],
    )
    assert disease.name == "Alzheimer disease"
    assert disease.acronym == "AD"

    # Legacy fields still accepted for backward compat.
    legacy_disease = DiseaseInfo(names=["X"], count=1)
    assert legacy_disease.names == ["X"]
    assert legacy_disease.name == ""

    view = ProteinView(accession="P00001", name="Test")
    assert view.accession == "P00001"

    cand = CandidateView(protein=view, match_score=0.95, rank=0)
    dump = cand.model_dump()
    assert dump["protein"]["accession"] == "P00001"
    assert dump["match_score"] == 0.95


# ---------------------------------------------------------------------------
# 2. protein_view_mapper: domain filter, alphafold fallback, disease.name fill.
# ---------------------------------------------------------------------------


def test_mapper_skips_invalid_domains_and_extracts_type() -> None:
    from backend.app_services.protein_view_mapper import _as_domains

    raw = [
        {"type": "Signal", "name": "Signal", "start": 1, "end": 24},
        {"type": "Domain", "name": "Ig-like", "start": 100, "end": 200, "description": "Immunoglobulin"},
        {"name": "no-type", "start": 300, "end": 400},   # type defaults to Domain
        {"name": "invalid-start", "start": 0, "end": 50},  # filtered out
        {"name": "invalid-end", "start": 100, "end": None},  # filtered out
        "garbage non-dict entry",  # filtered out
    ]
    domains = _as_domains(raw)
    assert len(domains) == 3
    assert domains[0].type == "Signal"
    assert domains[1].type == "Domain"
    assert domains[2].type == "Domain"


def test_mapper_fills_disease_name_and_alphafold_fallback() -> None:
    from backend.app_services.protein_view_mapper import _disease_info, protein_record_to_view

    disease = _disease_info({"disease_names": ["Alzheimer disease"], "disease_count": 1})
    assert disease is not None
    assert disease.name == "Alzheimer disease"  # filled from names[0]
    assert disease.names == ["Alzheimer disease"]  # legacy field preserved

    record = {"accession": "P12345", "protein_name": "Test"}
    view = protein_record_to_view(record)
    assert view.alphafold_accession == "P12345"  # fallback to accession


# ---------------------------------------------------------------------------
# 3. chat_pipeline frontend adapter: both shapes produce a renderable dict.
# ---------------------------------------------------------------------------


def _stub_streamlit():
    class _AttrDict(dict):
        def __getattr__(self, name):
            try:
                return self[name]
            except KeyError as exc:
                raise AttributeError(name) from exc
        def __setattr__(self, name, value):
            self[name] = value

    fake = types.ModuleType("streamlit")
    fake.session_state = _AttrDict()
    fake.cache_resource = lambda *a, **kw: (a[0] if a and callable(a[0]) and not kw else (lambda fn: fn))
    fake.cache_data = fake.cache_resource
    fake.spinner = lambda *_a, **_k: type("Ctx", (), {"__enter__": lambda s: s, "__exit__": lambda s, *_: False})()
    fake.warning = lambda *_a, **_k: None
    fake.info = lambda *_a, **_k: None
    fake.error = lambda *_a, **_k: None
    sys.modules["streamlit"] = fake


_stub_streamlit()
sys.modules.setdefault("streamlit_cookies_controller", types.ModuleType("streamlit_cookies_controller"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "frontend"))


def test_frontend_adapter_handles_new_shape() -> None:
    import chat_pipeline as cp

    backend_record = {
        "protein": {
            "accession": "O95185",
            "name": "Netrin receptor UNC5C",
            "gene": "UNC5C",
            "organism_scientific": "Homo sapiens",
            "function_text": "...",
            "disease": {
                "name": "Alzheimer disease",
                "acronym": "AD",
                "mim_id": "104300",
                "description": "...",
                "variants": ["T835M"],
            },
            "domains": [{"type": "Domain", "name": "Ig-like", "start": 100, "end": 200}],
            "keywords": ["receptor"],
            "go_terms": ["GO:0005515"],
            "pubmed_ids": ["25419706"],
            "xrefs": {"RefSeq": "NP_003719.3"},
        },
        "match_score": 0.95,
    }
    cand = cp._candidate_from_backend(backend_record)
    p = cand["protein"]
    assert p["accession"] == "O95185"
    assert p["disease"]["name"] == "Alzheimer disease"
    assert p["disease"]["acronym"] == "AD"
    assert p["domains"][0]["type"] == "Domain"
    assert p["alphafold_accession"] == "O95185"  # auto-filled from accession
    assert cand["match_score"] == 95.0  # 0..1 -> percent


def test_frontend_adapter_handles_legacy_shape() -> None:
    """Old persisted rows from before the unification: disease has names/count
    instead of name/acronym, domain has no type. Adapter must still produce
    a renderable shape.
    """
    import chat_pipeline as cp

    legacy_record = {
        "protein": {
            "accession": "P12345",
            "name": "Legacy protein",
            "disease": {
                "names": ["Old disease"],
                "count": 1,
                "xrefs": {"MIM": "999999"},
            },
            "domains": [{"name": "Untyped domain", "start": 50, "end": 150}],
        },
        "match_score": 0.7,
    }
    cand = cp._candidate_from_backend(legacy_record)
    p = cand["protein"]
    # Backward-compat: name back-filled from names[0], mim_id from xrefs.MIM,
    # domain type defaults to Domain.
    assert p["disease"]["name"] == "Old disease"
    assert p["disease"]["mim_id"] == "999999"
    assert p["domains"][0]["type"] == "Domain"
    # Filled defaults for keys missing from legacy shape:
    assert p["alt_names"] == []
    assert p["keywords"] == []


def test_frontend_adapter_drops_invalid_domains() -> None:
    import chat_pipeline as cp

    record = {
        "protein": {
            "accession": "X",
            "domains": [
                {"name": "ok", "start": 1, "end": 50},
                {"name": "bad-zero-start", "start": 0, "end": 50},
                {"name": "bad-none-end", "start": 1, "end": None},
                "non-dict",
            ],
        },
        "match_score": 0.5,
    }
    cand = cp._candidate_from_backend(record)
    domains = cand["protein"]["domains"]
    assert len(domains) == 1
    assert domains[0]["name"] == "ok"


def main() -> int:
    tests = [
        test_backend_contracts_accept_extended_fields,
        test_mapper_skips_invalid_domains_and_extracts_type,
        test_mapper_fills_disease_name_and_alphafold_fallback,
        test_frontend_adapter_handles_new_shape,
        test_frontend_adapter_handles_legacy_shape,
        test_frontend_adapter_drops_invalid_domains,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  [ok] {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  [FAIL] {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  [ERR ] {fn.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

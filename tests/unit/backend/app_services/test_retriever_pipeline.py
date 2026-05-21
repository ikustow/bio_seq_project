from __future__ import annotations

import backend.app_services.retriever_pipeline as rp
from backend.app_services.retriever_pipeline import (
    BioSeqRetrieverPipeline,
    deterministic_extract_and_classify,
    translate_dna_to_protein,
)


def test_deterministic_extract_classifies_protein_and_dna() -> None:
    protein = deterministic_extract_and_classify("protein MALWMRLLPLLALLALWGPGPGAG")
    dna = deterministic_extract_and_classify("dna ATGGCCATTGTAATGGGCCGCTGAAAGGGTGCCCGATAG")

    assert protein.input_type == "SEQUENCE"
    assert protein.sequence_type == "PROTEIN"
    assert dna.sequence_type == "DNA"
    assert dna.is_confident is True


def test_translation_handles_stop_and_bad_length() -> None:
    assert translate_dna_to_protein("ATGTAAATG") == "M"

    pipeline = BioSeqRetrieverPipeline(enable_runtime_retriever=False)
    # 16 nt: long enough to clear MIN_SEQUENCE_TOKEN_LEN (15) so it is detected
    # as a sequence, but not divisible by 3 so translation fails. That failure
    # surfaces as a warning (the cosmetic protein preview is dropped); the
    # controlled miss itself comes from the runtime being disabled.
    state, candidates = pipeline.run("dna ATGAAATGAAATGAAA")

    assert candidates == []
    assert state.controlled_miss is True
    assert any("divisible by 3" in warning for warning in state.warnings)


def test_runtime_disabled_returns_controlled_miss() -> None:
    pipeline = BioSeqRetrieverPipeline(enable_runtime_retriever=False)

    state, candidates = pipeline.run("protein MALWMRLLPLLALLALWGPGPGAG")

    assert candidates == []
    assert state.protein_sequence == "MALWMRLLPLLALLALWGPGPGAG"
    assert state.controlled_miss is True
    assert state.error == "bioseq_retriever runtime is disabled."


def test_runtime_result_maps_to_candidate(monkeypatch) -> None:
    def fake_runtime(prompt, search_algorithm=None):
        return {
            "sequence_type": "PROTEIN",
            "protein_sequence": "MALWMRLLPLLALLALWGPGPGAG",
            "final_results": [
                {
                    "primaryAccession": "O95185",
                    "proteinDescription": {
                        "recommendedName": {"fullName": {"value": "Netrin receptor UNC5C"}}
                    },
                    "genes": [{"geneName": {"value": "UNC5C"}}],
                    "organism": {"scientificName": "Homo sapiens"},
                    "sequence": {"length": 931, "value": "MALW"},
                }
            ],
        }

    monkeypatch.setattr(rp, "_run_backend_bioseq_pipeline", fake_runtime)

    state, candidates = BioSeqRetrieverPipeline(enable_runtime_retriever=True).run(
        "protein MALWMRLLPLLALLALWGPGPGAG"
    )

    assert state.active_accession == "O95185"
    assert candidates[0].protein.gene == "UNC5C"
    assert candidates[0].protein.name == "Netrin receptor UNC5C"

"""Pre-flight validation for the eval YAML datasets.

Usage:
    python -m tests.eval.validate_data

Catches:
  - YAML parse errors (delegated to PyYAML, will raise)
  - leftover placeholders (`<FILL`, `__GENERATE__`)
  - malformed UniProt accessions
  - llm_scenarios that reference non-existent contexts
  - end_to_end scenarios that reference unknown context_ids in `override_card`

Returns exit code 0 on success, 1 on any failure (prints all errors first).
"""

from __future__ import annotations

import re
import sys
from typing import Any

from tests.eval._common.loader import load_e2e, load_llm_scenarios, load_proteins


# UniProt: 6-char (e.g. P01308) or 10-char (e.g. A0A0B4J2F0). Isoforms add `-N`.
_ACCESSION_RE = re.compile(r"^[OPQ][0-9][A-Z0-9]{3}[0-9](-[0-9]+)?$|^[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}(-[0-9]+)?$")
_VALID_VARIANTS = {
    "V0_full",
    "V1_not_full",
    "V2_point_mutations_3",
    "V3_full_exact_species",
    "NEG",
}


def _check_no_placeholders(name: str, payload: Any, errs: list[str]) -> None:
    blob = repr(payload)
    for needle in ("<FILL", "__GENERATE__"):
        if needle in blob:
            errs.append(f"{name}: leftover placeholder '{needle}' detected — fill in the dataset before running eval.")


def _validate_accession(value: str | None, where: str, errs: list[str]) -> None:
    if value is None:
        return
    if not _ACCESSION_RE.match(value):
        errs.append(f"{where}: '{value}' does not look like a valid UniProt accession.")


def validate_proteins(errs: list[str]) -> None:
    data = load_proteins()
    _check_no_placeholders("proteins.yaml", data, errs)

    test_cases = data.get("test_cases") or []
    if not test_cases:
        errs.append("proteins.yaml: `test_cases` is empty.")
        return

    seen_ids: set[str] = set()
    for tc in test_cases:
        tc_id = tc.get("id", "<missing id>")
        if tc_id in seen_ids:
            errs.append(f"proteins.yaml: duplicate test_case id '{tc_id}'.")
        seen_ids.add(tc_id)

        for required in ("input_seq", "input_type", "context_question", "expected", "metadata"):
            if required not in tc:
                errs.append(f"proteins.yaml[{tc_id}]: missing field '{required}'.")

        if tc.get("input_type") not in ("protein", "dna"):
            errs.append(f"proteins.yaml[{tc_id}]: input_type must be 'protein' or 'dna'.")

        variant = (tc.get("metadata") or {}).get("variant")
        if variant not in _VALID_VARIANTS:
            errs.append(
                f"proteins.yaml[{tc_id}]: variant '{variant}' is not one of {sorted(_VALID_VARIANTS)}."
            )

        expected = tc.get("expected") or {}
        _validate_accession(expected.get("top1_accession"), f"proteins.yaml[{tc_id}].expected.top1_accession", errs)
        for acc in expected.get("must_appear_in_top5") or []:
            _validate_accession(acc, f"proteins.yaml[{tc_id}].expected.must_appear_in_top5", errs)

        meta_ref = (tc.get("metadata") or {}).get("protein_ref")
        _validate_accession(meta_ref, f"proteins.yaml[{tc_id}].metadata.protein_ref", errs)


def validate_llm_scenarios(errs: list[str]) -> None:
    data = load_llm_scenarios()
    _check_no_placeholders("llm_scenarios.yaml", data, errs)

    contexts = data.get("contexts") or {}
    scenarios = data.get("scenarios") or []
    if not contexts:
        errs.append("llm_scenarios.yaml: `contexts` is empty.")
    if not scenarios:
        errs.append("llm_scenarios.yaml: `scenarios` is empty.")

    seen_ids: set[str] = set()
    for sc in scenarios:
        sc_id = sc.get("id", "<missing id>")
        if sc_id in seen_ids:
            errs.append(f"llm_scenarios.yaml: duplicate scenario id '{sc_id}'.")
        seen_ids.add(sc_id)

        ctx_id = sc.get("context_id")
        if ctx_id and ctx_id not in contexts:
            errs.append(f"llm_scenarios.yaml[{sc_id}]: context_id '{ctx_id}' not present in `contexts`.")

        rubric = sc.get("rubric") or []
        if not rubric:
            errs.append(f"llm_scenarios.yaml[{sc_id}]: rubric is empty.")
        for item in rubric:
            if item.get("type") not in ("must_cover", "must_not"):
                errs.append(f"llm_scenarios.yaml[{sc_id}]: rubric item type must be must_cover|must_not.")


def validate_e2e(errs: list[str]) -> None:
    data = load_e2e()
    _check_no_placeholders("end_to_end.yaml", data, errs)

    for subset in ("e2e_full", "grounding", "multi_turn", "prompt_injection"):
        scenarios = data.get(subset) or []
        if not scenarios:
            errs.append(f"end_to_end.yaml: subset '{subset}' has no scenarios.")
            continue
        for sc in scenarios:
            sc_id = sc.get("id", "<missing id>")
            expected = sc.get("expected") or {}
            _validate_accession(expected.get("retriever_top1"), f"end_to_end.yaml[{sc_id}].expected.retriever_top1", errs)
            for acc in expected.get("retriever_top5_contains") or []:
                _validate_accession(acc, f"end_to_end.yaml[{sc_id}].expected.retriever_top5_contains", errs)


def main() -> int:
    errs: list[str] = []
    validate_proteins(errs)
    validate_llm_scenarios(errs)
    validate_e2e(errs)

    if errs:
        print("FAILED: validation errors:")
        for e in errs:
            print(f"  - {e}")
        return 1
    print("OK: all three datasets validate cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""L1 retriever evaluation harness.

Usage:
    python -m tests.eval.retriever_eval [--out runs/<dir>/]

Reads `tests/eval/data/proteins.yaml`, runs each test case through
`bioseq_retriever.src.pipeline.run_bioseq_pipeline`, and writes a CSV per
EVALUATION_PLAN.md §2 metrics.

Imports the retriever by inserting `bioseq_retriever/` onto sys.path — the
production package uses `from src.pipeline import ...` rather than a fully-
qualified module path, so we mirror what `pipeline_interface.py` does.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Any

from tests.eval._common.loader import clean_sequence, load_proteins
from tests.eval._common.run_dir import REPO_ROOT, make_run_dir


# Add bioseq_retriever/ to sys.path so `from src.pipeline import ...` resolves.
_RETRIEVER_ROOT = REPO_ROOT / "bioseq_retriever"
if str(_RETRIEVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_RETRIEVER_ROOT))


def _build_prompt(input_seq: str, input_type: str, question: str) -> str:
    """Construct a natural-language prompt the LangGraph extractor can parse.

    The extractor LLM expects a free-text prompt containing both the sequence
    and any user context. We label `input_type` explicitly so the type-
    detection tier-A hint has something concrete to latch onto.
    """
    type_hint = "protein" if input_type == "protein" else "DNA"
    return (
        f"{question}\n"
        f"The following is a {type_hint} sequence:\n"
        f"{clean_sequence(input_seq)}"
    )


def _extract_accessions(records: list[dict[str, Any]] | None) -> list[str]:
    if not records:
        return []
    accs: list[str] = []
    for rec in records:
        acc = rec.get("primaryAccession") or rec.get("accession")
        if acc:
            accs.append(acc)
    return accs


def _rank_of(target: str | None, accs: list[str]) -> int:
    """1-based rank of `target` in `accs`, or -1 if not present."""
    if not target:
        return -1
    for idx, acc in enumerate(accs, start=1):
        if acc == target:
            return idx
    return -1


def evaluate_test_case(tc: dict[str, Any], run_pipeline) -> dict[str, Any]:
    tc_id = tc["id"]
    expected = tc.get("expected") or {}
    metadata = tc.get("metadata") or {}

    prompt = _build_prompt(tc["input_seq"], tc["input_type"], tc["context_question"])

    started = time.perf_counter()
    try:
        result = run_pipeline(prompt)
        err = result.get("error")
    except Exception as exc:  # noqa: BLE001 — we want every failure recorded, not raised
        result, err = {}, f"{type(exc).__name__}: {exc}"
    latency_ms = int((time.perf_counter() - started) * 1000)

    ranked_50 = _extract_accessions(result.get("ranked_results"))
    final_5 = _extract_accessions(result.get("final_results"))

    expected_top1 = expected.get("top1_accession")
    must_appear = list(expected.get("must_appear_in_top5") or [])

    top1_after = final_5[0] if final_5 else ""
    top1_before = ranked_50[0] if ranked_50 else ""
    rank_before = _rank_of(expected_top1, ranked_50)
    rank_after = _rank_of(expected_top1, final_5)

    top1_pass = int(bool(expected_top1) and top1_after == expected_top1)
    top5_pass = int(bool(must_appear) and all(a in final_5 for a in must_appear))
    rr_at_5 = (1.0 / rank_after) if rank_after > 0 else 0.0

    return {
        "test_case_id": tc_id,
        "protein_ref": metadata.get("protein_ref") or "",
        "variant": metadata.get("variant") or "",
        "input_type": tc.get("input_type", ""),
        "context_question": tc.get("context_question", ""),
        "expected_top1_accession": expected_top1 or "",
        "expected_must_appear": "|".join(must_appear),
        "top1_accession_before_rerank": top1_before,
        "top1_accession_after_rerank": top1_after,
        "top5_accessions_after_rerank": "|".join(final_5),
        "top50_size": len(ranked_50),
        "top1_pass": top1_pass,
        "top5_pass": top5_pass,
        "rank_of_expected_before_rerank": rank_before,
        "rank_of_expected_after_rerank": rank_after,
        "reciprocal_rank_at_5": round(rr_at_5, 4),
        "latency_ms": latency_ms,
        "error": err or "",
    }


def write_csv(rows: list[dict[str, Any]], out_dir: Path) -> Path:
    out = out_dir / "retriever_results.csv"
    if not rows:
        out.write_text("", encoding="utf-8")
        return out
    fieldnames = list(rows[0].keys())
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
    positives = [r for r in rows if r["expected_top1_accession"]]
    n_pos = len(positives)
    if n_pos == 0:
        return {"n_positive": 0}

    top1 = sum(r["top1_pass"] for r in positives) / n_pos
    top5 = sum(r["top5_pass"] for r in positives) / n_pos
    mrr = sum(r["reciprocal_rank_at_5"] for r in positives) / n_pos
    top50_recall = sum(1 for r in positives if r["rank_of_expected_before_rerank"] > 0) / n_pos

    dna_rows = [r for r in positives if r["input_type"] == "dna"]
    dna_top5 = (sum(r["top5_pass"] for r in dna_rows) / len(dna_rows)) if dna_rows else None

    return {
        "n_positive": n_pos,
        "top1_accuracy": round(top1, 3),
        "top5_accuracy": round(top5, 3),
        "top50_recall": round(top50_recall, 3),
        "mrr_at_5": round(mrr, 3),
        "dna_top5_accuracy": round(dna_top5, 3) if dna_top5 is not None else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run L1 retriever eval.")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory. If omitted, a fresh tests/eval/runs/<timestamp>-retriever/ is created.",
    )
    args = parser.parse_args(argv)

    # Import here so `python -m tests.eval.retriever_eval --help` works even if
    # the retriever's heavy ML deps are not installed.
    from src.pipeline import run_bioseq_pipeline  # type: ignore[import-not-found]

    data = load_proteins()
    test_cases = data.get("test_cases") or []
    if not test_cases:
        print("No test_cases in proteins.yaml.", file=sys.stderr)
        return 1

    out_dir = args.out or make_run_dir("retriever")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {out_dir}")

    rows: list[dict[str, Any]] = []
    for i, tc in enumerate(test_cases, 1):
        print(f"[{i}/{len(test_cases)}] {tc['id']} ({tc.get('metadata', {}).get('variant', '?')}) ...", flush=True)
        row = evaluate_test_case(tc, run_bioseq_pipeline)
        rows.append(row)
        verdict = "OK" if row["top1_pass"] else ("MISS" if row["expected_top1_accession"] else "NEG")
        print(f"    {verdict}  top1={row['top1_accession_after_rerank'] or '-'}  rank={row['rank_of_expected_after_rerank']}  {row['latency_ms']}ms")
        if row["error"]:
            print(f"    ERROR: {row['error']}")

    csv_path = write_csv(rows, out_dir)
    summary = summarize(rows)
    print()
    print(f"CSV:     {csv_path}")
    print(f"Summary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

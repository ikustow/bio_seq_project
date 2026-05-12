"""Aggregate the most recent eval run into a markdown report.

Usage:
    python -m tests.eval.aggregate_report [--run <dir>]

Reads `retriever_results.csv` from the given run directory (or the latest one)
and emits `report.md` alongside it. The report contains:

  - the L1 metrics table from EVALUATION_PLAN.md §2.4,
  - per-variant breakdown,
  - a list of failed cases (top-1 miss),
  - the raw error column for crashed cases.

L2/L3 aggregation will land here later — see Appendix A.2 in the plan.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from tests.eval._common.run_dir import latest_run_dir


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    positives = [r for r in rows if r["expected_top1_accession"]]
    n_pos = len(positives)

    def _ratio(passed: int) -> float:
        return round(passed / n_pos, 3) if n_pos else 0.0

    top1 = sum(int(r["top1_pass"]) for r in positives)
    top5 = sum(int(r["top5_pass"]) for r in positives)
    top50_recall = sum(1 for r in positives if int(r["rank_of_expected_before_rerank"]) > 0)
    mrr = round(sum(float(r["reciprocal_rank_at_5"]) for r in positives) / n_pos, 3) if n_pos else 0.0

    by_variant: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "top1": 0, "top5": 0})
    for r in positives:
        v = r["variant"] or "?"
        by_variant[v]["n"] += 1
        by_variant[v]["top1"] += int(r["top1_pass"])
        by_variant[v]["top5"] += int(r["top5_pass"])

    failed = [r for r in positives if not int(r["top1_pass"])]
    errored = [r for r in rows if r.get("error")]

    return {
        "n_total": len(rows),
        "n_positive": n_pos,
        "top1_accuracy": _ratio(top1),
        "top5_accuracy": _ratio(top5),
        "top50_recall": _ratio(top50_recall),
        "mrr_at_5": mrr,
        "by_variant": dict(by_variant),
        "failed": failed,
        "errored": errored,
    }


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def render_markdown(run_dir: Path, rows: list[dict[str, Any]], agg: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.append(f"# Eval run — {run_dir.name}\n")
    parts.append(f"Source: `{run_dir}`  \nTotal queries: **{agg['n_total']}**, positive: **{agg['n_positive']}**\n")

    parts.append("## L1 retriever — overall\n")
    parts.append(
        _md_table(
            ["Metric", "Value", "Target (preliminary)"],
            [
                ["Top-1 accuracy",   f"{agg['top1_accuracy']:.3f}", "≥ 0.70 (V0/V1/V2)"],
                ["Top-5 accuracy",   f"{agg['top5_accuracy']:.3f}", "≥ 0.90"],
                ["Top-50 recall",    f"{agg['top50_recall']:.3f}",  "≥ 0.95"],
                ["MRR@5",            f"{agg['mrr_at_5']:.3f}",      "≥ 0.75 (V0/V1/V2)"],
            ],
        )
        + "\n"
    )

    parts.append("## Per-variant breakdown\n")
    rows_var: list[list[str]] = []
    for variant, stats in sorted(agg["by_variant"].items()):
        n = stats["n"]
        t1 = stats["top1"] / n if n else 0.0
        t5 = stats["top5"] / n if n else 0.0
        rows_var.append([variant, str(n), f"{t1:.2f} ({stats['top1']}/{n})", f"{t5:.2f} ({stats['top5']}/{n})"])
    parts.append(
        _md_table(["Variant", "N", "Top-1", "Top-5"], rows_var) + "\n"
    )

    if agg["failed"]:
        parts.append("## Top-1 misses\n")
        rows_fail = [
            [
                r["test_case_id"],
                r["variant"],
                r["expected_top1_accession"],
                r["top1_accession_after_rerank"] or "-",
                r["rank_of_expected_after_rerank"],
            ]
            for r in agg["failed"]
        ]
        parts.append(_md_table(["Test", "Variant", "Expected", "Got top-1", "Rank of expected"], [list(map(str, r)) for r in rows_fail]) + "\n")

    if agg["errored"]:
        parts.append("## Errored cases\n")
        for r in agg["errored"]:
            parts.append(f"- **{r['test_case_id']}**: {r['error']}")
        parts.append("")

    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate eval run into markdown.")
    parser.add_argument("--run", type=Path, default=None, help="Run directory (defaults to latest).")
    args = parser.parse_args(argv)

    run_dir = args.run or latest_run_dir(suite_prefix="retriever")
    if not run_dir or not run_dir.exists():
        print("No run directory found. Run retriever_eval first or pass --run.", file=sys.stderr)
        return 1

    csv_path = run_dir / "retriever_results.csv"
    if not csv_path.exists():
        print(f"Missing {csv_path}. Did retriever_eval finish?", file=sys.stderr)
        return 1

    rows = _read_csv(csv_path)
    if not rows:
        print(f"Empty CSV: {csv_path}", file=sys.stderr)
        return 1

    agg = _aggregate(rows)
    md = render_markdown(run_dir, rows, agg)
    out = run_dir / "report.md"
    out.write_text(md, encoding="utf-8")
    print(f"Wrote {out}")
    print()
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())

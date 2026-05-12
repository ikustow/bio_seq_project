"""Aggregate the most recent eval run into a markdown report.

Usage:
    python -m tests.eval.aggregate_report [--level L1|L2|L3] [--run <dir>]

Reads the CSV produced by the matching eval harness and emits `report.md`
alongside it.

L1 (retriever):       reads retriever_results.csv, emits §2.4 metrics + per-variant breakdown
L2 (LLM scenarios):   reads llm_results.csv, emits per-class coverage + behaviour pass rate (§3.5)
L3 (end-to-end):      reads e2e_results.csv, emits per-subset coverage + latency p50/p95 (§4.3)
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from tests.eval._common.env import load_env
from tests.eval._common.run_dir import latest_run_dir


LEVELS = ("L1", "L2", "L3")
_LEVEL_TO_SUFFIX = {"L1": "retriever", "L2": "llm", "L3": "e2e"}
_LEVEL_TO_CSV = {"L1": "retriever_results.csv", "L2": "llm_results.csv", "L3": "e2e_results.csv"}


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


# =============================================================================
# L1
# =============================================================================
def _aggregate_l1(rows: list[dict[str, Any]]) -> dict[str, Any]:
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

    return {
        "n_total": len(rows),
        "n_positive": n_pos,
        "top1_accuracy": _ratio(top1),
        "top5_accuracy": _ratio(top5),
        "top50_recall": _ratio(top50_recall),
        "mrr_at_5": mrr,
        "by_variant": dict(by_variant),
        "failed": [r for r in positives if not int(r["top1_pass"])],
        "errored": [r for r in rows if r.get("error")],
    }


def _render_l1(run_dir: Path, rows: list[dict[str, Any]], agg: dict[str, Any]) -> str:
    parts: list[str] = [f"# L1 Eval run — {run_dir.name}\n"]
    parts.append(f"Source: `{run_dir}`  \nTotal queries: **{agg['n_total']}**, positive: **{agg['n_positive']}**\n")
    parts.append("## L1 retriever — overall\n")
    parts.append(_md_table(
        ["Metric", "Value", "Target (preliminary)"],
        [
            ["Top-1 accuracy",   f"{agg['top1_accuracy']:.3f}", "≥ 0.70 (V0/V1/V2)"],
            ["Top-5 accuracy",   f"{agg['top5_accuracy']:.3f}", "≥ 0.90"],
            ["Top-50 recall",    f"{agg['top50_recall']:.3f}",  "≥ 0.95"],
            ["MRR@5",            f"{agg['mrr_at_5']:.3f}",      "≥ 0.75 (V0/V1/V2)"],
        ],
    ) + "\n")

    parts.append("## Per-variant breakdown\n")
    rows_var = []
    for v, stats in sorted(agg["by_variant"].items()):
        n = stats["n"]
        rows_var.append([v, str(n), f"{stats['top1']}/{n}", f"{stats['top5']}/{n}"])
    parts.append(_md_table(["Variant", "N", "Top-1", "Top-5"], rows_var) + "\n")

    if agg["failed"]:
        parts.append("## Top-1 misses\n")
        rows_f = [[r["test_case_id"], r["variant"], r["expected_top1_accession"], r["top1_accession_after_rerank"] or "-", str(r["rank_of_expected_after_rerank"])] for r in agg["failed"]]
        parts.append(_md_table(["Test", "Variant", "Expected", "Got top-1", "Rank of expected"], rows_f) + "\n")

    if agg["errored"]:
        parts.append("## Errored cases\n")
        for r in agg["errored"]:
            parts.append(f"- **{r['test_case_id']}**: {r['error']}")
        parts.append("")
    return "\n".join(parts)


# =============================================================================
# L2
# =============================================================================
def _aggregate_l2(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_scenario: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_scenario[r["scenario_id"]].append(r)

    by_class: dict[str, list[float]] = defaultdict(list)
    coverages: list[float] = []
    behaviour_pass: list[int] = []

    for sc_id, sc_rows in by_scenario.items():
        passed = sum(int(r["passed"]) for r in sc_rows)
        total = len(sc_rows)
        cov = passed / total if total else 0.0
        coverages.append(cov)
        cls = sc_rows[0].get("class", "")
        by_class[cls].append(cov)
        if cls == "B":
            mustnots = [r for r in sc_rows if r["rubric_type"] == "must_not"]
            behaviour_pass.append(int(all(int(r["passed"]) == 1 for r in mustnots)) if mustnots else 1)

    return {
        "n_scenarios": len(by_scenario),
        "n_rows": len(rows),
        "mean_coverage": round(sum(coverages) / len(coverages), 3) if coverages else 0.0,
        "behavior_pass_rate": round(sum(behaviour_pass) / len(behaviour_pass), 3) if behaviour_pass else None,
        "coverage_by_class": {k: round(sum(v) / len(v), 3) for k, v in by_class.items()},
        "by_scenario": by_scenario,
    }


def _render_l2(run_dir: Path, rows: list[dict[str, Any]], agg: dict[str, Any]) -> str:
    parts: list[str] = [f"# L2 LLM eval — {run_dir.name}\n"]
    parts.append(f"Source: `{run_dir}`  \nScenarios: **{agg['n_scenarios']}**, rubric items total: **{agg['n_rows']}**\n")
    parts.append("## Overall (plan §3.5)\n")
    parts.append(_md_table(
        ["Metric", "Value", "Target (preliminary)"],
        [
            ["Mean coverage", f"{agg['mean_coverage']:.3f}", "≥ 0.75"],
            ["Behaviour pass rate (class B)", f"{(agg['behavior_pass_rate'] or 0):.3f}" if agg['behavior_pass_rate'] is not None else "n/a", "= 1.0"],
        ],
    ) + "\n")

    parts.append("## Coverage by class\n")
    parts.append(_md_table(
        ["Class", "Mean coverage"],
        [[k, f"{v:.3f}"] for k, v in sorted(agg["coverage_by_class"].items())],
    ) + "\n")

    parts.append("## Per-scenario coverage\n")
    sc_rows = []
    for sc_id, items in sorted(agg["by_scenario"].items()):
        cls = items[0].get("class", "")
        ctx = items[0].get("context_id", "")
        passed = sum(int(r["passed"]) for r in items)
        total = len(items)
        sc_rows.append([sc_id, cls, ctx, f"{passed}/{total}"])
    parts.append(_md_table(["Scenario", "Class", "Context", "Passed"], sc_rows) + "\n")
    return "\n".join(parts)


# =============================================================================
# L3
# =============================================================================
def _aggregate_l3(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_subset: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"passes": [], "retriever": []})
    by_scenario: dict[tuple[str, str], list[int]] = defaultdict(list)

    for r in rows:
        subset = r["subset"]
        if r["passed"] in ("0", "1"):
            by_subset[subset]["passes"].append(int(r["passed"]))
            by_scenario[(subset, r["scenario_id"])].append(int(r["passed"]))
        if r["retriever_match"] in ("0", "1"):
            by_subset[subset]["retriever"].append(int(r["retriever_match"]))

    coverage_by_subset: dict[str, float] = {}
    coverage_per_scenario: dict[str, list[float]] = defaultdict(list)
    for (subset, _), items in by_scenario.items():
        coverage_per_scenario[subset].append(sum(items) / len(items) if items else 0.0)
    for subset, covs in coverage_per_scenario.items():
        coverage_by_subset[subset] = round(sum(covs) / len(covs), 3) if covs else 0.0

    retriever_hit_by_subset = {
        s: round(sum(v["retriever"]) / len(v["retriever"]), 3) if v["retriever"] else 0.0
        for s, v in by_subset.items()
    }

    # Latency p50/p95 over e2e_full turn 1 rows
    lat = sorted(
        int(r["retriever_latency_ms"]) + int(r["gemini_latency_ms"])
        for r in rows
        if r["subset"] == "e2e_full"
        and str(r.get("turn_index", "")) == "1"
        and r.get("retriever_latency_ms") not in ("", None)
        and r.get("gemini_latency_ms") not in ("", None)
    )
    def _pct(seq: list[int], pct: float) -> int | None:
        if not seq:
            return None
        idx = max(0, int(round((pct / 100) * (len(seq) - 1))))
        return seq[idx]

    return {
        "n_rows": len(rows),
        "coverage_by_subset": coverage_by_subset,
        "retriever_hit_by_subset": retriever_hit_by_subset,
        "p50_total_latency_ms": _pct(lat, 50),
        "p95_total_latency_ms": _pct(lat, 95),
    }


def _render_l3(run_dir: Path, rows: list[dict[str, Any]], agg: dict[str, Any]) -> str:
    parts: list[str] = [f"# L3 End-to-end eval — {run_dir.name}\n"]
    parts.append(f"Source: `{run_dir}`  \nRows: **{agg['n_rows']}**\n")

    parts.append("## Coverage by subset (plan §4.3)\n")
    rows_cov = [
        [subset, f"{agg['coverage_by_subset'].get(subset, 0.0):.3f}", f"{agg['retriever_hit_by_subset'].get(subset, 0.0):.3f}"]
        for subset in sorted(set(list(agg["coverage_by_subset"]) + list(agg["retriever_hit_by_subset"])))
    ]
    parts.append(_md_table(["Subset", "Mean coverage", "Retriever hit rate"], rows_cov) + "\n")

    parts.append("## Latency (e2e_full, turn 1)\n")
    p50 = agg["p50_total_latency_ms"]
    p95 = agg["p95_total_latency_ms"]
    parts.append(_md_table(
        ["Percentile", "Total ms"],
        [["p50", str(p50) if p50 is not None else "n/a"], ["p95", str(p95) if p95 is not None else "n/a"]],
    ) + "\n")

    return "\n".join(parts)


# =============================================================================
# main
# =============================================================================
_RENDERERS = {"L1": (_aggregate_l1, _render_l1), "L2": (_aggregate_l2, _render_l2), "L3": (_aggregate_l3, _render_l3)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate eval run into markdown.")
    parser.add_argument("--level", choices=LEVELS, default="L1")
    parser.add_argument("--run", type=Path, default=None, help="Run directory (defaults to latest matching level).")
    args = parser.parse_args(argv)
    load_env()

    suffix = _LEVEL_TO_SUFFIX[args.level]
    run_dir = args.run or latest_run_dir(suite_prefix=suffix)
    if not run_dir or not run_dir.exists():
        print(f"No {args.level} run directory found. Run the harness first or pass --run.", file=sys.stderr)
        return 1

    csv_path = run_dir / _LEVEL_TO_CSV[args.level]
    if not csv_path.exists():
        print(f"Missing {csv_path}. Did the harness finish?", file=sys.stderr)
        return 1

    rows = _read_csv(csv_path)
    if not rows:
        print(f"Empty CSV: {csv_path}", file=sys.stderr)
        return 1

    aggregator, renderer = _RENDERERS[args.level]
    agg = aggregator(rows)
    md = renderer(run_dir, rows, agg)
    out = run_dir / "report.md"
    out.write_text(md, encoding="utf-8")
    print(f"Wrote {out}")
    print()
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())

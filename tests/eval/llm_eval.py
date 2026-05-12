"""L2 LLM-answer-quality evaluation harness.

Usage:
    python -m tests.eval.llm_eval [--out runs/<dir>/]

Reads `tests/eval/data/llm_scenarios.yaml`, sends each scenario's question
through the production Gemini proxy with a frozen protein context (no
retriever in the loop), then scores each rubric item with the OpenRouter
judge.

Outputs land in `tests/eval/runs/<ISO-timestamp>-llm/`:
  - `llm_results.csv`            one row per (scenario, rubric item)
  - `llm_raw/<scenario>.txt`     raw Gemini answer
  - `judge_raw/<sc>_<item>.json` raw judge reply per rubric item
  - `report.md` (after aggregate_report)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

from tests.eval._common.env import load_env
from tests.eval._common.judge import score_rubric_item
from tests.eval._common.llm_clients import build_protein_context_text, call_gemini
from tests.eval._common.loader import load_llm_scenarios
from tests.eval._common.run_dir import make_run_dir


def _judge_config(data: dict[str, Any]) -> dict[str, Any]:
    cfg = data.get("judge") or {}
    return {
        "model": cfg.get("model") or "meta-llama/llama-3.1-8b-instruct:free",
        "temperature": float(cfg.get("temperature", 0.0)),
        "max_tokens": int(cfg.get("max_tokens", 300)),
    }


def _gemini_config(data: dict[str, Any]) -> dict[str, Any]:
    cfg = data.get("production_llm") or {}
    return {
        "temperature": float(cfg.get("temperature", 0.2)),
    }


def _evaluate_scenario(
    sc: dict[str, Any],
    *,
    contexts: dict[str, Any],
    llm_raw_dir: Path,
    judge_raw_dir: Path,
    judge_cfg: dict[str, Any],
    gemini_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    sc_id = sc["id"]
    context_id = sc["context_id"]
    question = sc["question"]
    rubric = sc.get("rubric") or []

    candidate = contexts.get(context_id) or {}
    protein_context = build_protein_context_text(candidate) or ""

    started = time.perf_counter()
    try:
        answer, raw_response = call_gemini(
            protein_context=protein_context,
            history=[],
            prompt=question,
            temperature=gemini_cfg["temperature"],
        )
        gemini_error = ""
    except Exception as exc:  # noqa: BLE001
        answer = ""
        raw_response = {}
        gemini_error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    # Persist raw Gemini reply for audit (plan §3.6).
    answer_path = llm_raw_dir / f"{sc_id}.txt"
    answer_path.write_text(
        answer + (f"\n\n# ERROR: {gemini_error}\n" if gemini_error else "")
        + (f"\n\n# RAW JSON:\n{json.dumps(raw_response, indent=2)}" if raw_response else ""),
        encoding="utf-8",
    )

    rows: list[dict[str, Any]] = []
    for item in rubric:
        item_id = item.get("id", "?")
        rubric_type = item.get("type", "must_cover")
        rubric_check = item.get("check", "")

        if gemini_error:
            verdict = {"passed": 0, "explanation": f"[gemini failed: {gemini_error}]"}
            judge_raw: dict[str, Any] = {}
        else:
            try:
                verdict, judge_raw = score_rubric_item(
                    protein_context=protein_context,
                    question=question,
                    answer=answer,
                    rubric_check=rubric_check,
                    rubric_type=rubric_type,
                    model=judge_cfg["model"],
                    temperature=judge_cfg["temperature"],
                    max_tokens=judge_cfg["max_tokens"],
                )
            except Exception as exc:  # noqa: BLE001
                verdict = {"passed": 0, "explanation": f"[judge failed] {type(exc).__name__}: {exc}"}
                judge_raw = {"error": str(exc)}

        judge_path = judge_raw_dir / f"{sc_id}_{item_id}.json"
        judge_path.write_text(json.dumps(judge_raw, indent=2), encoding="utf-8")

        rows.append({
            "scenario_id": sc_id,
            "class": sc.get("class", ""),
            "context_id": context_id,
            "question": question,
            "rubric_item": item_id,
            "rubric_type": rubric_type,
            "rubric_check": rubric_check,
            "passed": verdict["passed"],
            "judge_explanation": verdict["explanation"],
            "gemini_answer_path": str(answer_path.relative_to(answer_path.parents[1])),
            "judge_raw_path": str(judge_path.relative_to(judge_path.parents[1])),
            "gemini_error": gemini_error,
            "gemini_latency_ms": elapsed_ms,
        })

    return rows


def write_csv(rows: list[dict[str, Any]], out_dir: Path) -> Path:
    out = out_dir / "llm_results.csv"
    if not rows:
        out.write_text("", encoding="utf-8")
        return out
    fieldnames = list(rows[0].keys())
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean coverage + class-B behaviour pass rate (plan §3.5)."""
    if not rows:
        return {"n_rows": 0}

    # group by scenario
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_scenario.setdefault(r["scenario_id"], []).append(r)

    coverages: list[float] = []
    class_b_pass: list[int] = []
    for sc_id, sc_rows in by_scenario.items():
        passed = sum(int(r["passed"]) for r in sc_rows)
        total = len(sc_rows)
        coverages.append(passed / total if total else 0.0)
        # Behaviour rate: all must_not items must pass; counts only class B.
        cls = sc_rows[0].get("class", "")
        if cls == "B":
            mustnots = [r for r in sc_rows if r["rubric_type"] == "must_not"]
            class_b_pass.append(int(all(int(r["passed"]) == 1 for r in mustnots)) if mustnots else 1)

    return {
        "n_scenarios": len(by_scenario),
        "n_rows": len(rows),
        "mean_coverage": round(sum(coverages) / len(coverages), 3) if coverages else 0.0,
        "behavior_pass_rate": round(sum(class_b_pass) / len(class_b_pass), 3) if class_b_pass else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run L2 LLM eval.")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Comma-separated scenario IDs to run (e.g. 'A1,B10,C20'). Default: all.",
    )
    args = parser.parse_args(argv)
    load_env()

    data = load_llm_scenarios()
    contexts = data.get("contexts") or {}
    scenarios = data.get("scenarios") or []
    judge_cfg = _judge_config(data)
    gemini_cfg = _gemini_config(data)

    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        scenarios = [s for s in scenarios if s.get("id") in wanted]
        if not scenarios:
            print(f"No scenarios matched --only={args.only}", file=sys.stderr)
            return 1

    out_dir = args.out or make_run_dir("llm")
    out_dir.mkdir(parents=True, exist_ok=True)
    llm_raw = out_dir / "llm_raw"
    judge_raw = out_dir / "judge_raw"
    llm_raw.mkdir(exist_ok=True)
    judge_raw.mkdir(exist_ok=True)
    print(f"Output: {out_dir}")
    print(f"Judge:  {judge_cfg['model']}")

    all_rows: list[dict[str, Any]] = []
    for i, sc in enumerate(scenarios, 1):
        print(f"[{i}/{len(scenarios)}] {sc['id']} ({sc.get('class', '?')}/{sc.get('context_id', '?')}) ...", flush=True)
        rows = _evaluate_scenario(
            sc,
            contexts=contexts,
            llm_raw_dir=llm_raw,
            judge_raw_dir=judge_raw,
            judge_cfg=judge_cfg,
            gemini_cfg=gemini_cfg,
        )
        all_rows.extend(rows)
        passed = sum(int(r["passed"]) for r in rows)
        print(f"    coverage {passed}/{len(rows)}  (gemini {rows[0]['gemini_latency_ms']}ms)")
        if rows[0].get("gemini_error"):
            print(f"    GEMINI ERROR: {rows[0]['gemini_error']}")

    csv_path = write_csv(all_rows, out_dir)
    summary = summarize(all_rows)
    print()
    print(f"CSV:     {csv_path}")
    print(f"Summary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

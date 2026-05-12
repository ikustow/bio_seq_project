"""L3 end-to-end evaluation harness.

Usage:
    python -m tests.eval.e2e_eval [--out runs/<dir>/] [--subsets e2e_full,grounding,...]

Executes the subsets defined in `tests/eval/data/end_to_end.yaml`:

  e2e_full        sequence → retriever → card → 1 follow-up → judge
                  scores BOTH retriever top-1 match AND answer rubric items.
  grounding       same flow, but overrides one field on the retrieved card
                  (poisoned context) — checks the LLM follows the override
                  rather than its own pretraining.
  multi_turn      2-3 sequential follow-ups on one card; per-turn rubric.
  prompt_injection adversarial questions; judge looks at the final turn.

Subsets `budget` and `regression_baseline` are observation/comparison
buckets — not executed here. Latency/cost metrics fall out of the per-row
timings; regression comparison is a future addition (see plan §A.2).

Outputs in `tests/eval/runs/<ISO-timestamp>-e2e/`:
  - `e2e_results.csv`           one row per (scenario, turn, rubric item)
  - `llm_raw/<id>_turn<N>.txt`  raw Gemini answers
  - `judge_raw/<id>_<item>.json` raw judge replies
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from tests.eval._common.env import load_env
from tests.eval._common.judge import score_rubric_item
from tests.eval._common.llm_clients import apply_override, build_protein_context_text, call_gemini
from tests.eval._common.loader import clean_sequence, load_e2e, load_llm_scenarios
from tests.eval._common.run_dir import REPO_ROOT, make_run_dir


# Same import-path setup as retriever_eval.py.
_RETRIEVER_ROOT = REPO_ROOT / "bioseq_retriever"
if str(_RETRIEVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_RETRIEVER_ROOT))

os.environ.setdefault("BIOSEQ_USE_SERVICES", "false")


SUBSETS = ("e2e_full", "grounding", "multi_turn", "prompt_injection")


def _build_prompt(sequence: str, input_type: str, question: str) -> str:
    """Mirror retriever_eval._build_prompt so the retriever sees the
    same prompt shape across L1 and L3."""
    type_hint = "protein" if input_type == "protein" else "DNA"
    return (
        f"{question}\n"
        f"The following is a {type_hint} sequence:\n"
        f"{clean_sequence(sequence)}"
    )


def _record_to_candidate(record: dict[str, Any]) -> dict[str, Any]:
    """UniProt JSON record → candidate dict the Gemini context builder
    expects. Reuses production's `from_dict` so the flat-protein shape
    matches what the Streamlit frontend would produce."""
    from app.frontend.mock.protein_loader import from_dict  # local import — heavy module

    protein_view = dict(from_dict(record))
    score = record.get("_bioseq_embedding_score")
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    if score is None:
        match_score = 85.0
    elif score <= 1:
        match_score = float(score) * 100
    else:
        match_score = min(float(score), 100.0)
    return {"protein": protein_view, "match_score": match_score}


def _accessions(records: list[dict[str, Any]] | None) -> list[str]:
    if not records:
        return []
    return [r.get("primaryAccession") or r.get("accession") or "" for r in records]


def _run_retriever(test_input: dict[str, Any], run_pipeline) -> dict[str, Any]:
    """Execute the LangGraph pipeline and surface what L3 needs."""
    seq = test_input.get("sequence", "")
    input_type = test_input.get("input_type", "protein")
    first_question = (test_input.get("questions") or [""])[0]
    prompt = _build_prompt(seq, input_type, first_question)

    started = time.perf_counter()
    try:
        result = run_pipeline(prompt)
        err = result.get("error") or ""
    except Exception as exc:  # noqa: BLE001
        result, err = {}, f"{type(exc).__name__}: {exc}"
    latency_ms = int((time.perf_counter() - started) * 1000)

    final_5 = (result.get("final_results") or [])[:5]
    top5_accs = _accessions(final_5)
    candidate = _record_to_candidate(final_5[0]) if final_5 else {}
    return {
        "candidate": candidate,
        "top1": top5_accs[0] if top5_accs else "",
        "top5": top5_accs,
        "retriever_latency_ms": latency_ms,
        "retriever_error": err,
    }


def _call_gemini_turn(
    *,
    protein_context: str,
    history: list[dict[str, str]],
    question: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        answer, raw = call_gemini(
            protein_context=protein_context,
            history=history,
            prompt=question,
        )
        err = ""
    except Exception as exc:  # noqa: BLE001
        answer, raw, err = "", {}, f"{type(exc).__name__}: {exc}"
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {"answer": answer, "raw": raw, "error": err, "latency_ms": latency_ms}


def _score_items(
    *,
    sc_id: str,
    turn_index: int,
    rubric_items: list[dict[str, Any]],
    protein_context: str,
    question: str,
    answer: str,
    judge_raw_dir: Path,
    gemini_error: str,
    judge_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in rubric_items:
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

        judge_path = judge_raw_dir / f"{sc_id}_turn{turn_index}_{item_id}.json"
        judge_path.write_text(json.dumps(judge_raw, indent=2), encoding="utf-8")
        rows.append({
            "rubric_item": item_id,
            "rubric_type": rubric_type,
            "rubric_check": rubric_check,
            "passed": verdict["passed"],
            "judge_explanation": verdict["explanation"],
            "judge_raw_path": str(judge_path.relative_to(judge_path.parents[1])),
        })
    return rows


def _evaluate_single_turn_scenario(
    sc: dict[str, Any],
    subset: str,
    *,
    run_pipeline,
    llm_raw_dir: Path,
    judge_raw_dir: Path,
    judge_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Handles e2e_full / grounding / prompt_injection."""
    sc_id = sc["id"]
    inp = sc.get("input") or {}
    expected = sc.get("expected") or {}
    question = (inp.get("questions") or [""])[0]

    retr = _run_retriever(inp, run_pipeline)
    override = inp.get("override_card")
    candidate = apply_override(retr["candidate"], override) if override else retr["candidate"]
    protein_context = build_protein_context_text(candidate) or ""

    gemini = _call_gemini_turn(protein_context=protein_context, history=[], question=question)

    answer_path = llm_raw_dir / f"{sc_id}_turn1.txt"
    answer_path.write_text(
        gemini["answer"] + (f"\n\n# ERROR: {gemini['error']}\n" if gemini["error"] else ""),
        encoding="utf-8",
    )

    base_row = {
        "scenario_id": sc_id,
        "subset": subset,
        "turn_index": 1,
        "question": question,
        "expected_retriever_top1": expected.get("retriever_top1") or "",
        "retriever_top1": retr["top1"],
        "retriever_top5": "|".join(retr["top5"]),
        "retriever_match": int(bool(expected.get("retriever_top1") and retr["top1"] == expected.get("retriever_top1"))),
        "retriever_latency_ms": retr["retriever_latency_ms"],
        "retriever_error": retr["retriever_error"],
        "gemini_answer_path": str(answer_path.relative_to(answer_path.parents[1])),
        "gemini_latency_ms": gemini["latency_ms"],
        "gemini_error": gemini["error"],
    }

    rubric_items = expected.get("final_answer_rubric") or []
    rubric_rows = _score_items(
        sc_id=sc_id,
        turn_index=1,
        rubric_items=rubric_items,
        protein_context=protein_context,
        question=question,
        answer=gemini["answer"],
        judge_raw_dir=judge_raw_dir,
        gemini_error=gemini["error"],
        judge_cfg=judge_cfg,
    )

    if not rubric_rows:
        # Even without rubric, emit one row so the retriever metric is preserved.
        return [{**base_row, "rubric_item": "", "rubric_type": "", "rubric_check": "", "passed": "", "judge_explanation": "", "judge_raw_path": ""}]

    return [{**base_row, **r} for r in rubric_rows]


def _evaluate_multi_turn_scenario(
    sc: dict[str, Any],
    *,
    run_pipeline,
    llm_raw_dir: Path,
    judge_raw_dir: Path,
    judge_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    sc_id = sc["id"]
    inp = sc.get("input") or {}
    expected = sc.get("expected") or {}
    questions = inp.get("questions") or []
    per_turn = expected.get("per_turn_rubric") or {}

    retr = _run_retriever(inp, run_pipeline)
    candidate = retr["candidate"]
    protein_context = build_protein_context_text(candidate) or ""

    rows: list[dict[str, Any]] = []
    history: list[dict[str, str]] = []

    for idx, question in enumerate(questions, start=1):
        gemini = _call_gemini_turn(
            protein_context=protein_context,
            history=copy.deepcopy(history),
            question=question,
        )
        answer_path = llm_raw_dir / f"{sc_id}_turn{idx}.txt"
        answer_path.write_text(
            gemini["answer"] + (f"\n\n# ERROR: {gemini['error']}\n" if gemini["error"] else ""),
            encoding="utf-8",
        )

        # Append turn to history for the next iteration.
        history.append({"role": "user", "content": question})
        if gemini["answer"]:
            history.append({"role": "model", "content": gemini["answer"]})

        rubric_items = per_turn.get(f"turn_{idx}") or []
        base_row = {
            "scenario_id": sc_id,
            "subset": "multi_turn",
            "turn_index": idx,
            "question": question,
            "expected_retriever_top1": expected.get("retriever_top1") or "",
            "retriever_top1": retr["top1"],
            "retriever_top5": "|".join(retr["top5"]),
            "retriever_match": int(bool(expected.get("retriever_top1") and retr["top1"] == expected.get("retriever_top1"))) if idx == 1 else "",
            "retriever_latency_ms": retr["retriever_latency_ms"] if idx == 1 else "",
            "retriever_error": retr["retriever_error"] if idx == 1 else "",
            "gemini_answer_path": str(answer_path.relative_to(answer_path.parents[1])),
            "gemini_latency_ms": gemini["latency_ms"],
            "gemini_error": gemini["error"],
        }
        rubric_rows = _score_items(
            sc_id=sc_id,
            turn_index=idx,
            rubric_items=rubric_items,
            protein_context=protein_context,
            question=question,
            answer=gemini["answer"],
            judge_raw_dir=judge_raw_dir,
            gemini_error=gemini["error"],
            judge_cfg=judge_cfg,
        )
        if not rubric_rows:
            rows.append({**base_row, "rubric_item": "", "rubric_type": "", "rubric_check": "", "passed": "", "judge_explanation": "", "judge_raw_path": ""})
        else:
            rows.extend({**base_row, **r} for r in rubric_rows)

    return rows


def write_csv(rows: list[dict[str, Any]], out_dir: Path) -> Path:
    out = out_dir / "e2e_results.csv"
    if not rows:
        out.write_text("", encoding="utf-8")
        return out
    fieldnames = list(rows[0].keys())
    # Some subsets may have extra keys — union them so DictWriter doesn't fail.
    for r in rows:
        for k in r:
            if k not in fieldnames:
                fieldnames.append(k)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n_rows": 0}

    # Group by (subset, scenario_id) → list of rubric pass values
    sc_groups: dict[tuple[str, str], list[int]] = {}
    retriever_hits: dict[str, list[int]] = {}
    for r in rows:
        key = (r["subset"], r["scenario_id"])
        passed = r.get("passed")
        if passed in (0, 1):
            sc_groups.setdefault(key, []).append(int(passed))
        if r.get("retriever_match") in (0, 1):
            retriever_hits.setdefault(r["subset"], []).append(int(r["retriever_match"]))

    def _rate(group: dict[Any, list[int]]) -> dict[Any, float]:
        return {k: round(sum(v) / len(v), 3) if v else 0.0 for k, v in group.items()}

    # Coverage per scenario, then aggregate by subset
    coverages: dict[str, list[float]] = {}
    for (subset, _), passes in sc_groups.items():
        if not passes:
            continue
        coverages.setdefault(subset, []).append(sum(passes) / len(passes))
    subset_coverage = {k: round(sum(v) / len(v), 3) if v else 0.0 for k, v in coverages.items()}

    # Latency p50 / p95 over e2e_full scenarios
    e2e_latencies = sorted(
        int(r["retriever_latency_ms"]) + int(r["gemini_latency_ms"])
        for r in rows
        if r["subset"] == "e2e_full" and r["turn_index"] == 1 and r.get("retriever_latency_ms") not in ("", None) and r.get("gemini_latency_ms") not in ("", None)
    )
    def _pct(seq: list[int], pct: float) -> int | None:
        if not seq:
            return None
        idx = max(0, int(round((pct / 100) * (len(seq) - 1))))
        return seq[idx]

    return {
        "n_rows": len(rows),
        "coverage_by_subset": subset_coverage,
        "retriever_hit_by_subset": _rate(retriever_hits),
        "p50_total_latency_ms": _pct(e2e_latencies, 50),
        "p95_total_latency_ms": _pct(e2e_latencies, 95),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run L3 end-to-end eval.")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--subsets",
        type=str,
        default=",".join(SUBSETS),
        help=f"Comma-separated subsets to run. Default: {','.join(SUBSETS)}.",
    )
    parser.add_argument("--only", type=str, default=None, help="Comma-separated scenario IDs to keep.")
    parser.add_argument(
        "--inter-call-delay",
        type=float,
        default=float(os.getenv("EVAL_INTER_CALL_DELAY_S", "4")),
        help="Seconds to sleep between Gemini turns/scenarios. Default: 4s.",
    )
    args = parser.parse_args(argv)
    load_env()

    from src.pipeline import run_bioseq_pipeline  # type: ignore[import-not-found]

    data = load_e2e()
    # Judge config is shared with L2 — read it from llm_scenarios.yaml per the
    # `judge: inherit_from: llm_scenarios.yaml` pointer in end_to_end.yaml.
    judge_cfg_src = (load_llm_scenarios().get("judge") or {})
    judge_cfg = {
        "model": judge_cfg_src.get("model") or "meta-llama/llama-3.1-8b-instruct:free",
        "temperature": float(judge_cfg_src.get("temperature", 0.0)),
        "max_tokens": int(judge_cfg_src.get("max_tokens", 300)),
    }

    wanted_subsets = [s.strip() for s in args.subsets.split(",") if s.strip()]
    only = {x.strip() for x in args.only.split(",")} if args.only else None

    out_dir = args.out or make_run_dir("e2e")
    out_dir.mkdir(parents=True, exist_ok=True)
    llm_raw = out_dir / "llm_raw"
    judge_raw = out_dir / "judge_raw"
    llm_raw.mkdir(exist_ok=True)
    judge_raw.mkdir(exist_ok=True)
    print(f"Output: {out_dir}")
    print(f"Judge:  {judge_cfg['model']}")
    print(f"Subsets: {wanted_subsets}")

    all_rows: list[dict[str, Any]] = []
    for subset in wanted_subsets:
        if subset not in SUBSETS:
            print(f"[skip] unknown subset '{subset}'", file=sys.stderr)
            continue
        scenarios = data.get(subset) or []
        if only:
            scenarios = [s for s in scenarios if s.get("id") in only]
        if not scenarios:
            continue
        print(f"\n--- subset: {subset} ({len(scenarios)} scenarios) ---")
        for i, sc in enumerate(scenarios, 1):
            print(f"[{i}/{len(scenarios)}] {sc['id']} ...", flush=True)
            if subset == "multi_turn":
                rows = _evaluate_multi_turn_scenario(
                    sc,
                    run_pipeline=run_bioseq_pipeline,
                    llm_raw_dir=llm_raw,
                    judge_raw_dir=judge_raw,
                    judge_cfg=judge_cfg,
                )
            else:
                rows = _evaluate_single_turn_scenario(
                    sc,
                    subset,
                    run_pipeline=run_bioseq_pipeline,
                    llm_raw_dir=llm_raw,
                    judge_raw_dir=judge_raw,
                    judge_cfg=judge_cfg,
                )
            all_rows.extend(rows)
            n_pass = sum(int(r["passed"]) for r in rows if r["passed"] in (0, 1))
            n_total = sum(1 for r in rows if r["passed"] in (0, 1))
            print(f"    rubric {n_pass}/{n_total}  retr_top1={rows[0]['retriever_top1'] or '-'}  "
                  f"r:{rows[0]['retriever_latency_ms']}ms g:{rows[0]['gemini_latency_ms']}ms")
            # Preventive pacing between scenarios (multi_turn already
            # paces itself by virtue of sequential Gemini calls).
            if args.inter_call_delay > 0 and i < len(scenarios):
                time.sleep(args.inter_call_delay)

    csv_path = write_csv(all_rows, out_dir)
    summary = summarize(all_rows)
    print()
    print(f"CSV:     {csv_path}")
    print(f"Summary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

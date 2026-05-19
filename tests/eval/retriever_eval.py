"""L1 retriever evaluation harness.

Usage:
    python -m tests.eval.retriever_eval [--out runs/<dir>/]

Reads `tests/eval/data/proteins.yaml`, runs each test case through the
LangGraph pipeline in `app/backend/bioseq_retriever/src/pipeline.py`, and
writes a CSV per VALIDATION_PLAN.md §2 metrics.

Per-node timing: a LangGraph callback (`NodeTimer`) records wall-clock time
for each pipeline stage (extract / use_raw|resolve_file / rank_* / rerank)
so we can attribute slowness — e.g. distinguish a slow Mistral extract from
a slow rerank service call. Per-case timings are printed to console, written
as `<node>_ms` columns to the CSV, and aggregated (p50/max) in the summary.

Imports the retriever by inserting `app/backend/bioseq_retriever/` onto
sys.path — the production package uses `from src.pipeline import ...`
rather than a fully-qualified module path, so we mirror what
`pipeline_interface.py` does.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time
from pathlib import Path
from typing import Any

from langchain_core.callbacks.base import BaseCallbackHandler

from tests.eval._common.env import load_env
from tests.eval._common.loader import clean_sequence, load_proteins
from tests.eval._common.run_dir import REPO_ROOT, make_run_dir


# Add app/backend/bioseq_retriever/ to sys.path so `from src.pipeline import ...` resolves.
_RETRIEVER_ROOT = REPO_ROOT / "app" / "backend" / "bioseq_retriever"
if str(_RETRIEVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_RETRIEVER_ROOT))


# LangGraph node names whose timing we record. Anything else (root graph,
# sub-runnables inside `with_structured_output`, etc) is ignored.
_NODE_NAMES = ("extract", "resolve_file", "use_raw", "rank_dna", "rank_protein", "rerank")


class NodeTimer(BaseCallbackHandler):
    """LangGraph callback: per-node wall-clock time in milliseconds.

    LangGraph stamps `metadata["langgraph_node"]` on each node invocation;
    we match on that and ignore unrelated chain events. One instance per
    test case so timings don't leak between cases.
    """

    def __init__(self) -> None:
        self.per_node_ms: dict[str, float] = {}
        self._starts: dict = {}  # run_id -> (node_name, perf_counter())

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs):
        if metadata and metadata.get("langgraph_node") in _NODE_NAMES:
            self._starts[run_id] = (metadata["langgraph_node"], time.perf_counter())

    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
        if run_id in self._starts:
            name, t0 = self._starts.pop(run_id)
            self.per_node_ms[name] = (time.perf_counter() - t0) * 1000.0

    def on_chain_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        # Record even on failure so a slow-then-erroring stage still shows up.
        if run_id in self._starts:
            name, t0 = self._starts.pop(run_id)
            self.per_node_ms[name] = (time.perf_counter() - t0) * 1000.0


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


def evaluate_test_case(tc: dict[str, Any], pipeline) -> dict[str, Any]:
    tc_id = tc["id"]
    expected = tc.get("expected") or {}
    metadata = tc.get("metadata") or {}

    prompt = _build_prompt(tc["input_seq"], tc["input_type"], tc["context_question"])
    timer = NodeTimer()

    async def _run() -> dict[str, Any]:
        initial_state = {
            "prompt": prompt,
            "sequence_or_path": None,
            "input_type": None,
            "context": None,
            "sequence": None,
            "sequence_type": None,
            "ranked_results": None,
            "final_results": None,
            "error": None,
            "search_algorithm": "embeddings",
        }
        return await pipeline.ainvoke(initial_state, config={"callbacks": [timer]})

    started = time.perf_counter()
    try:
        # Each test case gets its own event loop so failures don't poison
        # subsequent runs. The compiled `pipeline` is reused across cases.
        result = asyncio.run(_run())
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

    row: dict[str, Any] = {
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
    # Per-node timings; nodes that didn't fire (e.g. resolve_file for raw input) get 0.
    for n in _NODE_NAMES:
        row[f"{n}_ms"] = int(timer.per_node_ms.get(n, 0))
    return row


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


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    positives = [r for r in rows if r["expected_top1_accession"]]
    n_pos = len(positives)
    out: dict[str, Any] = {"n_positive": n_pos}

    if n_pos > 0:
        top1 = sum(r["top1_pass"] for r in positives) / n_pos
        top5 = sum(r["top5_pass"] for r in positives) / n_pos
        mrr = sum(r["reciprocal_rank_at_5"] for r in positives) / n_pos
        top50_recall = sum(1 for r in positives if r["rank_of_expected_before_rerank"] > 0) / n_pos

        dna_rows = [r for r in positives if r["input_type"] == "dna"]
        dna_top5 = (sum(r["top5_pass"] for r in dna_rows) / len(dna_rows)) if dna_rows else None

        out.update({
            "top1_accuracy": round(top1, 3),
            "top5_accuracy": round(top5, 3),
            "top50_recall": round(top50_recall, 3),
            "mrr_at_5": round(mrr, 3),
            "dna_top5_accuracy": round(dna_top5, 3) if dna_top5 is not None else None,
        })

    # Per-stage timing aggregates across ALL rows (not just positives).
    # Zero means the node didn't fire — exclude from p50/max.
    stage_stats: dict[str, dict[str, int]] = {}
    for n in _NODE_NAMES:
        vals = sorted(r[f"{n}_ms"] for r in rows if r.get(f"{n}_ms", 0) > 0)
        if vals:
            stage_stats[n] = {
                "p50_ms": vals[len(vals) // 2],
                "max_ms": vals[-1],
                "n": len(vals),
            }
    if stage_stats:
        out["stage_timings"] = stage_stats
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run L1 retriever eval.")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory. If omitted, a fresh tests/eval/runs/<timestamp>-retriever/ is created.",
    )
    args = parser.parse_args(argv)
    load_env()

    # Import here so `python -m tests.eval.retriever_eval --help` works even if
    # the retriever's heavy ML deps are not installed.
    from src.pipeline import create_pipeline  # type: ignore[import-not-found]

    data = load_proteins()
    test_cases = data.get("test_cases") or []
    if not test_cases:
        print("No test_cases in proteins.yaml.", file=sys.stderr)
        return 1

    out_dir = args.out or make_run_dir("retriever")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {out_dir}")

    # Compile the LangGraph once; reused across all test cases.
    pipeline = create_pipeline()

    rows: list[dict[str, Any]] = []
    for i, tc in enumerate(test_cases, 1):
        print(f"[{i}/{len(test_cases)}] {tc['id']} ({tc.get('metadata', {}).get('variant', '?')}) ...", flush=True)
        row = evaluate_test_case(tc, pipeline)
        rows.append(row)
        verdict = "OK" if row["top1_pass"] else ("MISS" if row["expected_top1_accession"] else "NEG")
        print(f"    {verdict}  top1={row['top1_accession_after_rerank'] or '-'}  rank={row['rank_of_expected_after_rerank']}  total={row['latency_ms']}ms")
        stages = " ".join(f"{n}={row[f'{n}_ms']}ms" for n in _NODE_NAMES if row.get(f"{n}_ms", 0) > 0)
        if stages:
            print(f"    stages: {stages}")
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

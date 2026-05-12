# tests/eval — running the BioSeq evaluation suite

Master document: [`report/EVALUATION_PLAN.md`](../../report/EVALUATION_PLAN.md). This README is the operational view (how to run it); the plan is the methodological view (what we measure and why).

Live implementation status — see **Appendix A** of the plan.

## Layout

```
tests/eval/
├── data/                     # YAML datasets (L1 / L2 / L3) + UniProt JSON fixtures
├── _common/                  # shared helpers
│   ├── loader.py             #   YAML loaders
│   └── run_dir.py            #   timestamped run directories
├── validate_data.py          # dataset validator (run first)
├── retriever_eval.py         # L1
├── aggregate_report.py       # markdown summary
├── run_all.py                # master entrypoint
└── runs/                     # output, gitignored (except baseline/)
```

L2 (`llm_eval.py`) and L3 (`e2e_eval.py`) harnesses are **not yet implemented** — see plan Appendix A.2.

## Prerequisites

- Python 3.9+ with the `bioseq_retriever` runtime installed (i.e. you can already run `python bioseq_retriever/pipeline_interface.py`).
- `pyyaml` (likely already present transitively; install with `pip install pyyaml` if not).

For L2/L3 once they land you'll also need:

- `BIOSEQ_LLM_PROXY_URL`, `BIOSEQ_LLM_PROXY_TOKEN` — Cloudflare-proxied Gemini.
- `OPENROUTER_API_KEY` — judge LLM.

## Run it

From the repo root:

```powershell
# Default: validate, then L1 retriever + markdown report
python -m tests.eval.run_all

# Explicit level
python -m tests.eval.run_all --suite L1

# Validate only (no eval)
python -m tests.eval.validate_data

# Re-aggregate the latest run without re-running the retriever
python -m tests.eval.aggregate_report
```

Each run lands in `tests/eval/runs/<ISO-timestamp>-<level>/`:

- `retriever_results.csv` — one row per test case (schema in `data/README.md`).
- `report.md` — markdown summary aligned with the plan §2.4 metrics table.

`run_all.py` skips validation with `--skip-validate` if you have already
validated manually in the same session.

## When something fails

| Symptom | Likely cause |
|---|---|
| `validate_data` complains about a placeholder | Someone re-introduced `<FILL` / `__GENERATE__` in YAML — see the failing path. |
| `Top-50 recall < 0.95` on V0 | FAISS index or ProtT5 embedding is broken — fix this first, top-K means nothing without recall. |
| Top-50 recall fine, Top-1 fails after rerank | Reranker / context prompt issue (see plan §2.5). |
| DNA test cases fail end-to-end | DNA→protein translation step regressed (`bioseq_retriever/src/utils.translate_dna_to_protein`). |
| `ModuleNotFoundError: src.pipeline` | Running from somewhere other than the repo root. Use `python -m tests.eval.run_all` from `BioSeq investigator/`. |

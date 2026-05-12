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
- `python-dotenv` (already in `requirements.txt`) — only used by the eval harness to read a local `.env`, see below.

### Required env vars

For L1 (retriever):

- **`MISTRAL_API_KEY`** OR **`OPENAI_API_KEY`** — the retriever's LangGraph extractor needs an LLM. Set one. (Defaults to Mistral when both/none are set, matching HF Spaces deploy.)

For L2 / L3 (once those harnesses land):

- `BIOSEQ_LLM_PROXY_URL`, `BIOSEQ_LLM_PROXY_TOKEN` — Cloudflare-proxied Gemini.
- `OPENROUTER_API_KEY` — judge LLM.

### Where to put them

Every eval entry-point calls `load_env()` at startup, which reads `<repo>/.env`
if present. So the easiest path is:

```env
# .env at the repo root (already gitignored)
MISTRAL_API_KEY=msk_...
```

Alternatively, set the env vars in your shell (`$env:MISTRAL_API_KEY = "..."`)
— shell-level vars take precedence over `.env` (`override=False`).

**Production safety:** `load_env()` lives entirely in `tests/eval/_common/env.py`
and is only called from eval entry-points. Production code (HF Spaces deploy,
the Streamlit app, `bioseq_retriever`) never imports it. HF Spaces injects
secrets directly into the process environment, and `override=False` guarantees
any pre-existing env var wins over a `.env` file even hypothetically — there is
no path where this changes production behaviour.

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

# tests/eval — running the BioSeq evaluation suite

Master document: [`report/VALIDATION_PLAN.md`](../../report/VALIDATION_PLAN.md). This README is the operational view (how to run it); the plan is the methodological view (what we measure and why).

Live implementation status — see **Appendix A** of the plan.

## Layout

```
tests/eval/
├── data/                     # YAML datasets (L1 / L2 / L3) + UniProt JSON fixtures
├── _common/                  # shared helpers
│   ├── env.py                #   .env loader
│   ├── llm_clients.py        #   Gemini proxy direct caller + protein-context builder
│   ├── judge.py              #   OpenRouter rubric scorer
│   ├── loader.py             #   YAML loaders
│   └── run_dir.py            #   timestamped run directories
├── validate_data.py          # dataset validator (run first)
├── retriever_eval.py         # L1
├── llm_eval.py               # L2
├── e2e_eval.py               # L3
├── aggregate_report.py       # markdown summary (per level)
├── run_all.py                # master entrypoint
└── runs/                     # output, gitignored (except baseline/)
```

L2 (`llm_eval.py`) and L3 (`e2e_eval.py`) harnesses are wired and ready to run. See `report/VALIDATION_PLAN.md` Appendix A for the implementation status.

## Prerequisites

- Python 3.9+ with the BioSeq runtime installed (i.e. `pip install -r requirements.txt` has run successfully — same set HF Spaces uses).
- `pyyaml` (likely already present transitively; install with `pip install pyyaml` if not).
- `python-dotenv` (already in `requirements.txt`) — only used by the eval harness to read a local `.env`, see below.
- **`fastapi` and `uvicorn`** — required to run `bioseq_retriever/services/search_service.py` (the unified retriever service). Not in `requirements.txt` because HF Spaces production uses `app/frontend/embeddings_pipeline.py` instead, which doesn't need a service. Install locally with `pip install fastapi uvicorn`.

### Retriever runtime mode

The May-2026 retriever rewrite removed local in-process mode — `bioseq_retriever.src.pipeline`
now always calls the **unified search service** over HTTP. Before running
L1 or L3, start the service in its own terminal and leave it running:

```powershell
python bioseq_retriever/services/search_service.py
# listens on http://localhost:8002 by default
```

The unified service loads ProtT5 + FAISS itself; the harness only POSTs raw
sequences. The older `embedding_service.py` is gone (folded into the search
service).

`per-protein.h5` is still required (the service reads it on startup). If you
already use this laptop for local Streamlit tests, the file is in
`bioseq_retriever/data/`. If not — run `python -m bioseq_retriever.src.bootstrap`
once, or copy the file from an existing setup.

The harness calls `run_bioseq_pipeline` (now `async def`) via `asyncio.run`
per test case — same pattern `bioseq_retriever/pipeline_interface.py` uses.

### Required env vars

For L1 (retriever):

- **`MISTRAL_API_KEY`** OR **`OPENAI_API_KEY`** — the retriever's LangGraph extractor needs an LLM. Set one. (Defaults to Mistral when both/none are set, matching HF Spaces deploy.)

For L2 (LLM scenarios) and L3 (end-to-end):

- `BIOSEQ_LLM_PROXY_URL`, `BIOSEQ_LLM_PROXY_TOKEN` — Cloudflare-proxied Gemini (same names as production).
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
python -m tests.eval.run_all --suite L2
python -m tests.eval.run_all --suite L3
python -m tests.eval.run_all --suite all

# Validate only (no eval)
python -m tests.eval.validate_data

# Individual harnesses (useful for debugging)
python -m tests.eval.retriever_eval
python -m tests.eval.llm_eval --only A1,B10        # subset by scenario id
python -m tests.eval.e2e_eval --subsets e2e_full   # subset by L3 bucket

# Re-aggregate the latest run without re-running anything
python -m tests.eval.aggregate_report --level L1
python -m tests.eval.aggregate_report --level L2
python -m tests.eval.aggregate_report --level L3
```

Each run lands in `tests/eval/runs/<ISO-timestamp>-<level>/`:

- L1 → `retriever_results.csv` + `report.md`
- L2 → `llm_results.csv` + `llm_raw/<sc>.txt` + `judge_raw/<sc>_<item>.json` + `report.md`
- L3 → `e2e_results.csv` + `llm_raw/<sc>_turnN.txt` + `judge_raw/...` + `report.md`

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
| `WinError 10061 ... target machine actively refused` | The unified retriever service isn't running. Start `python bioseq_retriever/services/search_service.py` in another terminal first (see "Retriever runtime mode" above). |
| `OMP: Error #15: Initializing libomp140 ... already initialized` | Windows-only PyTorch/FAISS OpenMP collision. Harness already sets `KMP_DUPLICATE_LIB_OK=TRUE` automatically in `_common/env.py` — if you still see this, you're running the service or some other entry point that doesn't import our env module. Set the env var manually in that shell. |
| Gemini 429 with body mentioning `daily limit` | Free-tier daily quota exhausted (~1500 req/day on gemini-2.0-flash). Resets at midnight Pacific Time. Either wait, or enable billing in Google AI Studio. |
| Judge 404 from `openrouter.ai/api/v1/chat/completions` | The configured model id was removed from OpenRouter's catalog. Check `tests/eval/data/llm_scenarios.yaml::judge.model` against the live catalog at `https://openrouter.ai/models`. |
| Judge 429 with body mentioning `per-day` / `daily limit` / `add N credits` | OpenRouter free-tier **daily** quota exhausted (50/day without $10 lifetime credit, 1000/day with). `judge.py` detects this and fails fast — retrying is wasted. Either wait for UTC midnight reset, top up $10 credit, or use a different judge model. |
| Judge 429 without daily-quota markers | Transient 20-RPM ceiling. `judge.py` paces itself (`EVAL_JUDGE_MIN_INTERVAL_S=3.5` default) and retries with backoff (`EVAL_JUDGE_MAX_RETRIES=4`); should self-recover. Bump `EVAL_JUDGE_MIN_INTERVAL_S` if you still see this. |

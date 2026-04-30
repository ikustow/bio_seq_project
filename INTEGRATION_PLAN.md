# BioSeq Investigator — Integration Plan

**Branch:** `integration/ui-rank-rerank`
**Goal:** first end-to-end Streamlit UI ↔ `bioseq_retriever` pipeline running locally on a 32 GB Windows laptop, with a clean path to HF Spaces for the May 14 checkpoint.
**Last update:** 2026-05-01

This file is the entry point when a new Claude Code session takes over. Read it top-to-bottom, then execute the **What still needs to be done** section.

---

## Background

Two branches were merged into this integration branch:

- `streamlit-mock-ui` — Streamlit UI mock at `streamlit_ui/`. Scripted conversation in `mock/conversation.py`, view-model loader in `mock/protein_loader.py`, two-column layout (chat + protein card). Hardcoded UNC5C demo.
- `ranking_reranking_dev` (Miloš) — LangGraph pipeline at `bioseq_retriever/`. Pipeline: extract & classify (Mistral structured output) → resolve filepath / use raw → translate DNA / pass protein → **rank** (ProtT5 embed + FAISS HNSW top-50 + UniProt fetch) → **rerank** (Mistral embeddings, top-5) → return `final_results: List[Dict]` of UniProt JSON records.

The team's `dev` branch has independent parallel work (`backend/agents_core/`, `backend/graph_core/`, supabase, neo4j, frontend placeholder). Out of scope here. We PR back into `dev` only after integration is stable, after coordinating folder layout with Ilya.

## Architecture decisions

1. **Single-process deployment.** Streamlit imports `bioseq_retriever` directly as a Python package. Same code on local laptop and HF Spaces. No FastAPI/HTTP between them.
2. **Local 32 GB Windows laptop first, then HF Spaces (16 GB CPU free tier).** RAM budget: ProtT5 ~2.5 GB + FAISS index ~2 GB + Streamlit ~300 MB ≈ 6 GB baseline, comfortably fits.
3. **Env-var-driven config.** All paths and secrets via env vars, no hardcodes. Code identical between deployments — only env differs.
4. **`per-protein.h5` (1.3 GB Swiss-Prot ProtT5 embeddings)** lives at `bioseq_retriever/data/per-protein.h5`. Downloaded from `https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/embeddings/uniprot_sprot/per-protein.h5`. NOT committed to git. On HF Spaces: download on first boot to persistent storage.
5. **First integration keeps the scripted chat** in `mock/conversation.py`. Only the **right card** gets real data from the backend. Replacing chat with a real LLM narrator is a follow-up — not blocking the demo.

## What's already done

- ✅ Branches merged into `integration/ui-rank-rerank`, pushed to origin (`https://github.com/ikustow/bio_seq_project`)
- ✅ Old `../bio_seq_project-ranking` worktree removed
- ✅ `pysam` removed from `bioseq_retriever/src/utils.py` (commit `3c3d6c1`) — it has no Windows build. Replaced with pure-Python FASTA reader; works identically on Linux.
- ✅ `bioseq_retriever/` made a proper Python package (commit `d43b9a4`) — added `__init__.py` to package + `src/`, converted `from src.X` → `from .X` relative imports in `pipeline.py` and `reranking.py`.
- ✅ Strong laptop set up: OpenSSH server, Remote-SSH access from primary, Miniconda3 installed, conda-forge channel default, conda-libmamba solver, `./.venv/` env at project root, all deps installed (`faiss-cpu`, `h5py`, `transformers`, `pytorch`, `sentence-transformers`, `numpy`, `requests`, `langchain-mistralai`, `langgraph`, `tiktoken`, `sentencepiece`, `protobuf`, `streamlit`, `pandas`, `plotly`, `py3Dmol`).
- ✅ `per-protein.h5` downloaded (or downloading) to `bioseq_retriever/data/`.
- ✅ `MISTRAL_API_KEY` set as Windows User env var on strong laptop.

## What still needs to be done — code work

All edits go on branch `integration/ui-rank-rerank`. Commit + push after each file or as a batch — user's choice.

### File 1: `streamlit_ui/mock/protein_loader.py` — refactor

Currently `load(path)` reads a JSON file and parses it into `ProteinView`. The pipeline returns the same UniProt JSON shape as in-memory dicts. We need both shapes to share the parser.

**Change:** extract the parsing body into `from_dict(record: dict) -> ProteinView`. `load(path)` becomes:
```python
def load(path: str | Path) -> ProteinView:
    return from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
```

`load_candidates(directory, specs)` continues to work (it calls `load`). No callers need to change.

### File 2: `streamlit_ui/backend_adapter.py` — new file

Public surface:
```python
def run_search(prompt: str) -> list[Candidate]:
    """Run the bioseq pipeline and return UI-ready Candidate list."""
```

Implementation:
- `from bioseq_retriever.src.pipeline import run_bioseq_pipeline`
- `from streamlit_ui.mock.protein_loader import from_dict, Candidate`
- Call `result = run_bioseq_pipeline(prompt)`.
- If `result.get("error")` — raise `RuntimeError(result["error"])`. The UI catches and shows nicely.
- Iterate `result.get("final_results", [])`: each is a UniProt JSON dict. `protein = from_dict(record)`.
- Pack `Candidate(protein=protein, match_score=0.0)` — score placeholder; rerank step currently drops scores. Flag this in the UI (gray badge "match-confidence unavailable") instead of showing a fake percent.

Edge cases:
- Empty `final_results` → return `[]`. UI must handle this (currently it doesn't, will need a small tweak in `protein_card.py`).
- `MISTRAL_API_KEY` missing → pipeline raises `ValueError("MISTRAL_API_KEY environment variable is not set.")` from `setup_environment`. We surface that to the user as a clear error.

### File 3: `bioseq_retriever/src/pipeline.py` — env-driven paths

Currently in `rank_node`:
```python
H5_PATH, INDEX_PATH, CACHE_PATH = "data/per-protein.h5", "data/per-protein.index", "data/per-protein.accessions.pkl"
```

Replace with module-level constants:
```python
import os
_DATA_DIR = os.getenv("BIOSEQ_DATA_DIR", os.path.join("bioseq_retriever", "data"))
H5_PATH = os.path.join(_DATA_DIR, "per-protein.h5")
INDEX_PATH = os.path.join(_DATA_DIR, "per-protein.index")
CACHE_PATH = os.path.join(_DATA_DIR, "per-protein.accessions.pkl")
```

Default points to repo-relative `bioseq_retriever/data/` — works without any env var when running from project root (locally and on HF Spaces). Override via `BIOSEQ_DATA_DIR` for unusual layouts.

### File 4: `streamlit_ui/app.py` — feature flag

Read `BIOSEQ_BACKEND` env var (default `"mock"`). When `"real"`:

- Replace `_load_protein` to call `backend_adapter.run_search(prompt)` instead of `protein_loader.load_candidates(...)`.
- Wrap the call in `with st.spinner("Searching databases — this can take 30–90 seconds…"):`.
- The prompt passed to `run_search` should combine the user's pasted sequence + their question — current chat input already provides both. Use the **first user message** content.
- On `RuntimeError` — `st.error(...)` with the message, don't crash the page.

When `"mock"`: existing behavior preserved.

### File 5: `.gitignore` — extend

Add to root `.gitignore` (create if missing):
```
.venv/
bioseq_retriever/data/
.env
*.pyc
__pycache__/
```

Verify `bioseq_retriever/data/per-protein.h5` is not staged anywhere accidentally.

## Sanity checks

Run these on the strong laptop **before** end-to-end test (in `(./.venv)` activated terminal):

```powershell
# Imports
python -c "from bioseq_retriever.src.pipeline import run_bioseq_pipeline; print('pipeline ok')"
python -c "from streamlit_ui.mock.protein_loader import from_dict; print('parser ok')"
python -c "from streamlit_ui.backend_adapter import run_search; print('adapter ok')"

# Env var present
python -c "import os; assert os.getenv('MISTRAL_API_KEY'), 'MISTRAL_API_KEY missing'; print('key ok')"

# Data file present and right size
Get-Item bioseq_retriever/data/per-protein.h5 | Select-Object Length
# Should be ~1_300_000_000 bytes (1.3 GB)
```

## End-to-end test

```powershell
$env:BIOSEQ_BACKEND = "real"
streamlit run streamlit_ui/app.py
```

**First run** is slow:
- ProtT5 model auto-downloads to `~/.cache/huggingface/hub` (~3 GB, 5–10 min).
- FAISS HNSW index builds from `per-protein.h5` (5–15 min, single-threaded CPU).
- Total: ~15–25 min before the first query returns.

Subsequent runs reuse the cached model + on-disk index → cold start <30s, query 30–90s.

**In the UI:** paste the demo FASTA from `streamlit_ui/test_chat.txt` + ask a question. The right protein card should populate from real UniProt data returned by the backend. Chat side stays scripted (intentional — see decision #5).

## Known gotchas / follow-ups (not blocking the demo)

1. **No match-confidence scores.** `rerank_by_context` in `bioseq_retriever/src/reranking.py` drops scores when returning records. UI shows a placeholder badge for now. Follow-up: ask Miloš to return `(record, score)` tuples — 5-line change.
2. **Tests in `bioseq_retriever/tests/`** use `@patch('src.pipeline.X')` paths that no longer resolve after the package rename. They fail. Not blocking; fix when bringing this branch back to `dev`.
3. **`pipeline_interface.py`** at `bioseq_retriever/` root still uses `from src.pipeline import ...` — works only when invoked as `cd bioseq_retriever && python pipeline_interface.py`. Not blocking; legacy CLI entry point.
4. **Scripted chat** in `mock/conversation.py` will look incongruous with real backend data on the right (e.g., user pastes a non-UNC5C sequence, gets correct UniProt match in the card, but chat says "UNC5C" hardcoded). For first demo, accept this. Follow-up: replace with a real LLM narrator that summarises `final_results[0]`.
5. **`per-protein.h5` not committed.** If a teammate clones fresh, they need to run the curl command. Add a one-liner to `bioseq_retriever/README.md` after this integration commit.
6. **Power-on requirements** — strong laptop needs to stay on with sleep disabled. Already configured. If it sleeps, Remote-SSH disconnects.

## Repo state at the start of next session

Most recent commits on `integration/ui-rank-rerank`:
- `d43b9a4` — Make bioseq_retriever a proper Python package with relative imports
- `3c3d6c1` — Drop pysam dependency in get_first_fasta_entry for Windows support
- `601231f` — Merge ranking_reranking_dev into integration/ui-rank-rerank
- `38a7c47` — Show top-5 candidate matches with switcher (from streamlit-mock-ui)

The next session should:
1. Read this file.
2. Read `MEMORY.md` (auto-memory) for project-level context.
3. Confirm with the user that sanity checks pass on her machine.
4. Begin **File 1** in the "What still needs to be done" section.
5. Commit each file or batch and push to `integration/ui-rank-rerank`.

After this integration is end-to-end green, separate planning is needed for: HF Spaces deployment, eventual PR into `dev`, replacement of scripted chat with LLM narrator. Those are NOT in scope of this plan.

## What's beyond this integration (for visibility, not for action)

- **HF Spaces deployment** for May 14 checkpoint: single-process Space, `MISTRAL_API_KEY` in Secrets, `per-protein.h5` downloaded on first boot via `huggingface_hub.hf_hub_download` or `urllib.request.urlretrieve` into persistent storage.
- **PR into `dev`** after integration green: requires structural alignment with Ilya (whether `streamlit_ui/` should move to `frontend/`, whether `bioseq_retriever/` should sit under `backend/`).
- **Replace scripted chat with real LLM narrator** — a small LangChain chain that takes `result["final_results"][0]` and the user's question, produces a grounded paragraph.
- **Add planned features** (memory DB, question-suggester agent, MD download, classification, summary) — all fit in HF Spaces RAM budget per earlier analysis. Architectural rule: Mistral API + SQLite + REST APIs, nothing heavy locally.

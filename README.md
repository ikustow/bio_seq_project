---
title: BioSeq Investigator
emoji: 🧬
colorFrom: indigo
colorTo: green
sdk: streamlit
sdk_version: 1.57.0
app_file: streamlit_ui/app.py
pinned: false
license: mit
short_description: Paste a FASTA, get an evidence-grounded UniProt match.
---

# BioSeq Investigator

Paste a DNA or protein FASTA sequence, ask a question, and get an
evidence-grounded answer backed by ProtT5 sequence search over Swiss-Prot
plus Mistral-driven contextual reranking.

This Space is the course-capstone artifact for an "Intro to AI agents"
project. The chat column is currently scripted; the right protein card is
populated live from the retrieval pipeline at
[`bioseq_retriever/`](bioseq_retriever/).

## Configuration (HF Space Secrets / Variables)

| Name                  | Where        | Required | Notes |
|-----------------------|--------------|----------|-------|
| `MISTRAL_API_KEY`     | **Secret**   | yes      | Mistral API key — drives extract/classify and reranking. |
| `BIOSEQ_BACKEND`      | **Variable** | yes      | Set to `real` for live pipeline. `mock` keeps the demo UI without backend. |
| `BIOSEQ_DATA_SOURCE`  | **Variable** | optional | `uniprot` (default, downloads ~1.3 GB from UniProt FTP on first boot) or `hf:OWNER/DATASET` to pull `per-protein.h5` (and optionally a pre-built FAISS index) from a HF dataset repo. |
| `BIOSEQ_DATA_DIR`     | **Variable** | optional | Override the default `bioseq_retriever/data` location for the embeddings + index. |

## Cold-start expectations (free-tier 16 GB CPU Space)

1. `per-protein.h5` (~1.3 GB) downloads to `bioseq_retriever/data/`
   (~5–10 min from UniProt FTP, ~1–2 min from a HF dataset).
2. `Rostlab/prot_t5_xl_uniref50` weights (~3 GB) download to the
   HF cache from the public Hub on first ProtT5 use.
3. FAISS HNSW index builds from the .h5 (5–15 min, single-threaded).
4. Each subsequent query is ~30–90 s.

For demo days, hitting the Space at least once before the audience does
warms all of the above.

## Local development

```bash
# Install
pip install -r requirements.txt

# .env in the repo root or one level up:
#   MISTRAL_API_KEY=...
# (load_dotenv() picks it up automatically)

# Mock UI (no backend, no key needed):
streamlit run streamlit_ui/app.py

# Real pipeline:
BIOSEQ_BACKEND=real streamlit run streamlit_ui/app.py
```

## Project layout

- [`streamlit_ui/`](streamlit_ui/) — Streamlit UI, scripted chat, view-model parser, real-backend adapter.
- [`bioseq_retriever/`](bioseq_retriever/) — LangGraph pipeline: extract → classify → translate → rank (FAISS over ProtT5) → rerank (Mistral embeddings).
- [`INTEGRATION_PLAN.md`](INTEGRATION_PLAN.md) — session-handoff document that drove this integration.
- [`DEPLOY.md`](DEPLOY.md) — step-by-step Space deployment guide (one-time).

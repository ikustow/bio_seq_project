---
title: BioSeq Investigator
emoji: 🧬
colorFrom: indigo
colorTo: green
sdk: streamlit
sdk_version: 1.57.0
app_file: app/frontend/app.py
pinned: false
license: mit
short_description: Paste a FASTA, get an evidence-grounded UniProt match.
---

# BioSeq Investigator

BioSeq Investigator is a research assistant for first-pass DNA and protein
FASTA analysis (FASTA is a simple text format for writing a DNA or protein
sequence).
A user pastes a sequence and asks a natural-language question; the app finds
the closest UniProt/Swiss-Prot candidates (UniProt is a large public database
of proteins, and Swiss-Prot is its manually reviewed, high-quality part),
shows an evidence-grounded protein card, and supports a follow-up dialogue
grounded in the retrieved context.

Russian README: [README_RU.md](README_RU.md).

## Business Value

BioSeq Investigator bridges the gap between raw FASTA and understandable
biological context. Instead of manually moving across BLAST (a classic
bioinformatics tool that searches a database for sequences similar to your
query), UniProt, papers, feature tables, and notes, a researcher gets one
working screen: the sequence,
top-5 candidates, match explanation, structured annotations, and question
history.

The project is useful for:

- pre-screening unknown or poorly documented sequences;
- quick demos and education scenarios where a clear FASTA-to-function path is
  needed;
- product validation of an AI-assisted bioinformatics workflow;
- reproducible analysis: the session, candidates, selected protein, and
  follow-up context are persisted in Postgres;
- team development of bioinformatics UX where backend, frontend, retrieval,
  and eval layers can evolve independently.

The main idea is not to replace curated bioinformatics review, but to remove
the routine first-pass search work and produce a checkable starting hypothesis
in minutes.

## What The Project Does

1. Accepts raw sequence text, FASTA, or a UniProt accession/mnemonic ID.
2. Classifies the input as DNA, protein, or a plain text follow-up.
3. Runs the runtime retriever for sequence turns:
   - ProtT5/FAISS search over protein embeddings;
   - DNA path through the DNA index/search where available;
   - alternative BLAST path for protein search;
   - UniProt metadata fetch;
   - contextual rerank based on the user's question.
4. Returns top-5 UniProt candidates and a UI-ready `ProteinView`.
5. Renders the Streamlit UI: chat, protein card, features, domains,
   interactions, variants, alignment viewer, and session sidebar.
6. Uses a Chat LLM for follow-up questions over the already retrieved context
   without resetting the active card.
7. Persists history and compact session state to Supabase/Postgres when
   `SUPABASE_DB_URL` is set.

## Architecture

```text
User
  -> app/frontend Streamlit UI
  -> backend.app_contracts.ChatTurnRequest
  -> backend.app_services.BioSeqChatService
  -> BioSeqRetrieverPipeline / ChatLLMService / SuggestedQuestionsService
  -> backend.bioseq_retriever LangGraph pipeline
  -> FastAPI search gateway: ProtT5 + FAISS + rerank
  -> UniProt metadata
  -> CandidateView / ProteinView
  -> Streamlit protein card + persisted session
```

The layers are separated so the UI does not know FAISS/UniProt details, and
the retriever does not know Streamlit state. The contract between them is the
Pydantic DTO layer in `app/backend/app_contracts`.

## Technical Documentation

### Inside The Repository

- [Backend layer](app/backend/README.md) ([RU](app/backend/README_RU.md)) -
  services, contracts, agents, persistence, and search gateway.
- [Frontend layer](app/frontend/README.md) ([RU](app/frontend/README_RU.md)) -
  Streamlit entrypoint, UI components, object registry, session restore, and
  runtime modes.
- [Retriever library](app/backend/bioseq_retriever/README.md) - LangGraph
  pipeline, ProtT5/FAISS gateway, UniProt fetch, and rerank.
- [Data preparation](data_prep/README.md) - offline pipeline for preparing
  Swiss-Prot/RefSeq data and HDF5 artifacts.
- [Evaluation harness](tests/eval/README.md) - L1/L2/L3 eval pipelines and
  retrieval/LLM answer quality checks.
- [Environment template](example.env.txt) - minimal variables for local
  runtime.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp example.env.txt .env
```

Minimum `.env` for live runtime:

```dotenv
MISTRAL_API_KEY=...
# or OPENAI_API_KEY=...

BIOSEQ_BACKEND=runtime
BIOSEQ_ENABLE_RUNTIME_RETRIEVER=true
BIOSEQ_SEARCH_SERVICE_URL=http://localhost:8002
BIOSEQ_DATA_SOURCE=hf:radda-i/bioseq-data

# optional, but recommended for session history
SUPABASE_DB_URL=postgresql://user:password@host:5432/postgres
```

Run in two terminals:

```bash
# 1. Heavy search/rerank gateway: ProtT5, FAISS, rerank.
python app/backend/bioseq_retriever/services/search_service.py

# 2. Streamlit UI.
streamlit run app/frontend/app.py
```

Demo mode without the heavy backend and API keys:

```bash
BIOSEQ_BACKEND=mock streamlit run app/frontend/app.py
```

## Main Runtime Variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `BIOSEQ_BACKEND` | yes | `runtime` for the live pipeline, `mock` for scripted demo. |
| `BIOSEQ_ENABLE_RUNTIME_RETRIEVER` | yes for runtime | Allows the service layer to run `app/backend/bioseq_retriever`. |
| `BIOSEQ_SEARCH_SERVICE_URL` | yes for runtime | FastAPI gateway URL, default `http://localhost:8002`. |
| `MISTRAL_API_KEY` | one LLM key | LLM extraction/rerank path and optional think mode. |
| `OPENAI_API_KEY` | one LLM key | fallback LLM provider and Chat LLM provider. |
| `BIOSEQ_CHAT_LLM_PROVIDER` | no | `auto`, `gemini_proxy`, or `openai` for follow-up answers. |
| `BIOSEQ_LLM_PROXY_URL` | for `gemini_proxy` | Cloudflare Worker URL for Gemini proxy. |
| `BIOSEQ_LLM_PROXY_TOKEN` | for `gemini_proxy` | Bearer token for proxy. |
| `SUPABASE_DB_URL` | recommended | Postgres connection string for `public.chat_sessions`. |
| `BIOSEQ_DATA_SOURCE` | recommended | `hf:radda-i/bioseq-data` gives faster cold starts, `uniprot` downloads source data. |
| `BIOSEQ_DATA_DIR` | no | Folder for HDF5, FAISS index, and accession cache. |
| `APP_PASSWORD` | no | Simple password gate for the public UI. |

Full template: [example.env.txt](example.env.txt).

## Repository Structure

```text
app/
  backend/
    app_contracts/       Pydantic DTOs between UI and backend.
    app_services/        Application orchestration and turn routing.
    agents_core/         LangGraph session-agent, memory, persistence.
    bioseq_retriever/    Retrieval pipeline and FastAPI search gateway.
  frontend/
    app.py               Streamlit entrypoint.
    components/          Chat, protein card, alignment, sidebar, debug panel.
    assets/              Logo, icons, CSS.
    mock/                Scripted demo mode.
data_prep/               Offline data-build scripts.
tests/
  unit/                  Unit tests for frontend/backend services.
  backend/               Retriever/backend integration tests.
  eval/                  Retrieval and LLM evaluation harnesses.
to_delete/               Archive of old documentation and deprecated code.
```

## Data And Cold Start

The runtime gateway works with heavy artifacts:

- `per-protein.h5` - protein embeddings;
- `per-protein.index` - FAISS HNSW index;
- accession cache - mapping from index row to UniProt accession;
- optional DNA artifacts for the DNA path.

On a CPU Hugging Face Space, cold start can take minutes: first data artifacts
are downloaded, then ProtT5 weights, then the gateway loads the FAISS index.
For demos, warm up the Space with one request in advance.

## Quality And Validation

Retrieval quality is checked through [tests/eval](tests/eval/README.md).
Important metrics: top-k recall, DNA/protein classification correctness,
follow-up answer quality, and session restore stability. Details of the
current retriever pipeline and known quality risks are in
[app/backend/bioseq_retriever/README.md](app/backend/bioseq_retriever/README.md).

## Development Workflow

1. Work from a fresh `main`.
2. Create a short branch: `feature/...`, `fix/...`, `docs/...`.
3. Run relevant tests before PR:

```bash
pytest tests/unit
pytest tests/backend/bioseq_retriever
python scripts/smoke_chat_pipeline_routing.py
```

4. For retrieval quality changes, run the eval harness from
   [tests/eval/README.md](tests/eval/README.md).
5. In the PR, write what changed, why, and how it was checked.

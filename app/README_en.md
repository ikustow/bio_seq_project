# BioSeq Investigator app

🇷🇺 Russian version: [README.md](README.md).

Current state as of 2026-05-13: `app/` is the main runtime contour of the Streamlit app. Protein search and protein cards flow through `app/backend/bioseq_retriever`, `app/backend/app_services` and local FAISS/HDF5 artifacts. The Neo4j / graph-database contour has been removed from runtime.

## What's in app

| Path | Purpose |
| --- | --- |
| `frontend/` | Streamlit UI: chat, protein card, sidebar with session history, cookie identity. |
| `backend/app_contracts/` | Pydantic contracts between UI, service layer and session agent. |
| `backend/app_services/` | `BioSeqChatService`, wrapper over the retriever pipeline, mapping of UniProt records into UI cards, service factory. |
| `backend/agents_core/` | LangGraph session agent and persistence glue for Supabase / Postgres. |
| `backend/bioseq_retriever/` | Working copy of the bioseq retriever inside backend: pipeline, UniProt fetch, search-service client, rerank. |

The old root-level retriever has been moved to `depricated/bioseq_retriever/` as a rollback / reference snapshot. New runtime integration must go through `app/backend/bioseq_retriever`.

## Quick start

```bash
pip install -r app/frontend/requirements.txt
```

Start the search / rerank gateway:

```bash
python app/backend/bioseq_retriever/services/search_service.py
```

Start Streamlit:

```bash
BIOSEQ_BACKEND=runtime streamlit run app/frontend/app.py
```

Locally the app is available at the standard Streamlit URL, usually `http://localhost:8501`.

## Minimal configuration

Example variables live in `example.env.txt`. The main ones:

| Variable | Purpose |
| --- | --- |
| `BIOSEQ_BACKEND=runtime` | Enables the main runtime backend. `mock` is also allowed for the scripted UI demo. |
| `BIOSEQ_SEARCH_SERVICE_URL=http://localhost:8002` | URL of the unified BioSeq search / rerank gateway. |
| `BIOSEQ_ENABLE_RUNTIME_RETRIEVER=true` | Allows the service layer to call `app/backend/bioseq_retriever`. |
| `MISTRAL_API_KEY` or `OPENAI_API_KEY` | Required by the current `backend/bioseq_retriever/src/pipeline.py` for LLM extraction. |
| `SUPABASE_DB_URL` | Optional, but required for persistent history, sidebar restore and correct follow-up routing. |
| `APP_WORKSPACE_ID`, `APP_USER_ROLE` | Optional metadata for the session context. |
| `APP_PASSWORD` | Optional simple password gate for Streamlit. |

Local data artifacts:

| Artifact | Purpose |
| --- | --- |
| `data/per-protein.h5` | Protein embeddings. |
| `data/per-protein.index` | FAISS protein index. |
| `data/per-protein.accessions.json` | FAISS row → UniProt accession cache. |
| `data/per-gene.*` | Optional DNA artifacts. |

These files are heavy and must stay local or live in a dataset / object storage. They must not land in git.

## Chat LLM follow-up

The first user turn that contains a sequence goes to the runtime retriever. Follow-up questions after the saved first turn go to `frontend/chat_llm_pipeline.py` and do not redraw the protein card.

Provider is selected through:

| Variable | Behaviour |
| --- | --- |
| `BIOSEQ_CHAT_LLM_PROVIDER=auto` | Default: Gemini proxy if a proxy URL / token are set, otherwise OpenAI when `OPENAI_API_KEY` is available. |
| `BIOSEQ_CHAT_LLM_PROVIDER=gemini_proxy` | Force the proxy. |
| `BIOSEQ_CHAT_LLM_PROVIDER=openai` | Force OpenAI. |
| `BIOSEQ_LLM_PROXY_URL`, `BIOSEQ_LLM_PROXY_TOKEN` | Gemini proxy endpoint / token. |
| `OPENAI_API_KEY`, `BIOSEQ_OPENAI_CHAT_MODEL` | OpenAI key / model for the follow-up chat. |

`OPENAI_API_KEY` can be used simultaneously by the current retriever pipeline and by the follow-up chat LLM. For retriever-provider selection there is a separate `BIOSEQ_LLM_PROVIDER=mistral|openai`.

Important: the current routing for first / follow-up turns relies on `working_memory.turn_count` in `public.chat_sessions`. Without `SUPABASE_DB_URL` persistence is disabled and follow-up routing degrades to running the retriever again.

## Runtime data flow

```text
Streamlit submit
  -> frontend/chat_pipeline.py
  -> backend/app_services/BioSeqChatService
  -> backend/app_services/BioSeqRetrieverPipeline
  -> backend/bioseq_retriever/src/pipeline.py
  -> backend/bioseq_retriever/services/search_service.py
  -> UniProt metadata + CandidateView/ProteinView
  -> agents_core/retriever_agent/runtime_agent.py
  -> session_db_adapter -> public.chat_sessions
  -> Streamlit protein card
```

The search service is a separate heavy process that loads the embedding models and the FAISS indices. It is not a FastAPI API "inside the agent"; the agent and app services use it as a runtime dependency through `BIOSEQ_SEARCH_SERVICE_URL`.

## Persistence

If `SUPABASE_DB_URL` is set, the backend turns on:

- LangGraph checkpoints;
- LangGraph store;
- compact session rows in `public.chat_sessions`;
- sidebar history and restore;
- the turn counter used for retriever vs follow-up LLM routing.

If `SUPABASE_DB_URL` is missing or init fails, a memory fallback is used. The app still starts, but history and follow-up routing will not be reliably preserved across turns / reruns.

## Tests

All tests are collected under the top-level `tests/`:

| Path | Purpose |
| --- | --- |
| `tests/backend/bioseq_retriever/` | Tests for the working backend copy of the retriever. |
| `tests/depricated/bioseq_retriever/` | Tests for the deprecated snapshot. |
| `tests/scripts/` | Tests for old utility / script checks. |
| `tests/eval/` | Evaluation suite and validation datasets. |

## Outside of app

`data_prep/` remains a separate project-level contour for preparing local artifacts. It is not a runtime part of `app/`, but it is needed to generate / refresh data.

## Legacy and cleanup

- `frontend/embeddings_pipeline.py` and `frontend/vector_db_adapter.py` are kept as legacy paths pending a separate frontend cleanup.
- `depricated/bioseq_retriever/` is kept as a rollback / reference snapshot.
- Neo4j, `graph_core` and the graph retriever agent are not part of the current runtime.

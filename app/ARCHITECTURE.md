# BioSeq Investigator architecture

Дата актуализации: 2026-05-13.

## Коротко

`app/` сейчас работает как Streamlit monorepo app с in-process backend service layer и отдельным heavy search/rerank gateway. Основной retriever живет в `app/backend/bioseq_retriever`. Graph database/Neo4j runtime удален из актуального контура.

```text
Browser
  -> Streamlit frontend
  -> app_services.BioSeqChatService
  -> app_services.BioSeqRetrieverPipeline
  -> backend/bioseq_retriever/src.pipeline
  -> backend/bioseq_retriever/services.search_service
  -> local FAISS/HDF5 artifacts + UniProt API
  -> app_contracts.CandidateView / ProteinView
  -> agents_core.BioSeqRuntimeSessionAgent
  -> Supabase/Postgres public.chat_sessions
```

## Runtime modules

| Путь | Роль |
| --- | --- |
| `frontend/app.py` | Streamlit entrypoint, layout, password gate, session bootstrap. |
| `frontend/chat_pipeline.py` | Turn router: retriever turn vs follow-up chat LLM. |
| `frontend/chat_llm_pipeline.py` | Follow-up LLM answers over the currently selected protein card. |
| `frontend/session_identity.py` | Cookie-based `user_id` and `session_id`. |
| `frontend/session_db_adapter.py` | Read/merge/write adapter to `public.chat_sessions`. |
| `frontend/components/` | Chat, protein card, sidebar, domain/alignment views. |
| `backend/app_contracts/` | Pydantic contracts for chat, pipeline, session and protein card data. |
| `backend/app_services/bioseq_chat.py` | Service facade: `ChatTurnRequest` -> retriever/session -> `ChatTurnResult`. |
| `backend/app_services/retriever_pipeline.py` | Deterministic input extraction, DNA/protein classification, runtime retriever bridge. |
| `backend/app_services/protein_view_mapper.py` | UniProt/raw records -> `CandidateView` / `ProteinView`. |
| `backend/app_services/service_factory.py` | Builds runtime or mock chat service. |
| `backend/agents_core/retriever_agent/runtime_agent.py` | LangGraph-backed session state and sync to persistence. |
| `backend/bioseq_retriever/` | Actual bioseq retriever code inside backend. |

## First turn flow

First turn is the turn where `public.chat_sessions.working_memory.turn_count` is absent or zero.

```text
components/chat.py
  appends user message to st.session_state.messages
  -> app._handle_vector_db_submission()
  -> chat_pipeline.run_turn()
  -> _is_first_turn_in_session()
  -> BioSeqChatService.submit_turn(ChatTurnRequest)
  -> BioSeqRetrieverPipeline.run()
  -> deterministic_extract_and_classify()
  -> app/backend/bioseq_retriever/src/pipeline.run_bioseq_pipeline()
  -> src.search.search_top_k/search_dna_top_k()
  -> BIOSEQ_SEARCH_SERVICE_URL
  -> UniProt fetch + rerank
  -> protein_view_mapper -> CandidateView list
  -> runtime_agent.update_current_state()
  -> session_db_adapter.save_turn(update_candidates=True)
  -> UI replaces protein card
```

`BioSeqRetrieverPipeline` performs deterministic pre-extraction for the app contract, then bridges into `app/backend/bioseq_retriever/src/pipeline.py`. That inner retriever pipeline still has its own LLM extraction step, so the current runtime requires `MISTRAL_API_KEY` or `OPENAI_API_KEY` unless that inner step is refactored later.

## Follow-up flow

Follow-up turns are routed when `working_memory.turn_count > 0`.

```text
chat_pipeline.run_turn()
  -> chat_llm_pipeline.run_turn_chat_llm()
  -> Gemini proxy or OpenAI
  -> session_db_adapter.save_turn(update_candidates=False)
  -> UI keeps the previous candidates/card visible
```

The follow-up LLM receives:

- current selected protein context from `st.session_state.candidates`;
- recent chat messages from `st.session_state.messages`;
- a fixed protein-analysis system prompt.

Provider selection:

| Provider mode | Variables |
| --- | --- |
| Auto | `BIOSEQ_CHAT_LLM_PROVIDER=auto` |
| Gemini proxy | `BIOSEQ_LLM_PROXY_URL`, `BIOSEQ_LLM_PROXY_TOKEN` |
| OpenAI | `OPENAI_API_KEY`, optional `BIOSEQ_OPENAI_CHAT_MODEL` |

Important current behavior: if `SUPABASE_DB_URL` is missing, `session_db_adapter.is_persistent()` is false and `_is_first_turn_in_session()` falls back to retriever mode. That means reliable follow-up routing requires Postgres persistence.

## Search service

`backend/bioseq_retriever/services/search_service.py` is a separate FastAPI gateway for heavy biological retrieval work:

- loads ProtT5, HyenaDNA and rerank model;
- loads or builds FAISS indices;
- exposes `/search/protein`, `/search/dna`, `/rerank`;
- reads local artifacts from `BIOSEQ_H5_PATH`, `BIOSEQ_INDEX_PATH`, `BIOSEQ_ACCESSIONS_CACHE_PATH` and optional DNA equivalents.

The Streamlit app and session agent do not expose a separate HTTP backend. They run in-process and call the search service through `BIOSEQ_SEARCH_SERVICE_URL`.

The search service handles embedding/search/rerank. LLM provider keys are consumed by the retriever pipeline extraction layer, not by this FastAPI gateway.

## Persistence model

Persistence is created in `backend/agents_core/shared/services/persistence.py`.

With `SUPABASE_DB_URL`:

- LangGraph checkpointer: `PostgresSaver`;
- LangGraph store: `PostgresStore`;
- app session rows: `PostgresSessionRepository`;
- primary app table: `public.chat_sessions`.

Without `SUPABASE_DB_URL` or on init failure:

- LangGraph falls back to in-memory checkpointer/store;
- `NullSessionRepository` disables DB writes;
- sidebar history, restore and follow-up turn routing are not durable.

Two writers touch `public.chat_sessions` during retriever turns:

| Writer | Responsibility |
| --- | --- |
| `BioSeqRuntimeSessionAgent` | Compact LangGraph/session state: active accession, proteins, sequences, working memory summary. |
| `session_db_adapter.save_turn()` | UI transcript, full candidate cards, revealed card sections, turn counter. |

`session_db_adapter` does read/merge/write so agent fields are preserved while UI fields are appended.

## Contracts

The stable service boundary is `backend/app_contracts/`:

| Contract | Purpose |
| --- | --- |
| `ChatTurnRequest` | UI/user/session input into `BioSeqChatService`. |
| `ChatTurnResult` | Assistant text, candidates, revealed sections, session snapshot, warnings. |
| `BioSeqPipelineSnapshot` | Extracted input, sequence type, protein sequence, active accession, warnings/errors. |
| `ProteinView` / `CandidateView` | UI-ready protein card data. |
| `SessionSnapshot` | Current session state exposed by backend services. |

Frontend still normalizes backend candidates in `chat_pipeline._candidate_from_backend()` as a compatibility layer for older persisted rows.

## Data artifacts

Runtime artifacts are local files, not graph database data:

```text
data/per-protein.h5
data/per-protein.index
data/per-protein.accessions.json
data/per-gene.*              # optional DNA branch
```

These files are intentionally ignored by git. The intended source of truth is local cache, Hugging Face Dataset, object storage or a reproducible `data_prep/` run.

`data_prep/` lives outside `app/` as a project-level offline preparation pipeline. It should not be mixed with Streamlit runtime code.

## Testing layout

All tests are centralized under top-level `tests/`:

| Path | Scope |
| --- | --- |
| `tests/backend/bioseq_retriever/` | Runtime backend retriever tests. |
| `tests/depricated/bioseq_retriever/` | Deprecated snapshot tests. |
| `tests/scripts/` | Former script-level checks. |
| `tests/eval/` | Validation/evaluation datasets and runners. |

## Removed and legacy contours

Removed from active runtime:

- Neo4j settings and clients;
- `GraphRetrievalService`;
- old graph retriever agent files;
- `app/backend/graph_core/`.

Still present as legacy/reference:

- `depricated/bioseq_retriever/` - old root retriever snapshot;
- `frontend/embeddings_pipeline.py` - legacy ProtT5/FAISS frontend path;
- `frontend/vector_db_adapter.py` - legacy adapter;
- old frontend docs with `_old` suffix.

## Operational notes

- Main app command: `streamlit run app/frontend/app.py`.
- Search service command: `python app/backend/bioseq_retriever/services/search_service.py`.
- `BIOSEQ_BACKEND=mock` runs mock chat service; `BIOSEQ_BACKEND=runtime` is the normal mode.
- `APP_PASSWORD` enables simple Streamlit password gate.
- The app is not currently structured as a standalone FastAPI backend API. The only FastAPI process in the active runtime is the heavy search/rerank gateway.

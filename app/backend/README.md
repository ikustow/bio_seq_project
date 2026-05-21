# Backend Layer

Russian version: [README_RU.md](README_RU.md).

`app/backend` is the application backend of BioSeq Investigator. It accepts
structured requests from the Streamlit UI, decides whether a turn should run a
new retrieval search or a follow-up answer, maps UniProt results into UI-ready
contracts, and persists compact session state.

## Why This Layer Exists

The backend separates product logic from Streamlit. The frontend works with
simple DTOs (`ChatTurnRequest`, `ChatTurnResult`, `CandidateView`,
`ProteinView`) and does not need to know how LangGraph, FAISS, ProtT5, UniProt
lookup, Chat LLMs, or Supabase persistence are executed.

Practical benefits:

- the retriever can change without rewriting the UI;
- turn routing and session state are testable outside Streamlit;
- the same contract can serve Streamlit, a future API, and eval harnesses;
- heavy models and the search gateway stay separate from the user interface;
- session restore is reproducible because the backend writes compact state to
  Postgres.

## Runtime Flow

```text
ChatTurnRequest
  -> app_services.BioSeqChatService.submit_turn()
  -> route:
       direct UniProt lookup
       object follow-up
       sequence retrieval
       plain Chat LLM follow-up
  -> BioSeqRetrieverPipeline / ChatLLMService / SuggestedQuestionsService
  -> agents_core runtime session state
  -> ChatTurnResult
```

The sequence retrieval path goes deeper:

```text
BioSeqRetrieverPipeline
  -> app/backend/bioseq_retriever/src/pipeline.py
  -> search_service.py FastAPI gateway
  -> ProtT5/FAISS or BLAST/DNA path
  -> UniProt metadata
  -> rerank
  -> protein_view_mapper.uniprot_record_to_candidate()
```

## Directory Responsibilities

| Path | Responsibility |
| --- | --- |
| `app_contracts/` | Pydantic DTOs for the backend/frontend boundary: requests, responses, session snapshots, protein/candidate views. |
| `app_services/` | Application orchestration: turn routing, retriever adapter, Chat LLM, suggested questions, direct UniProt lookup. |
| `agents_core/` | LangGraph session agent, shared `AppContext`, persistence resources, compact session state. |
| `agents_core/docs/` | Technical notes for the agent layer, contracts, and Supabase/Postgres integration. |
| `bioseq_retriever/` | Runtime retriever library: LangGraph pipeline, UniProt fetch, search clients, rerank, FastAPI gateway. |
| `graph_core/` | Reserved/legacy graph data area; not the main runtime path. |

## Key Entrypoints

- `app_services/service_factory.py` - creates runtime or mock chat service
  according to `BIOSEQ_BACKEND`.
- `app_services/bioseq_chat.py` - main application service and turn router.
- `app_services/retriever_pipeline.py` - service-level wrapper around
  `bioseq_retriever`, deterministic DNA/protein extraction, and safety checks.
- `app_services/chat_llm.py` - follow-up LLM provider abstraction.
- `app_services/protein_view_mapper.py` - UniProt JSON to `CandidateView`.
- `app_services/uniprot_lookup.py` - direct accession/mnemonic ID lookup.
- `agents_core/retriever_agent/runtime_agent.py` - session agent state.
- `bioseq_retriever/services/search_service.py` - FastAPI gateway with heavy
  models and FAISS indexes.

## Contracts

Main public backend contract:

```python
from backend.app_contracts import ChatTurnRequest
from backend.app_services.service_factory import create_bioseq_chat_service

service = create_bioseq_chat_service()
result = service.submit_turn(
    ChatTurnRequest(
        message=">seq\nMENS...",
        session_id="session_001",
        user_id="local-user",
    )
)
```

Important DTOs:

- `ChatTurnRequest` - input for one user turn.
- `ChatTurnResult` - backend response consumed by the UI.
- `ObjectsPatch` - patch applied to the frontend object registry.
- `BioSeqPipelineSnapshot` - extraction/retrieval pipeline state.
- `SessionSnapshot` - compact session snapshot.
- `ProteinView` and `CandidateView` - UI-ready protein card models.

The source of truth for these contracts is `app_contracts/`; service behavior
is implemented in `app_services/`.

## Runtime Modes

`BIOSEQ_BACKEND`:

- `runtime`, `bioseq`, `bioseq_retriever` - live backend;
- `mock` - scripted service for UI/dev demos without heavy models.

Live backend creates:

1. `PersistenceResources` from `SUPABASE_DB_URL` or a null fallback.
2. `BioSeqRuntimeSessionAgent`.
3. `BioSeqRetrieverPipeline`.
4. `ChatLLMService`.
5. `SuggestedQuestionsService`.
6. `BioSeqChatService`.

## Search Gateway

`bioseq_retriever/services/search_service.py` starts a FastAPI app and keeps
heavy models in a separate process.

| Endpoint | Purpose |
| --- | --- |
| `POST /search/protein` | ProtT5 embedding + FAISS protein index. |
| `POST /search/dna` | DNA embedding/index path, when artifacts are available. |
| `POST /rerank` | Contextual rerank over UniProt records and the user's question. |

Locally, the gateway is usually started in a separate terminal:

```bash
python app/backend/bioseq_retriever/services/search_service.py
```

A Streamlit Space can start the gateway through the frontend supervisor when
`BIOSEQ_SPAWN_GATEWAY` is enabled.

## Persistence

When `SUPABASE_DB_URL` is set, the backend uses Postgres-compatible storage:

- LangGraph checkpointer/store for agent state;
- `public.chat_sessions` repository for sidebar/session restore;
- compact `working_memory` with latest candidates, objects, messages, and the
  selected accession/sequence.

If `SUPABASE_DB_URL` is not set or the connection fails, the backend continues
with a null repository, but session history is not persisted.

The persistence implementation lives in `agents_core/shared/services/persistence.py`.

## Configuration

Minimum live backend configuration:

```dotenv
BIOSEQ_BACKEND=runtime
BIOSEQ_ENABLE_RUNTIME_RETRIEVER=true
BIOSEQ_SEARCH_SERVICE_URL=http://localhost:8002
MISTRAL_API_KEY=...
# or OPENAI_API_KEY=...
```

Recommended:

```dotenv
SUPABASE_DB_URL=postgresql://user:password@host:5432/postgres
BIOSEQ_DATA_SOURCE=hf:radda-i/bioseq-data
BIOSEQ_CHAT_LLM_PROVIDER=auto
```

Full template: [../../example.env.txt](../../example.env.txt).

## Tests

Useful backend checks:

```bash
pytest tests/unit/backend/app_services
pytest tests/unit/backend/agents_core
pytest tests/backend/bioseq_retriever
python scripts/smoke_chat_pipeline_routing.py
python scripts/smoke_first_turn_router.py
```

For retrieval quality:

```bash
python tests/eval/run_all.py
```

Eval scenarios are documented in [../../tests/eval/README.md](../../tests/eval/README.md).

## Technical Links

Internal:

- [BioSeq Retriever](bioseq_retriever/README.md)

External:

- [FastAPI docs](https://fastapi.tiangolo.com/)
- [LangGraph docs](https://docs.langchain.com/oss/python/langgraph)
- [FAISS docs](https://faiss.ai/index.html)
- [ProtT5 model card](https://huggingface.co/Rostlab/prot_t5_xl_uniref50)
- [UniProt API help](https://www.uniprot.org/help/api)
- [Supabase Postgres docs](https://supabase.com/docs/guides/database/overview)

# Agent Core Documentation

This directory documents the current agent system in `app/backend/agents_core`.

## What Exists Today

`agents_core` is the layer for LangGraph agents and shared infrastructure for context, memory, and graph database access.

Current structure:

| Path | Purpose |
| --- | --- |
| `retriever_agent/` | DB-only LangGraph pipeline for finding similar proteins by sequence or accession context. |
| `shared/config.py` | Env settings, Neo4j profiles, session state limits. |
| `shared/models.py` | Shared context, session, and persistence resource models. |
| `shared/services/graph.py` | Thin Neo4j client and read-only Cypher guard helper. |
| `shared/services/persistence.py` | LangGraph checkpointer/store and `chat_sessions` repository. |
| `shared/services/session_state.py` | Session patch extraction from regular chat/tool agent messages. |
| `docs/` | Russian documentation. |
| `docs_eng/` | English documentation. |

`app/backend/app_services/service_factory.py` also references `backend.agents_core.session_agent.agent.SessionGraphAgent`, but that package is not present in the current `agents_core` tree. The working agent in this directory is `retriever_agent`.

## Overall Architecture

The data flow for an agent looks like this:

1. The external layer creates `AppContext`.
2. The factory creates a Neo4j client, graph retrieval service, and persistence resources.
3. The agent compiles a LangGraph pipeline with `persistence.checkpointer`.
4. Every agent call uses `context.session_id` as the LangGraph `thread_id`.
5. LangGraph stores the full state for that thread through the checkpointer.
6. The agent separately builds a compact session patch and writes it to `public.chat_sessions`.

`AppContext`:

```python
class AppContext(BaseModel):
    user_id: str
    session_id: str
    workspace_id: str | None = None
    user_role: str | None = None
```

`session_id` is used as:

- the external session id;
- the LangGraph `thread_id`;
- the `thread_id` in the `public.chat_sessions` row.

## Persistence Modes

Persistence is created through:

```python
create_persistence_resources(os.getenv("SUPABASE_DB_URL"), exit_stack)
```

If `SUPABASE_DB_URL` is set and the dependencies are available, `mode="postgres"` is enabled:

- `PostgresSaver` - short-term LangGraph state/checkpoints;
- `PostgresStore` - long-term LangGraph store;
- `PostgresSessionRepository` - compact session row in `public.chat_sessions`.

If `SUPABASE_DB_URL` is not set or Postgres/Supabase initialization fails, `mode="memory"` is enabled:

- `InMemorySaver`;
- `InMemoryStore`;
- `NullSessionRepository`;
- a warning in `PersistenceResources.warnings`.

Important: memory mode loses state after the process restarts.

## Main Classes And Models

| Class/model | File | Purpose |
| --- | --- | --- |
| `AppContext` | `shared/models.py` | User and session context. Passed into every agent call. |
| `PersistenceResources` | `shared/models.py` | Groups `checkpointer`, `store`, `session_repository`, `mode`, and `warnings`. |
| `SessionPatch` | `shared/models.py` | Canonical compact state that can be stored in `chat_sessions`. |
| `SessionRow` | `shared/models.py` | Full `public.chat_sessions` row. |
| `SessionStateView` | `shared/models.py` | Simplified view used by derive logic over message history. |
| `Neo4jGraphClient` | `shared/services/graph.py` | Executes Cypher through the Neo4j driver. |
| `PostgresSessionRepository` | `shared/services/persistence.py` | Reads/writes `public.chat_sessions`. |
| `BioSeqRetrieverGraphAgent` | `retriever_agent/agent.py` | Public wrapper around the retriever LangGraph pipeline. |
| `GraphRetrievalService` | `app_services/graph_retrieval.py` | Domain layer for searching proteins/candidates in Neo4j. |

## Tools And Service Operations

The current `retriever_agent` does not use a LangChain tool-calling loop and does not define functions as `@tool`. Instead, it has a fixed LangGraph pipeline where node functions and domain service methods play the role of "tools".

Graph nodes:

| Node | Operation type |
| --- | --- |
| `extract` | LLM/deterministic structured extraction. |
| `resolve_file` | Controlled miss for runtime filepath input. |
| `use_raw` | Sequence normalization. |
| `translate` | DNA -> protein translation. |
| `pass_protein` | Protein normalization. |
| `rank` | Exact graph/hash retrieval. |
| `rerank` | Context-aware candidate reranking. |

Available domain operations in `GraphRetrievalService`:

| Method | Purpose |
| --- | --- |
| `resolve_input` | Searches protein by accession/gene/entry/name. |
| `find_by_sequence_hash` | Searches protein by protein sequence hash. |
| `find_encoded_protein_by_sequence_hash` | Searches protein encoded by DNA sequence, with fallback to translated protein hash. |
| `retrieve_candidates` | Returns target protein and similar neighbors from Neo4j. |
| `get_protein_view` | Returns one `ProteinView`. |
| `get_candidate_context` | Returns compact neighbor context. |

## Env Options

Common variables:

| Env | Meaning |
| --- | --- |
| `SUPABASE_DB_URL` | Supabase Postgres connection string. Required for production persistence. |
| `APP_USER_ID` | User id for `AppContext`. |
| `APP_SESSION_ID` | Session/thread id. |
| `APP_WORKSPACE_ID` | Workspace id, optional. |
| `APP_USER_ROLE` | User role, optional. |
| `NEO4J_PROFILE` | `local` or `cloud`. |
| `NEO4J_*` / `NEO4J_LOCAL_*` / `NEO4J_CLOUD_*` | URI, database, username, password, insecure flag. |
| `BIOSEQ_LLM_PROVIDER` | `openai` or `mistral` for LLM extraction. |
| `OPENAI_API_KEY` / `MISTRAL_API_KEY` | API keys for the extractor. |
| `OPENAI_MODEL` / `MISTRAL_MODEL` | Extractor model. |
| `BIOSEQ_INPUT_EXTRACTOR` | In `pipeline_interface.py`: `llm` enables LLM extraction; otherwise deterministic extraction is used. |

## Documents

- [retriever_agent.md](retriever_agent.md) - detailed documentation for `retriever_agent`.
- [app_services_contracts.md](app_services_contracts.md) - how agents connect to `app_services` and `app_contracts`.
- [adding_agents_supabase.md](adding_agents_supabase.md) - how to connect context, LangGraph memory, and Supabase session storage to new agents.

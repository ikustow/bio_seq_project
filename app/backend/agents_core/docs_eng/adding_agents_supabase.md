# Adding New Agents With Context And Supabase Persistence

This document describes the current local pattern for connecting new agents to `AppContext`, LangGraph memory, and Supabase/Postgres session storage.

## Minimal New Agent Contract

A new agent should accept `AppContext` in its public methods and always use `context.session_id` as the LangGraph `thread_id`.

Recommended public API:

```python
class MyAgent:
    @property
    def warnings(self) -> list[str]:
        return self._persistence.warnings

    @property
    def persistence_mode(self) -> str:
        return self._persistence.mode

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]:
        config = {"configurable": {"thread_id": context.session_id}}
        result = self._graph.invoke(initial_state(message), config=config)
        current_state = dict(self._graph.get_state(config).values)
        self._sync_session(context, current_state)
        return result, current_state

    def get_current_state(self, context: AppContext) -> dict[str, Any]:
        config = {"configurable": {"thread_id": context.session_id}}
        return dict(self._graph.get_state(config).values)

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]:
        config = {"configurable": {"thread_id": context.session_id}}
        self._graph.update_state(config, patch)
        current_state = dict(self._graph.get_state(config).values)
        self._sync_session(context, current_state)
        return current_state
```

## Creating Persistence Resources

The factory must keep `ExitStack` alive for as long as the agent/service lives.

```python
from contextlib import ExitStack
from backend.agents_core.shared.services.persistence import create_persistence_resources

exit_stack = ExitStack()
persistence = create_persistence_resources(os.getenv("SUPABASE_DB_URL"), exit_stack)
exit_stack.callback(persistence.session_repository.close)

agent = MyAgent(..., persistence=persistence)
agent._exit_stack = exit_stack
```

Why `ExitStack` matters:

- `PostgresSaver.from_conn_string(...)` and `PostgresStore.from_conn_string(...)` open context-managed resources;
- if the stack is closed right after the factory returns, the checkpointer/store will stop working;
- this is why `service_factory.py` stores `_exit_stack` on the agent/service.

## Compiling LangGraph

For thread-scoped short-term memory:

```python
graph = builder.compile(checkpointer=persistence.checkpointer)
```

If the agent needs long-term memory across different sessions, also pass the store at compile time:

```python
graph = builder.compile(
    checkpointer=persistence.checkpointer,
    store=persistence.store,
)
```

The current `retriever_agent` only passes the checkpointer. `persistence.store` is created, but not used directly.

## Using thread_id

Always pass config to `invoke`, `get_state`, and `update_state`:

```python
config = {"configurable": {"thread_id": context.session_id}}
```

Without `thread_id`, LangGraph state will not be correctly tied to a session. In the current project, `session_id` is the only stable key shared by the API, LangGraph checkpoints, and `chat_sessions`.

## Session Storage In `public.chat_sessions`

The LangGraph checkpointer stores the full technical state. `public.chat_sessions` stores a compact application snapshot for the UI/API.

Write to it through:

```python
saved_session = self._persistence.session_repository.get_session(context.session_id)
patch = derive_or_build_session_patch(current_state)
merged = merge_with_saved(saved_session, patch)
self._persistence.session_repository.upsert_session(context, merged)
```

For a simple chat/tool agent, you can reuse the shared helper:

```python
from backend.agents_core.shared.services.session_state import derive_session_patch

patch = derive_session_patch(current_state)
self._persistence.session_repository.upsert_session(context, patch)
```

`derive_session_patch` can:

- read `messages`;
- extract protein records from JSON tool output;
- extract protein-like sequences from text;
- update `working_memory.message_count`;
- set `active_accession`, `active_sequence_id`, `working_set_ids`, and summaries.

If the agent has a different domain state shape, it is better to implement a custom `_derive_session_patch`, like in `retriever_agent/agent.py`, while keeping the same `SessionPatch` fields.

## SessionPatch Fields

The stored patch should match `SessionPatch`:

| Field | How to use it |
| --- | --- |
| `session_summary` | Short human-readable summary of the current session/latest step. |
| `proteins` | List of `ProteinRecord`. |
| `sequences` | List of `SequenceRecord`. |
| `working_memory` | Compact machine-readable memory for UI/next steps. |
| `active_sequence_id` | Current selected sequence. |
| `active_accession` | Current selected protein accession. |
| `last_analysis_summary` | Short summary of the latest analysis. |
| `working_set_ids` | Stable set of accession/sequence ids for current work. |
| `current_mode` | Mode/agent name, for example `bioseq_retriever_langgraph`. |
| `last_tool_results_summary` | Short summary of the latest tool/db output. |

## Supabase/Postgres Requirements

`create_persistence_resources` automatically calls:

```python
checkpointer.setup()
store.setup()
```

This creates LangGraph's internal checkpoint/store tables.

However, the application table `public.chat_sessions` must exist separately. `PostgresSessionRepository` expects these columns:

```sql
create table if not exists public.chat_sessions (
    session_id text primary key,
    thread_id text not null,
    user_id text not null,
    workspace_id text,
    user_role text,
    session_summary text,
    proteins jsonb not null default '[]'::jsonb,
    sequences jsonb not null default '[]'::jsonb,
    working_memory jsonb not null default '{}'::jsonb,
    active_sequence_id text,
    active_accession text,
    last_analysis_summary text,
    working_set_ids jsonb not null default '[]'::jsonb,
    current_mode text,
    last_tool_results_summary text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
```

If the table is exposed through the Supabase Data API, enable RLS and create policies that match the real access model. The backend connection string used by the agent must be a server-side secret and must not be exposed to the frontend.

## Factory Checklist For A New Agent

1. Load `.env` through `load_env_file(DEFAULT_ENV_PATH)`.
2. Create external clients: Neo4j, LLM, tools, domain services.
3. Create `ExitStack`.
4. Call `create_persistence_resources(os.getenv("SUPABASE_DB_URL"), exit_stack)`.
5. Register `exit_stack.callback(persistence.session_repository.close)`.
6. Pass `persistence` into the agent.
7. Store `exit_stack` on a long-lived agent/service object.
8. In every public call, build `AppContext` and config with `thread_id=context.session_id`.
9. After `invoke`/`update_state`, synchronize the compact session patch into `chat_sessions`.
10. Return `warnings` so the UI/API can show memory-mode fallback or persistence errors.

## Minimal Skeleton

```python
from typing import Any, Annotated, TypedDict
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, AIMessage

from backend.agents_core.shared.models import AppContext, PersistenceResources
from backend.agents_core.shared.services.session_state import derive_session_patch


class MyState(TypedDict):
    messages: Annotated[list[Any], add_messages]
    current_mode: str | None
    working_memory: dict[str, Any]


class MyAgent:
    def __init__(self, persistence: PersistenceResources) -> None:
        self._persistence = persistence
        builder = StateGraph(MyState)
        builder.add_node("respond", self._respond)
        builder.set_entry_point("respond")
        builder.add_edge("respond", END)
        self._graph = builder.compile(checkpointer=persistence.checkpointer)

    @property
    def warnings(self) -> list[str]:
        return self._persistence.warnings

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]:
        config = {"configurable": {"thread_id": context.session_id}}
        result = self._graph.invoke(
            {
                "messages": [HumanMessage(content=message)],
                "current_mode": "my_agent",
                "working_memory": {},
            },
            config=config,
        )
        self._graph.update_state(config, {"messages": [AIMessage(content="Done.")]})
        current_state = dict(self._graph.get_state(config).values)
        self._persistence.session_repository.upsert_session(
            context,
            derive_session_patch(current_state),
        )
        return result, current_state

    def _respond(self, state: MyState) -> dict[str, Any]:
        return {
            "current_mode": "my_agent",
            "working_memory": {
                **(state.get("working_memory") or {}),
                "last_sync_source": "my_agent",
            },
        }
```

## Practical Rules

- Do not write directly to `chat_sessions` from node functions. Prefer synchronizing the session patch at the public method boundary after the graph run completes.
- For lists in LangGraph state, use a reducer such as `add_messages`; otherwise new values overwrite old ones.
- Do not store large raw payloads in `working_memory`; prefer compact summaries, ids, and counts.
- Use `SUPABASE_DB_URL` in production; memory mode is only suitable for local development.
- Reset/delete session needs a separate design: LangGraph checkpoints/store and `chat_sessions` are different storage layers.

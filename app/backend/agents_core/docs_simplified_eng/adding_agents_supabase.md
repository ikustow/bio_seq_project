# How To Add A New Agent And Connect Supabase

This document explains, in plain language, how to connect new agents to:

- `AppContext`;
- LangGraph memory;
- Supabase/Postgres persistence;
- `public.chat_sessions`.

## What We Want

A new agent should be able to:

1. receive a user message;
2. know which session it is working in;
3. save its state;
4. restore state by `session_id`;
5. write a short snapshot for UI;
6. work locally even without Supabase, while warning about that.

## Minimal Idea

Every agent call has context:

```python
AppContext(
    user_id="...",
    session_id="...",
    workspace_id="...",
    user_role="...",
)
```

The most important field is:

```python
session_id
```

Because it is used as:

```text
session_id
  = external session id
  = LangGraph thread_id
  = thread_id in public.chat_sessions
```

## Minimal Agent Contract

A new agent should have roughly these methods:

```python
class MyAgent:
    @property
    def warnings(self) -> list[str]:
        return self._persistence.warnings

    @property
    def persistence_mode(self) -> str:
        return self._persistence.mode

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]:
        ...

    def get_current_state(self, context: AppContext) -> dict[str, Any]:
        ...

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]:
        ...
```

Plain meaning:

| Method | Why it is needed |
| --- | --- |
| `warnings` | Report persistence warnings or fallback behavior. |
| `persistence_mode` | Show whether `postgres` or `memory` is used. |
| `invoke` | Run the agent. |
| `get_current_state` | Get current session state. |
| `update_current_state` | Manually change session state. |

## How To Create Persistence

The project already has a helper:

```python
create_persistence_resources(...)
```

It lives in:

```text
app/backend/agents_core/shared/services/persistence.py
```

Usage:

```python
from contextlib import ExitStack
from backend.agents_core.shared.services.persistence import create_persistence_resources

exit_stack = ExitStack()
persistence = create_persistence_resources(os.getenv("SUPABASE_DB_URL"), exit_stack)
exit_stack.callback(persistence.session_repository.close)

agent = MyAgent(..., persistence=persistence)
agent._exit_stack = exit_stack
```

## Why ExitStack Is Needed

`PostgresSaver` and `PostgresStore` open connections/contexts.

If those are closed right after the agent is created, the agent will not be able to save state later.

So `ExitStack` must live as long as the agent or service.

The project uses this simple trick:

```python
agent._exit_stack = exit_stack
```

or:

```python
service._exit_stack = exit_stack
```

This prevents Python from closing resources too early.

## What create_persistence_resources Returns

It returns:

```python
PersistenceResources(
    checkpointer=...,
    store=...,
    session_repository=...,
    mode="postgres" or "memory",
    warnings=[...],
)
```

Meaning:

| Field | Plain explanation |
| --- | --- |
| `checkpointer` | Saves full LangGraph state. |
| `store` | Long-term LangGraph memory. |
| `session_repository` | Reads/writes `public.chat_sessions`. |
| `mode` | Where memory is stored: Postgres or memory. |
| `warnings` | What failed or which fallback was enabled. |

## Two Persistence Modes

### Postgres Mode

Enabled if:

```text
SUPABASE_DB_URL is set
```

and required dependencies are installed:

```text
langgraph-checkpoint-postgres
psycopg[binary]
```

Then the system uses:

- `PostgresSaver`;
- `PostgresStore`;
- `PostgresSessionRepository`.

### Memory Mode

Enabled if:

- `SUPABASE_DB_URL` is missing;
- or dependencies are missing;
- or Supabase/Postgres connection fails.

Then the system uses:

- `InMemorySaver`;
- `InMemoryStore`;
- `NullSessionRepository`.

Important:

```text
memory mode loses data after process restart
```

## How To Connect LangGraph Checkpointer

When building a graph:

```python
builder = StateGraph(MyState)
```

finish with:

```python
self._graph = builder.compile(checkpointer=persistence.checkpointer)
```

This tells LangGraph:

```text
Save state after graph steps.
```

If the agent needs long-term store memory:

```python
self._graph = builder.compile(
    checkpointer=persistence.checkpointer,
    store=persistence.store,
)
```

The current retriever agent only uses `checkpointer`.

## How To Use thread_id

In every public agent method, create:

```python
config = {"configurable": {"thread_id": context.session_id}}
```

Then use it:

```python
result = self._graph.invoke(initial_state, config=config)
current_state = dict(self._graph.get_state(config).values)
self._graph.update_state(config, patch)
```

If you forget `thread_id`, LangGraph will not properly know which session the state belongs to.

## How To Save Compact Session In chat_sessions

Full LangGraph state can be large and technical.

The UI usually does not need all of it.

So after running the agent, build a short patch:

```python
patch = derive_session_patch(current_state)
```

and write it:

```python
self._persistence.session_repository.upsert_session(context, patch)
```

For simple chat/tool agents, you can use the shared helper:

```python
from backend.agents_core.shared.services.session_state import derive_session_patch
```

If the agent has a custom state shape, it is better to implement a custom `_derive_session_patch`.

That is how it is done in:

```text
app/backend/agents_core/retriever_agent/agent.py
```

## What SessionPatch Is

`SessionPatch` is the short-state format.

Fields:

| Field | Plain explanation |
| --- | --- |
| `session_summary` | Short description of the current session. |
| `proteins` | Proteins the agent found or is using. |
| `sequences` | Sequences the agent found or is using. |
| `working_memory` | Small machine-readable memory for next steps. |
| `active_sequence_id` | Which sequence is active. |
| `active_accession` | Which protein accession is active. |
| `last_analysis_summary` | Short summary of the latest analysis. |
| `working_set_ids` | Object ids the user is working with. |
| `current_mode` | Which mode/agent is currently active. |
| `last_tool_results_summary` | Short summary of the latest tool/db result. |

## The public.chat_sessions Table

LangGraph creates its own technical tables through:

```python
checkpointer.setup()
store.setup()
```

But the application table:

```text
public.chat_sessions
```

must be created separately.

Minimal schema:

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

If the table is exposed through the Supabase Data API, think about RLS.

Simple rule:

```text
server-side connection string must not reach the frontend
```

## Minimal Agent Example

This skeleton does nothing smart, but shows the correct shape:

```python
from typing import Any, Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

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

    @property
    def persistence_mode(self) -> str:
        return self._persistence.mode

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

    def get_current_state(self, context: AppContext) -> dict[str, Any]:
        config = {"configurable": {"thread_id": context.session_id}}
        return dict(self._graph.get_state(config).values)

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]:
        config = {"configurable": {"thread_id": context.session_id}}
        self._graph.update_state(config, patch)
        current_state = dict(self._graph.get_state(config).values)
        self._persistence.session_repository.upsert_session(
            context,
            derive_session_patch(current_state),
        )
        return current_state

    def _respond(self, state: MyState) -> dict[str, Any]:
        return {
            "current_mode": "my_agent",
            "working_memory": {
                **(state.get("working_memory") or {}),
                "last_sync_source": "my_agent",
            },
        }
```

## Checklist For Adding A New Agent

1. Create an agent package in `app/backend/agents_core`.
2. Define the agent `State`.
3. Create `StateGraph`.
4. Add nodes.
5. Add edges.
6. Compile graph with `persistence.checkpointer`.
7. In public methods, use `thread_id=context.session_id`.
8. After running, synchronize `chat_sessions`.
9. Return `warnings`.
10. Create a factory in `app_services` that assembles agent dependencies.

## Common Mistakes

### Mistake: Forgetting thread_id

Bad:

```python
self._graph.invoke(state)
```

Good:

```python
self._graph.invoke(state, config={"configurable": {"thread_id": context.session_id}})
```

### Mistake: Closing ExitStack Too Early

Bad:

```python
with ExitStack() as stack:
    persistence = create_persistence_resources(url, stack)
    agent = MyAgent(persistence)
return agent
```

After leaving `with`, resources are closed.

Better:

```python
exit_stack = ExitStack()
persistence = create_persistence_resources(url, exit_stack)
agent = MyAgent(persistence)
agent._exit_stack = exit_stack
return agent
```

### Mistake: Writing Huge Data Into working_memory

`working_memory` should stay compact.

Prefer storing:

- ids;
- counts;
- short summaries;
- last mode;
- last source.

Do not store huge raw results there.

### Mistake: Thinking Supabase chat_sessions Is Created Automatically

LangGraph tables are created through `setup()`.

But:

```text
public.chat_sessions
```

is an application table. It must be created separately.

## Shortest Version

For a new agent to work properly with memory:

```text
1. Pass PersistenceResources into it
2. Compile graph with checkpointer
3. Always use thread_id=context.session_id
4. After invoke/update_state, write compact patch into chat_sessions
5. Keep ExitStack alive
```


# How Agents Connect To app_services And app_contracts

This document explains why `app_services` and `app_contracts` exist when we already have `agents_core`.

Main idea:

```text
agents_core should not directly be the UI/API.
agents_core does the agent work.
app_services connects that work to the application.
app_contracts describes application inputs and outputs.
```

## Three Layers In Plain Words

### `app_contracts`

This folder contains Pydantic models.

Think of it as:

```text
app_contracts = the data shape contract
```

For example:

- what comes from the UI;
- what is returned to the UI;
- what a protein card looks like;
- what a candidate looks like;
- what a session snapshot looks like.

### `app_services`

This is the application logic layer.

It is not the agent itself, but it decides:

- which agent to create;
- which clients to pass into it;
- how to turn a request into `AppContext`;
- when to call the agent;
- when not to call the agent;
- how to build a UI response.

### `agents_core`

This is the agent layer.

It contains:

- LangGraph pipeline;
- agent state;
- persistence;
- context models;
- session memory.

## Overall Flow

```text
UI/API
  -> ChatTurnRequest from app_contracts
  -> BioSeqChatService from app_services
  -> AppContext
  -> Agent from agents_core
  -> GraphRetrievalService
  -> Neo4j
  -> Agent state
  -> SessionSnapshot
  -> ChatTurnResult from app_contracts
```

## Why The Agent Does Not Accept ChatTurnRequest Directly

Because `ChatTurnRequest` is the external application format.

Inside the agent, a simpler context is needed:

```python
class AppContext(BaseModel):
    user_id: str
    session_id: str
    workspace_id: str | None = None
    user_role: str | None = None
```

So the agent cares about:

- who the user is;
- which session this is;
- which workspace;
- which role.

Fields like:

- `selected_candidate_index`;
- `ui_context`;
- response shape for UI;

belong to `app_services`, not the agent.

## What ChatTurnRequest Is

`ChatTurnRequest` is in:

```text
app/backend/app_contracts/chat.py
```

It describes the incoming request.

Fields:

| Field | Plain explanation |
| --- | --- |
| `message` | User text. |
| `session_id` | Session id. Very important. |
| `user_id` | User id. |
| `workspace_id` | Workspace id, if present. |
| `user_role` | User role, if present. |
| `selected_accession` | If the user selected a specific protein accession. |
| `selected_candidate_index` | Which candidate is selected in UI. |
| `ui_context` | Extra UI context. Currently barely used. |

## How ChatTurnRequest Becomes AppContext

`BioSeqChatService` has this helper:

```python
def _context_from_request(request: ChatTurnRequest) -> AppContext:
    return AppContext(
        user_id=request.user_id,
        session_id=request.session_id,
        workspace_id=request.workspace_id,
        user_role=request.user_role,
    )
```

The service takes the external model and turns it into internal agent context.

## What Contract An Agent Should Support

`BioSeqChatService` does not need to know the exact agent class.

It expects the agent to support:

```python
class SessionGraphAgent(Protocol):
    @property
    def warnings(self) -> list[str]: ...

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def get_current_state(self, context: AppContext) -> dict[str, Any]: ...

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]: ...
```

In plain words:

| Method | What it should do |
| --- | --- |
| `warnings` | Return warnings, for example that persistence is running in memory mode. |
| `invoke` | Run the agent on the user's message. |
| `get_current_state` | Return current session state. |
| `update_current_state` | Manually change session state. |

If a new agent supports these methods, it is easier to plug into `BioSeqChatService`.

## What BioSeqChatService Does

File:

```text
app/backend/app_services/bioseq_chat.py
```

Main method:

```python
submit_turn(request: ChatTurnRequest) -> ChatTurnResult
```

It chooses one of several scenarios.

## Scenario 1: User Selected An Accession

If request contains:

```python
selected_accession
```

the service calls:

```python
agent.update_current_state(context, {"active_accession": request.selected_accession})
```

It tells the agent:

```text
This protein is now active.
```

Then the service fetches candidates itself:

```python
GraphRetrievalService.retrieve_candidates(...)
```

and returns the response.

In this scenario, full `agent.invoke` is not needed.

## Scenario 2: User Sent A Sequence Or Filepath

The service first runs the compatibility pipeline:

```python
BioSeqRetrieverPipeline
```

This is not the exact same thing as `retriever_agent`; it is a service-level pipeline that quickly handles sequence-like input.

If the pipeline finds candidates, the service can return them without calling the main agent `invoke`.

If the pipeline gets a controlled miss, the service returns a graceful message.

At the same time, the service patches agent state:

```python
agent.update_current_state(context, state_patch)
```

So the agent session still remembers what happened.

## Scenario 3: Normal Message

If the request is not an accession selection and not sequence-like input, the service calls:

```python
result, state = agent.invoke(request.message, context)
```

Then it:

1. extracts assistant message from the agent result;
2. checks `active_accession`;
3. if `active_accession` is missing, tries `GraphRetrievalService.resolve_input`;
4. if an accession is found, updates state;
5. fetches candidates;
6. builds `ChatTurnResult`.

## What ChatTurnResult Is

`ChatTurnResult` is the response returned outward.

It contains:

| Field | Meaning |
| --- | --- |
| `session_id` | Session id. |
| `assistant_message` | Assistant response text. |
| `candidates` | List of protein candidates. |
| `selected_candidate_index` | Which candidate is selected. |
| `revealed_sections` | Which card sections can be shown. |
| `session` | Session snapshot. |
| `pipeline` | Pipeline snapshot, if it ran. |
| `warnings` | Warnings. |

## What SessionSnapshot Is

`SessionSnapshot` is simplified state for UI.

It contains:

- `session_id`;
- `user_id`;
- `workspace_id`;
- `user_role`;
- `active_accession`;
- `active_sequence_id`;
- `current_mode`;
- `proteins`;
- `sequences`;
- `working_memory`;
- `message_history`.

It is not the full LangGraph state.
It is a convenient application-facing view.

## What ProteinView And CandidateView Are

`ProteinView` is a UI-ready protein description.

It has:

- accession;
- name;
- gene;
- organism;
- function text;
- disease;
- domains;
- keywords;
- GO terms;
- PubMed ids;
- sequence.

`CandidateView` is a protein plus scores:

- `protein`;
- `match_score`;
- `rank`;
- `similarity_score`;
- `context_score`;
- `evidence`.

## Where GraphRetrievalService Appears

`GraphRetrievalService` is in:

```text
app/backend/app_services/graph_retrieval.py
```

It is the bridge between agents/services and Neo4j.

It can:

| Method | What it does |
| --- | --- |
| `resolve_input` | Searches protein by accession/gene/name. |
| `find_by_sequence_hash` | Searches protein by protein sequence hash. |
| `find_encoded_protein_by_sequence_hash` | For DNA, finds the protein encoded by the sequence. |
| `retrieve_candidates` | Returns target protein and neighbors. |
| `get_protein_view` | Returns one protein card. |
| `get_candidate_context` | Returns compact neighbor context. |

## Why GraphRetrievalService Lives In app_services But Is Used By The Agent

Ideally, `agents_core` could be fully independent.

But currently `retriever_agent` reuses existing domain logic from `app_services`.

That means:

```text
retriever_agent
  -> GraphRetrievalService
  -> Neo4jGraphClient
  -> Neo4j
```

This avoids duplicating Cypher and mapping logic.

## Where Neo4j Records Become UI Models

This happens in:

```text
app/backend/app_services/protein_view_mapper.py
```

Flow:

```text
Neo4j record
  -> protein_record_to_view(...)
  -> ProteinView
  -> neighbor_record_to_candidate(...)
  -> CandidateView
```

## What service_factory Does

File:

```text
app/backend/app_services/service_factory.py
```

It assembles all dependencies:

```text
read .env
  -> resolve Neo4j settings
  -> create Neo4jGraphClient
  -> create GraphRetrievalService
  -> create PersistenceResources
  -> create Agent
  -> create BioSeqChatService
```

For the retriever agent:

```python
create_bioseq_retriever_graph_agent(...)
```

For chat service:

```python
create_bioseq_chat_service()
```

## Important Note About graph-backed chat service

`service_factory.py` contains:

```python
from backend.agents_core.session_agent.agent import SessionGraphAgent
```

But the `session_agent` folder is currently missing.

So if `BIOSEQ_BACKEND=graph` is enabled, the code may expect an agent that is not present in the current tree.

The working and documented agent today is:

```text
app/backend/agents_core/retriever_agent
```

## Rule For New Agents

When adding a new agent:

1. Do not make the agent accept `ChatTurnRequest`.
2. Let the agent accept `AppContext`.
3. Let `app_services` decide how to convert request into context.
4. Do not expose internal LangGraph state directly to UI.
5. Return external data through `ChatTurnResult`, `SessionSnapshot`, `CandidateView`, `ProteinView`.
6. If Neo4j is needed, pass a service into the agent instead of creating a driver inside a node.

## Simple Mental Model

```text
app_contracts = input/output shape
app_services = dispatcher and response builder
agents_core = the working agent mechanism
GraphRetrievalService = bridge to Neo4j
```


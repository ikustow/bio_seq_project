# Agents, App Services, And App Contracts

This document explains how `agents_core` is connected to `app/backend/app_services` and `app/backend/app_contracts`.

## Layers

```text
UI / API
  -> app_contracts
  -> app_services
  -> agents_core
  -> Neo4j / Supabase / LLM providers
```

Layer responsibilities:

| Layer | Role |
| --- | --- |
| `app_contracts` | Pydantic DTOs for the external API/UI: request, response, session snapshot, protein/candidate views. |
| `app_services` | Application orchestration: agent creation, client wiring, request -> context -> response adaptation. |
| `agents_core` | LangGraph agents, shared context models, persistence, graph state, and agent runtime. |

In practice, `agents_core/retriever_agent` currently depends on part of `app_services`: it reuses `GraphRetrievalService` and helper functions from `retriever_pipeline.py`.

## How A Request Reaches An Agent

The main chat flow lives in `app_services/bioseq_chat.py`.

```text
ChatTurnRequest
  -> _context_from_request(...)
  -> AppContext
  -> agent.invoke(...) / agent.update_current_state(...)
  -> internal LangGraph state
  -> SessionSnapshot
  -> ChatTurnResult
```

`ChatTurnRequest` from `app_contracts/chat.py` contains:

| Field | How it is used |
| --- | --- |
| `message` | User prompt for the pipeline/agent. |
| `session_id` | Main session/thread id. Goes into `AppContext.session_id`. |
| `user_id` | Goes into `AppContext.user_id` and the session snapshot. |
| `workspace_id` | Optional context/snapshot field. |
| `user_role` | Optional context/snapshot field. |
| `selected_accession` | If set, the service does not ask the agent and instead patches `active_accession`. |
| `selected_candidate_index` | UI selection index in `ChatTurnResult`. |
| `ui_context` | Currently barely used in the agent flow; reserved for UI-side context. |

Conversion into agent context:

```python
def _context_from_request(request: ChatTurnRequest) -> AppContext:
    return AppContext(
        user_id=request.user_id,
        session_id=request.session_id,
        workspace_id=request.workspace_id,
        user_role=request.user_role,
    )
```

## Agent Protocol In app_services

`BioSeqChatService` is not bound to a specific agent class. It expects an object matching this protocol:

```python
class SessionGraphAgent(Protocol):
    @property
    def warnings(self) -> list[str]: ...

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def get_current_state(self, context: AppContext) -> dict[str, Any]: ...

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]: ...
```

A new agent that should work through `BioSeqChatService` must support this minimal contract.

## What BioSeqChatService Does

`BioSeqChatService.submit_turn(request)` chooses one of these branches:

1. If `selected_accession` is present:
   - calls `agent.update_current_state(context, {"active_accession": ...})`;
   - fetches candidates through `GraphRetrievalService.retrieve_candidates`;
   - returns `ChatTurnResult`.

2. If the prompt looks like a sequence/filepath:
   - runs the compatibility `BioSeqRetrieverPipeline`;
   - stores the pipeline snapshot in agent state through `update_current_state`;
   - if candidates exist, returns them without calling the main agent `invoke`;
   - if there is a controlled miss/error, returns a controlled response.

3. Otherwise:
   - calls `agent.invoke(request.message, context)`;
   - gets the assistant message from the last agent message;
   - if `active_accession` is missing, tries `GraphRetrievalService.resolve_input`;
   - returns candidates and `SessionSnapshot`.

## How app_contracts Are Used By Agents

Agents should rarely depend directly on external DTOs. The current retriever agent depends on them indirectly:

- `GraphRetrievalService.retrieve_candidates(...)` returns `list[CandidateView]`;
- the retriever agent stores candidates in `GraphState` as `candidate.model_dump()`;
- `BioSeqChatService` returns `CandidateView` to the outside world in `ChatTurnResult`.

Key contracts:

| Contract | Where it is used |
| --- | --- |
| `ChatTurnRequest` | Input to `BioSeqChatService.submit_turn`. |
| `ChatTurnResult` | Output from `BioSeqChatService.submit_turn`. |
| `SessionSnapshot` | UI/API snapshot of agent/session state. |
| `BioSeqPipelineSnapshot` | Snapshot of the compatibility retriever pipeline. |
| `BioSeqInputExtraction` | Structured extraction model for the service-level pipeline. |
| `ProteinView` | UI-ready view of one protein record. |
| `CandidateView` | UI-ready candidate: `ProteinView`, scores, rank, evidence. |
| `EvidenceItem`, `DiseaseInfo`, `DomainFeature` | Nested UI-ready parts of protein/candidate views. |

## GraphRetrievalService As A Bridge

`GraphRetrievalService` lives in `app_services`, but it is used by both services and `retriever_agent`.

It performs domain-level Neo4j queries through `Neo4jGraphClient`:

| Method | Who uses it |
| --- | --- |
| `resolve_input` | `BioSeqChatService` fallback for text accession/gene/name input. |
| `find_by_sequence_hash` | `retriever_agent.rank_node`, `BioSeqRetrieverPipeline`. |
| `find_encoded_protein_by_sequence_hash` | `retriever_agent.rank_node`, `BioSeqRetrieverPipeline`. |
| `retrieve_candidates` | `retriever_agent.rank/rerank`, `BioSeqChatService`, `BioSeqRetrieverPipeline`. |
| `get_protein_view` | Used inside `retrieve_candidates`. |
| `get_candidate_context` | Available helper for compact neighbor context. |

Neo4j records are mapped into API-ready models in `app_services/protein_view_mapper.py`:

```text
Neo4j record
  -> protein_record_to_view(...)
  -> ProteinView
  -> neighbor_record_to_candidate(...)
  -> CandidateView
```

## service_factory

`app_services/service_factory.py` assembles dependencies:

```text
.env
  -> Neo4j settings
  -> Neo4jGraphClient
  -> GraphRetrievalService
  -> PersistenceResources
  -> Agent
  -> BioSeqChatService
```

For the retriever graph agent:

```python
create_bioseq_retriever_graph_agent(use_llm_extractor=True)
```

creates:

- `Neo4jGraphClient`;
- `GraphRetrievalService`;
- `PersistenceResources`;
- optional LLM extractor factory;
- `BioSeqRetrieverGraphAgent`.

For the chat service:

```python
create_bioseq_chat_service()
```

selects:

- `BIOSEQ_BACKEND=mock` -> `MockBioSeqChatService`;
- `BIOSEQ_BACKEND=graph` -> graph-backed service.

In the current code, the graph-backed chat service imports `backend.agents_core.session_agent.agent.SessionGraphAgent`, but that package is not present in the current `agents_core` tree. Therefore, the documented working agent today is `retriever_agent`.

## Rules For New Agents

- Keep external request/response models in `app_contracts`, not in `agents_core`.
- Agent public methods should accept `AppContext`, not `ChatTurnRequest`.
- `app_services` should adapt `ChatTurnRequest` into `AppContext` and agent state into `ChatTurnResult`.
- Do not expose internal LangGraph state directly to the frontend.
- If an agent needs Neo4j domain access, pass in a service such as `GraphRetrievalService` instead of building the driver inside a node.
- If an agent returns protein/candidate data for the UI, use `ProteinView`/`CandidateView` through the service layer.
- For compatibility with `BioSeqChatService`, a new agent must implement `warnings`, `invoke`, `get_current_state`, and `update_current_state`.

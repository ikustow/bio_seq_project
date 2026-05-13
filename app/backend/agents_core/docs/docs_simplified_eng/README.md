# Agent System In Plain English

This document is for someone opening `app/backend/agents_core` for the first time and trying to understand what is going on: where the agent is, where the database is, where the session is, and why `app_services` and `app_contracts` exist.

The very short version:

```text
The user sends a message
  -> a service turns it into context the agent can understand
  -> the agent runs a chain of steps
  -> the agent asks Neo4j for proteins and similar candidates
  -> the conversation state is saved
  -> the service returns an answer and UI-ready data
```

## What An Agent Means In This Project

Here, an agent is not one big LLM that freely decides which tools to call.

The current `retriever_agent` is simpler and more explicit:

- there is a predefined chain of steps;
- the steps run in a specific order;
- each step receives shared `state`;
- each step adds new fields to that `state`;
- at the end, the system has a search result.

This chain is implemented with LangGraph.

Think about it like this:

```text
LangGraph = the process diagram
Node = one step in the process
State = the shared folder of data passed between steps
Thread = one user's separate session
Checkpointer = the mechanism that saves thread state
```

## Main Folders

| Folder | What it contains |
| --- | --- |
| `app/backend/agents_core` | Agents and shared infrastructure for them. |
| `app/backend/agents_core/retriever_agent` | The current working agent for protein search by sequence. |
| `app/backend/agents_core/shared` | Shared models, config, session persistence, Neo4j helper. |
| `app/backend/app_services` | The layer that connects the agent to the app: creates the agent, calls it, builds the response. |
| `app/backend/app_contracts` | Pydantic request/response models that are convenient for UI/API. |

## What Actually Works Today

The currently available working package is:

```text
app/backend/agents_core/retriever_agent
```

It can:

- accept user text;
- extract a biological sequence or filepath from it;
- decide whether it is DNA or protein;
- translate DNA into a protein sequence;
- find a protein in the prepared Neo4j graph by sequence hash;
- find similar graph neighbors;
- reorder candidates using the user's text context;
- save session state.

Important: this agent works in DB-only mode.

That means:

- it does not calculate embeddings at request time;
- it does not run ProtT5;
- it does not run FAISS search during the request;
- the sequence must already have been loaded into Neo4j.

If the sequence is not in the prepared database, the agent returns a controlled error.

## The Most Important Idea: Two Kinds Of State

The project saves state at two levels.

### 1. Full Technical LangGraph State

This is everything the agent needs while it runs:

- messages;
- original prompt;
- extracted sequence;
- sequence type;
- protein sequence;
- intermediate candidates;
- final candidates;
- error, if there was one.

This is stored by the LangGraph checkpointer.

If Supabase/Postgres is enabled, it is stored in LangGraph Postgres tables.
If Supabase is not configured, it is stored in process memory.

### 2. Short Application State

This is a compact record that is useful for UI/API:

- which session;
- active accession;
- active sequence;
- short summary;
- list of proteins;
- list of sequences;
- working memory;
- latest results.

This is written to:

```text
public.chat_sessions
```

## Most Important Terms

| Term | Plain explanation |
| --- | --- |
| `AppContext` | Who is working and in which session. |
| `session_id` | Main conversation id. It is also the LangGraph `thread_id`. |
| `thread_id` | LangGraph memory branch id. Each session has its own thread. |
| `GraphState` | Internal state of the retriever agent. |
| `SessionPatch` | A short piece of state that can be stored in `chat_sessions`. |
| `PersistenceResources` | A bundle of memory objects: checkpointer, store, repository. |
| `checkpointer` | Saves technical LangGraph state. |
| `store` | Long-term LangGraph memory across threads. The retriever does not directly use it yet. |
| `session_repository` | Reads and writes `public.chat_sessions`. |
| `GraphRetrievalService` | Service that queries Neo4j and returns proteins/candidates. |
| `CandidateView` | UI-ready protein candidate. |
| `ProteinView` | UI-ready description of one protein. |

## Main Data Flow

A normal request looks like this:

```text
1. UI/API sends ChatTurnRequest
2. BioSeqChatService receives the request
3. BioSeqChatService creates AppContext
4. BioSeqChatService calls agent.invoke(...)
5. Agent runs the LangGraph pipeline
6. Pipeline calls GraphRetrievalService
7. GraphRetrievalService queries Neo4j
8. Agent receives results and updates state
9. Agent saves state through the checkpointer
10. Agent saves a compact session patch in chat_sessions
11. BioSeqChatService builds ChatTurnResult
12. UI/API receives the response
```

## What To Read Next

If you do not know where to start:

1. Read this file.
2. Then read [retriever_agent.md](retriever_agent.md), which explains the current agent step by step.
3. Then read [app_services_contracts.md](app_services_contracts.md), which explains how the agent connects to `app_services` and `app_contracts`.
4. If you need to add a new agent, read [adding_agents_supabase.md](adding_agents_supabase.md).

## Important Note About `session_agent`

`app/backend/app_services/service_factory.py` contains this import:

```python
from backend.agents_core.session_agent.agent import SessionGraphAgent
```

But the current `app/backend/agents_core` tree does not contain a `session_agent` folder.

So this documentation explains what exists right now:

```text
retriever_agent
shared
persistence
context
service/contracts integration
```


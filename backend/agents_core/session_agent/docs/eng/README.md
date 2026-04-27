# Session Agent

`session_agent` is a stateful agent for working with a protein graph in Neo4j. It is built on `LangChain create_agent`, uses `LangGraph` for conversation state, and can persist both short-lived session history and durable user data.

Interaction diagram: [схема.puml](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/docs/схема.puml)

## What the agent does

- Accepts a user message and an `AppContext` with `user_id` and `session_id`.
- Loads session state using `thread_id = session_id`.
- Optionally restores a saved session snapshot from `chat_sessions`.
- Gives the model access to Neo4j domain tools, user memory, and the current session context.
- After the model responds, recalculates a session patch: summary, detected proteins, sequences, working memory, and active ids.
- Persists the updated state through the persistence layer.

## Execution flow

1. [main.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/main.py) reads CLI arguments, loads `.env`, and creates `AppContext`.
2. It also creates `Neo4jGraphClient` and `PersistenceResources`.
3. [agent.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/agent.py) builds `SessionGraphAgent` via `create_agent(...)`.
4. `SessionGraphAgent.invoke()` prepares the input payload and calls the LangChain agent.
5. After the response, `derive_session_patch(...)` extracts structured data from message history.
6. The final state is saved through `session_repository`, while checkpoint/state store is handled by `LangGraph`.

## File structure

- [agent.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/agent.py) - main runtime class `SessionGraphAgent`.
- [main.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/main.py) - CLI entrypoint.
- [config.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/config.py) - constants, prompt, `.env` loading.
- [models.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/models.py) - `pydantic` models for context, session state, and persistence payloads.
- [services/graph.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/services/graph.py) - Neo4j client and read-only Cypher guardrails.
- [services/persistence.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/services/persistence.py) - persistence setup and `chat_sessions` access.
- [services/session_state.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/services/session_state.py) - protein/sequence extraction and session patch assembly.
- [tools/](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/tools) - all tools exposed to the LLM.
- [docs/схема.puml](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/docs/схема.puml) - sequence UML diagram.

## Key classes and functions

### `agent.py`

- `SessionGraphAgent.__init__(...)`
  Creates `ChatOpenAI`, registers tools, `state_schema`, `context_schema`, `checkpointer`, and `store`.

- `SessionGraphAgent.invoke(message, context)`
  Main entrypoint. Calls the agent, then normalizes and saves session state.

- `SessionGraphAgent.get_current_state(context)`
  Returns the current LangGraph state for the given `thread_id`.

- `SessionGraphAgent.get_message_history(context)`
  Returns serialized message history in a simple `{role, content}` shape.

- `SessionGraphAgent._build_input_payload(...)`
  If the active thread is empty, tries to restore session fields from `session_repository`.

### `config.py`

- `load_env_file(env_path)`
  Loads environment variables from `.env` without overwriting already-defined values.

- `SESSION_STATE_KEYS`
  Canonical list of session fields that are displayed and restored.

- `SYSTEM_PROMPT`
  Agent rules: prefer domain tools first, stay careful with biological conclusions, support user memory.

### `models.py`

- `AppContext`
  Invocation context: `user_id`, `session_id`, `workspace_id`, `user_role`.

- `ProteinRecord` and `SequenceRecord`
  Normalized entities extracted from tool output and message text.

- `SessionPatch`
  Model for partial session-state updates after an agent response.

- `SessionRow`
  Model for a row stored in `chat_sessions`.

- `SessionStateView`
  Simplified state view used during post-processing.

- `PersistenceResources`
  Container for `checkpointer`, `store`, `session_repository`, persistence mode, and warnings.

### `services/graph.py`

- `resolve_driver_uri(uri, insecure)`
  Converts a secure URI into a `+ssc` variant for environments with self-signed TLS.

- `ensure_read_only_cypher(query)`
  Validates that custom Cypher remains read-only.

- `Neo4jGraphClient.execute(...)`
  Executes a Neo4j query and retries with a fallback URI on `ServiceUnavailable`.

### `services/persistence.py`

- `NullSessionRepository`
  No-op repository used when Postgres persistence is unavailable.

- `PostgresSessionRepository.get_session(session_id)`
  Reads a session snapshot from `public.chat_sessions`.

- `PostgresSessionRepository.upsert_session(context, state)`
  Saves a session snapshot into `public.chat_sessions`.

- `build_session_row(context, state)`
  Converts current `state` into a `SessionRow`.

- `create_persistence_resources(db_url, exit_stack)`
  Chooses between Postgres-backed persistence and in-memory fallback.

### `services/session_state.py`

- `get_message_text(...)`
  Converts LangChain messages into plain text.

- `serialize_message(...)`
  Builds a compact history representation for CLI and debugging.

- `extract_proteins(messages, existing)`
  Finds protein records and neighbor records returned by tools.

- `extract_sequences(messages, existing)`
  Extracts amino-acid sequences using regex.

- `derive_session_patch(state)`
  Builds the final patch: summary, proteins, sequences, working memory, active ids, and `last_tool_results_summary`.

### `tools`

- [tools/graph.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/tools/graph.py)
  Graph domain tools:
  `graph_schema_guide`, `find_proteins`, `get_protein_neighbors`, `get_neighbor_diseases`, `summarize_neighbor_disease_context`, `run_read_cypher`.

- [tools/memory.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/tools/memory.py)
  Durable memory tools:
  `save_user_profile`, `get_user_profile`, `save_user_preference`, `save_user_fact`, `save_investigation_default`, `get_investigation_defaults`.

- [tools/session.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/tools/session.py)
  `get_session_context`, which gives the model the current user context and compact session state.

- [tools/base.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/tools/base.py)
  Small helpers for JSON responses and store updates.

- [tools/__init__.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/tools/__init__.py)
  `build_tools(client)` assembles the final tool list for the agent.

## How state is stored

There are two memory layers:

- `checkpointer` and `store` from LangGraph
  Used by the agent during execution. They can be PostgreSQL-backed or in-memory.

- `session_repository`
  Explicitly stores a compact session snapshot in `public.chat_sessions`, so key fields can be restored independently of the internal checkpoint mechanism.

If `SUPABASE_DB_URL` is not configured, or Postgres persistence fails to initialize, the agent continues in `memory` mode.

## What goes into session state

Important fields are listed in `SESSION_STATE_KEYS`:

- `session_summary`
- `proteins`
- `sequences`
- `working_memory`
- `active_sequence_id`
- `active_accession`
- `last_analysis_summary`
- `working_set_ids`
- `current_mode`
- `last_tool_results_summary`

## How to run

Example CLI call:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "Find neighbors for TP53" \
  --show-session-state
```

Required environment variables:

- `OPENAI_API_KEY`
- `NEO4J_URI`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`
- `SUPABASE_DB_URL`

## Limitations and notes

- `run_read_cypher` only allows read-only queries.
- Sequence extraction is regex-based and only works for sufficiently long amino-acid-like strings.
- Session patching happens after the fact from message history, so structure quality depends on tools returning stable JSON.
- The UML diagram in [схема.puml](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/docs/схема.puml) is manually synchronized with the current code.

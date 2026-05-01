# Session Agent Scripts

This file is a practical cheat sheet for a human or another LLM: what to run for `session_agent`, when to use each command, and which commands can be pasted directly into the terminal.

All examples below assume you run them from the project root:

```bash
cd /Users/ilia_kustov/Documents/dev/bio_seq_project
```

Main command:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main
```

## Quick rules

- If you need to send one question to the agent, use `--message`.
- If you want to inspect session state after the answer, add `--show-session-state`.
- If you want to load an existing session and inspect its history, use `--dump-history`.
- If you need a fresh clean session, always pass a new `--session-id`.
- If you do not pass `--message`, the agent starts in interactive mode.

## Ready-to-run commands

### 1. Check that the agent starts at all

What it does:
Starts a new session and asks the agent to briefly describe its available capabilities.

Command:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "What tools do you have available for this protein graph? Please answer in one short paragraph." \
  --session-id smoke_test_$(date +%s) \
  --show-session-state
```

When to use:
- smoke test after changes
- checking keys, network, and persistence

What to tell an LLM:
`Run a smoke test for session_agent in a new session and show the answer together with session state.`

### 2. Run the agent in a new clean session

What it does:
Creates a new `session_id`, sends one question, and stores the result as a fresh session.

Command:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "Find proteins related to TP53 and summarize what you found." \
  --session-id session_$(date +%s) \
  --show-session-state
```

When to use:
- when you do not want to mix a new request with old history
- when you need a reproducible isolated scenario

What to tell an LLM:
`Run session_agent in a fresh session with this message and show the resulting session state.`

### 3. Test a concrete accession scenario

What it does:
Tests a biological scenario with disease summary across neighbors of a protein.

Command:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "For accession A2ACJ2, summarize common diseases across its neighbors and give a short interpretation. Ответь на русском" \
  --session-id a2acj2_case_$(date +%s) \
  --show-session-state
```

When to use:
- for manual answer-quality checks
- for regression testing after prompt or tool changes

What to tell an LLM:
`Run the A2ACJ2 scenario in a new session and evaluate how well the answer matches the tool output.`

### 4. Start interactive mode

What it does:
Starts the agent as a REPL so you can ask multiple follow-up questions within one session.

Command:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --session-id interactive_$(date +%s) \
  --show-session-state
```

When to use:
- for manual graph exploration
- for checking how the agent accumulates context within one session

What to tell an LLM:
`Start session_agent in interactive mode using a fresh session.`

### 5. Inspect history of an existing session

What it does:
Prints message history for a specific `session_id`.

Command:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --dump-history \
  --session-id test_session_a2acj2_1777312279
```

When to use:
- to see which tools were actually called
- to inspect restored history
- to debug a weak or incorrect answer

What to tell an LLM:
`Load the history for this session and analyze which tools the agent used.`

### 6. Check that session state is restored across runs

What it does:
Creates a session first, then continues it in a second process using the same `session_id`.

Step 1:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "Find protein A2ACJ2 and remember it as the active target." \
  --session-id restore_test_manual \
  --show-session-state
```

Step 2:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "What protein are we currently focused on?" \
  --session-id restore_test_manual \
  --show-session-state
```

When to use:
- to validate persistence
- after changing `session_repository` or `derive_session_patch`

What to tell an LLM:
`Check whether session state is restored across two separate runs with the same session_id.`

### 7. Read history without making a new model call

What it does:
Does not invoke the model, only reads already accumulated history.

Command:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --dump-history \
  --session-id restore_test_manual
```

When to use:
- when you only need the audit trail
- when you do not want to spend another model call

What to tell an LLM:
`Read the history of this session without making a new model call.`

### 8. Run with explicit user and workspace context

What it does:
Checks how the agent behaves with explicit `user_id`, `workspace_id`, and `user_role`.

Command:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "Save that I prefer concise answers in Russian." \
  --session-id user_context_test_$(date +%s) \
  --user-id demo-user \
  --workspace-id demo-workspace \
  --user-role ADMIN \
  --show-session-state
```

When to use:
- to validate user memory behavior
- for scenarios where user context matters

What to tell an LLM:
`Run the agent with explicit user_id and workspace_id and verify that user context reaches the tools.`

### 9. Run with a different model

What it does:
Lets you test behavior on another model without changing `.env`.

Command:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --model gpt-4.1-mini \
  --message "Find neighbors for BRCA1 and summarize the result briefly." \
  --session-id alt_model_$(date +%s)
```

When to use:
- for quality and cost comparisons
- for a smoke test on another model

What to tell an LLM:
`Run the same scenario on another model and compare the result with the baseline.`

### 10. Run without Postgres persistence

What it does:
Forces in-memory mode by temporarily clearing `SUPABASE_DB_URL`.

Command:

```bash
SUPABASE_DB_URL= ./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "What persistence mode are you using?" \
  --session-id memory_mode_$(date +%s) \
  --show-session-state
```

When to use:
- to validate fallback logic
- when you want to isolate the agent from Supabase

What to tell an LLM:
`Run session_agent without SUPABASE_DB_URL and confirm that it switched to memory mode.`

### 11. Check a read-only graph exploration flow

What it does:
Gives the agent a task that is likely to use graph tools and should remain fully read-only.

Command:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "Find proteins matching Hps5 and list their closest neighbors." \
  --session-id graph_read_test_$(date +%s) \
  --show-session-state
```

When to use:
- to check `find_proteins` and `get_protein_neighbors`
- to confirm the agent stays in read-only graph usage

What to tell an LLM:
`Run a safe read-only protein graph scenario and show what data the agent returned.`

### 12. Check durable user preferences

What it does:
Creates one session where the user states a preference, then starts another session for the same user and checks whether the preference is remembered.

Step 1:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "Please remember that I prefer answers in Russian and in a concise style." \
  --session-id pref_save_1 \
  --user-id pref-user
```

Step 2:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "What do you remember about my answer preferences?" \
  --session-id pref_save_2 \
  --user-id pref-user \
  --show-session-state
```

When to use:
- to validate durable user memory
- after changes in `tools/memory.py`

What to tell an LLM:
`Check whether user memory is preserved across different sessions for the same user_id.`

## Useful templates

### New unique session id

```bash
--session-id test_$(date +%s)
```

### Run with state output

```bash
--show-session-state
```

### Read history instead of sending a new prompt

```bash
--dump-history --session-id <existing_session_id>
```

### Override Neo4j connection settings

```bash
--uri <neo4j_uri> --database <db_name> --user <neo4j_user> --password <neo4j_password>
```

## How to phrase tasks for another LLM

Good prompts:

- `Run session_agent in a new session and show the answer together with session state.`
- `Load the history for this session and analyze the tool calls.`
- `Test the A2ACJ2 scenario and evaluate answer correctness against tool output.`
- `Compare agent behavior in postgres mode versus memory mode.`
- `Check whether state is restored across two separate runs with the same session_id.`

Bad prompts:

- `check the agent`
- `run something`
- `look at the history`

These are bad because they do not specify:
- whether a new or existing session should be used
- whether session state should be shown
- whether the goal is only to execute a scenario or also to evaluate answer quality

## Minimal copy-paste set

New session:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "<your_message>" \
  --session-id test_$(date +%s) \
  --show-session-state
```

Existing session history:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --dump-history \
  --session-id <existing_session_id>
```

Interactive mode:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --session-id interactive_$(date +%s) \
  --show-session-state
```

# Session-Aware Graph Agent

This folder contains a copy of the Neo4j graph agent with:

- LangGraph `thread_id`-based session persistence
- long-term user context storage through `store`
- app-level session sync with `public.chat_sessions`

## What it adds on top of `simple_agent`

- `context_schema` with `user_id`, `session_id`, `workspace_id`, and `user_role`
- `state_schema` for compact session metadata such as `session_summary`, `proteins`, and `sequences`
- long-term memory tools for user profile, preferences, and saved facts
- optional Supabase/Postgres persistence via `SUPABASE_DB_URL`
- fallback to in-memory persistence when Postgres packages or DB config are missing

## Install

From the project root:

```bash
./.venv/bin/pip install -r requirements.txt
```

## Run

Interactive mode:

```bash
./.venv/bin/python backend/agents_core/session_agent/main.py \
  --user-id ilia \
  --session-id session_001
```

Single-shot mode:

```bash
./.venv/bin/python backend/agents_core/session_agent/main.py \
  --user-id ilia \
  --session-id session_001 \
  --message "Find protein P13439 and summarize its nearest neighbors."
```

## Environment

The agent reads the project `.env`. For full Supabase-backed persistence add:

```text
SUPABASE_DB_URL=postgresql://postgres:[YOUR-PASSWORD]@db.rkeuqcetflhosdmbbbxl.supabase.co:5432/postgres?sslmode=require
```

If you are on an IPv4-only network, use the Supabase Session Pooler connection string instead of the direct `db.<project-ref>.supabase.co` host.

If `SUPABASE_DB_URL` is missing, or if Postgres persistence packages are not installed yet, the agent still runs with in-memory checkpointer/store.

## Notes

- `thread_id` is set equal to `session_id`, matching the PRD.
- `messages` remain in LangGraph checkpoints; `chat_sessions` stores compact session metadata.
- The session row is upserted before and after each invocation.

from __future__ import annotations

import json
from contextlib import ExitStack
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from ..models import AppContext, PersistenceResources, SessionPatch, SessionRow


class NullSessionRepository:
    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return None

    def upsert_session(self, context: AppContext, state: dict[str, Any]) -> None:
        return None

    def close(self) -> None:
        return None


class PostgresSessionRepository:
    def __init__(self, db_url: str) -> None:
        import psycopg

        self._conn = psycopg.connect(db_url, autocommit=True)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                select
                    session_id,
                    thread_id,
                    user_id,
                    workspace_id,
                    user_role,
                    session_summary,
                    proteins,
                    sequences,
                    working_memory,
                    active_sequence_id,
                    active_accession,
                    last_analysis_summary,
                    working_set_ids,
                    current_mode,
                    last_tool_results_summary,
                    created_at,
                    updated_at
                from public.chat_sessions
                where session_id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            columns = [desc.name for desc in cur.description]
        return dict(zip(columns, row, strict=False))

    def upsert_session(self, context: AppContext, state: dict[str, Any]) -> None:
        payload = build_session_row(context, state)
        with self._conn.cursor() as cur:
            cur.execute(
                """
                insert into public.chat_sessions (
                    session_id,
                    thread_id,
                    user_id,
                    workspace_id,
                    user_role,
                    session_summary,
                    proteins,
                    sequences,
                    working_memory,
                    active_sequence_id,
                    active_accession,
                    last_analysis_summary,
                    working_set_ids,
                    current_mode,
                    last_tool_results_summary
                ) values (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s::jsonb,
                    %s::jsonb,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s,
                    %s
                )
                on conflict (session_id) do update
                set
                    thread_id = excluded.thread_id,
                    user_id = excluded.user_id,
                    workspace_id = excluded.workspace_id,
                    user_role = excluded.user_role,
                    session_summary = excluded.session_summary,
                    proteins = excluded.proteins,
                    sequences = excluded.sequences,
                    working_memory = excluded.working_memory,
                    active_sequence_id = excluded.active_sequence_id,
                    active_accession = excluded.active_accession,
                    last_analysis_summary = excluded.last_analysis_summary,
                    working_set_ids = excluded.working_set_ids,
                    current_mode = excluded.current_mode,
                    last_tool_results_summary = excluded.last_tool_results_summary
                """,
                (
                    payload.session_id,
                    payload.thread_id,
                    payload.user_id,
                    payload.workspace_id,
                    payload.user_role,
                    payload.session_summary,
                    json.dumps(payload.proteins_payload(), ensure_ascii=False),
                    json.dumps(payload.sequences_payload(), ensure_ascii=False),
                    json.dumps(payload.working_memory, ensure_ascii=False),
                    payload.active_sequence_id,
                    payload.active_accession,
                    payload.last_analysis_summary,
                    json.dumps(payload.working_set_ids, ensure_ascii=False),
                    payload.current_mode,
                    payload.last_tool_results_summary,
                ),
            )

    def close(self) -> None:
        self._conn.close()


def build_session_row(context: AppContext, state: dict[str, Any]) -> SessionRow:
    patch = SessionPatch.model_validate(state)
    return SessionRow(
        session_id=context.session_id,
        thread_id=context.session_id,
        user_id=context.user_id,
        workspace_id=context.workspace_id,
        user_role=context.user_role,
        **patch.model_dump(),
    )


def create_persistence_resources(db_url: str | None, exit_stack: ExitStack) -> PersistenceResources:
    warnings: list[str] = []

    if db_url:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore[import-not-found]
            from langgraph.store.postgres import PostgresStore  # type: ignore[import-not-found]

            checkpointer = exit_stack.enter_context(PostgresSaver.from_conn_string(db_url))
            checkpointer.setup()
            store = exit_stack.enter_context(PostgresStore.from_conn_string(db_url))
            store.setup()
            return PersistenceResources(
                checkpointer=checkpointer,
                store=store,
                session_repository=PostgresSessionRepository(db_url),
                mode="postgres",
                warnings=warnings,
            )
        except ImportError as exc:
            warnings.append(
                "Postgres persistence packages are missing; falling back to in-memory persistence. "
                f"Install `langgraph-checkpoint-postgres` and `psycopg[binary]` to enable Supabase-backed memory. ({exc})"
            )
        except Exception as exc:
            warnings.append(
                "Could not initialize Supabase/Postgres persistence; falling back to in-memory persistence. "
                f"Reason: {exc}"
            )
    else:
        warnings.append("SUPABASE_DB_URL is not set; using in-memory persistence.")

    return PersistenceResources(
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        session_repository=NullSessionRepository(),
        mode="memory",
        warnings=warnings,
    )

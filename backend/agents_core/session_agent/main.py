from __future__ import annotations

import argparse
import json
import os
import uuid
from contextlib import ExitStack

from backend.agents_core.session_agent.agent import SessionGraphAgent
from backend.agents_core.session_agent.config import (
    DEFAULT_DATABASE,
    DEFAULT_ENV_PATH,
    DEFAULT_MODEL,
    DEFAULT_URI,
    SESSION_STATE_KEYS,
    load_env_file,
)
from backend.agents_core.session_agent.models import AppContext
from backend.agents_core.session_agent.services.graph import Neo4jGraphClient, resolve_driver_uri
from backend.agents_core.session_agent.services.persistence import create_persistence_resources

load_env_file(DEFAULT_ENV_PATH)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", help="Single message to send to the agent.")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--uri", default=os.getenv("NEO4J_URI", DEFAULT_URI))
    parser.add_argument("--database", default=os.getenv("NEO4J_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--user", default=os.getenv("NEO4J_USERNAME", os.getenv("USERNAME")))
    parser.add_argument("--password", default=os.getenv("NEO4J_PASSWORD", os.getenv("PASSWORD")))
    parser.add_argument("--user-id", default=os.getenv("APP_USER_ID", "local-user"))
    parser.add_argument("--session-id", default=os.getenv("APP_SESSION_ID", f"session_{uuid.uuid4().hex[:8]}"))
    parser.add_argument("--workspace-id", default=os.getenv("APP_WORKSPACE_ID"))
    parser.add_argument("--user-role", default=os.getenv("APP_USER_ROLE"))
    parser.add_argument("--supabase-db-url", default=os.getenv("SUPABASE_DB_URL"))
    parser.add_argument("--show-session-state", action="store_true", help="Print compact session state after each invocation.")
    parser.add_argument("--dump-history", action="store_true", help="Print the stored message history for the current session and exit.")
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=os.getenv("NEO4J_INSECURE", "1").lower() not in {"0", "false", "no"},
        help="Use neo4j+ssc:// or bolt+ssc:// for environments with self-signed TLS chains.",
    )
    return parser.parse_args()


def print_session_state(session_state: dict) -> None:
    print("\n[session_state]")
    print(json.dumps({key: session_state.get(key) for key in SESSION_STATE_KEYS}, ensure_ascii=False, indent=2))


def main() -> None:
    args = parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is missing in the environment or .env file.")
    if not args.user or not args.password:
        raise ValueError("Neo4j credentials are missing in the environment or .env file.")

    context = AppContext(
        user_id=args.user_id,
        session_id=args.session_id,
        workspace_id=args.workspace_id,
        user_role=args.user_role,
    )
    client = Neo4jGraphClient(
        uri=resolve_driver_uri(args.uri, insecure=args.insecure),
        user=args.user,
        password=args.password,
        database=args.database,
    )

    with ExitStack() as exit_stack:
        persistence = create_persistence_resources(args.supabase_db_url, exit_stack)
        exit_stack.callback(persistence.session_repository.close)
        agent = SessionGraphAgent(args.model, client, persistence)

        for warning in agent.warnings:
            print(f"[persistence] {warning}")
        print(f"[persistence] mode={agent.persistence_mode} thread_id={context.session_id} user_id={context.user_id}")

        if args.dump_history:
            print(json.dumps(agent.get_message_history(context), ensure_ascii=False, indent=2))
            return

        if args.message:
            result, session_state = agent.invoke(args.message, context)
            print(result["messages"][-1].content)
            if args.show_session_state:
                print_session_state(session_state)
            return

        print("Session-aware graph agent is ready. Type 'exit' to stop.")
        while True:
            try:
                user_input = input("\nYou> ").strip()
            except EOFError:
                print()
                break
            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit"}:
                break

            result, session_state = agent.invoke(user_input, context)
            print(f"\nAgent> {result['messages'][-1].content}")
            if args.show_session_state:
                print_session_state(session_state)


if __name__ == "__main__":
    main()

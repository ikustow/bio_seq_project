from __future__ import annotations

import argparse
import json
import os
import uuid
from contextlib import ExitStack

from backend.agents_core.retriever_agent.agent import BioSeqRetrieverGraphAgent
from backend.agents_core.retriever_agent.llm import create_extraction_llm_factory, require_llm_api_key, select_llm_provider
from backend.agents_core.shared.config import DEFAULT_ENV_PATH, load_env_file, resolve_neo4j_settings
from backend.agents_core.shared.models import AppContext
from backend.agents_core.shared.services.graph import Neo4jGraphClient, resolve_driver_uri
from backend.agents_core.shared.services.persistence import create_persistence_resources
from backend.agents_core.shared.services.session_state import serialize_message
from backend.app_services.graph_retrieval import GraphRetrievalService

load_env_file(DEFAULT_ENV_PATH)


def parse_args():
    neo4j = resolve_neo4j_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", help="Single prompt to send to the retriever graph agent.")
    parser.add_argument("--provider", choices=["openai", "mistral"], default=os.getenv("BIOSEQ_LLM_PROVIDER"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--neo4j-profile", default=neo4j.profile, help="Neo4j env profile: local or cloud.")
    parser.add_argument("--uri", default=neo4j.uri)
    parser.add_argument("--database", default=neo4j.database)
    parser.add_argument("--user", default=neo4j.user)
    parser.add_argument("--password", default=neo4j.password)
    parser.add_argument("--user-id", default=os.getenv("APP_USER_ID", "local-user"))
    parser.add_argument("--session-id", default=os.getenv("APP_SESSION_ID", f"retriever_{uuid.uuid4().hex[:8]}"))
    parser.add_argument("--workspace-id", default=os.getenv("APP_WORKSPACE_ID"))
    parser.add_argument("--user-role", default=os.getenv("APP_USER_ROLE"))
    parser.add_argument("--supabase-db-url", default=os.getenv("SUPABASE_DB_URL"))
    parser.add_argument("--deterministic-extractor", action="store_true", help="Skip LLM extraction and use local parsing.")
    parser.add_argument("--dump-history", action="store_true", help="Print stored message history for the session and exit.")
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=neo4j.insecure,
    )
    args = parser.parse_args()
    if args.neo4j_profile != neo4j.profile:
        selected_neo4j = resolve_neo4j_settings(args.neo4j_profile)
        if args.uri == neo4j.uri:
            args.uri = selected_neo4j.uri
        if args.database == neo4j.database:
            args.database = selected_neo4j.database
        if args.user == neo4j.user:
            args.user = selected_neo4j.user
        if args.password == neo4j.password:
            args.password = selected_neo4j.password
        if args.insecure == neo4j.insecure:
            args.insecure = selected_neo4j.insecure
    return args


def main() -> None:
    args = parse_args()
    if not args.user or not args.password:
        raise ValueError("Neo4j credentials are missing in the environment or .env file.")
    provider = select_llm_provider(args.provider)
    if not args.deterministic_extractor:
        require_llm_api_key(provider)

    client = Neo4jGraphClient(
        uri=resolve_driver_uri(args.uri, insecure=args.insecure),
        user=args.user,
        password=args.password,
        database=args.database,
    )
    context = AppContext(
        user_id=args.user_id,
        session_id=args.session_id,
        workspace_id=args.workspace_id,
        user_role=args.user_role,
    )

    with ExitStack() as exit_stack:
        persistence = create_persistence_resources(args.supabase_db_url, exit_stack)
        exit_stack.callback(persistence.session_repository.close)

        llm_factory = None
        if not args.deterministic_extractor:
            llm_factory = create_extraction_llm_factory(provider=provider, model=args.model)

        agent = BioSeqRetrieverGraphAgent(
            graph_retrieval=GraphRetrievalService(client),
            persistence=persistence,
            llm_factory=llm_factory,
            use_llm_extractor=not args.deterministic_extractor,
        )
        if args.dump_history:
            print(json.dumps(agent.get_message_history(context), ensure_ascii=False, indent=2))
            return
        if not args.message:
            raise ValueError("--message is required unless --dump-history is used.")
        result, session_state = agent.invoke(args.message, context)
        print(json.dumps({"result": _json_safe(result), "session_state": _json_safe(session_state)}, ensure_ascii=False, indent=2))


def _json_safe(value):
    if hasattr(value, "content") and hasattr(value, "type"):
        return serialize_message(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


if __name__ == "__main__":
    main()

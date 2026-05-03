from __future__ import annotations

import argparse
import json
import os
import uuid
from contextlib import ExitStack

from backend.agents_core.retriever_agent.agent import BioSeqRetrieverGraphAgent
from backend.agents_core.retriever_agent.llm import create_extraction_llm_factory, require_llm_api_key, select_llm_provider
from backend.agents_core.session_agent.config import DEFAULT_DATABASE, DEFAULT_ENV_PATH, DEFAULT_URI, load_env_file
from backend.agents_core.session_agent.models import AppContext
from backend.agents_core.session_agent.services.graph import Neo4jGraphClient, resolve_driver_uri
from backend.agents_core.session_agent.services.persistence import create_persistence_resources
from backend.app_services.graph_retrieval import GraphRetrievalService

load_env_file(DEFAULT_ENV_PATH)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", required=True, help="Single prompt to send to the retriever graph agent.")
    parser.add_argument("--provider", choices=["openai", "mistral"], default=os.getenv("BIOSEQ_LLM_PROVIDER"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--uri", default=os.getenv("NEO4J_URI", DEFAULT_URI))
    parser.add_argument("--database", default=os.getenv("NEO4J_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--user", default=os.getenv("NEO4J_USERNAME", os.getenv("USERNAME")))
    parser.add_argument("--password", default=os.getenv("NEO4J_PASSWORD", os.getenv("PASSWORD")))
    parser.add_argument("--user-id", default=os.getenv("APP_USER_ID", "local-user"))
    parser.add_argument("--session-id", default=os.getenv("APP_SESSION_ID", f"retriever_{uuid.uuid4().hex[:8]}"))
    parser.add_argument("--workspace-id", default=os.getenv("APP_WORKSPACE_ID"))
    parser.add_argument("--user-role", default=os.getenv("APP_USER_ROLE"))
    parser.add_argument("--supabase-db-url", default=os.getenv("SUPABASE_DB_URL"))
    parser.add_argument("--deterministic-extractor", action="store_true", help="Skip LLM extraction and use local parsing.")
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=os.getenv("NEO4J_INSECURE", "1").lower() not in {"0", "false", "no"},
    )
    return parser.parse_args()


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
        result, session_state = agent.invoke(args.message, context)
        print(json.dumps({"result": result, "session_state": session_state}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

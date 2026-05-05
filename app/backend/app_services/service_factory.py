from __future__ import annotations

import os
from contextlib import ExitStack

from backend.agents_core.shared.config import DEFAULT_ENV_PATH, DEFAULT_MODEL, load_env_file, resolve_neo4j_settings

from .bioseq_chat import BioSeqChatService, MockBioSeqChatService
from .graph_retrieval import GraphRetrievalService
from .retriever_pipeline import BioSeqRetrieverPipeline


def create_bioseq_retriever_graph_agent(use_llm_extractor: bool = True):
    load_env_file(DEFAULT_ENV_PATH)
    neo4j = resolve_neo4j_settings()
    if not neo4j.user or not neo4j.password:
        raise ValueError("Neo4j credentials are missing. Set NEO4J_USERNAME/NEO4J_PASSWORD or profile-specific credentials.")
    from backend.agents_core.retriever_agent.agent import BioSeqRetrieverGraphAgent
    from backend.agents_core.retriever_agent.llm import create_extraction_llm_factory, select_llm_provider
    from backend.agents_core.shared.services.graph import Neo4jGraphClient, resolve_driver_uri
    from backend.agents_core.shared.services.persistence import create_persistence_resources

    client = Neo4jGraphClient(
        uri=resolve_driver_uri(neo4j.uri, insecure=neo4j.insecure),
        user=neo4j.user,
        password=neo4j.password,
        database=neo4j.database,
    )
    exit_stack = ExitStack()
    persistence = create_persistence_resources(os.getenv("SUPABASE_DB_URL"), exit_stack)
    exit_stack.callback(persistence.session_repository.close)

    llm_factory = None
    if use_llm_extractor:
        provider = select_llm_provider()
        llm_factory = create_extraction_llm_factory(provider=provider)

    agent = BioSeqRetrieverGraphAgent(
        graph_retrieval=GraphRetrievalService(client),
        persistence=persistence,
        llm_factory=llm_factory,
        use_llm_extractor=use_llm_extractor,
    )
    agent._exit_stack = exit_stack
    return agent


def create_bioseq_chat_service() -> BioSeqChatService | MockBioSeqChatService:
    load_env_file(DEFAULT_ENV_PATH)
    backend_mode = os.getenv("BIOSEQ_BACKEND", "mock").strip().lower()
    if backend_mode == "mock":
        return MockBioSeqChatService()
    if backend_mode != "graph":
        raise ValueError("BIOSEQ_BACKEND must be either 'mock' or 'graph'.")

    neo4j = resolve_neo4j_settings()
    if not neo4j.user or not neo4j.password:
        raise ValueError("Neo4j credentials are missing. Set NEO4J_USERNAME/NEO4J_PASSWORD or profile-specific credentials.")
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is missing; graph mode needs it for SessionGraphAgent.")

    from backend.agents_core.session_agent.agent import SessionGraphAgent
    from backend.agents_core.shared.services.graph import Neo4jGraphClient, resolve_driver_uri
    from backend.agents_core.shared.services.persistence import create_persistence_resources
    from langchain_openai import ChatOpenAI

    client = Neo4jGraphClient(
        uri=resolve_driver_uri(neo4j.uri, insecure=neo4j.insecure),
        user=neo4j.user,
        password=neo4j.password,
        database=neo4j.database,
    )
    exit_stack = ExitStack()
    persistence = create_persistence_resources(os.getenv("SUPABASE_DB_URL"), exit_stack)
    exit_stack.callback(persistence.session_repository.close)
    agent = SessionGraphAgent(os.getenv("OPENAI_MODEL", DEFAULT_MODEL), client, persistence)
    graph_retrieval = GraphRetrievalService(client)
    retriever_pipeline = BioSeqRetrieverPipeline(
        graph_retrieval=graph_retrieval,
        llm_factory=lambda: ChatOpenAI(model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL), temperature=0),
    )
    service = BioSeqChatService(agent=agent, graph_retrieval=graph_retrieval, retriever_pipeline=retriever_pipeline)
    service._exit_stack = exit_stack  # Keep persistence contexts alive while Streamlit caches the service.
    return service

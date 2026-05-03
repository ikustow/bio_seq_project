from __future__ import annotations

import os
from contextlib import ExitStack

from backend.agents_core.session_agent.config import DEFAULT_DATABASE, DEFAULT_ENV_PATH, DEFAULT_MODEL, DEFAULT_URI, load_env_file

from .bioseq_chat import BioSeqChatService, MockBioSeqChatService
from .graph_retrieval import GraphRetrievalService
from .retriever_pipeline import BioSeqRetrieverPipeline


def create_bioseq_retriever_graph_agent(use_llm_extractor: bool = True):
    load_env_file(DEFAULT_ENV_PATH)
    user = os.getenv("NEO4J_USERNAME", os.getenv("USERNAME"))
    password = os.getenv("NEO4J_PASSWORD", os.getenv("PASSWORD"))
    if not user or not password:
        raise ValueError("Neo4j credentials are missing. Set NEO4J_USERNAME and NEO4J_PASSWORD.")
    from backend.agents_core.retriever_agent.agent import BioSeqRetrieverGraphAgent
    from backend.agents_core.retriever_agent.llm import create_extraction_llm_factory, select_llm_provider
    from backend.agents_core.session_agent.services.graph import Neo4jGraphClient, resolve_driver_uri
    from backend.agents_core.session_agent.services.persistence import create_persistence_resources

    insecure = os.getenv("NEO4J_INSECURE", "1").lower() not in {"0", "false", "no"}
    client = Neo4jGraphClient(
        uri=resolve_driver_uri(os.getenv("NEO4J_URI", DEFAULT_URI), insecure=insecure),
        user=user,
        password=password,
        database=os.getenv("NEO4J_DATABASE", DEFAULT_DATABASE),
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

    user = os.getenv("NEO4J_USERNAME", os.getenv("USERNAME"))
    password = os.getenv("NEO4J_PASSWORD", os.getenv("PASSWORD"))
    if not user or not password:
        raise ValueError("Neo4j credentials are missing. Set NEO4J_USERNAME and NEO4J_PASSWORD.")
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is missing; graph mode needs it for SessionGraphAgent.")

    from backend.agents_core.session_agent.agent import SessionGraphAgent
    from backend.agents_core.session_agent.services.graph import Neo4jGraphClient, resolve_driver_uri
    from backend.agents_core.session_agent.services.persistence import create_persistence_resources
    from langchain_openai import ChatOpenAI

    insecure = os.getenv("NEO4J_INSECURE", "1").lower() not in {"0", "false", "no"}
    client = Neo4jGraphClient(
        uri=resolve_driver_uri(os.getenv("NEO4J_URI", DEFAULT_URI), insecure=insecure),
        user=user,
        password=password,
        database=os.getenv("NEO4J_DATABASE", DEFAULT_DATABASE),
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

from __future__ import annotations

import os
from contextlib import ExitStack

from backend.agents_core.shared.config import DEFAULT_ENV_PATH, load_env_file

from .bioseq_chat import BioSeqChatService, MockBioSeqChatService
from .chat_llm import ChatLLMService
from .retriever_pipeline import BioSeqRetrieverPipeline


def create_bioseq_chat_service() -> BioSeqChatService | MockBioSeqChatService:
    load_env_file(DEFAULT_ENV_PATH)
    backend_mode = os.getenv("BIOSEQ_BACKEND", "runtime").strip().lower()
    if backend_mode == "mock":
        return MockBioSeqChatService()
    if backend_mode not in {"runtime", "bioseq", "bioseq_retriever"}:
        raise ValueError("BIOSEQ_BACKEND must be one of: 'runtime', 'bioseq', 'bioseq_retriever', or 'mock'.")

    from backend.agents_core.retriever_agent.runtime_agent import BioSeqRuntimeSessionAgent
    from backend.agents_core.shared.services.persistence import create_persistence_resources

    exit_stack = ExitStack()
    persistence = create_persistence_resources(os.getenv("SUPABASE_DB_URL"), exit_stack)
    exit_stack.callback(persistence.session_repository.close)

    agent = BioSeqRuntimeSessionAgent(persistence=persistence)
    retriever_pipeline = BioSeqRetrieverPipeline(enable_runtime_retriever=True)
    chat_llm_service = ChatLLMService()
    service = BioSeqChatService(
        agent=agent,
        retriever_pipeline=retriever_pipeline,
        chat_llm_service=chat_llm_service,
    )
    service._exit_stack = exit_stack  # Keep persistence contexts alive while Streamlit caches the service.
    return service

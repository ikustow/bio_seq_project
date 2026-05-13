__all__ = [
    "BioSeqChatService",
    "BioSeqRetrieverPipeline",
    "MockBioSeqChatService",
    "create_bioseq_chat_service",
]


def __getattr__(name: str):
    if name in {"BioSeqChatService", "MockBioSeqChatService"}:
        from .bioseq_chat import BioSeqChatService, MockBioSeqChatService

        return {"BioSeqChatService": BioSeqChatService, "MockBioSeqChatService": MockBioSeqChatService}[name]
    if name == "create_bioseq_chat_service":
        from .service_factory import create_bioseq_chat_service

        return create_bioseq_chat_service
    if name == "BioSeqRetrieverPipeline":
        from .retriever_pipeline import BioSeqRetrieverPipeline

        return BioSeqRetrieverPipeline
    raise AttributeError(name)

__all__ = [
    "BioSeqChatService",
    "GraphRetrievalService",
    "MockBioSeqChatService",
    "ProteinLookupHit",
    "BioSeqRetrieverPipeline",
    "create_bioseq_chat_service",
    "create_bioseq_retriever_graph_agent",
]


def __getattr__(name: str):
    if name in {"BioSeqChatService", "MockBioSeqChatService"}:
        from .bioseq_chat import BioSeqChatService, MockBioSeqChatService

        return {"BioSeqChatService": BioSeqChatService, "MockBioSeqChatService": MockBioSeqChatService}[name]
    if name in {"GraphRetrievalService", "ProteinLookupHit"}:
        from .graph_retrieval import GraphRetrievalService, ProteinLookupHit

        return {"GraphRetrievalService": GraphRetrievalService, "ProteinLookupHit": ProteinLookupHit}[name]
    if name == "create_bioseq_chat_service":
        from .service_factory import create_bioseq_chat_service

        return create_bioseq_chat_service
    if name == "create_bioseq_retriever_graph_agent":
        from .service_factory import create_bioseq_retriever_graph_agent

        return create_bioseq_retriever_graph_agent
    if name == "BioSeqRetrieverPipeline":
        from .retriever_pipeline import BioSeqRetrieverPipeline

        return BioSeqRetrieverPipeline
    raise AttributeError(name)

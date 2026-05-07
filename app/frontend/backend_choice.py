"""Search-backend selection for the Streamlit frontend.

Keeps which retrieval engine the active session uses (``graph`` for the
current Neo4j-backed agent, ``embeddings`` for the legacy ProtT5+FAISS
pipeline). Per-tab state in ``st.session_state``; restored from the active
session's row in ``chat_sessions`` on reload so the choice survives F5.
"""

from __future__ import annotations

import streamlit as st

BACKEND_GRAPH = "graph"
BACKEND_EMBEDDINGS = "embeddings"

ALL_BACKENDS: tuple[str, ...] = (BACKEND_GRAPH, BACKEND_EMBEDDINGS)

DEFAULT_BACKEND = BACKEND_GRAPH  # TODO: flip to BACKEND_EMBEDDINGS when ready

LABELS: dict[str, str] = {
    BACKEND_GRAPH: "Graph (Neo4j)",
    BACKEND_EMBEDDINGS: "Embeddings (ProtT5 + FAISS)",
}

DESCRIPTIONS: dict[str, str] = {
    BACKEND_GRAPH: (
        "Looks up the prepared protein graph in Neo4j by sequence hash. "
        "Fast, no local model. Requires the sequence to already be in the graph."
    ),
    BACKEND_EMBEDDINGS: (
        "Embeds the input with ProtT5, searches a local FAISS index, then "
        "reranks via the configured LLM embedder. Works on any sequence but "
        "needs the H5/index files locally and ~2 GB of model weights."
    ),
}


def get_backend() -> str:
    return st.session_state.get("search_backend") or DEFAULT_BACKEND


def set_backend(value: str) -> None:
    if value not in ALL_BACKENDS:
        raise ValueError(f"Unknown backend: {value!r}")
    st.session_state.search_backend = value


def label_for(backend: str) -> str:
    return LABELS.get(backend, backend)


def description_for(backend: str) -> str:
    return DESCRIPTIONS.get(backend, "")

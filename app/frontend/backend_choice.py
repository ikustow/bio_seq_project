"""Search-backend identity for the Streamlit frontend.

The UI runs on a single retrieval backend — the legacy ProtT5+FAISS
embeddings pipeline. This module exists to keep one stable name for that
backend across the codebase (chat_pipeline, sidebar, persisted session
rows). It is a single-backend module today; if a second backend comes
back, restore the radio in ``session_sidebar`` and the dispatch in
``chat_pipeline.run_turn``.
"""

from __future__ import annotations

BACKEND_EMBEDDINGS = "embeddings"

DEFAULT_BACKEND = BACKEND_EMBEDDINGS


def get_backend() -> str:
    return DEFAULT_BACKEND

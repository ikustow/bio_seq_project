"""Runtime switches for the Streamlit frontend."""

from __future__ import annotations

import os


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}

# When True, every chat prompt is sent through ``chat_pipeline.run_turn``
# (live embeddings retriever + chat-LLM follow-up). When False, the UI
# falls back to the scripted-demo flow in ``mock.conversation``.
USE_VECTOR_DB_MODE: bool = True

# Developer-only panels stay wired for diagnostics but are hidden from the
# normal UI unless explicitly requested.
SHOW_DEBUG_PANELS: bool = _truthy(os.getenv("BIOSEQ_SHOW_DEBUG_PANELS"))

"""Search-backend identity for the Streamlit frontend."""

from __future__ import annotations

BACKEND_RUNTIME = "bioseq_runtime"

DEFAULT_BACKEND = BACKEND_RUNTIME


def get_backend() -> str:
    return DEFAULT_BACKEND

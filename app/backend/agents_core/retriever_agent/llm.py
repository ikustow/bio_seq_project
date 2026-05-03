from __future__ import annotations

import os
from typing import Callable

from backend.agents_core.session_agent.config import DEFAULT_MODEL

DEFAULT_MISTRAL_MODEL = "mistral-small-latest"


def select_llm_provider(provider: str | None = None) -> str:
    requested = (provider or os.getenv("BIOSEQ_LLM_PROVIDER") or "").strip().lower()
    if requested:
        if requested not in {"openai", "mistral"}:
            raise ValueError("BIOSEQ_LLM_PROVIDER must be either 'openai' or 'mistral'.")
        return requested
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("MISTRAL_API_KEY"):
        return "mistral"
    return "openai"


def require_llm_api_key(provider: str) -> None:
    if provider == "mistral":
        if not os.getenv("MISTRAL_API_KEY"):
            raise ValueError("MISTRAL_API_KEY is missing; set BIOSEQ_LLM_PROVIDER=openai or use deterministic extraction.")
        return
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is missing; set BIOSEQ_LLM_PROVIDER=mistral or use deterministic extraction.")


def create_extraction_llm_factory(provider: str | None = None, model: str | None = None) -> Callable[[], object]:
    selected_provider = select_llm_provider(provider)
    require_llm_api_key(selected_provider)
    if selected_provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        selected_model = model or os.getenv("MISTRAL_MODEL", DEFAULT_MISTRAL_MODEL)
        return lambda: ChatMistralAI(model=selected_model, temperature=0)

    from langchain_openai import ChatOpenAI

    selected_model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    return lambda: ChatOpenAI(model=selected_model, temperature=0)

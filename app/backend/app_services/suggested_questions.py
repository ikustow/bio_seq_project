"""LangChain-backed generation of follow-up question chips."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.agents_core.suggested_questions_agent import SuggestedQuestionsAgent, normalize_questions


OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_CHAT_MODEL_ENV = "BIOSEQ_OPENAI_CHAT_MODEL"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
MISTRAL_API_KEY_ENV = "MISTRAL_API_KEY"
THINK_PROVIDER_ENV = "BIOSEQ_THINK_PROVIDER"
THINK_OPENAI_MODEL_ENV = "BIOSEQ_THINK_OPENAI_MODEL"
THINK_MISTRAL_MODEL_ENV = "BIOSEQ_THINK_MISTRAL_MODEL"
THINK_TIMEOUT_ENV = "BIOSEQ_THINK_TIMEOUT_SECONDS"
OPENAI_DEFAULT_MODEL = "gpt-4.1-nano"
MISTRAL_DEFAULT_MODEL = "mistral-small-latest"
DEFAULT_TIMEOUT_SECONDS = 12


@dataclass
class SuggestedQuestionsRequest:
    user_message: str
    assistant_message: str
    history: list[dict[str, Any]] = field(default_factory=list)
    selected_candidate: dict[str, Any] | None = None
    provider_override: str | None = None


@dataclass
class SuggestedQuestionsResponse:
    questions: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class SuggestedQuestionsProvider(Protocol):
    name: str

    def generate(self, request: SuggestedQuestionsRequest) -> SuggestedQuestionsResponse: ...


class SuggestedQuestionsService:
    def __init__(
        self,
        openai_provider: SuggestedQuestionsProvider | None = None,
        mistral_provider: SuggestedQuestionsProvider | None = None,
    ) -> None:
        self._openai = openai_provider
        self._mistral = mistral_provider

    def generate(self, request: SuggestedQuestionsRequest) -> SuggestedQuestionsResponse:
        try:
            provider = self._select_provider(request.provider_override)
        except RuntimeError as exc:
            return SuggestedQuestionsResponse(warnings=[str(exc)])
        try:
            response = provider.generate(request)
        except Exception as exc:
            return SuggestedQuestionsResponse(
                provider=getattr(provider, "name", None),
                warnings=[f"Could not generate suggested questions: {exc}"],
                raw={"error": str(exc)},
            )
        questions = normalize_questions(response.questions)
        warnings = list(response.warnings)
        if questions and len(questions) != 3:
            warnings.append("Suggested questions provider did not return exactly three usable questions.")
            questions = []
        return SuggestedQuestionsResponse(
            questions=questions,
            provider=response.provider or getattr(provider, "name", None),
            model=response.model,
            raw=response.raw,
            warnings=warnings,
        )

    def _select_provider(self, override: str | None) -> SuggestedQuestionsProvider:
        configured = (override or os.getenv(THINK_PROVIDER_ENV) or "auto").strip().lower()
        if configured in {"auto", ""}:
            if (os.getenv(OPENAI_API_KEY_ENV) or "").strip():
                return self._openai or OpenAISuggestedQuestionsProvider()
            if (os.getenv(MISTRAL_API_KEY_ENV) or "").strip():
                return self._mistral or MistralSuggestedQuestionsProvider()
            raise RuntimeError(
                "Think Mode is enabled, but no LangChain chat provider is configured. "
                f"Set {OPENAI_API_KEY_ENV} or {MISTRAL_API_KEY_ENV}."
            )
        if configured in {"openai", "chatgpt"}:
            return self._openai or OpenAISuggestedQuestionsProvider()
        if configured in {"mistral", "mistralai"}:
            return self._mistral or MistralSuggestedQuestionsProvider()
        raise RuntimeError(f"{THINK_PROVIDER_ENV} must be 'auto', 'openai', or 'mistral'.")


class OpenAISuggestedQuestionsProvider:
    name = "openai"

    def generate(self, request: SuggestedQuestionsRequest) -> SuggestedQuestionsResponse:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError("Think Mode OpenAI provider requires `langchain-openai`.") from exc

        model = (
            os.getenv(THINK_OPENAI_MODEL_ENV)
            or os.getenv(OPENAI_CHAT_MODEL_ENV)
            or os.getenv(OPENAI_MODEL_ENV)
            or OPENAI_DEFAULT_MODEL
        ).strip()
        llm = ChatOpenAI(model=model, temperature=0.25, timeout=_timeout_seconds())
        output = SuggestedQuestionsAgent(llm).generate(
            user_message=request.user_message,
            assistant_message=request.assistant_message,
            history=request.history,
            selected_candidate=request.selected_candidate,
        )
        return SuggestedQuestionsResponse(
            questions=output.questions,
            provider=self.name,
            model=model,
            raw={"mode": "think_mode", "provider": self.name, "model": model},
        )


class MistralSuggestedQuestionsProvider:
    name = "mistral"

    def generate(self, request: SuggestedQuestionsRequest) -> SuggestedQuestionsResponse:
        try:
            from langchain_mistralai import ChatMistralAI
        except ImportError as exc:
            raise RuntimeError("Think Mode Mistral provider requires `langchain-mistralai`.") from exc

        model = (os.getenv(THINK_MISTRAL_MODEL_ENV) or MISTRAL_DEFAULT_MODEL).strip()
        llm = ChatMistralAI(model=model, temperature=0.25, timeout=_timeout_seconds())
        output = SuggestedQuestionsAgent(llm).generate(
            user_message=request.user_message,
            assistant_message=request.assistant_message,
            history=request.history,
            selected_candidate=request.selected_candidate,
        )
        return SuggestedQuestionsResponse(
            questions=output.questions,
            provider=self.name,
            model=model,
            raw={"mode": "think_mode", "provider": self.name, "model": model},
        )


def _timeout_seconds() -> int:
    try:
        value = int(os.getenv(THINK_TIMEOUT_ENV) or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    return max(1, min(value, 60))

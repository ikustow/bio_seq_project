from __future__ import annotations

from backend.agents_core.suggested_questions_agent import normalize_questions
from backend.agents_core.suggested_questions_agent.tools import (
    infer_open_bioseq_threads,
    render_current_protein_context,
    render_recent_dialogue,
)
from backend.app_services.suggested_questions import (
    OPENAI_API_KEY_ENV,
    SuggestedQuestionsRequest,
    SuggestedQuestionsResponse,
    SuggestedQuestionsService,
)


class FakeProvider:
    name = "fake"

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises
        self.calls = []

    def generate(self, request: SuggestedQuestionsRequest) -> SuggestedQuestionsResponse:
        self.calls.append(request)
        if self.raises:
            raise self.raises
        return SuggestedQuestionsResponse(
            questions=[" What domains matter? ", "What domains matter?", "How strong is evidence?", "Which pathway?"],
            provider=self.name,
            model="fake-model",
        )


def test_normalize_questions_trims_dedupes_and_limits() -> None:
    assert normalize_questions(["  A? ", "a?", "", "B?", "C?", "D?"]) == ["A?", "B?", "C?"]


def test_context_tools_render_candidate_and_history(candidate_dict: dict) -> None:
    protein_text = render_current_protein_context(candidate_dict)
    dialogue = render_recent_dialogue([{"role": "user", "content": "Explain UNC5C"}])
    threads = infer_open_bioseq_threads(candidate_dict, "Database evidence is limited.")

    assert "Accession: O95185" in protein_text
    assert "Ig-like" in protein_text
    assert "user: Explain UNC5C" in dialogue
    assert "domain architecture" in threads
    assert "limitations" in threads


def test_service_returns_disabled_warning_without_provider() -> None:
    response = SuggestedQuestionsService().generate(
        SuggestedQuestionsRequest(user_message="q", assistant_message="a")
    )

    assert response.questions == []
    assert "Think Mode is enabled" in response.warnings[0]


def test_service_selects_openai_provider_and_normalizes(monkeypatch) -> None:
    provider = FakeProvider()
    monkeypatch.setenv(OPENAI_API_KEY_ENV, "dummy")

    response = SuggestedQuestionsService(openai_provider=provider).generate(
        SuggestedQuestionsRequest(user_message="q", assistant_message="a")
    )

    assert response.questions == ["What domains matter?", "How strong is evidence?", "Which pathway?"]
    assert response.provider == "fake"
    assert response.model == "fake-model"
    assert provider.calls[0].user_message == "q"


def test_service_provider_error_is_non_fatal(monkeypatch) -> None:
    monkeypatch.setenv(OPENAI_API_KEY_ENV, "dummy")

    response = SuggestedQuestionsService(openai_provider=FakeProvider(raises=RuntimeError("boom"))).generate(
        SuggestedQuestionsRequest(user_message="q", assistant_message="a")
    )

    assert response.questions == []
    assert "boom" in response.warnings[0]

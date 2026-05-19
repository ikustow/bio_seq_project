from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app_services.chat_llm import (
    CHAT_PROVIDER_ENV,
    OPENAI_API_KEY_ENV,
    PROXY_TOKEN_ENV,
    PROXY_URL_ENV,
    ChatLLMRequest,
    ChatLLMResponse,
    ChatLLMService,
    _build_gemini_contents,
    _build_system_instruction,
    _extract_gemini_text,
    _extract_openai_text,
    build_protein_context,
)


class FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = []

    def generate(self, request: ChatLLMRequest, system_prompt: str) -> ChatLLMResponse:
        self.calls.append((request, system_prompt))
        return ChatLLMResponse(reply=f"{self.name} reply", provider=self.name, model=None, raw={"mode": self.name})


def service() -> tuple[ChatLLMService, FakeProvider, FakeProvider]:
    gemini = FakeProvider("gemini_proxy")
    openai = FakeProvider("openai")
    return ChatLLMService(gemini, openai), gemini, openai


def test_auto_provider_prefers_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    svc, gemini, openai = service()
    monkeypatch.setenv(PROXY_URL_ENV, "https://proxy.test")
    monkeypatch.setenv(PROXY_TOKEN_ENV, "dummy-proxy-token")

    response = svc.generate(ChatLLMRequest(prompt="What does it do?"))

    assert response.provider == "gemini_proxy"
    assert len(gemini.calls) == 1
    assert not openai.calls
    assert "Do not claim that a new database search was performed" in gemini.calls[0][1]


def test_auto_provider_falls_back_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    svc, gemini, openai = service()
    monkeypatch.setenv(OPENAI_API_KEY_ENV, "dummy-openai-key")

    response = svc.generate(ChatLLMRequest(prompt="Explain domains"))

    assert response.provider == "openai"
    assert len(openai.calls) == 1
    assert not gemini.calls


def test_provider_override_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    svc, _gemini, openai = service()
    monkeypatch.setenv(CHAT_PROVIDER_ENV, "gemini_proxy")

    response = svc.generate(ChatLLMRequest(prompt="q", provider_override="openai"))

    assert response.provider == "openai"
    assert len(openai.calls) == 1


def test_auto_provider_without_credentials_raises() -> None:
    svc, _gemini, _openai = service()

    with pytest.raises(RuntimeError, match="Set BIOSEQ_LLM_PROXY_URL"):
        svc.generate(ChatLLMRequest(prompt="q"))


def test_build_protein_context_includes_rich_fields(candidate_dict: dict) -> None:
    text = build_protein_context(candidate_dict)

    assert text is not None
    assert "Accession: O95185" in text
    assert "Match confidence: 98.7%" in text
    assert "**Function:**" in text
    assert "Ig-like (62-159)" in text
    assert "GO:0005515" in text


def test_build_gemini_contents_deduplicates_current_prompt() -> None:
    request = ChatLLMRequest(
        prompt="What domains are present?",
        history=[
            {"role": "assistant", "content": "old welcome before any user message"},
            {"role": "user", "content": "What domains are present?"},
        ],
    )

    contents = _build_gemini_contents(request)

    user_texts = [
        part["text"]
        for item in contents
        if item["role"] == "user"
        for part in item["parts"]
    ]
    # Leading assistant chatter (welcome) is stripped, contents starts with user.
    assert contents[0]["role"] == "user"
    assert "old welcome" not in user_texts
    # The current prompt appears exactly once even though it's already in history.
    assert user_texts.count("What domains are present?") == 1


def test_build_system_instruction_carries_workspace_and_protein_context(
    candidate_dict: dict,
) -> None:
    request = ChatLLMRequest(
        prompt="What domains are present?",
        selected_candidate=candidate_dict,
        objects={
            "seq_test": {
                "id": "seq_test",
                "kind": "sequence",
                "label": "Seq_A",
                "sequence_type": "PROTEIN",
                "length": 147,
                "status": "matched",
                "matches": [
                    {
                        "accession": candidate_dict.get("protein", {}).get("accession"),
                        "match_score": 0.987,
                        "protein": candidate_dict.get("protein") or {},
                    }
                ],
                "selected_match_index": 0,
            }
        },
        selected_object_id="seq_test",
    )

    system_text = _build_system_instruction(request, "BASE_PROMPT")

    assert system_text.startswith("BASE_PROMPT")
    assert "**Workspace objects:**" in system_text
    # Protein context block is wired to the workspace label, not generic.
    assert "**Context for `@" in system_text
    assert "Accession: O95185" in system_text


def test_extract_gemini_text_errors_are_explicit() -> None:
    assert _extract_gemini_text({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}) == "ok"

    with pytest.raises(RuntimeError, match="no candidates"):
        _extract_gemini_text({})
    with pytest.raises(RuntimeError, match="empty text"):
        _extract_gemini_text({"candidates": [{"content": {"parts": []}}]})


def test_extract_openai_text_supports_string_and_chunks() -> None:
    assert _extract_openai_text(SimpleNamespace(content=" answer ")) == "answer"
    assert _extract_openai_text(SimpleNamespace(content=[{"text": "a"}, {"content": "b"}])) == "a\nb"

    with pytest.raises(RuntimeError, match="empty text"):
        _extract_openai_text(SimpleNamespace(content=" "))

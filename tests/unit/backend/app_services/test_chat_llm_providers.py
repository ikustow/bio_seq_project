from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

import backend.app_services.chat_llm as chat_llm
from backend.app_services.chat_llm import (
    OPENAI_API_KEY_ENV,
    OPENAI_CHAT_MODEL_ENV,
    PROXY_TOKEN_ENV,
    PROXY_URL_ENV,
    ChatLLMRequest,
    GeminiProxyChatProvider,
    OpenAIChatProvider,
)


def test_gemini_proxy_posts_expected_payload(monkeypatch: pytest.MonkeyPatch, candidate_dict: dict) -> None:
    captured = {}

    class Response:
        def raise_for_status(self) -> None:
            captured["raised"] = False

        def json(self) -> dict:
            return {"candidates": [{"content": {"parts": [{"text": "grounded answer"}]}}]}

    def fake_post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setenv(PROXY_URL_ENV, "https://proxy.test/gemini")
    monkeypatch.setenv(PROXY_TOKEN_ENV, "dummy-proxy-token")
    monkeypatch.setattr(chat_llm.requests, "post", fake_post)

    response = GeminiProxyChatProvider().generate(
        ChatLLMRequest(prompt="What is known?", selected_candidate=candidate_dict),
        "system",
    )

    assert response.reply == "grounded answer"
    assert captured["url"] == "https://proxy.test/gemini"
    assert captured["headers"]["X-BioSeq-Token"] == "dummy-proxy-token"
    assert captured["json"]["systemInstruction"]["parts"][0]["text"] == "system"
    assert captured["json"]["contents"][0]["parts"][0]["text"].startswith("**Current protein context:**")


def test_gemini_proxy_requires_url_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match=PROXY_URL_ENV):
        GeminiProxyChatProvider().generate(ChatLLMRequest(prompt="q"), "system")

    monkeypatch.setenv(PROXY_URL_ENV, "https://proxy.test")
    with pytest.raises(RuntimeError, match=PROXY_TOKEN_ENV):
        GeminiProxyChatProvider().generate(ChatLLMRequest(prompt="q"), "system")


def test_openai_provider_uses_langchain_adapter(monkeypatch: pytest.MonkeyPatch, candidate_dict: dict) -> None:
    calls = {}

    class FakeChatOpenAI:
        def __init__(self, *, model: str, temperature: float, timeout: int) -> None:
            calls["init"] = (model, temperature, timeout)

        def invoke(self, messages):
            calls["messages"] = messages
            return SimpleNamespace(content="openai answer")

    module = types.ModuleType("langchain_openai")
    module.ChatOpenAI = FakeChatOpenAI
    monkeypatch.setitem(sys.modules, "langchain_openai", module)
    monkeypatch.setenv(OPENAI_API_KEY_ENV, "dummy-openai-key")
    monkeypatch.setenv(OPENAI_CHAT_MODEL_ENV, "gpt-test")

    response = OpenAIChatProvider().generate(
        ChatLLMRequest(prompt="Summarize", selected_candidate=candidate_dict),
        "system",
    )

    assert response.reply == "openai answer"
    assert response.model == "gpt-test"
    assert calls["init"][0] == "gpt-test"
    assert calls["messages"][0].content == "system"
    assert "Current protein context" in calls["messages"][1].content

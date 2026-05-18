from __future__ import annotations

from typing import Any

from backend.app_contracts import BioSeqPipelineSnapshot, ChatTurnRequest
from backend.app_services.bioseq_chat import BioSeqChatService
from backend.app_services.chat_llm import ChatLLMResponse
from backend.app_services.suggested_questions import SuggestedQuestionsResponse


class FakeAgent:
    warnings: list[str] = []

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state = state or {"working_memory": {}}
        self.patches: list[dict[str, Any]] = []
        self.invoked: list[str] = []

    def invoke(self, message, context):
        self.invoked.append(message)
        return {"messages": []}, self.state

    def get_current_state(self, context):
        return self.state

    def update_current_state(self, context, patch):
        self.patches.append(patch)
        self.state = {**self.state, **patch}
        self.state["working_memory"] = {
            **(self.state.get("working_memory") or {}),
            **(patch.get("working_memory") or {}),
        }
        return self.state


class FakeRetriever:
    def __init__(self, snapshot, candidates):
        self.snapshot = snapshot
        self.candidates = candidates
        self.calls = []

    def run(self, message, *, limit=5, search_algorithm=None):
        self.calls.append((message, limit, search_algorithm))
        return self.snapshot, self.candidates


class FakeChatLLM:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if self.raises:
            raise self.raises
        return ChatLLMResponse(
            reply="follow-up answer",
            provider="fake",
            model="fake-model",
            raw={"mode": "chat_llm", "trace": "ok"},
        )


class FakeSuggestedQuestions:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if self.raises:
            raise self.raises
        return SuggestedQuestionsResponse(
            questions=["What domains matter most?", "How strong is the evidence?", "Which pathway is relevant?"],
            provider="fake_think",
            model="fake-think-model",
            raw={"trace": "think"},
        )


def test_sequence_turn_updates_agent_and_returns_candidates(candidate_view) -> None:
    snapshot = BioSeqPipelineSnapshot(
        prompt="seq",
        input_type="SEQUENCE",
        sequence="MALWMRLLPLLALLALWGPGPGAG",
        sequence_type="PROTEIN",
        protein_sequence="MALWMRLLPLLALLALWGPGPGAG",
        active_accession="O95185",
    )
    agent = FakeAgent()
    service = BioSeqChatService(agent, FakeRetriever(snapshot, [candidate_view]), FakeChatLLM())

    result = service.submit_turn(ChatTurnRequest(message="seq", session_id="s1", user_id="u1"))

    assert result.update_card is True
    assert result.candidates[0].protein.accession == "O95185"
    assert "pathways" in result.revealed_sections
    patch = agent.patches[0]
    assert patch["active_accession"] == "O95185"
    assert patch["working_memory"]["last_candidates"][0]["protein"]["accession"] == "O95185"


def test_sequence_turn_with_think_mode_returns_suggested_questions(candidate_view) -> None:
    snapshot = BioSeqPipelineSnapshot(
        prompt="seq",
        input_type="SEQUENCE",
        sequence="MALWMRLLPLLALLALWGPGPGAG",
        sequence_type="PROTEIN",
        protein_sequence="MALWMRLLPLLALLALWGPGPGAG",
        active_accession="O95185",
    )
    suggested = FakeSuggestedQuestions()
    service = BioSeqChatService(
        FakeAgent(),
        FakeRetriever(snapshot, [candidate_view]),
        FakeChatLLM(),
        suggested,
    )

    result = service.submit_turn(
        ChatTurnRequest(message="seq", session_id="s1", user_id="u1", think_mode=True)
    )

    assert result.suggested_questions == [
        "What domains matter most?",
        "How strong is the evidence?",
        "Which pathway is relevant?",
    ]
    assert result.metadata["suggested_questions_provider"] == "fake_think"
    assert suggested.requests[0].selected_candidate["protein"]["accession"] == "O95185"


def test_follow_up_routes_to_chat_llm_without_card_update(candidate_view, candidate_dict) -> None:
    snapshot = BioSeqPipelineSnapshot(prompt="tell me more", input_type="TEXT", context="tell me more")
    agent = FakeAgent({"working_memory": {"last_candidates": [candidate_view.model_dump()]}})
    chat = FakeChatLLM()
    service = BioSeqChatService(agent, FakeRetriever(snapshot, []), chat)

    result = service.submit_turn(
        ChatTurnRequest(
            message="tell me more",
            session_id="s1",
            user_id="u1",
            ui_context={
                "turn_count": 1,
                "messages": [{"role": "user", "content": "tell me more"}],
                "selected_candidate": candidate_dict,
                "selected_candidate_index": 0,
            },
        )
    )

    assert result.update_card is False
    assert result.assistant_message == "follow-up answer"
    assert result.provider == "fake"
    assert chat.requests[0].selected_candidate["protein"]["accession"] == "O95185"
    assert chat.requests[0].history[0]["content"] == "tell me more"
    assert not agent.invoked


def test_follow_up_with_think_mode_returns_suggested_questions(candidate_view, candidate_dict) -> None:
    snapshot = BioSeqPipelineSnapshot(prompt="tell me more", input_type="TEXT", context="tell me more")
    agent = FakeAgent({"working_memory": {"last_candidates": [candidate_view.model_dump()]}})
    suggested = FakeSuggestedQuestions()
    service = BioSeqChatService(agent, FakeRetriever(snapshot, []), FakeChatLLM(), suggested)

    result = service.submit_turn(
        ChatTurnRequest(
            message="tell me more",
            session_id="s1",
            user_id="u1",
            think_mode=True,
            ui_context={
                "turn_count": 1,
                "messages": [{"role": "user", "content": "tell me more"}],
                "selected_candidate": candidate_dict,
                "selected_candidate_index": 0,
            },
        )
    )

    assert result.update_card is False
    assert result.suggested_questions[0] == "What domains matter most?"
    assert result.metadata["suggested_questions_model"] == "fake-think-model"
    assert suggested.requests[0].assistant_message == "follow-up answer"


def test_follow_up_error_still_preserves_card(candidate_view) -> None:
    snapshot = BioSeqPipelineSnapshot(prompt="q", input_type="TEXT")
    agent = FakeAgent({"working_memory": {"last_candidates": [candidate_view.model_dump()]}})
    service = BioSeqChatService(agent, FakeRetriever(snapshot, []), FakeChatLLM(raises=RuntimeError("boom")))

    result = service.submit_turn(
        ChatTurnRequest(message="q", session_id="s1", user_id="u1", ui_context={"turn_count": 2})
    )

    assert result.update_card is False
    assert result.current_mode == "chat_llm_error"
    assert result.assistant_message.startswith("**Chat LLM error:**")
    assert "boom" in result.warnings


def test_think_mode_error_does_not_replace_follow_up_answer(candidate_view, candidate_dict) -> None:
    snapshot = BioSeqPipelineSnapshot(prompt="q", input_type="TEXT")
    agent = FakeAgent({"working_memory": {"last_candidates": [candidate_view.model_dump()]}})

    class ErrorSuggested:
        def generate(self, request):
            return SuggestedQuestionsResponse(warnings=["think failed"])

    service = BioSeqChatService(agent, FakeRetriever(snapshot, []), FakeChatLLM(), ErrorSuggested())

    result = service.submit_turn(
        ChatTurnRequest(
            message="q",
            session_id="s1",
            user_id="u1",
            think_mode=True,
            ui_context={"turn_count": 1, "selected_candidate": candidate_dict},
        )
    )

    assert result.assistant_message == "follow-up answer"
    assert result.suggested_questions == []
    assert "think failed" in result.warnings

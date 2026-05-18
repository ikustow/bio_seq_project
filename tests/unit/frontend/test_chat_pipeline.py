from __future__ import annotations

from types import SimpleNamespace

import chat_pipeline
from backend.app_contracts import ChatTurnResult, SessionSnapshot


class State(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class FakeService:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def submit_turn(self, request):
        self.requests.append(request)
        return self.response


def install_state(monkeypatch, state: State) -> None:
    monkeypatch.setattr(chat_pipeline, "st", SimpleNamespace(session_state=state))


def test_build_ui_context_uses_selected_candidate(monkeypatch, candidate_dict) -> None:
    state = State(
        messages=[{"role": "user", "content": "q"}],
        candidates=[{"protein": {"accession": "A"}}, candidate_dict],
        selected_candidate_idx=1,
    )
    install_state(monkeypatch, state)
    monkeypatch.setattr(chat_pipeline.session_db_adapter, "is_persistent", lambda: True)
    monkeypatch.setattr(
        chat_pipeline.session_db_adapter,
        "load_session",
        lambda sid: {"working_memory": {"turn_count": "2"}},
    )

    context = chat_pipeline._build_ui_context("s1")

    assert context["turn_count"] == 2
    assert context["selected_candidate_index"] == 1
    assert context["selected_candidate"]["protein"]["accession"] == "O95185"


def test_run_turn_follow_up_keeps_existing_card_and_questions(monkeypatch, candidate_dict) -> None:
    state = State(
        user_id="u1",
        session_id="s1",
        think_mode_enabled=True,
        candidates=[candidate_dict],
        selected_candidate_idx=0,
        card_sections_revealed={"header", "pathways"},
        query_protein_sequence="MALW",
        messages=[{"role": "user", "content": "previous"}],
    )
    install_state(monkeypatch, state)
    saved = {}

    response = ChatTurnResult(
        session_id="s1",
        assistant_message="follow-up",
        session=SessionSnapshot(session_id="s1", user_id="u1"),
        update_card=False,
        current_mode="chat_llm",
        suggested_questions=["What domains matter?", "How strong is evidence?", "Which pathway?"],
        metadata={
            "suggested_questions_provider": "fake",
            "suggested_questions_model": "fake-model",
        },
    )
    service = FakeService(response)

    monkeypatch.setattr(chat_pipeline, "_chat_service", lambda: service)
    monkeypatch.setattr(chat_pipeline.session_db_adapter, "make_context", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(chat_pipeline.session_db_adapter, "get_warnings", lambda: [])
    monkeypatch.setattr(chat_pipeline.session_db_adapter, "is_persistent", lambda: True)
    monkeypatch.setattr(chat_pipeline.session_db_adapter, "load_session", lambda sid: {"working_memory": {"turn_count": 1}})
    monkeypatch.setattr(chat_pipeline, "_sort_by_alignment", lambda q, raw, ui: (raw, ui))

    def fake_save_turn(context, **kwargs):
        saved.update(kwargs)

    monkeypatch.setattr(chat_pipeline.session_db_adapter, "save_turn", fake_save_turn)

    outcome = chat_pipeline._run_turn_backend("tell me more")

    assert outcome["update_card"] is False
    assert outcome["suggested_questions"] == [
        "What domains matter?",
        "How strong is evidence?",
        "Which pathway?",
    ]
    assert outcome["candidates"] == [candidate_dict]
    assert outcome["reveals"] == {"header", "pathways"}
    assert saved["update_candidates"] is False
    assert saved["candidates"] == []
    assert saved["suggested_questions"] == outcome["suggested_questions"]
    assert saved["think_mode"] is True
    assert saved["suggested_questions_metadata"]["suggested_questions_provider"] == "fake"
    assert service.requests[0].ui_context["selected_candidate"]["protein"]["accession"] == "O95185"
    assert service.requests[0].think_mode is True

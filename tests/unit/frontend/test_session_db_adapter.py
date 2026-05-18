from __future__ import annotations

import copy

import session_db_adapter
from backend.agents_core.shared.models import AppContext


class MemoryRepo:
    def __init__(self, row: dict | None = None) -> None:
        self.row = copy.deepcopy(row) if row else None

    def get_session(self, session_id):
        return copy.deepcopy(self.row)

    def upsert_session(self, context, state):
        self.row = {"session_id": context.session_id, "user_id": context.user_id, **copy.deepcopy(state)}


def context() -> AppContext:
    return AppContext(user_id="u1", session_id="s1")


def test_save_retriever_turn_stores_candidates(monkeypatch, candidate_dict) -> None:
    repo = MemoryRepo()
    monkeypatch.setattr(session_db_adapter, "get_repository", lambda: repo)

    row = session_db_adapter.save_turn(
        context(),
        user_message="seq",
        assistant_message="hit",
        candidates=[candidate_dict],
        revealed_sections={"header", "pathways"},
        current_mode="bioseq_runtime_retriever",
        query_protein_sequence="MALW",
    )

    wm = row["working_memory"]
    assert row["active_accession"] == "O95185"
    assert wm["turn_count"] == 1
    assert wm["last_candidates"][0]["protein"]["accession"] == "O95185"
    assert wm["last_revealed_sections"] == ["header", "pathways"]
    assert wm["last_query_protein_sequence"] == "MALW"


def test_save_follow_up_preserves_existing_card_state(monkeypatch, candidate_dict) -> None:
    saved = {
        "proteins": [{"accession": "O95185", "protein_name": "UNC5C"}],
        "active_accession": "O95185",
        "working_set_ids": ["O95185"],
        "last_tool_results_summary": "Returned 1 candidate(s); top: O95185",
        "working_memory": {
            "turn_count": 1,
            "last_candidates": [candidate_dict],
            "last_revealed_sections": ["header", "pathways"],
            "last_query_protein_sequence": "MALW",
        },
    }
    repo = MemoryRepo(saved)
    monkeypatch.setattr(session_db_adapter, "get_repository", lambda: repo)

    row = session_db_adapter.save_turn(
        context(),
        user_message="follow up",
        assistant_message="answer",
        candidates=[],
        revealed_sections=None,
        current_mode="chat_llm",
        update_candidates=False,
        suggested_questions=["What domains matter?", "How strong is evidence?", "Which pathway?"],
        think_mode=True,
        suggested_questions_metadata={"suggested_questions_provider": "fake"},
    )

    wm = row["working_memory"]
    assert row["active_accession"] == "O95185"
    assert row["proteins"] == saved["proteins"]
    assert row["working_set_ids"] == ["O95185"]
    assert wm["turn_count"] == 2
    assert wm["last_candidates"] == [candidate_dict]
    assert wm["last_revealed_sections"] == ["header", "pathways"]
    assert wm["last_query_protein_sequence"] == "MALW"
    assert wm["last_suggested_questions"] == [
        "What domains matter?",
        "How strong is evidence?",
        "Which pathway?",
    ]
    assert wm["think_mode_last_enabled"] is True
    assert [m["role"] for m in wm["messages"]] == ["user", "assistant"]
    assert wm["messages"][1]["metadata"]["suggested_questions_provider"] == "fake"
    assert wm["messages"][1]["metadata"]["suggested_questions"][0] == "What domains matter?"


def test_extract_messages_preserves_suggested_question_metadata() -> None:
    row = {
        "working_memory": {
            "messages": [
                {"role": "user", "content": "q"},
                {
                    "role": "assistant",
                    "content": "a",
                    "metadata": {
                        "suggested_questions": ["Follow up 1?", "Follow up 2?", "Follow up 3?"],
                        "think_mode": True,
                    },
                },
            ]
        }
    }

    messages = session_db_adapter.extract_messages(row)

    assert messages[1]["suggested_questions"] == ["Follow up 1?", "Follow up 2?", "Follow up 3?"]
    assert messages[1]["metadata"]["think_mode"] is True

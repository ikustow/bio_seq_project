"""Smoke test for the first-turn router (retriever -> chat-LLM stub).

Verifies the dispatch logic in ``chat_pipeline._is_first_turn_in_session``
against a real Supabase row, plus the end-to-end behaviour of the
``chat_llm_pipeline`` stub:

1. New session, no row in DB -> ``_is_first_turn_in_session()`` returns True.
2. After a save_turn that bumps ``turn_count`` to 1, the same check returns
   False — subsequent turns route to the chat-LLM stub.
3. ``chat_llm_pipeline.run_turn_chat_llm(...)`` writes a row tagged
   ``current_mode='chat_llm_stub'``, leaves ``proteins`` /
   ``last_candidates`` / ``active_accession`` from the prior retriever
   turn untouched, and bumps ``turn_count`` to 2.

Usage:
    streamlit_ui/.venv/Scripts/python.exe scripts/smoke_first_turn_router.py
"""

from __future__ import annotations

import sys
import types
import uuid
from pathlib import Path


# ---------------------------------------------------------------------------
# Fake streamlit (no real server) so chat_pipeline / chat_llm_pipeline imports
# work in a plain Python process.
# ---------------------------------------------------------------------------


class _AttrDict(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


_session_state = _AttrDict()


def _cache_resource(*args, **kwargs):
    if args and callable(args[0]) and not kwargs:
        return args[0]

    def deco(fn):
        return fn

    return deco


def _stub(*_a, **_k):
    return None


def _stub_ctx():
    class _C:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _C()


fake_streamlit = types.ModuleType("streamlit")
fake_streamlit.session_state = _session_state
fake_streamlit.cache_resource = _cache_resource
fake_streamlit.cache_data = _cache_resource
fake_streamlit.spinner = lambda *_a, **_k: _stub_ctx()
fake_streamlit.warning = _stub
fake_streamlit.info = _stub
fake_streamlit.error = _stub
sys.modules["streamlit"] = fake_streamlit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "app"))
from backend.agents_core.shared.config import DEFAULT_ENV_PATH, load_env_file

load_env_file(DEFAULT_ENV_PATH)

import os  # noqa: E402

if not os.getenv("SUPABASE_DB_URL"):
    print("FAIL: SUPABASE_DB_URL not set")
    sys.exit(1)

sys.path.insert(0, str(PROJECT_ROOT / "app" / "frontend"))


def main() -> int:
    user_id = f"smoke_user_router_{uuid.uuid4().hex[:8]}"
    session_id = f"smoke_session_router_{uuid.uuid4().hex[:8]}"
    _session_state.update({
        "user_id": user_id,
        "session_id": session_id,
        "workspace_id": None,
        "user_role": None,
        "candidates": [],
        "card_sections_revealed": set(),
    })
    print(f"[setup] user_id={user_id}")
    print(f"[setup] session_id={session_id}")

    import session_db_adapter
    import chat_pipeline
    import chat_llm_pipeline
    from backend.agents_core.shared.models import AppContext

    repo = session_db_adapter.get_repository()
    if type(repo).__name__ == "NullSessionRepository":
        print("FAIL: persistence is null")
        return 1
    print(f"[setup] repo: {type(repo).__name__}")

    # 1. Brand-new session: no row -> first turn.
    is_first = chat_pipeline._is_first_turn_in_session()
    print(f"[1] is_first (no row yet): {is_first}")
    assert is_first is True, "expected first turn for new session"
    print("    [ok] empty session -> routed to retriever")

    # 2. Simulate a successful retriever turn by writing one via save_turn.
    fake_candidates = [
        {
            "protein": {
                "accession": "P00001",
                "name": "Test Protein A",
                "gene": "GENA",
                "organism_scientific": "Homo sapiens",
                "function_text": "Test fn",
                "domains": [{"name": "Test Domain", "start": 1, "end": 50, "type": "Domain"}],
                "keywords": ["test"],
                "go_terms": ["GO:0000001"],
                "pubmed_ids": ["1"],
                "xrefs": {"RefSeq": "NP_test"},
                "alphafold_accession": "P00001",
            },
            "match_score": 0.9,
            "rank": 0,
        }
    ]
    ctx = AppContext(user_id=user_id, session_id=session_id)
    session_db_adapter.save_turn(
        ctx,
        user_message="What is this sequence?",
        assistant_message="It is Protein A.",
        candidates=fake_candidates,
        revealed_sections={"header", "keyfacts", "function"},
        current_mode="bioseq_retriever_langgraph",
    )

    is_first_after = chat_pipeline._is_first_turn_in_session()
    print(f"[2] is_first (after first save_turn): {is_first_after}")
    assert is_first_after is False, "expected NOT-first after one turn"
    print("    [ok] turn_count bumped -> routed to chat-LLM")

    # 3. Run the chat-LLM stub. Should preserve cards and tag the row.
    _session_state["candidates"] = fake_candidates  # what UI is rendering
    _session_state["card_sections_revealed"] = {"header", "keyfacts", "function"}

    outcome = chat_llm_pipeline.run_turn_chat_llm("Tell me more about diseases?")
    print(f"[3] stub outcome.update_card: {outcome.get('update_card')}")
    print(f"    stub outcome.backend: {outcome.get('backend')}")
    assert outcome["update_card"] is False, "stub must not update card"
    assert outcome["backend"] == "chat_llm_stub"
    assert "baking" in outcome["reply"].lower(), outcome["reply"]
    assert outcome["candidates"] == fake_candidates, "candidates must be preserved"
    assert outcome["reveals"] == {"header", "keyfacts", "function"}, outcome["reveals"]
    print("    [ok] stub preserves cards, returns baking message")

    # 4. Verify the DB row reflects the stub turn.
    row = session_db_adapter.load_session(session_id)
    assert row is not None
    wm = row.get("working_memory") or {}
    if isinstance(wm, str):
        import json
        wm = json.loads(wm)

    print(f"[4] row.current_mode: {row.get('current_mode')}")
    print(f"    row.working_memory.turn_count: {wm.get('turn_count')}")
    print(f"    row.active_accession: {row.get('active_accession')}")
    print(f"    row.proteins (count): {len(row.get('proteins') or [])}")
    print(f"    last_candidates (count): {len(wm.get('last_candidates') or [])}")

    assert row.get("current_mode") == "chat_llm_stub", row.get("current_mode")
    assert wm.get("turn_count") == 2, wm.get("turn_count")
    assert row.get("active_accession") == "P00001", row.get("active_accession")
    assert len(row.get("proteins") or []) == 1, "proteins must be preserved"
    assert len(wm.get("last_candidates") or []) == 1, "last_candidates must be preserved"
    print("    [ok] stub turn persisted; cards untouched")

    # 5. Verify message log has all 4 messages (1 retriever Q+A, 1 stub Q+A).
    msgs = session_db_adapter.extract_messages(row)
    print(f"[5] message log: {len(msgs)} entries")
    assert len(msgs) == 4, [m["role"] for m in msgs]
    assert msgs[-1]["role"] == "assistant"
    assert "baking" in msgs[-1]["content"].lower()
    print("    [ok] message transcript continuous across retriever->stub")

    # 6. Edge: SUPABASE_DB_URL absent -> router falls back to retriever path.
    #    Simulated by toggling the cache. We can't actually clear the
    #    @st.cache_resource cache here, so just verify the function-level
    #    fallback for missing session_id.
    _session_state["session_id"] = None
    is_first_no_session = chat_pipeline._is_first_turn_in_session()
    print(f"[6] is_first (no session_id): {is_first_no_session}")
    assert is_first_no_session is True, "no session_id must fall back to retriever"
    print("    [ok] missing session_id -> retriever fallback")

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

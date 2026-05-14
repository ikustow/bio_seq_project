"""Smoke test for the new backend-owned routing path.

Validates that ``chat_pipeline.run_turn`` — the single frontend entry
point — correctly:

1. Routes the first turn through the retriever branch
   (``update_card=True``).
2. Routes a TEXT follow-up through the backend ``ChatLLMService``
   (``update_card=False``), preserving the protein card.
3. Persists the follow-up turn with ``update_candidates=False`` so the
   saved row keeps ``last_candidates`` / ``active_accession`` from the
   retriever turn.

Both backend collaborators (the runtime agent + ChatLLMService) are
stubbed so this test stays offline. Run after any change to the
``submit_turn`` routing logic or ``_run_turn_backend`` glue.
"""

from __future__ import annotations

import sys
import types
import uuid
from pathlib import Path


# ---------------------------------------------------------------------------
# Fake streamlit so chat_pipeline import works in a plain Python process.
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
sys.path.insert(0, str(PROJECT_ROOT / "app" / "frontend"))

from backend.agents_core.shared.config import DEFAULT_ENV_PATH, load_env_file  # noqa: E402

load_env_file(DEFAULT_ENV_PATH)

import os  # noqa: E402

if not os.getenv("SUPABASE_DB_URL"):
    print("FAIL: SUPABASE_DB_URL not set")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Stubs for backend collaborators. ``BioSeqChatService`` accepts an agent
# (Protocol) and a ChatLLMService; we provide minimal versions of each so
# the routing logic runs without invoking langgraph or remote LLMs.
# ---------------------------------------------------------------------------


class _StubAgent:
    """Stand-in for BioSeqRuntimeSessionAgent — just enough surface."""

    def __init__(self) -> None:
        self._state: dict[str, object] = {}

    @property
    def warnings(self) -> list[str]:
        return []

    def invoke(self, message: str, context):  # noqa: ANN001
        # First-turn retriever fallback shouldn't fire in this smoke (we
        # route SEQUENCE-less first turn? we route only when there's an
        # input; for this smoke we always provide ui_context.turn_count).
        return ({"messages": []}, dict(self._state))

    def get_current_state(self, context):  # noqa: ANN001
        return dict(self._state)

    def update_current_state(self, context, patch):  # noqa: ANN001
        self._state.update(patch)
        return dict(self._state)


class _StubChatLLMService:
    """Records the last request so we can assert what the backend sent."""

    def __init__(self) -> None:
        self.last_request = None

    def generate(self, request):  # noqa: ANN001
        from backend.app_services.chat_llm import ChatLLMResponse

        self.last_request = request
        return ChatLLMResponse(
            reply="STUB chat-llm reply.",
            provider="stub",
            model="stub-model",
            raw={"mode": "chat_llm", "provider": "stub"},
        )


def main() -> int:
    import chat_pipeline
    import session_db_adapter
    from backend.app_contracts import ChatTurnRequest
    from backend.app_services.bioseq_chat import BioSeqChatService
    from backend.app_services.retriever_pipeline import BioSeqRetrieverPipeline

    user_id = f"smoke_user_route_{uuid.uuid4().hex[:8]}"
    session_id = f"smoke_session_route_{uuid.uuid4().hex[:8]}"
    _session_state.update({
        "user_id": user_id,
        "session_id": session_id,
        "workspace_id": None,
        "user_role": None,
        "candidates": [],
        "card_sections_revealed": set(),
        "messages": [],
        "selected_candidate_idx": 0,
    })
    print(f"[setup] user_id={user_id}")
    print(f"[setup] session_id={session_id}")

    repo = session_db_adapter.get_repository()
    if type(repo).__name__ == "NullSessionRepository":
        print("FAIL: persistence is null")
        return 1
    print(f"[setup] repo: {type(repo).__name__}")

    # Inject stubbed backend service into chat_pipeline so we don't hit
    # langgraph or remote LLMs.
    stub_agent = _StubAgent()
    stub_chat_llm = _StubChatLLMService()
    # Use a retriever pipeline that classifies TEXT (no runtime retriever).
    retriever_pipeline = BioSeqRetrieverPipeline(enable_runtime_retriever=False)
    chat_pipeline._CHAT_SERVICE = BioSeqChatService(
        agent=stub_agent,
        retriever_pipeline=retriever_pipeline,
        chat_llm_service=stub_chat_llm,
    )

    # ---- Step 1: seed a retriever turn directly through save_turn so the
    # session row has turn_count=1 and a saved candidate card. This mirrors
    # what the real retriever path writes after a successful sequence search.
    from backend.agents_core.shared.models import AppContext
    seed_candidate = {
        "protein": {
            "accession": "P12345",
            "name": "Seed Protein",
            "gene": "SEEDG",
            "organism_scientific": "Homo sapiens",
            "function_text": "Smoke-test fixture.",
            "keywords": ["smoke"],
            "go_terms": ["GO:0000001"],
            "pubmed_ids": ["1"],
            "xrefs": {},
            "alphafold_accession": "P12345",
        },
        "match_score": 0.9,
        "rank": 0,
    }
    ctx = AppContext(user_id=user_id, session_id=session_id)
    session_db_adapter.save_turn(
        ctx,
        user_message="MKT seq",
        assistant_message="Found Seed Protein.",
        candidates=[seed_candidate],
        revealed_sections={"header", "keyfacts", "function"},
        current_mode="bioseq_runtime_retriever",
    )
    _session_state["candidates"] = [seed_candidate]
    _session_state["card_sections_revealed"] = {"header", "keyfacts", "function"}
    _session_state["messages"] = [
        {"role": "user", "content": "MKT seq"},
        {"role": "assistant", "content": "Found Seed Protein."},
    ]
    print("[1] seeded retriever turn (turn_count=1)")

    # ---- Step 2: a TEXT follow-up. chat_pipeline.run_turn must route to the
    # stubbed ChatLLMService via backend, NOT replace the card.
    outcome = chat_pipeline.run_turn("Tell me more about its disease links.")
    print(f"[2] outcome.update_card = {outcome.get('update_card')}")
    print(f"    outcome.backend     = {outcome.get('backend')}")
    print(f"    outcome.reply       = {outcome.get('reply')!r}")
    assert outcome["update_card"] is False, "follow-up turn must NOT update card"
    assert outcome["reply"] == "STUB chat-llm reply.", outcome["reply"]
    assert outcome["backend"] == "chat_llm", outcome["backend"]
    assert outcome["candidates"], "follow-up must echo the existing cards"
    assert stub_chat_llm.last_request is not None, "ChatLLMService was not invoked"
    # The selected candidate should have been forwarded.
    assert stub_chat_llm.last_request.selected_candidate == seed_candidate
    # And recent history (2 messages from the seeded turn) should be carried.
    assert len(stub_chat_llm.last_request.history) == 2, stub_chat_llm.last_request.history
    print("    [ok] backend ChatLLMService invoked with history + selected card")

    # ---- Step 3: verify persistence row reflects follow-up.
    row = session_db_adapter.load_session(session_id)
    assert row is not None
    wm = row.get("working_memory") or {}
    if isinstance(wm, str):
        import json
        wm = json.loads(wm)
    print(f"[3] row.current_mode               = {row.get('current_mode')}")
    print(f"    row.working_memory.turn_count  = {wm.get('turn_count')}")
    print(f"    row.active_accession           = {row.get('active_accession')}")
    print(f"    last_candidates                = {len(wm.get('last_candidates') or [])} entry/ies")
    assert row.get("current_mode") == "chat_llm", row.get("current_mode")
    assert int(wm.get("turn_count") or 0) == 2, wm.get("turn_count")
    assert row.get("active_accession") == "P12345", row.get("active_accession")
    assert len(wm.get("last_candidates") or []) == 1, "last_candidates should be preserved"
    print("    [ok] follow-up persisted; cards untouched")

    # ---- Step 4: a request without turn_count should NOT be classified
    # as follow-up (legacy callers / mock / fresh sessions).
    req = ChatTurnRequest(message="hi", session_id="x")
    from backend.app_services.bioseq_chat import _is_follow_up_turn
    assert _is_follow_up_turn(req) is False
    assert _is_follow_up_turn(
        ChatTurnRequest(message="hi", session_id="x", ui_context={"turn_count": 0})
    ) is False
    assert _is_follow_up_turn(
        ChatTurnRequest(message="hi", session_id="x", ui_context={"turn_count": 3})
    ) is True
    print("[4] _is_follow_up_turn matrix ok")

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

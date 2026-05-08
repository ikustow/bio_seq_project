"""Smoke test for the embeddings backend dispatch (no Streamlit, no torch).

Verifies that on a laptop WITHOUT the heavy ML deps installed:
1. Selecting the embeddings backend doesn't crash on import.
2. ``embeddings_pipeline.run_turn_embeddings(...)`` returns a friendly preflight
   error rather than raising.
3. The error turn IS still persisted to ``public.chat_sessions`` with
   ``current_mode='embeddings_retriever'`` so the sidebar history stays
   honest.

Usage:
    streamlit_ui/.venv/Scripts/python.exe scripts/smoke_embeddings_dispatch.py
"""

from __future__ import annotations

import sys
import types
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Stand up a fake ``streamlit`` so embeddings_pipeline imports cleanly
# without a running Streamlit server.
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
    """Pass-through shim for @st.cache_resource."""
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

# Load env so SUPABASE_DB_URL is available before session_db_adapter is imported.
sys.path.insert(0, str(PROJECT_ROOT / "app"))
from backend.agents_core.shared.config import DEFAULT_ENV_PATH, load_env_file

load_env_file(DEFAULT_ENV_PATH)

import os

if not os.getenv("SUPABASE_DB_URL"):
    print("FAIL: SUPABASE_DB_URL not set; cannot verify DB persistence.")
    sys.exit(1)

sys.path.insert(0, str(PROJECT_ROOT / "app" / "frontend"))


def main() -> int:
    user_id = f"smoke_user_emb_{uuid.uuid4().hex[:8]}"
    session_id = f"smoke_session_emb_{uuid.uuid4().hex[:8]}"
    _session_state.update({
        "user_id": user_id,
        "session_id": session_id,
        "workspace_id": None,
        "user_role": None,
    })

    print(f"[setup] user_id={user_id} session_id={session_id}")

    import session_db_adapter
    import embeddings_pipeline

    repo = session_db_adapter.get_repository()
    if type(repo).__name__ == "NullSessionRepository":
        print("FAIL: persistence is in-memory; SUPABASE_DB_URL not connected.")
        return 1
    print(f"[setup] repo: {type(repo).__name__}")

    # 1. Verify preflight catches missing deps cleanly.
    preflight = embeddings_pipeline._preflight_check()
    print(f"[1] preflight result: {preflight[:100] if preflight else 'OK'}...")
    assert preflight is not None, "expected preflight to flag missing deps"
    assert "dependencies are not installed" in preflight, preflight
    print("    [ok] preflight reports missing deps")

    # 2. Run a turn — should NOT raise; should return error reply + persist row.
    print("[2] running embeddings_pipeline.run_turn_embeddings(...)")
    outcome = embeddings_pipeline.run_turn_embeddings(
        "MALWMRLLPLLALLALWGPDPAAAFVNQHLCG identify this please"
    )
    assert isinstance(outcome, dict), "expected dict result"
    assert outcome["candidates"] == [], outcome["candidates"]
    assert outcome["reply"], "reply must not be empty"
    assert "dependencies are not installed" in outcome["reply"], outcome["reply"]
    print(f"    [ok] reply (truncated): {outcome['reply'][:80]}...")

    # 3. Verify a row was written and tagged as embeddings_retriever.
    row = session_db_adapter.load_session(session_id)
    assert row is not None, "expected a chat_sessions row"
    print(f"[3] row.current_mode = {row.get('current_mode')}")
    assert row.get("current_mode") == "embeddings_retriever", row.get("current_mode")
    print("    [ok] row tagged as embeddings_retriever")

    # 4. Verify message log was appended (user + assistant).
    msgs = session_db_adapter.extract_messages(row)
    print(f"[4] message count: {len(msgs)}")
    assert len(msgs) == 2, msgs
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    print("    [ok] turn persisted with both messages")

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

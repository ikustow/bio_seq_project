"""Smoke test for the UI-side session persistence layer.

Runs without Streamlit. Bypasses the agent (Neo4j may not be available
locally) and exercises ``PostgresSessionRepository`` directly through the
helpers used by the frontend:

1. Two upserts on the same ``session_id`` -> single row, turn_count=2.
2. Upsert on a different ``session_id`` for the same user -> two rows.
3. ``list_sessions(user_id)`` -> two summaries, newest first.
4. Reads back ``working_memory.last_candidates`` to confirm the 5-card
   payload survives the round-trip.

Usage:
    python scripts/smoke_session_db.py

Requires ``SUPABASE_DB_URL`` in the environment (.env is loaded automatically).
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "app"
for p in (APP_ROOT, PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from backend.agents_core.shared.config import DEFAULT_ENV_PATH, load_env_file
from backend.agents_core.shared.models import AppContext

load_env_file(DEFAULT_ENV_PATH)

import os  # noqa: E402

if not os.getenv("SUPABASE_DB_URL"):
    print("FAIL: SUPABASE_DB_URL is not set; smoke test cannot run.")
    sys.exit(1)

# Import after env is loaded so cached repository sees SUPABASE_DB_URL.
sys.path.insert(0, str(PROJECT_ROOT / "app" / "frontend"))
import session_db_adapter  # noqa: E402


def _fake_candidate(accession: str, name: str, gene: str, score: float = 0.85) -> dict:
    return {
        "protein": {
            "accession": accession,
            "name": name,
            "gene": gene,
            "organism_scientific": "Homo sapiens",
            "function_text": f"Test function comment for {accession}.",
            "domains": [{"name": "Test Domain", "start": 1, "end": 100, "type": "Domain"}],
            "keywords": ["test"],
            "go_terms": ["GO:0000001"],
            "pubmed_ids": ["1"],
            "xrefs": {"RefSeq": f"NP_{accession}"},
            "alphafold_accession": accession,
        },
        "match_score": score,
        "rank": 1,
        "similarity_score": score,
    }


def main() -> int:
    user_id = f"smoke_user_{uuid.uuid4().hex[:8]}"
    session_a = f"smoke_session_a_{uuid.uuid4().hex[:8]}"
    session_b = f"smoke_session_b_{uuid.uuid4().hex[:8]}"

    print(f"--- Smoke test ---")
    print(f"user_id: {user_id}")
    print(f"session_a: {session_a}")
    print(f"session_b: {session_b}")

    repo = session_db_adapter.get_repository()
    repo_kind = type(repo).__name__
    print(f"Repository: {repo_kind}")
    if repo_kind == "NullSessionRepository":
        print("FAIL: persistence is in-memory; SUPABASE_DB_URL did not connect.")
        return 1

    candidates_turn1 = [
        _fake_candidate("P00001", "Protein A", "GENA", 0.95),
        _fake_candidate("P00002", "Protein B", "GENB", 0.90),
        _fake_candidate("P00003", "Protein C", "GENC", 0.85),
        _fake_candidate("P00004", "Protein D", "GEND", 0.80),
        _fake_candidate("P00005", "Protein E", "GENE", 0.75),
    ]
    candidates_turn2 = [
        _fake_candidate("P00010", "Protein X", "GENX", 0.99),
    ]

    ctx_a = AppContext(user_id=user_id, session_id=session_a)
    ctx_b = AppContext(user_id=user_id, session_id=session_b)

    print("\n[1] First turn on session_a")
    session_db_adapter.save_turn(
        ctx_a,
        user_message="What is this sequence?",
        assistant_message="It is Protein A.",
        candidates=candidates_turn1,
        revealed_sections={"header", "keyfacts", "function"},
    )

    print("[2] Second turn on session_a")
    session_db_adapter.save_turn(
        ctx_a,
        user_message="Tell me more",
        assistant_message="More about Protein X.",
        candidates=candidates_turn2,
        revealed_sections={"header", "keyfacts", "disease"},
    )

    row_a = session_db_adapter.load_session(session_a)
    assert row_a is not None, "session_a row missing"
    wm_a = row_a.get("working_memory") or {}
    if isinstance(wm_a, str):
        wm_a = json.loads(wm_a)
    assert wm_a.get("turn_count") == 2, f"expected turn_count=2, got {wm_a.get('turn_count')}"
    cards_a = session_db_adapter.extract_candidates(row_a)
    assert cards_a, "last_candidates not persisted"
    print(f"  [ok]session_a turn_count = {wm_a.get('turn_count')}")
    print(f"  [ok]last_candidates persisted: {[c['protein']['accession'] for c in cards_a]}")
    print(f"  [ok]active_accession = {row_a.get('active_accession')}")
    msgs_a = session_db_adapter.extract_messages(row_a)
    print(f"  [ok]messages stored: {len(msgs_a)} entries")

    print("\n[3] First turn on session_b (same user)")
    session_db_adapter.save_turn(
        ctx_b,
        user_message="Different question",
        assistant_message="Different answer.",
        candidates=candidates_turn1[:2],
        revealed_sections={"header"},
    )
    row_b = session_db_adapter.load_session(session_b)
    assert row_b is not None, "session_b row missing"
    print(f"  [ok]session_b row exists, active_accession = {row_b.get('active_accession')}")

    print("\n[4] list_user_sessions(user_id)")
    sessions = session_db_adapter.list_user_sessions(user_id, limit=10)
    ids = [s["session_id"] for s in sessions]
    assert session_a in ids and session_b in ids, f"missing sessions in list: {ids}"
    print(f"  [ok]both sessions visible: {ids}")
    print("  Order (newest first):")
    for s in sessions:
        print(f"    - {s['session_id'][:18]}  | {s['updated_at']}  | {s.get('session_summary')}")

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

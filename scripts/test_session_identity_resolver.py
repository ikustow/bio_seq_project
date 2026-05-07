"""Unit tests for the session_identity._resolve_id decision table.

Exercises every branch of the cookie hydration state machine without a real
Streamlit runtime. Uses a fake ``st.session_state`` dict and a fake cookie
controller that records ``set`` calls.

Run:
    streamlit_ui/.venv/Scripts/python.exe scripts/test_session_identity_resolver.py
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

# ---------------------------------------------------------------------------
# Stand up a fake ``streamlit`` module so importing session_identity works
# outside a Streamlit run.
# ---------------------------------------------------------------------------

FAKE_STATE: dict = {}


class _AttrDict(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


_session_state = _AttrDict()

fake_streamlit = types.ModuleType("streamlit")
fake_streamlit.session_state = _session_state
sys.modules["streamlit"] = fake_streamlit


# Avoid loading the real cookie library; we'll inject a fake controller
# directly.
class _FakeCookies:
    def __init__(self, initial: dict | None = None):
        self.cookies = dict(initial or {})
        self.set_calls: list[tuple[str, str]] = []

    def getAll(self):
        return dict(self.cookies)

    def set(self, name: str, value: str, **kwargs):
        self.cookies[name] = value
        self.set_calls.append((name, value))


# Block the real lib so _read_cookies_with_state falls back; we'll bypass it
# entirely by calling _resolve_id directly with our fake controller.
sys.modules.setdefault("streamlit_cookies_controller", types.ModuleType("streamlit_cookies_controller"))


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "app" / "frontend"))

import session_identity  # noqa: E402

# Rebind module-level streamlit reference too
session_identity.st = fake_streamlit


def reset_state() -> None:
    _session_state.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pending_first_visit_mints_temp_no_cookie_write() -> None:
    reset_state()
    fake = _FakeCookies(initial={})
    result = session_identity._resolve_id(
        controller=fake,
        state="pending",
        cookie_value=None,
        session_state_key="user_id",
        promotion_flag="_promo",
        cookie_name="bioseq_user_id",
        cookie_max_age=10,
        mint=lambda: "u_TEMP",
    )
    assert result == "u_TEMP", result
    assert _session_state["user_id"] == "u_TEMP"
    assert _session_state["_promo"] is True
    assert fake.set_calls == [], "must not write cookie while pending"


def test_pending_reuses_existing_temp() -> None:
    reset_state()
    _session_state["user_id"] = "u_TEMP_PRIOR"
    _session_state["_promo"] = True
    fake = _FakeCookies(initial={})
    result = session_identity._resolve_id(
        controller=fake,
        state="pending",
        cookie_value=None,
        session_state_key="user_id",
        promotion_flag="_promo",
        cookie_name="bioseq_user_id",
        cookie_max_age=10,
        mint=lambda: "u_NEW",
    )
    assert result == "u_TEMP_PRIOR"
    assert _session_state["_promo"] is True
    assert fake.set_calls == []


def test_ready_with_cookie_adopts_value_replacing_temp() -> None:
    """The big bug: returning user with cookie must NOT have it overwritten."""
    reset_state()
    _session_state["user_id"] = "u_TEMP_FROM_RENDER0"
    _session_state["_promo"] = True
    fake = _FakeCookies(initial={"bioseq_user_id": "u_REAL_FROM_BROWSER"})
    result = session_identity._resolve_id(
        controller=fake,
        state="ready",
        cookie_value="u_REAL_FROM_BROWSER",
        session_state_key="user_id",
        promotion_flag="_promo",
        cookie_name="bioseq_user_id",
        cookie_max_age=10,
        mint=lambda: "u_NEW",
    )
    assert result == "u_REAL_FROM_BROWSER", result
    assert _session_state["user_id"] == "u_REAL_FROM_BROWSER"
    assert "_promo" not in _session_state
    assert fake.set_calls == [], "must not re-write cookie when adopting existing value"


def test_ready_empty_cookie_promotes_temp() -> None:
    """First-time visitor: temp id minted on render 0 gets promoted to cookie."""
    reset_state()
    _session_state["user_id"] = "u_TEMP_FIRST_TIME"
    _session_state["_promo"] = True
    fake = _FakeCookies(initial={})
    result = session_identity._resolve_id(
        controller=fake,
        state="ready",
        cookie_value=None,
        session_state_key="user_id",
        promotion_flag="_promo",
        cookie_name="bioseq_user_id",
        cookie_max_age=10,
        mint=lambda: "u_FRESH",
    )
    assert result == "u_TEMP_FIRST_TIME"
    assert "_promo" not in _session_state
    assert fake.set_calls == [("bioseq_user_id", "u_TEMP_FIRST_TIME")]


def test_ready_empty_cookie_no_temp_mints_and_writes() -> None:
    reset_state()
    fake = _FakeCookies(initial={})
    result = session_identity._resolve_id(
        controller=fake,
        state="ready",
        cookie_value=None,
        session_state_key="user_id",
        promotion_flag="_promo",
        cookie_name="bioseq_user_id",
        cookie_max_age=10,
        mint=lambda: "u_FRESH",
    )
    assert result == "u_FRESH"
    assert _session_state["user_id"] == "u_FRESH"
    assert fake.set_calls == [("bioseq_user_id", "u_FRESH")]


def test_ready_cookie_matches_existing_no_writes() -> None:
    reset_state()
    _session_state["user_id"] = "u_REAL"
    fake = _FakeCookies(initial={"bioseq_user_id": "u_REAL"})
    result = session_identity._resolve_id(
        controller=fake,
        state="ready",
        cookie_value="u_REAL",
        session_state_key="user_id",
        promotion_flag="_promo",
        cookie_name="bioseq_user_id",
        cookie_max_age=10,
        mint=lambda: "u_NEW",
    )
    assert result == "u_REAL"
    assert fake.set_calls == [], "no cookie writes on stable adoption"


def test_two_render_sequence_returning_user() -> None:
    """End-to-end: render 0 (pending), render 1 (ready+cookie). Cookie preserved."""
    reset_state()
    fake = _FakeCookies(initial={"bioseq_user_id": "u_RETURNING"})

    # Render 0: pending
    result0 = session_identity._resolve_id(
        controller=fake, state="pending", cookie_value=None,
        session_state_key="user_id", promotion_flag="_promo",
        cookie_name="bioseq_user_id", cookie_max_age=10,
        mint=lambda: "u_TEMP",
    )
    assert result0 == "u_TEMP"
    assert fake.set_calls == [], "no writes during pending"

    # Render 1: ready, cookie present
    result1 = session_identity._resolve_id(
        controller=fake, state="ready", cookie_value="u_RETURNING",
        session_state_key="user_id", promotion_flag="_promo",
        cookie_name="bioseq_user_id", cookie_max_age=10,
        mint=lambda: "u_NEW",
    )
    assert result1 == "u_RETURNING", f"returning user lost their id: {result1}"
    assert _session_state["user_id"] == "u_RETURNING"
    assert fake.set_calls == [], "must not re-write cookie that already matches"


def test_two_render_sequence_first_time_visitor() -> None:
    """End-to-end: render 0 (pending), render 1 (ready, empty cookie). Cookie written."""
    reset_state()
    fake = _FakeCookies(initial={})

    # Render 0
    result0 = session_identity._resolve_id(
        controller=fake, state="pending", cookie_value=None,
        session_state_key="user_id", promotion_flag="_promo",
        cookie_name="bioseq_user_id", cookie_max_age=10,
        mint=lambda: "u_TEMP",
    )
    assert result0 == "u_TEMP"

    # Render 1: ready, no cookie -> promote temp
    result1 = session_identity._resolve_id(
        controller=fake, state="ready", cookie_value=None,
        session_state_key="user_id", promotion_flag="_promo",
        cookie_name="bioseq_user_id", cookie_max_age=10,
        mint=lambda: "u_NEW",
    )
    assert result1 == "u_TEMP", "temp should be promoted, not replaced"
    assert fake.set_calls == [("bioseq_user_id", "u_TEMP")]


def main() -> int:
    tests = [
        test_pending_first_visit_mints_temp_no_cookie_write,
        test_pending_reuses_existing_temp,
        test_ready_with_cookie_adopts_value_replacing_temp,
        test_ready_empty_cookie_promotes_temp,
        test_ready_empty_cookie_no_temp_mints_and_writes,
        test_ready_cookie_matches_existing_no_writes,
        test_two_render_sequence_returning_user,
        test_two_render_sequence_first_time_visitor,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  [ok] {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  [FAIL] {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  [ERR ] {fn.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

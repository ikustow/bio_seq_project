"""Anonymous identity bootstrap for the Streamlit frontend.

Two-tier identifier model:
- ``user_id``: stable UUID per browser, stored in a long-lived cookie
  (``bioseq_user_id``, 1y TTL). Survives page reloads and tab restarts.
- ``session_id``: identifies the active conversation. Stored in
  ``bioseq_session_id`` (7d TTL) so a Chrome reload resumes the same chat.
  ``start_new_session()`` mints a fresh one and overwrites the cookie.

How ``streamlit-cookies-controller`` actually behaves
-----------------------------------------------------
The library ships a Streamlit Component. On the **first script run** of a
browser session the constructor calls

    _cookie_controller(method='getAll', key='bioseq_cookies_dict', default={})

which returns ``default={}`` synchronously. The JS iframe then reads
``document.cookie``, sends the real values back via ``setComponentValue``,
and Streamlit auto-triggers a rerun. On the **next run** the constructor
takes the cached branch (``key in st.session_state``) and returns the real
cookies.

So ``controller.getAll()`` returns ``{}`` on render 0 — even for returning
users — and the actual cookies on render 1+. We detect render 0 by checking
whether ``bioseq_cookies_dict`` was already in ``st.session_state`` *before*
we constructed the controller. While we're in render 0 ("pending"), we mint
a temporary in-memory id but do NOT write a cookie — that would clobber the
existing one. Once the controller hydrates, we either adopt the real cookie
value or, if the cookie really was empty (first-time visitor), promote the
temp id into a fresh cookie.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Optional

import streamlit as st

USER_COOKIE_NAME = "bioseq_user_id"
SESSION_COOKIE_NAME = "bioseq_session_id"
USER_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365      # 1 year
SESSION_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 7     # 7 days

# The session_state key that streamlit-cookies-controller uses to cache the
# cookie dict. Picking a stable name lets us detect "render 0" reliably
# (presence of this key implies the JS component has hydrated at least once).
COOKIES_DICT_KEY = "bioseq_cookies_dict"

_USER_PROMOTION_FLAG = "_bioseq_user_id_pending_promotion"
_SESSION_PROMOTION_FLAG = "_bioseq_session_id_pending_promotion"

# Per-script-run cache of the (state, cookies, controller) tuple. The
# CookieController constructor registers a Streamlit custom component
# iframe — calling it more than once per run leaves orphaned instances
# behind, which surface in the browser console as "missing our iframe!"
# and "unregistered ComponentInstance" warnings. bootstrap_identity()
# clears the cache at the start of every run; downstream callers
# (cookie_state_snapshot, switch_session, start_new_session) reuse it.
_CACHE_KEY = "_bioseq_cookie_run_cache"


# ---------------------------------------------------------------------------
# Cookie controller plumbing
# ---------------------------------------------------------------------------


def _read_cookies_with_state() -> tuple[str, dict[str, Any], Any]:
    """Return ``(state, cookies_dict, controller)``.

    ``state`` is one of:
      'pending' — first script run after a page load; ``cookies_dict`` is the
                  empty placeholder, the JS component will hydrate on the
                  next rerun. Do NOT write cookies in this state.
      'ready'   — controller has hydrated (or is unavailable). ``cookies_dict``
                  is the real value, possibly ``{}`` for a first-time visitor.

    ``controller`` is None if the library is unavailable.
    """
    try:
        from streamlit_cookies_controller import CookieController
    except ImportError:
        return "ready", {}, None

    # The cookies dict key is added to session_state synchronously by the
    # CookieController constructor on its first invocation. Capturing this
    # before we instantiate the controller is how we know which render this
    # is. (We deliberately re-create the controller every call: the
    # constructor caches via session_state, so subsequent calls are cheap.)
    is_first_render = COOKIES_DICT_KEY not in st.session_state

    try:
        controller = CookieController(key=COOKIES_DICT_KEY)
    except Exception:
        return "ready", {}, None

    try:
        cookies = dict(controller.getAll() or {})
    except Exception:
        cookies = {}

    if is_first_render:
        return "pending", cookies, controller
    return "ready", cookies, controller


def _cached_cookie_state() -> tuple[str, dict[str, Any], Any]:
    """Return the per-run cookie state tuple, populating the cache lazily.

    Only ``bootstrap_identity`` should invalidate this cache (it runs at
    the start of every rerun). Other callers read from the cache so the
    CookieController iframe is registered exactly once per run.
    """
    cached = st.session_state.get(_CACHE_KEY)
    if cached is not None:
        return cached
    result = _read_cookies_with_state()
    st.session_state[_CACHE_KEY] = result
    return result


def _set_cookie(controller, name: str, value: str, max_age: int) -> None:
    if controller is None:
        return
    try:
        controller.set(name, value, max_age=max_age)
    except Exception:
        pass


def _new_user_id() -> str:
    return f"u_{uuid.uuid4().hex}"


def _new_session_id() -> str:
    return f"s_{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def bootstrap_identity() -> tuple[str, str]:
    """Resolve ``user_id`` and ``session_id`` for the current Streamlit run.

    Idempotent: safe to call on every rerun. On each call we:
    1. Read the current cookie state ("pending" vs "ready").
    2. Reconcile against ``st.session_state`` so a temp id minted while the
       controller was pending gets either replaced by the real cookie value
       or promoted to a new cookie when hydration confirms it's empty.
    3. Persist any newly minted id to the cookie — only when the controller
       is known to be hydrated, never during 'pending'.
    """
    # Refresh the per-run cookie cache. bootstrap_identity is the single
    # entrypoint that runs at the top of every script rerun, so this is
    # the right place to invalidate; downstream callers within the same
    # run then reuse the cached controller instead of constructing a
    # second iframe.
    st.session_state.pop(_CACHE_KEY, None)
    state, cookies, controller = _cached_cookie_state()

    user_id = _resolve_id(
        controller=controller,
        state=state,
        cookie_value=cookies.get(USER_COOKIE_NAME),
        session_state_key="user_id",
        promotion_flag=_USER_PROMOTION_FLAG,
        cookie_name=USER_COOKIE_NAME,
        cookie_max_age=USER_COOKIE_MAX_AGE_SECONDS,
        mint=_new_user_id,
    )
    session_id = _resolve_id(
        controller=controller,
        state=state,
        cookie_value=cookies.get(SESSION_COOKIE_NAME),
        session_state_key="session_id",
        promotion_flag=_SESSION_PROMOTION_FLAG,
        cookie_name=SESSION_COOKIE_NAME,
        cookie_max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
        mint=_new_session_id,
    )

    if "workspace_id" not in st.session_state:
        st.session_state.workspace_id = os.getenv("APP_WORKSPACE_ID") or None
    if "user_role" not in st.session_state:
        st.session_state.user_role = os.getenv("APP_USER_ROLE") or None

    return user_id, session_id


def _resolve_id(
    *,
    controller,
    state: str,
    cookie_value: Any,
    session_state_key: str,
    promotion_flag: str,
    cookie_name: str,
    cookie_max_age: int,
    mint,
) -> str:
    """Single resolution rule, reused for user_id and session_id.

    Decision table:
      state=pending, no st.session_state[key] -> mint temp (do NOT set promotion
                                                 flag — the temp must yield to a
                                                 real cookie value once hydration
                                                 lands; if the cookie really is
                                                 empty the temp gets promoted on
                                                 the ready render naturally)
      state=pending, has st.session_state[key] -> reuse
      state=ready,   pending_promotion + existing -> force-write existing to cookie
                                                     (set only by switch_session /
                                                     start_new_session, which can't
                                                     write from inside a button
                                                     callback container)
      state=ready,   cookie_value present       -> adopt cookie (overrides any temp)
      state=ready,   cookie empty, has temp     -> promote temp into cookie
      state=ready,   cookie empty, no temp      -> mint and write cookie

    The crucial invariant: ``pending_promotion`` must NOT be set by the pending
    render's temp mint. If it were, a returning visitor (real cookie present)
    would have their cookie overwritten by the freshly minted temp on the very
    next render — and every page reload would mint yet another user_id,
    making the sidebar history look empty because the DB rows are filed under
    the previous id.
    """
    existing = st.session_state.get(session_state_key)
    pending_promotion = st.session_state.get(promotion_flag, False)

    if state == "pending":
        if not existing:
            existing = mint()
            st.session_state[session_state_key] = existing
        return existing

    # state == "ready"
    # A pending promotion means switch_session / start_new_session deferred
    # the cookie write to us — session_state holds the intended new id and
    # we must propagate it into the cookie regardless of what the cookie
    # currently contains. Without this branch the next line would
    # overwrite session_state back to the stale cookie value.
    if pending_promotion and existing:
        _set_cookie(controller, cookie_name, existing, cookie_max_age)
        st.session_state.pop(promotion_flag, None)
        return existing

    if cookie_value:
        cookie_str = str(cookie_value)
        if existing != cookie_str:
            st.session_state[session_state_key] = cookie_str
        st.session_state.pop(promotion_flag, None)
        return cookie_str

    # state ready and cookie empty
    if not existing:
        existing = mint()
        st.session_state[session_state_key] = existing
    _set_cookie(controller, cookie_name, existing, cookie_max_age)
    st.session_state.pop(promotion_flag, None)
    return existing


def start_new_session(reason: Optional[str] = None) -> str:
    """Mint a fresh session_id (e.g. 'New chat' / 'Reset') and persist it.

    Cookie write is ALWAYS deferred to the next bootstrap_identity. Calling
    ``controller.set()`` here would invoke the streamlit-cookies-controller
    component from inside whatever container the caller sits in (e.g. the
    'New chat' button's session item), and Streamlit would attach the
    component's iframe (with a skeleton placeholder) to that container —
    showing as an empty button-shaped block right under the click for a
    few frames until the rerun completes.
    """
    new_id = _new_session_id()
    st.session_state.session_id = new_id
    if reason:
        st.session_state["_last_session_reset_reason"] = reason
    st.session_state[_SESSION_PROMOTION_FLAG] = True
    st.session_state.pop("_auto_restore_attempted", None)
    return new_id


def switch_session(session_id: str) -> None:
    """Switch the active session to an existing one (e.g. from sidebar).

    See ``start_new_session`` for why the cookie write is deferred.
    """
    st.session_state.session_id = session_id
    st.session_state[_SESSION_PROMOTION_FLAG] = True
    st.session_state.pop("_auto_restore_attempted", None)


def short_id(value: str, prefix_keep: int = 6, suffix_keep: int = 4) -> str:
    """Format a long UUID-like id for compact display in the debug expander."""
    if not value or len(value) <= prefix_keep + suffix_keep + 1:
        return value or ""
    return f"{value[:prefix_keep]}…{value[-suffix_keep:]}"


def cookie_state_snapshot() -> dict[str, Any]:
    """Diagnostic helper: returns what ``bootstrap_identity`` is seeing."""
    state, cookies, controller = _cached_cookie_state()
    return {
        "controller_loaded": controller is not None,
        "state": state,
        "cookie_user_id": cookies.get(USER_COOKIE_NAME),
        "cookie_session_id": cookies.get(SESSION_COOKIE_NAME),
        "session_user_id": st.session_state.get("user_id"),
        "session_session_id": st.session_state.get("session_id"),
        "user_pending_promotion": st.session_state.get(_USER_PROMOTION_FLAG, False),
        "session_pending_promotion": st.session_state.get(_SESSION_PROMOTION_FLAG, False),
        "auto_restore_attempted": st.session_state.get("_auto_restore_attempted"),
    }

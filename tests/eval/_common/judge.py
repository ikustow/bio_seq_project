"""OpenRouter-backed judge for rubric scoring.

Used by both L2 (`llm_eval.py`) and L3 (`e2e_eval.py`). Talks to the
OpenAI-compatible OpenRouter chat-completions endpoint directly via
`requests` — no openai SDK dependency.

The judge prompt template and behavioural contract come from
`llm_scenarios.yaml::judge`:

  - `provider: openrouter`
  - default model: `meta-llama/llama-3.3-70b-instruct:free`
  - `temperature: 0`, `max_tokens: 300`
  - system prompt: judge sees ONE rubric item at a time, returns
    `{"passed": 0|1, "explanation": "..."}`.

Env vars:
  OPENROUTER_API_KEY              — required, bearer token.
  EVAL_JUDGE_MIN_INTERVAL_S       — min gap between judge calls (default 3.5s,
                                    keeps us under OpenRouter's 20 RPM free-tier).
  EVAL_JUDGE_MAX_RETRIES          — transient-429/5xx retry count (default 4).
  EVAL_JUDGE_BACKOFF_BASE_S       — exponential backoff base (default 4s).
  EVAL_JUDGE_BACKOFF_CAP_S        — single-wait cap (default 60s).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import requests


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
API_KEY_ENV = "OPENROUTER_API_KEY"
REQUEST_TIMEOUT_SECONDS = 60

# OpenRouter `:free` models cap at ~20 RPM AND have a daily request quota
# (50/day without $10 lifetime credit, 1000/day with). Retry/backoff smooths
# transient per-minute bursts; the daily quota is unrecoverable — we detect
# it from the error body and fail fast so the user doesn't burn 5×backoff
# minutes on every rubric item.
JUDGE_MIN_INTERVAL_S = float(os.getenv("EVAL_JUDGE_MIN_INTERVAL_S", "5"))
JUDGE_MAX_RETRIES = int(os.getenv("EVAL_JUDGE_MAX_RETRIES", "4"))
JUDGE_BACKOFF_BASE_S = float(os.getenv("EVAL_JUDGE_BACKOFF_BASE_S", "4"))
JUDGE_BACKOFF_CAP_S = float(os.getenv("EVAL_JUDGE_BACKOFF_CAP_S", "120"))

_DAILY_QUOTA_BODY_RE = re.compile(
    r"(per[-\s_]day|daily limit|free[-\s_]models[-\s_]per[-\s_]day|exhausted|add\s+\d+\s+credits)",
    re.IGNORECASE,
)

_last_call_ts: float = 0.0


JUDGE_SYSTEM_PROMPT = (
    "You are an evaluator for a protein chat assistant.\n\n"
    "You receive: the protein context shown to the assistant, the user's "
    "question, the assistant's answer, and ONE rubric item with a `type` "
    'field that is either "must_cover" or "must_not".\n\n'
    'Return strictly a JSON object: {"passed": 0 or 1, "explanation": "..."}.\n\n'
    "Rules:\n"
    "  - type=must_cover passes (1) only if the answer clearly addresses the "
    "item using information from the provided context.\n"
    "  - type=must_not passes (1) only if the answer does NOT do the forbidden thing.\n"
    "  - Do not award partial credit.\n"
    "  - Explanation: one short sentence, English."
)


_JSON_BLOCK_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _parse_judge_reply(text: str) -> dict[str, Any]:
    """Pull `{"passed": 0|1, "explanation": "..."}` out of the judge reply.

    Tries direct json.loads first; falls back to the first {...} block.
    Always returns a dict with int `passed` (defaults to 0) and string
    `explanation` so the caller can write a clean CSV row without extra
    error handling.
    """
    text = (text or "").strip()
    # 1. Direct parse
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return _normalize(obj, text)
    except json.JSONDecodeError:
        pass

    # 2. First JSON object substring
    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return _normalize(obj, text)
        except json.JSONDecodeError:
            pass

    # 3. Give up — record raw text, mark failed
    return {"passed": 0, "explanation": f"[unparseable judge reply] {text[:200]}"}


def _normalize(obj: dict[str, Any], raw: str) -> dict[str, Any]:
    passed_raw = obj.get("passed")
    try:
        passed = int(passed_raw)
    except (TypeError, ValueError):
        passed = 1 if str(passed_raw).strip().lower() in ("true", "yes", "1") else 0
    if passed not in (0, 1):
        passed = 1 if passed else 0
    explanation = str(obj.get("explanation") or "").strip() or raw.strip()[:200]
    return {"passed": passed, "explanation": explanation}


def score_rubric_item(
    *,
    protein_context: str,
    question: str,
    answer: str,
    rubric_check: str,
    rubric_type: str,
    model: str = "meta-llama/llama-3.3-70b-instruct:free",
    temperature: float = 0.0,
    max_tokens: int = 300,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask the judge to score one rubric item.

    Returns `(parsed_verdict, raw_response_json)`. `parsed_verdict` is a
    dict with keys `passed` (int 0|1) and `explanation` (str).
    """
    api_key = (os.getenv(API_KEY_ENV) or "").strip()
    if not api_key:
        raise RuntimeError(f"{API_KEY_ENV} is not set.")

    user_payload = (
        f"=== Protein context ===\n{protein_context}\n\n"
        f"=== User question ===\n{question}\n\n"
        f"=== Assistant answer ===\n{answer}\n\n"
        f"=== Rubric item (type={rubric_type}) ===\n{rubric_check}\n\n"
        'Reply ONLY with the JSON object {"passed": 0|1, "explanation": "..."}.'
    )

    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ],
    }

    _pace_for_rpm()
    data = _post_with_retry(body, api_key)
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    verdict = _parse_judge_reply(text)
    return verdict, data


def _pace_for_rpm() -> None:
    """Sleep just enough to keep the per-process call cadence under
    `JUDGE_MIN_INTERVAL_S` (default 3.5s ≈ 17 RPM, safely below OpenRouter's
    20 RPM free-tier ceiling).

    Module-level last-call clock — first call passes through immediately.
    """
    global _last_call_ts
    if JUDGE_MIN_INTERVAL_S <= 0:
        _last_call_ts = time.perf_counter()
        return
    now = time.perf_counter()
    elapsed = now - _last_call_ts
    if _last_call_ts > 0 and elapsed < JUDGE_MIN_INTERVAL_S:
        time.sleep(JUDGE_MIN_INTERVAL_S - elapsed)
    _last_call_ts = time.perf_counter()


def _post_with_retry(body: dict[str, Any], api_key: str) -> dict[str, Any]:
    """POST to OpenRouter with exponential backoff on transient 429/5xx.

    Mirrors `llm_clients._post_with_retry` for symmetry. Two differences:
      - Honors a `Retry-After` header if OpenRouter sends one.
      - On 429, inspects the body for daily-quota markers ("per-day",
        "daily limit", "add N credits", etc.) and fails fast in that case —
        retrying a depleted daily quota is pure waste, the user needs to
        wait for UTC midnight or top up credit.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_exc: Exception | None = None
    for attempt in range(JUDGE_MAX_RETRIES):
        try:
            response = requests.post(
                OPENROUTER_URL,
                headers=headers,
                json=body,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            last_exc = exc
            wait = min(JUDGE_BACKOFF_CAP_S, JUDGE_BACKOFF_BASE_S * (2 ** attempt))
            print(f"    [judge] network error: {exc}; retrying in {wait:.0f}s", flush=True)
            time.sleep(wait)
            continue

        if response.status_code < 400:
            return response.json()

        body_snippet = (response.text or "")[:500].replace("\n", " ").strip()
        retryable = response.status_code == 429 or 500 <= response.status_code < 600

        # Daily-quota 429 is unrecoverable until UTC midnight — fail fast
        # with the full body so the user sees exactly which limit was hit.
        if response.status_code == 429 and _DAILY_QUOTA_BODY_RE.search(body_snippet):
            raise RuntimeError(
                f"OpenRouter daily quota appears exhausted (HTTP 429). "
                f"Resets at UTC midnight; top up $10 lifetime credit for "
                f"1000/day, or wait. Body: {body_snippet or '<empty>'}"
            )

        if not retryable or attempt == JUDGE_MAX_RETRIES - 1:
            raise RuntimeError(
                f"OpenRouter returned HTTP {response.status_code} after "
                f"{attempt + 1} attempt(s). Body: {body_snippet or '<empty>'}"
            )

        retry_after = response.headers.get("Retry-After")
        try:
            wait = float(retry_after) if retry_after else JUDGE_BACKOFF_BASE_S * (2 ** attempt)
        except ValueError:
            wait = JUDGE_BACKOFF_BASE_S * (2 ** attempt)
        wait = min(JUDGE_BACKOFF_CAP_S, max(wait, 1.0))
        print(
            f"    [judge] HTTP {response.status_code}; retrying in {wait:.0f}s "
            f"(attempt {attempt + 1}/{JUDGE_MAX_RETRIES}); body: {body_snippet[:200] or '<empty>'}",
            flush=True,
        )
        time.sleep(wait)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("OpenRouter request failed after all retries.")

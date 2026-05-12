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
  OPENROUTER_API_KEY  — required, bearer token.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
API_KEY_ENV = "OPENROUTER_API_KEY"
REQUEST_TIMEOUT_SECONDS = 60


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

    response = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    verdict = _parse_judge_reply(text)
    return verdict, data

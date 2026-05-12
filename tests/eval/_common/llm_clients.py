"""Direct Gemini proxy caller for the eval harness.

We deliberately do NOT go through `app/frontend/chat_llm_pipeline.run_turn_chat_llm`
because it is coupled to Streamlit `st.session_state` (reads `messages`,
`candidates`, `selected_candidate_idx`). The eval harness instead calls the
same Cloudflare-proxied Gemini endpoint directly, passing the protein context
and chat history explicitly.

The `build_protein_context_text` function below mirrors the production helper
`chat_llm_pipeline._get_current_protein_context`. If the production format
changes, this must be updated in lock-step — see VALIDATION_PLAN.md §A.3.

Env vars (same names used by production):
  BIOSEQ_LLM_PROXY_URL    — Cloudflare proxy URL.
  BIOSEQ_LLM_PROXY_TOKEN  — required header `X-BioSeq-Token`.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests


PROXY_URL_ENV = "BIOSEQ_LLM_PROXY_URL"
PROXY_TOKEN_ENV = "BIOSEQ_LLM_PROXY_TOKEN"
REQUEST_TIMEOUT_SECONDS = 60

# Free-tier Gemini limits ~15 RPM AND has a daily request quota (~1500/day
# on 2.0-flash). The Cloudflare proxy in front of it may layer its own
# limit on top. Retry/backoff helps with bursty per-minute hits but cannot
# rescue an exhausted daily quota — in that case we surface the response
# body so the user can see WHICH quota was hit.
GEMINI_MAX_RETRIES = int(os.getenv("EVAL_GEMINI_MAX_RETRIES", "6"))
GEMINI_BACKOFF_BASE_S = float(os.getenv("EVAL_GEMINI_BACKOFF_BASE_S", "5"))
GEMINI_BACKOFF_CAP_S = float(os.getenv("EVAL_GEMINI_BACKOFF_CAP_S", "120"))


# System prompt is copied verbatim from chat_llm_pipeline._call_gemini_proxy.
# Keep in sync with that file.
GEMINI_SYSTEM_PROMPT = (
    "You are an expert assistant for protein sequence analysis. "
    "Your primary goal is to answer the user's question accurately and helpfully. "
    "\n\n"
    "You have been provided with database information about the protein the user is asking about. "
    "Use this data to ground your answers: explain what the protein does, where it's found, how it interacts, "
    "and why it matters clinically or biologically. "
    "\n\n"
    "Guidelines: "
    "- Answer the user's question directly and concisely "
    "- Connect relevant data points (function, location, interactions, disease links) to build a coherent explanation "
    "- If information is missing from the database, acknowledge it: 'The database doesn't have data on X, but based on Y we can infer...' "
    "- Maintain scientific accuracy while keeping explanations clear and accessible "
    "- Do not claim that a new database search was performed—use only the information provided"
)


def build_protein_context_text(candidate: dict[str, Any]) -> str | None:
    """Render a candidate (`{"protein": {...}, "match_score": float}`) as
    a single context string, mirroring production's
    `_get_current_protein_context`.

    Returns None when there is nothing to render (no protein dict).
    """
    if not candidate:
        return None
    protein = candidate.get("protein") or {}
    if not protein:
        return None

    try:
        match_score = float(candidate.get("match_score") or 0)
    except (TypeError, ValueError):
        match_score = 0.0

    lines = [
        "**Current protein context:**",
        f"Accession: {protein.get('accession', 'N/A')}",
        f"Name: {protein.get('name', 'Unknown')}",
        f"Gene: {protein.get('gene', 'N/A')}",
        f"Organism: {protein.get('organism_scientific', '')} ({protein.get('organism_common', '')})",
        f"Match confidence: {match_score:.1f}%",
        "",
    ]

    length = protein.get("length")
    if length:
        lines.append(f"Length: {length:,} amino acids")

    mol_weight = protein.get("mol_weight")
    if mol_weight:
        lines.append(f"Molecular weight: {mol_weight:,} Da")

    function = (protein.get("function_text") or "").strip()
    if function:
        lines.append(f"\n**Function:**\n{function}")

    tissue = (protein.get("tissue_specificity") or "").strip()
    if tissue:
        lines.append(f"\n**Tissue specificity:**\n{tissue}")

    subunit = (protein.get("subunit_text") or "").strip()
    if subunit:
        lines.append(f"\n**Subunit composition:**\n{subunit}")

    subcellular = protein.get("subcellular_locations") or []
    if subcellular:
        lines.append(f"\n**Subcellular locations:** {', '.join(subcellular)}")

    domains = protein.get("domains") or []
    if domains:
        domain_names = [
            f"{d.get('name', 'Domain')} ({d.get('start')}-{d.get('end')})"
            for d in domains[:5]
        ]
        lines.append(f"\n**Domains:** {', '.join(domain_names)}")

    interactions = protein.get("interactions") or []
    if interactions:
        interaction_summary = ", ".join(
            f"{item.get('gene') or item.get('accession') or item.get('partner') or 'Partner'}"
            for item in interactions[:3]
        )
        lines.append(f"\n**Known interaction partners:** {interaction_summary}")

    disease = protein.get("disease")
    if disease and isinstance(disease, dict) and disease.get("name"):
        lines.append(f"\n**Associated disease:** {disease.get('name')}")
        if disease.get("description"):
            lines.append(f"Description: {disease.get('description')}")

    keywords = protein.get("keywords") or []
    if keywords:
        lines.append(f"\n**Keywords:** {', '.join(keywords[:8])}")

    pathways = protein.get("pathways") or []
    if pathways:
        pathway_names = [p.get("name", "Pathway") for p in pathways[:3]]
        lines.append(f"\n**Key pathways:** {', '.join(pathway_names)}")

    return "\n".join(lines)


def apply_override(candidate: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of `candidate` with top-level fields in
    `override` shallow-merged into `candidate["protein"]`.

    Used by L3 `grounding` scenarios that swap out a single field
    (function_text, disease, ...) on a real retriever result so the test
    can check whether the LLM follows the (now wrong) context or pulls
    from pretraining.
    """
    if not override:
        return candidate
    new_protein = dict(candidate.get("protein") or {})
    for k, v in override.items():
        new_protein[k] = v
    return {**candidate, "protein": new_protein}


def _build_contents(context_text: str | None, history: list[dict[str, str]], current_prompt: str) -> list[dict[str, Any]]:
    """Assemble Gemini `contents` array. Mirrors production's
    `_build_gemini_contents`, but works from explicit args instead of
    `st.session_state`.

    `history` items: `{"role": "user"|"model"|"assistant", "content": "..."}`.
    """
    contents: list[dict[str, Any]] = []

    if context_text:
        contents.append({"role": "user", "parts": [{"text": context_text}]})
        contents.append({
            "role": "model",
            "parts": [{"text": "I understand. I have the context about the current protein. I'll use this information to answer your questions."}],
        })

    seen_current = False
    for msg in (history or [])[-20:]:
        role = msg.get("role")
        text = str(msg.get("content") or "").strip()
        if not text:
            continue
        if role == "user":
            seen_current = seen_current or text == current_prompt
            contents.append({"role": "user", "parts": [{"text": text}]})
        elif role in ("assistant", "model"):
            contents.append({"role": "model", "parts": [{"text": text}]})

    if not seen_current:
        contents.append({"role": "user", "parts": [{"text": current_prompt}]})
    return contents


def _extract_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no candidates.")
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    text = "\n".join(str(part.get("text") or "") for part in parts if part.get("text")).strip()
    if not text:
        raise RuntimeError("Gemini returned an empty text response.")
    return text


def call_gemini(
    *,
    protein_context: str | None,
    history: list[dict[str, str]] | None = None,
    prompt: str,
    temperature: float = 0.2,
    max_output_tokens: int = 4096,
) -> tuple[str, dict[str, Any]]:
    """Call the production Cloudflare-proxied Gemini endpoint.

    Returns `(answer_text, raw_response_json)`. Raises `RuntimeError` if
    env vars are missing or Gemini returns no text.
    """
    proxy_url = (os.getenv(PROXY_URL_ENV) or "").strip()
    proxy_token = (os.getenv(PROXY_TOKEN_ENV) or "").strip()
    if not proxy_url:
        raise RuntimeError(f"{PROXY_URL_ENV} is not set.")
    if not proxy_token:
        raise RuntimeError(f"{PROXY_TOKEN_ENV} is not set.")

    payload = {
        "contents": _build_contents(protein_context, history or [], prompt),
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
        },
        "systemInstruction": {"parts": [{"text": GEMINI_SYSTEM_PROMPT}]},
    }

    data = _post_with_retry(proxy_url, proxy_token, payload)
    return _extract_text(data), data


def _post_with_retry(proxy_url: str, proxy_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST with exponential backoff on 429/5xx.

    Gemini free tier rate-limits at ~15 RPM AND has a daily request quota
    (~1500/day on 2.0-flash). We honor a `Retry-After` header when the
    proxy passes one through, otherwise exponential backoff capped at
    `GEMINI_BACKOFF_CAP_S`. Response body is captured on every failure
    so the caller (and stdout) can see *which* quota was actually hit.
    """
    last_exc: Exception | None = None
    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            response = requests.post(
                proxy_url,
                json=payload,
                headers={"X-BioSeq-Token": proxy_token},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            last_exc = exc
            wait = min(GEMINI_BACKOFF_CAP_S, GEMINI_BACKOFF_BASE_S * (2 ** attempt))
            print(f"    [gemini] network error: {exc}; retrying in {wait:.0f}s", flush=True)
            time.sleep(wait)
            continue

        if response.status_code < 400:
            return response.json()

        body_snippet = (response.text or "")[:500].replace("\n", " ").strip()
        retryable = response.status_code == 429 or 500 <= response.status_code < 600
        if not retryable or attempt == GEMINI_MAX_RETRIES - 1:
            # Surface the body so the caller knows WHICH quota was hit.
            raise RuntimeError(
                f"Gemini proxy returned HTTP {response.status_code} after "
                f"{attempt + 1} attempt(s). Body: {body_snippet or '<empty>'}"
            )

        retry_after = response.headers.get("Retry-After")
        try:
            wait = float(retry_after) if retry_after else GEMINI_BACKOFF_BASE_S * (2 ** attempt)
        except ValueError:
            wait = GEMINI_BACKOFF_BASE_S * (2 ** attempt)
        wait = min(GEMINI_BACKOFF_CAP_S, max(wait, 1.0))
        print(
            f"    [gemini] HTTP {response.status_code}; retrying in {wait:.0f}s "
            f"(attempt {attempt + 1}/{GEMINI_MAX_RETRIES}); body: {body_snippet[:200] or '<empty>'}",
            flush=True,
        )
        time.sleep(wait)

    # All retries exhausted — surface the last requests exception if we had one.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Gemini request failed after all retries.")

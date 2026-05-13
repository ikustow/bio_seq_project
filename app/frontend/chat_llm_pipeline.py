"""Chat-LLM follow-up turn handler.

Follow-up questions in an existing session are routed here after the first
retriever turn. This module calls the configured chat provider and preserves
the currently rendered protein card, so a chat answer does not wipe the right
column.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import requests
import streamlit as st

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _PROJECT_ROOT / "app"
_FRONTEND_ROOT = Path(__file__).resolve().parent
for _path in (_FRONTEND_ROOT, _APP_ROOT, _PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import session_db_adapter  # noqa: E402


PROXY_URL_ENV = "BIOSEQ_LLM_PROXY_URL"
PROXY_TOKEN_ENV = "BIOSEQ_LLM_PROXY_TOKEN"
CHAT_PROVIDER_ENV = "BIOSEQ_CHAT_LLM_PROVIDER"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_CHAT_MODEL_ENV = "BIOSEQ_OPENAI_CHAT_MODEL"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
OPENAI_DEFAULT_MODEL = "gpt-4.1-nano"
REQUEST_TIMEOUT_SECONDS = 45


def run_turn_chat_llm(prompt: str) -> dict[str, Any]:
    """Run a follow-up chat turn through the configured chat provider."""
    user_id = st.session_state.get("user_id") or "anonymous"
    session_id = st.session_state.get("session_id")
    if not session_id:
        raise RuntimeError(
            "session_id is missing from st.session_state; bootstrap_identity() must run first."
        )

    context = session_db_adapter.make_context(
        user_id=user_id,
        session_id=session_id,
        workspace_id=st.session_state.get("workspace_id"),
        user_role=st.session_state.get("user_role"),
    )
    warnings: list[str] = list(session_db_adapter.get_warnings())

    current_mode = "chat_llm"
    try:
        reply, result = _call_chat_llm(prompt)
        current_mode = str(result.get("mode") or "chat_llm")
    except Exception as exc:
        reply = f"**Chat LLM error:** {exc}"
        result = {"mode": "chat_llm", "error": str(exc)}
        current_mode = "chat_llm_error"
        warnings.append(str(exc))

    # Persist the turn but do not touch the saved candidate state. The user is
    # still looking at the protein card from the previous retriever turn.
    try:
        session_db_adapter.save_turn(
            context,
            user_message=prompt,
            assistant_message=reply,
            candidates=[],
            revealed_sections=None,
            current_mode=current_mode,
            update_candidates=False,
        )
    except Exception as exc:
        warnings.append(f"Could not save session turn: {exc}")

    return {
        "reply": reply,
        "candidates": st.session_state.get("candidates") or [],
        "candidates_raw": [],
        "reveals": set(st.session_state.get("card_sections_revealed") or set()),
        "warnings": warnings,
        "result": result,
        "persisted": session_db_adapter.is_persistent(),
        "backend": current_mode,
        "update_card": False,
    }


def _call_chat_llm(prompt: str) -> tuple[str, dict[str, Any]]:
    provider = (os.getenv(CHAT_PROVIDER_ENV) or "auto").strip().lower()
    if provider in {"auto", ""}:
        proxy_url = (os.getenv(PROXY_URL_ENV) or "").strip()
        proxy_token = (os.getenv(PROXY_TOKEN_ENV) or "").strip()
        if proxy_url or proxy_token:
            return _call_gemini_proxy(prompt)
        if (os.getenv(OPENAI_API_KEY_ENV) or "").strip():
            return _call_openai(prompt)
        raise RuntimeError(
            f"Set {PROXY_URL_ENV}/{PROXY_TOKEN_ENV} for Gemini proxy "
            f"or {OPENAI_API_KEY_ENV} for OpenAI chat."
        )

    if provider in {"gemini", "gemini_proxy", "proxy"}:
        return _call_gemini_proxy(prompt)
    if provider in {"openai", "chatgpt"}:
        return _call_openai(prompt)

    raise RuntimeError(
        f"{CHAT_PROVIDER_ENV} must be 'auto', 'gemini_proxy', or 'openai'."
    )


def _call_gemini_proxy(prompt: str) -> tuple[str, dict[str, Any]]:
    proxy_url = (os.getenv(PROXY_URL_ENV) or "").strip()
    proxy_token = (os.getenv(PROXY_TOKEN_ENV) or "").strip()
    if not proxy_url:
        raise RuntimeError(f"{PROXY_URL_ENV} is not set.")
    if not proxy_token:
        raise RuntimeError(f"{PROXY_TOKEN_ENV} is not set.")

    payload = {
        "contents": _build_gemini_contents(prompt),
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096,
        },
        "systemInstruction": {
            "parts": [
                {
                    "text": _system_prompt()
                }
            ]
        },
    }

    response = requests.post(
        proxy_url,
        json=payload,
        headers={"X-BioSeq-Token": proxy_token},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    return _extract_gemini_text(data), {
        "mode": "chat_llm",
        "provider": "gemini_proxy",
        "gemini": data,
    }


def _call_openai(prompt: str) -> tuple[str, dict[str, Any]]:
    api_key = (os.getenv(OPENAI_API_KEY_ENV) or "").strip()
    if not api_key:
        raise RuntimeError(f"{OPENAI_API_KEY_ENV} is not set.")

    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI chat provider requires `langchain-openai` in the frontend environment."
        ) from exc

    model = (
        os.getenv(OPENAI_CHAT_MODEL_ENV)
        or os.getenv(OPENAI_MODEL_ENV)
        or OPENAI_DEFAULT_MODEL
    ).strip()
    llm = ChatOpenAI(
        model=model,
        temperature=0.2,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response = llm.invoke(_build_openai_messages(prompt))
    return _extract_openai_text(response), {
        "mode": "chat_llm",
        "provider": "openai",
        "model": model,
    }


def _build_openai_messages(prompt: str) -> list[Any]:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    session_messages = st.session_state.get("messages") or []
    messages: list[Any] = [SystemMessage(content=_system_prompt())]

    protein_context = _get_current_protein_context()
    if protein_context:
        messages.append(HumanMessage(content=protein_context))
        messages.append(
            AIMessage(
                content=(
                    "I understand. I have the context about the current protein. "
                    "I'll use this information to answer your questions."
                )
            )
        )

    seen_current_prompt = False
    for message in session_messages[-20:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            seen_current_prompt = seen_current_prompt or content == prompt
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))

    if not seen_current_prompt:
        messages.append(HumanMessage(content=prompt))
    return messages


def _build_gemini_contents(prompt: str) -> list[dict[str, Any]]:
    messages = st.session_state.get("messages") or []
    contents: list[dict[str, Any]] = []

    protein_context = _get_current_protein_context()
    if protein_context:
        contents.append({"role": "user", "parts": [{"text": protein_context}]})
        contents.append({
            "role": "model",
            "parts": [{"text": "I understand. I have the context about the current protein. I'll use this information to answer your questions."}]
        })

    seen_current_prompt = False
    for message in messages[-20:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            seen_current_prompt = seen_current_prompt or content == prompt
            contents.append({"role": "user", "parts": [{"text": content}]})
        elif role == "assistant" and contents:
            contents.append({"role": "model", "parts": [{"text": content}]})

    if not seen_current_prompt:
        contents.append({"role": "user", "parts": [{"text": prompt}]})
    return contents


def _get_current_protein_context() -> str | None:
    """Build a text description of the currently selected protein."""
    candidates = st.session_state.get("candidates")
    if not candidates:
        return None

    selected_idx = st.session_state.get("selected_candidate_idx", 0)
    try:
        selected_idx = int(selected_idx)
    except (TypeError, ValueError):
        selected_idx = 0

    if selected_idx < 0 or selected_idx >= len(candidates):
        return None

    candidate = candidates[selected_idx]
    protein = candidate.get("protein", {})
    match_score = candidate.get("match_score", 0)

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

    function = protein.get("function_text", "").strip()
    if function:
        lines.append(f"\n**Function:**\n{function}")

    tissue = protein.get("tissue_specificity", "").strip()
    if tissue:
        lines.append(f"\n**Tissue specificity:**\n{tissue}")

    subunit = protein.get("subunit_text", "").strip()
    if subunit:
        lines.append(f"\n**Subunit composition:**\n{subunit}")

    subcellular = protein.get("subcellular_locations", [])
    if subcellular:
        lines.append(f"\n**Subcellular locations:** {', '.join(subcellular)}")

    domains = protein.get("domains", [])
    if domains:
        domain_names = [f"{d.get('name', 'Domain')} ({d.get('start')}-{d.get('end')})" for d in domains[:5]]
        lines.append(f"\n**Domains:** {', '.join(domain_names)}")

    interactions = protein.get("interactions", [])
    if interactions:
        interaction_summary = ", ".join([
            f"{item.get('gene') or item.get('accession') or 'Partner'}"
            for item in interactions[:3]
        ])
        lines.append(f"\n**Known interaction partners:** {interaction_summary}")

    disease = protein.get("disease")
    if disease and isinstance(disease, dict) and disease.get("name"):
        lines.append(f"\n**Associated disease:** {disease.get('name')}")
        if disease.get("description"):
            lines.append(f"Description: {disease.get('description')}")

    keywords = protein.get("keywords", [])
    if keywords:
        lines.append(f"\n**Keywords:** {', '.join(keywords[:8])}")

    pathways = protein.get("pathways", [])
    if pathways:
        pathway_names = [p.get("name", "Pathway") for p in pathways[:3]]
        lines.append(f"\n**Key pathways:** {', '.join(pathway_names)}")

    return "\n".join(lines)


def _system_prompt() -> str:
    return (
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


def _extract_gemini_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no candidates.")
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    text = "\n".join(str(part.get("text") or "") for part in parts if part.get("text")).strip()
    if not text:
        raise RuntimeError("Gemini returned an empty text response.")
    return text


def _extract_openai_text(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if value:
                    chunks.append(str(value))
        text = "\n".join(chunks).strip()
    else:
        text = str(content or "").strip()

    if not text:
        raise RuntimeError("OpenAI returned an empty text response.")
    return text

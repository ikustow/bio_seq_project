"""Backend Chat-LLM service for follow-up turns.

This module owns the LLM provider selection, prompt/context building and
HTTP/SDK calls that used to live in ``app/frontend/chat_llm_pipeline.py``.
It is intentionally free of Streamlit dependencies and never reads
``st.session_state``. Frontend passes whatever context it has via
``ChatLLMRequest``; the service decides which provider to call and
returns a structured ``ChatLLMResponse``.

Persistence (writing to ``public.chat_sessions``) and session-state
mutation remain in the frontend for the first migration step.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests


PROXY_URL_ENV = "BIOSEQ_LLM_PROXY_URL"
PROXY_TOKEN_ENV = "BIOSEQ_LLM_PROXY_TOKEN"
CHAT_PROVIDER_ENV = "BIOSEQ_CHAT_LLM_PROVIDER"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_CHAT_MODEL_ENV = "BIOSEQ_OPENAI_CHAT_MODEL"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
OPENAI_DEFAULT_MODEL = "gpt-4.1-nano"
REQUEST_TIMEOUT_SECONDS = 45


@dataclass
class ChatLLMRequest:
    prompt: str
    history: list[dict[str, Any]] = field(default_factory=list)
    selected_candidate: dict[str, Any] | None = None
    provider_override: str | None = None
    # Workspace object registry sent by the frontend on follow-up turns.
    objects: dict[str, Any] = field(default_factory=dict)
    selected_object_id: str | None = None


@dataclass
class ChatLLMResponse:
    reply: str
    provider: str
    model: str | None
    raw: dict[str, Any]


class ChatLLMProvider(Protocol):
    name: str

    def generate(self, request: ChatLLMRequest, system_prompt: str) -> ChatLLMResponse: ...


class ChatLLMService:
    """Orchestrates a single follow-up Chat-LLM turn.

    The service is stateless: callers pass full context per call. Provider
    selection follows the existing env-var contract so the migration does
    not change deployment configuration.
    """

    def __init__(
        self,
        gemini_provider: "ChatLLMProvider | None" = None,
        openai_provider: "ChatLLMProvider | None" = None,
    ) -> None:
        self._gemini = gemini_provider or GeminiProxyChatProvider()
        self._openai = openai_provider or OpenAIChatProvider()

    def generate(self, request: ChatLLMRequest) -> ChatLLMResponse:
        provider = self._select_provider(request.provider_override)
        return provider.generate(request, system_prompt())

    def _select_provider(self, override: str | None) -> ChatLLMProvider:
        configured = (override or os.getenv(CHAT_PROVIDER_ENV) or "auto").strip().lower()
        if configured in {"auto", ""}:
            proxy_url = (os.getenv(PROXY_URL_ENV) or "").strip()
            proxy_token = (os.getenv(PROXY_TOKEN_ENV) or "").strip()
            if proxy_url or proxy_token:
                return self._gemini
            if (os.getenv(OPENAI_API_KEY_ENV) or "").strip():
                return self._openai
            raise RuntimeError(
                f"Set {PROXY_URL_ENV}/{PROXY_TOKEN_ENV} for Gemini proxy "
                f"or {OPENAI_API_KEY_ENV} for OpenAI chat."
            )
        if configured in {"gemini", "gemini_proxy", "proxy"}:
            return self._gemini
        if configured in {"openai", "chatgpt"}:
            return self._openai
        raise RuntimeError(
            f"{CHAT_PROVIDER_ENV} must be 'auto', 'gemini_proxy', or 'openai'."
        )


class GeminiProxyChatProvider:
    name = "gemini_proxy"

    def generate(self, request: ChatLLMRequest, system_prompt: str) -> ChatLLMResponse:
        proxy_url = (os.getenv(PROXY_URL_ENV) or "").strip()
        proxy_token = (os.getenv(PROXY_TOKEN_ENV) or "").strip()
        if not proxy_url:
            raise RuntimeError(f"{PROXY_URL_ENV} is not set.")
        if not proxy_token:
            raise RuntimeError(f"{PROXY_TOKEN_ENV} is not set.")

        payload = {
            "contents": _build_gemini_contents(request),
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096},
            "systemInstruction": {"parts": [{"text": system_prompt}]},
        }
        headers = {
            "Content-Type": "application/json",
            "X-BioSeq-Token": proxy_token,
        }
        debug_request = {
            "provider": self.name,
            "method": "POST",
            "url": proxy_url,
            "headers": headers,
            "system_prompt": system_prompt,
            "payload": payload,
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        }
        response = requests.post(
            proxy_url,
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        return ChatLLMResponse(
            reply=_extract_gemini_text(data),
            provider=self.name,
            model=None,
            raw={
                "mode": "chat_llm",
                "provider": self.name,
                "gemini": data,
                "debug_request": debug_request,
            },
        )


class OpenAIChatProvider:
    name = "openai"

    def generate(self, request: ChatLLMRequest, system_prompt: str) -> ChatLLMResponse:
        api_key = (os.getenv(OPENAI_API_KEY_ENV) or "").strip()
        if not api_key:
            raise RuntimeError(f"{OPENAI_API_KEY_ENV} is not set.")

        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI chat provider requires `langchain-openai` in the runtime environment."
            ) from exc

        model = (
            os.getenv(OPENAI_CHAT_MODEL_ENV)
            or os.getenv(OPENAI_MODEL_ENV)
            or OPENAI_DEFAULT_MODEL
        ).strip()
        llm = ChatOpenAI(model=model, temperature=0.2, timeout=REQUEST_TIMEOUT_SECONDS)
        messages = _build_openai_messages(request, system_prompt)
        debug_request = {
            "provider": self.name,
            "model": model,
            "temperature": 0.2,
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "system_prompt": system_prompt,
            "messages": [_serialize_openai_message(message) for message in messages],
        }
        response = llm.invoke(messages)
        return ChatLLMResponse(
            reply=_extract_openai_text(response),
            provider=self.name,
            model=model,
            raw={
                "mode": "chat_llm",
                "provider": self.name,
                "model": model,
                "debug_request": debug_request,
            },
        )


def system_prompt() -> str:
    return (
        "You are an expert assistant for protein sequence analysis. "
        "Your primary goal is to answer the user's question accurately and helpfully. "
        "\n\n"
        "The user works in a chat with a workspace of objects: each `Sequence` "
        "(labelled `Seq_A`, `Seq_B`, ...) is a biological sequence they pasted "
        "or uploaded, and each `Protein` (labelled by UniProt accession like "
        "`O95185`) is a card from UniProt. Tokens like `@Seq_A` or `@O95185` "
        "in user messages are references to these objects. "
        "\n\n"
        "You have been provided with database information about the protein the user is asking about. "
        "Use this data to ground your answers: explain what the protein does, where it's found, how it interacts, "
        "and why it matters clinically or biologically. "
        "\n\n"
        "Guidelines: "
        "- Answer the user's question directly and concisely "
        "- When the user references an object, echo it in your reply (e.g. "
        "'For `@Seq_A`: ...') so they can see how you resolved the reference "
        "- If a contextual reference like 'the second one' or 'the previous "
        "protein' is ambiguous, ask a short clarifying question instead of guessing "
        "- Connect relevant data points (function, location, interactions, disease links) to build a coherent explanation "
        "- If information is missing from the database, acknowledge it: 'The database doesn't have data on X, but based on Y we can infer...' "
        "- Maintain scientific accuracy while keeping explanations clear and accessible "
        "- Do not claim that a new database search was performed—use only the information provided"
    )


def build_protein_context(candidate: dict[str, Any] | None) -> str | None:
    """Render the selected candidate's protein metadata as a context string.

    Accepts the same dict shape the frontend uses for ``st.session_state.candidates``
    (i.e. ``{"protein": {...}, "match_score": float}``). Returns ``None`` if
    there is no usable candidate so callers can skip the context turn.
    """
    if not candidate or not isinstance(candidate, dict):
        return None
    protein = candidate.get("protein") or {}
    if not isinstance(protein, dict):
        return None
    match_score = candidate.get("match_score", 0) or 0

    lines = [
        "**Current protein context:**",
        f"Accession: {protein.get('accession', 'N/A')}",
        f"Name: {protein.get('name', 'Unknown')}",
        f"Gene: {protein.get('gene', 'N/A')}",
        f"Organism: {protein.get('organism_scientific', '')} ({protein.get('organism_common', '')})",
        f"Match confidence: {float(match_score):.1f}%",
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
            f"{item.get('gene') or item.get('accession') or 'Partner'}"
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

    go_terms = protein.get("go_terms") or []
    if go_terms:
        lines.append(f"\n**GO terms:** {', '.join(go_terms[:8])}")

    pathways = protein.get("pathways") or []
    if pathways:
        pathway_names = [p.get("name", "Pathway") for p in pathways[:3]]
        lines.append(f"\n**Key pathways:** {', '.join(pathway_names)}")

    return "\n".join(lines)


_MENTION_TOKEN_RE = re.compile(r"@([A-Za-z0-9_]+)")


def build_mentioned_protein_contexts(
    prompt: str,
    objects: dict[str, Any],
    *,
    skip_accessions: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Render full UniProt context for every ``@label`` named in ``prompt``.

    For each Sequence object referenced in the user's message, picks the
    user-selected protein match (``matches[selected_match_index]``, or the
    top match when no index is set) and runs it through
    :func:`build_protein_context`. That gives the LLM the same rich block
    the right-hand card shows — for **each** referenced Sequence, not just
    the workspace-wide selected one — so it can compare two proteins side
    by side.

    Returns ``[(label, context_text), ...]``. ``skip_accessions`` suppresses
    proteins that were already injected by the caller (e.g. via
    ``selected_candidate``) to avoid duplicate context blocks.
    """
    if not prompt or not objects:
        return []
    skip = {a.upper() for a in (skip_accessions or set()) if a}
    out: list[tuple[str, str]] = []
    seen_tokens: set[str] = set()
    for match in _MENTION_TOKEN_RE.finditer(prompt):
        token = match.group(1)
        if token in seen_tokens:
            continue
        seen_tokens.add(token)

        target: dict[str, Any] | None = None
        for obj in objects.values():
            if not isinstance(obj, dict):
                continue
            label = str(obj.get("label") or "")
            accession = str(obj.get("accession") or "")
            if token == label or token == accession:
                target = obj
                break
        if target is None:
            continue

        protein: dict[str, Any] | None = None
        match_score: Any = None
        kind = target.get("kind")
        if kind == "sequence":
            matches = target.get("matches") or []
            if not matches:
                continue
            try:
                chosen = int(target.get("selected_match_index") or 0)
            except (TypeError, ValueError):
                chosen = 0
            if chosen < 0 or chosen >= len(matches):
                chosen = 0
            mrec = matches[chosen]
            if isinstance(mrec, dict):
                protein = mrec.get("protein")
                match_score = mrec.get("match_score")
        elif kind == "protein":
            card = target.get("card")
            if isinstance(card, dict):
                protein = card
                match_score = target.get("match_score")

        if not isinstance(protein, dict):
            continue
        accession = str(protein.get("accession") or "").upper()
        if accession and accession in skip:
            continue

        rendered = build_protein_context({"protein": protein, "match_score": match_score or 0})
        if not rendered:
            continue
        out.append((token, f"**Context for `@{token}`:**\n{rendered}"))
        if accession:
            skip.add(accession)
    return out


def build_objects_context(
    objects: dict[str, Any],
    selected_object_id: str | None,
) -> str | None:
    """Render the workspace object registry as compact text for the LLM.

    For each ``Sequence`` we include only the currently-selected protein
    match (top-1 by default; whatever the user picked otherwise). This
    keeps the context cost low while still respecting the user's choice.
    """
    if not objects:
        return None
    lines = ["**Workspace objects:**"]
    sequences: list[dict[str, Any]] = []
    proteins: list[dict[str, Any]] = []
    for obj in objects.values():
        if not isinstance(obj, dict):
            continue
        if obj.get("kind") == "sequence":
            sequences.append(obj)
        elif obj.get("kind") == "protein":
            proteins.append(obj)

    if sequences:
        lines.append("\n**Sequences:**")
        for seq in sequences:
            label = seq.get("label") or seq.get("id")
            seq_type = seq.get("sequence_type") or "UNKNOWN"
            length = seq.get("length") or 0
            status = seq.get("status") or "draft"
            line = f"- `@{label}` ({seq_type}, {length} aa, {status})"
            matches = seq.get("matches") or []
            chosen_idx = int(seq.get("selected_match_index") or 0)
            if matches and 0 <= chosen_idx < len(matches):
                match = matches[chosen_idx]
                protein = match.get("protein") or {}
                acc = protein.get("accession") or match.get("accession") or ""
                name = protein.get("name") or ""
                gene = protein.get("gene") or ""
                score = match.get("match_score")
                score_str = (
                    f"{score:.0%}" if isinstance(score, float) and score <= 1 else
                    f"{score:.0f}%" if isinstance(score, (int, float)) else "n/a"
                )
                line += (
                    f"; user-selected match: `{acc}` ({gene} — {name}, score {score_str})"
                )
            lines.append(line)

    if proteins:
        lines.append("\n**Proteins:**")
        for protein in proteins:
            acc = protein.get("accession") or protein.get("label")
            gene = protein.get("gene") or ""
            organism = protein.get("organism") or ""
            line = f"- `@{acc}` (gene {gene or '—'}, {organism or '—'})"
            linked = protein.get("linked_sequence_ids") or []
            if linked:
                line += f" — linked to {', '.join('@' + lid for lid in linked)}"
            lines.append(line)

    if selected_object_id:
        obj = objects.get(selected_object_id)
        if isinstance(obj, dict):
            label = obj.get("label") or selected_object_id
            lines.append(f"\n**Currently selected:** `@{label}`")

    return "\n".join(lines)


def _build_gemini_contents(request: ChatLLMRequest) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    workspace_context = build_objects_context(request.objects, request.selected_object_id)
    if workspace_context:
        contents.append({"role": "user", "parts": [{"text": workspace_context}]})
        contents.append(
            {
                "role": "model",
                "parts": [
                    {
                        "text": (
                            "Thanks — I have the workspace context. I'll reference "
                            "objects by their `@<label>` ids in my answers."
                        )
                    }
                ],
            }
        )
    injected_accessions: set[str] = set()
    protein_context = build_protein_context(request.selected_candidate)
    if protein_context:
        contents.append({"role": "user", "parts": [{"text": protein_context}]})
        contents.append(
            {
                "role": "model",
                "parts": [
                    {
                        "text": (
                            "I understand. I have the context about the current protein. "
                            "I'll use this information to answer your questions."
                        )
                    }
                ],
            }
        )
        if isinstance(request.selected_candidate, dict):
            sel_protein = request.selected_candidate.get("protein") or {}
            if isinstance(sel_protein, dict):
                acc = str(sel_protein.get("accession") or "").upper()
                if acc:
                    injected_accessions.add(acc)

    for label, mention_context in build_mentioned_protein_contexts(
        request.prompt, request.objects, skip_accessions=injected_accessions
    ):
        contents.append({"role": "user", "parts": [{"text": mention_context}]})
        contents.append(
            {
                "role": "model",
                "parts": [
                    {
                        "text": (
                            f"Got it — I now have the full UniProt context for `@{label}`."
                        )
                    }
                ],
            }
        )

    seen_current_prompt = False
    for message in (request.history or [])[-20:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            seen_current_prompt = seen_current_prompt or content == request.prompt
            contents.append({"role": "user", "parts": [{"text": content}]})
        elif role == "assistant" and contents:
            contents.append({"role": "model", "parts": [{"text": content}]})

    if not seen_current_prompt:
        contents.append({"role": "user", "parts": [{"text": request.prompt}]})
    return contents


def _build_openai_messages(request: ChatLLMRequest, system_prompt_text: str) -> list[Any]:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    messages: list[Any] = [SystemMessage(content=system_prompt_text)]
    workspace_context = build_objects_context(request.objects, request.selected_object_id)
    if workspace_context:
        messages.append(HumanMessage(content=workspace_context))
        messages.append(
            AIMessage(
                content=(
                    "Thanks — I have the workspace context. I'll reference objects "
                    "by their `@<label>` ids in my answers."
                )
            )
        )
    injected_accessions: set[str] = set()
    protein_context = build_protein_context(request.selected_candidate)
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
        if isinstance(request.selected_candidate, dict):
            sel_protein = request.selected_candidate.get("protein") or {}
            if isinstance(sel_protein, dict):
                acc = str(sel_protein.get("accession") or "").upper()
                if acc:
                    injected_accessions.add(acc)

    for label, mention_context in build_mentioned_protein_contexts(
        request.prompt, request.objects, skip_accessions=injected_accessions
    ):
        messages.append(HumanMessage(content=mention_context))
        messages.append(
            AIMessage(
                content=f"Got it — I now have the full UniProt context for `@{label}`."
            )
        )

    seen_current_prompt = False
    for message in (request.history or [])[-20:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            seen_current_prompt = seen_current_prompt or content == request.prompt
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))

    if not seen_current_prompt:
        messages.append(HumanMessage(content=request.prompt))
    return messages


def _serialize_openai_message(message: Any) -> dict[str, Any]:
    """Render a LangChain message into a JSON-serialisable dict for debug display."""
    role = getattr(message, "type", None) or message.__class__.__name__
    content = getattr(message, "content", "")
    if not isinstance(content, (str, list, dict)):
        content = str(content)
    return {"role": role, "content": content}


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

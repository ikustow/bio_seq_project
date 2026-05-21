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

        full_system_prompt = _build_system_instruction(request, system_prompt)
        payload = {
            "contents": _build_gemini_contents(request),
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096},
            "systemInstruction": {"parts": [{"text": full_system_prompt}]},
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
            "system_prompt": full_system_prompt,
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
        full_system_prompt = (
            messages[0].content if messages and hasattr(messages[0], "content") else system_prompt
        )
        debug_request = {
            "provider": self.name,
            "model": model,
            "temperature": 0.2,
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "system_prompt": full_system_prompt,
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
        "The user works in a chat with a workspace of objects. A `Sequence` is "
        "a biological sequence the user pasted or uploaded; once resolved against "
        "UniProt it is addressed by the entry name of its top match (e.g. "
        "`@HBD_HUMAN`), or by `@Seq_A`, `@Seq_B`, ... before resolution. "
        "A `Protein` card is addressed by its UniProt accession (e.g. `@P02042`) "
        "or gene symbol (e.g. `@HBD`). All these forms can refer to the same "
        "underlying object — treat them as equivalent. "
        "\n\n"
        "Below this paragraph you will find the current workspace state and the "
        "UniProt details for the object(s) under discussion. Use that data to "
        "ground your answer — explain what the protein does, where it's found, "
        "how it interacts, and why it matters clinically or biologically. "
        "\n\n"
        "Guidelines: "
        "- Answer the user's question directly and concisely "
        "- When the user references an object, echo it in your reply (e.g. "
        "'For `@HBD_HUMAN`: ...') so they can see how you resolved the reference "
        "- If a contextual reference like 'the second one' or 'the previous "
        "protein' is ambiguous, ask a short clarifying question instead of guessing "
        "- Connect relevant data points (function, location, interactions, disease links) to build a coherent explanation "
        "- If information is missing from the database, acknowledge it: 'The database doesn't have data on X, but based on Y we can infer...' "
        "- Maintain scientific accuracy while keeping explanations clear and accessible "
        "- Do not claim that a new database search was performed—use only the information provided"
    )


def _resolved_label(obj: dict[str, Any] | None) -> str:
    """Backend mirror of frontend ``display_label``: entry_name > original label.

    For a Sequence with a chosen match, returns the match's ``entry_name``
    (e.g. ``HBD_HUMAN``). Otherwise returns the stored ``label`` (``Seq_A``)
    or the accession for Protein objects. Used so the LLM sees the same
    ``@HBD_HUMAN`` chip the user sees in the UI.
    """
    if not isinstance(obj, dict):
        return ""
    fallback = str(obj.get("label") or obj.get("id") or "")
    if obj.get("kind") != "sequence":
        return fallback
    matches = obj.get("matches") or []
    try:
        idx = int(obj.get("selected_match_index") or 0)
    except (TypeError, ValueError):
        idx = 0
    if 0 <= idx < len(matches):
        protein = (matches[idx] or {}).get("protein") or {}
        entry = str(protein.get("entry_name") or "").strip()
        if entry:
            return entry
    return fallback


def _label_tokens(obj: dict[str, Any]) -> set[str]:
    """All @-tokens by which the user can address ``obj``.

    Covers original label, accession, entry_name and gene so the mention
    parser resolves ``@Seq_A``, ``@HBD_HUMAN``, ``@P02042`` and ``@HBD``
    all to the same object.
    """
    tokens: set[str] = set()
    if not isinstance(obj, dict):
        return tokens
    for key in ("label", "accession"):
        value = str(obj.get(key) or "").strip()
        if value:
            tokens.add(value)
    if obj.get("kind") == "sequence":
        matches = obj.get("matches") or []
        try:
            idx = int(obj.get("selected_match_index") or 0)
        except (TypeError, ValueError):
            idx = 0
        if 0 <= idx < len(matches):
            protein = (matches[idx] or {}).get("protein") or {}
            for key in ("entry_name", "gene", "accession"):
                value = str(protein.get(key) or "").strip()
                if value:
                    tokens.add(value)
    elif obj.get("kind") == "protein":
        card = obj.get("card") or {}
        if isinstance(card, dict):
            for key in ("entry_name", "gene"):
                value = str(card.get(key) or "").strip()
                if value:
                    tokens.add(value)
    return tokens


def build_protein_context(
    candidate: dict[str, Any] | None,
    *,
    label: str | None = None,
) -> str | None:
    """Render the selected candidate's protein metadata as a context string.

    Accepts the same dict shape the frontend uses for ``st.session_state.candidates``
    (i.e. ``{"protein": {...}, "match_score": float}``). When ``label`` is
    given, the header reads ``**Context for @LABEL:**`` so the LLM can wire
    the protein details back to the workspace object the user sees in the UI.
    Returns ``None`` if there is no usable candidate so callers can skip
    the context block.
    """
    if not candidate or not isinstance(candidate, dict):
        return None
    protein = candidate.get("protein") or {}
    if not isinstance(protein, dict):
        return None
    match_score = candidate.get("match_score", 0) or 0
    # ``match_score`` arrives in two shapes depending on the upstream caller:
    # a ``[0, 1]`` fraction (most live pipelines) or a ``[0, 100]`` percent
    # (some fixtures and legacy paths). Treat anything ``<= 1`` as a fraction
    # so the rendered string is always a real percentage.
    try:
        score_value = float(match_score)
    except (TypeError, ValueError):
        score_value = 0.0
    if score_value <= 1.0:
        score_value *= 100

    header = f"**Context for `@{label}`:**" if label else "**Current protein context:**"
    lines = [
        header,
        f"Accession: {protein.get('accession', 'N/A')}",
        f"Name: {protein.get('name', 'Unknown')}",
        f"Gene: {protein.get('gene', 'N/A')}",
        f"Organism: {protein.get('organism_scientific', '')} ({protein.get('organism_common', '')})",
        f"Match confidence: {score_value:.1f}%",
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
    seen_object_ids: set[str] = set()
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
            if token in _label_tokens(obj):
                target = obj
                break
        if target is None:
            continue
        target_id = str(target.get("id") or "")
        if target_id and target_id in seen_object_ids:
            continue
        if target_id:
            seen_object_ids.add(target_id)

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

        echo_label = _resolved_label(target) or token
        rendered = build_protein_context(
            {"protein": protein, "match_score": match_score or 0},
            label=echo_label,
        )
        if not rendered:
            continue
        out.append((echo_label, rendered))
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
            label = _resolved_label(seq) or seq.get("id")
            seq_type = seq.get("sequence_type") or "UNKNOWN"
            length = seq.get("length") or 0
            status = seq.get("status") or "draft"
            # Protein sequences are measured in amino acids; nucleic acids in
            # nucleotides. Mislabelling DNA length as ``aa`` confused the LLM
            # because the workspace block disagreed with the UniProt ``Length``
            # below (where the protein truly is 110 aa, not 333).
            if seq_type == "PROTEIN":
                unit = "aa"
            elif seq_type in ("DNA", "RNA"):
                unit = "nt"
            else:
                unit = "chars"
            line = f"- `@{label}` ({seq_type}, {length} {unit}, {status})"
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
                    f"; resolved to `{acc}` ({gene} — {name}, score {score_str})"
                )
            lines.append(line)
            if seq_type in ("DNA", "RNA"):
                protein_sequence = str(seq.get("protein_sequence") or "")
                if protein_sequence:
                    lines.append(
                        f"  Translated protein: {len(protein_sequence)} aa"
                    )

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
            label = _resolved_label(obj) or selected_object_id
            lines.append(f"\n**Currently selected:** `@{label}`")

    return "\n".join(lines)


def _label_for_selected_candidate(request: ChatLLMRequest) -> str | None:
    """Find which workspace label the ``selected_candidate`` belongs to.

    Tries, in order: the explicitly-selected object, then any sequence whose
    chosen match shares the candidate's accession, then the candidate's own
    entry_name/accession. Used to print ``**Context for @LABEL:**`` so the
    LLM knows which workspace chip these protein details describe.
    """
    candidate = request.selected_candidate
    if not isinstance(candidate, dict):
        return None
    objects = request.objects or {}
    if request.selected_object_id and isinstance(objects, dict):
        obj = objects.get(request.selected_object_id)
        if isinstance(obj, dict):
            label = _resolved_label(obj)
            if label:
                return label
    sel_protein = candidate.get("protein") or {}
    acc = (
        str(sel_protein.get("accession") or "").upper()
        if isinstance(sel_protein, dict)
        else ""
    )
    if acc and isinstance(objects, dict):
        for obj in objects.values():
            if not isinstance(obj, dict) or obj.get("kind") != "sequence":
                continue
            matches = obj.get("matches") or []
            try:
                idx = int(obj.get("selected_match_index") or 0)
            except (TypeError, ValueError):
                idx = 0
            if 0 <= idx < len(matches):
                mprot = (matches[idx] or {}).get("protein") or {}
                if str(mprot.get("accession") or "").upper() == acc:
                    return _resolved_label(obj)
    if isinstance(sel_protein, dict):
        for key in ("entry_name", "accession"):
            value = str(sel_protein.get(key) or "").strip()
            if value:
                return value
    return None


def _build_system_instruction(request: ChatLLMRequest, base_system_prompt: str) -> str:
    """Concatenate the base system prompt with workspace + protein context.

    Putting workspace state into ``systemInstruction`` (instead of faking
    user/model turns inside ``contents``) means the LLM sees the context
    as situational background rather than as something the user just typed,
    and the real chat history stays clean — strictly alternating user/model.
    """
    pieces: list[str] = [base_system_prompt]

    workspace_context = build_objects_context(request.objects, request.selected_object_id)
    if workspace_context:
        pieces.append(workspace_context)

    injected_accessions: set[str] = set()
    selected_label = _label_for_selected_candidate(request)
    protein_context = build_protein_context(request.selected_candidate, label=selected_label)
    if protein_context:
        pieces.append(protein_context)
        if isinstance(request.selected_candidate, dict):
            sel_protein = request.selected_candidate.get("protein") or {}
            if isinstance(sel_protein, dict):
                acc = str(sel_protein.get("accession") or "").upper()
                if acc:
                    injected_accessions.add(acc)

    for _label, mention_context in build_mentioned_protein_contexts(
        request.prompt, request.objects, skip_accessions=injected_accessions
    ):
        pieces.append(mention_context)

    return "\n\n---\n\n".join(pieces)


def _strip_leading_assistant(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop welcome / pre-conversation assistant messages.

    A real dialog must start with a user turn; anything the assistant said
    before the first user message (typically the BioSeq welcome) is UI
    chatter that shouldn't leak into the LLM context.
    """
    trimmed = list(history)
    while trimmed and (
        not isinstance(trimmed[0], dict) or trimmed[0].get("role") != "user"
    ):
        trimmed.pop(0)
    return trimmed


def _build_gemini_contents(request: ChatLLMRequest) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    history = _strip_leading_assistant(list(request.history or [])[-20:])

    seen_current_prompt = False
    for message in history:
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

    full_system_prompt = _build_system_instruction(request, system_prompt_text)
    messages: list[Any] = [SystemMessage(content=full_system_prompt)]
    history = _strip_leading_assistant(list(request.history or [])[-20:])

    seen_current_prompt = False
    for message in history:
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

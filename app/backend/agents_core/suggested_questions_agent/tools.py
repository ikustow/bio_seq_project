from __future__ import annotations

from typing import Any

from langchain_core.tools import tool


def render_current_protein_context(selected_candidate: dict[str, Any] | None) -> str:
    if not selected_candidate or not isinstance(selected_candidate, dict):
        return "No selected protein context is available."
    protein = selected_candidate.get("protein") or {}
    if not isinstance(protein, dict):
        return "No selected protein context is available."

    lines = [
        f"Accession: {protein.get('accession') or 'N/A'}",
        f"Name: {protein.get('name') or protein.get('protein_name') or 'Unknown'}",
        f"Gene: {protein.get('gene') or protein.get('gene_primary') or 'N/A'}",
        f"Organism: {protein.get('organism_scientific') or protein.get('organism_name') or 'N/A'}",
    ]

    function = str(protein.get("function_text") or "").strip()
    if function:
        lines.append(f"Function: {_clip(function, 900)}")

    domains = protein.get("domains") or []
    domain_names: list[str] = []
    for item in domains[:6]:
        if isinstance(item, dict):
            name = item.get("name") or item.get("type") or "Domain"
            start = item.get("start")
            end = item.get("end")
            domain_names.append(f"{name} ({start}-{end})" if start and end else str(name))
    if domain_names:
        lines.append(f"Domains: {', '.join(domain_names)}")

    disease = protein.get("disease")
    if isinstance(disease, dict) and disease.get("name"):
        lines.append(f"Disease: {disease.get('name')}")

    for key, label in (
        ("keywords", "Keywords"),
        ("go_terms", "GO terms"),
        ("pathways", "Pathways"),
        ("subcellular_locations", "Subcellular locations"),
    ):
        values = protein.get(key) or []
        rendered = _render_values(values)
        if rendered:
            lines.append(f"{label}: {rendered}")

    interactions = protein.get("interactions") or []
    rendered_interactions = _render_values(interactions, keys=("gene", "accession", "name"))
    if rendered_interactions:
        lines.append(f"Interactions: {rendered_interactions}")

    return "\n".join(lines)


def render_recent_dialogue(history: list[dict[str, Any]], limit: int = 8) -> str:
    lines: list[str] = []
    for message in (history or [])[-limit:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {_clip(content, 700)}")
    return "\n".join(lines) or "No recent dialogue is available."


def infer_open_bioseq_threads(selected_candidate: dict[str, Any] | None, assistant_message: str) -> str:
    protein = (selected_candidate or {}).get("protein") if isinstance(selected_candidate, dict) else {}
    protein = protein if isinstance(protein, dict) else {}
    threads: list[str] = []

    if protein.get("function_text"):
        threads.append("biological function")
    if protein.get("domains"):
        threads.append("domain architecture")
    if protein.get("disease"):
        threads.append("disease association and evidence")
    if protein.get("pathways") or protein.get("go_terms"):
        threads.append("pathways and GO annotations")
    if protein.get("interactions"):
        threads.append("interaction partners")
    assistant_lower = assistant_message.lower()
    if "infer" in assistant_lower or "missing" in assistant_lower or "evidence" in assistant_lower:
        threads.append("limitations of available database evidence")

    if not threads:
        threads.append("next evidence-grounded analysis step")
    return ", ".join(threads)


def build_context_tools(
    *,
    selected_candidate: dict[str, Any] | None,
    history: list[dict[str, Any]],
    assistant_message: str,
) -> list[Any]:
    protein_context = render_current_protein_context(selected_candidate)
    dialogue = render_recent_dialogue(history)
    open_threads = infer_open_bioseq_threads(selected_candidate, assistant_message)

    @tool
    def get_current_protein_context() -> str:
        """Return compact facts about the currently selected protein card."""
        return protein_context

    @tool
    def get_recent_dialogue_summary() -> str:
        """Return the recent user and assistant messages in this chat."""
        return dialogue

    @tool
    def get_open_bioseq_threads() -> str:
        """Return promising biological topics for follow-up questions."""
        return open_threads

    return [get_current_protein_context, get_recent_dialogue_summary, get_open_bioseq_threads]


def _render_values(values: Any, keys: tuple[str, ...] = ("name", "id")) -> str:
    if not isinstance(values, list):
        return ""
    rendered: list[str] = []
    for item in values[:8]:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = next((str(item.get(key)) for key in keys if item.get(key)), "")
        else:
            text = str(item)
        if text and text not in rendered:
            rendered.append(text)
    return ", ".join(rendered)


def _clip(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_core.messages import BaseMessage

from ..config import (
    AMINO_ACID_SEQUENCE_RE,
    MAX_TRACKED_PROTEINS,
    MAX_TRACKED_SEQUENCES,
    MAX_WORKING_SET_IDS,
)
from ..models import ProteinRecord, SequenceRecord, SessionPatch, SessionStateView


def get_message_text(message: BaseMessage | dict[str, Any] | str) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        return str(message.get("content", ""))

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
        return "\n".join(parts)
    return str(content)


def get_message_role(message: BaseMessage | dict[str, Any] | str) -> str:
    if isinstance(message, dict):
        return str(message.get("role", "unknown"))
    if isinstance(message, str):
        return "text"
    return str(getattr(message, "type", "unknown"))


def serialize_message(message: BaseMessage | dict[str, Any] | str) -> dict[str, Any]:
    return {"role": get_message_role(message), "content": get_message_text(message)}


def maybe_parse_json_records(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text or text[0] not in "[{":
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []

    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def merge_unique_records(existing: list[dict[str, Any]], incoming: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in [*existing, *incoming]:
        key = tuple(item.get(field) for field in key_fields)
        if not any(value is not None for value in key):
            continue
        merged = dict(by_key.get(key, {}))
        merged.update({key: value for key, value in item.items() if value is not None})
        by_key[key] = merged
    return list(by_key.values())


def trim_tail(items: list[Any], limit: int) -> list[Any]:
    return items if len(items) <= limit else items[-limit:]


def extract_proteins(messages: list[Any], existing: list[ProteinRecord]) -> list[ProteinRecord]:
    extracted: list[dict[str, Any]] = []
    for message in messages:
        for record in maybe_parse_json_records(get_message_text(message)):
            if record.get("accession"):
                extracted.append(
                    ProteinRecord(
                        accession=record.get("accession"),
                        gene_name=record.get("gene_primary"),
                        protein_name=record.get("protein_name"),
                        source="tool_output",
                        status="active",
                        notes=record.get("organism_name"),
                    ).model_dump()
                )
            if record.get("neighbor_accession"):
                extracted.append(
                    ProteinRecord(
                        accession=record.get("neighbor_accession"),
                        gene_name=record.get("neighbor_gene"),
                        protein_name=record.get("neighbor_protein_name"),
                        source="tool_output",
                        status="candidate_neighbor",
                        notes=record.get("neighbor_organism"),
                    ).model_dump()
                )
            if record.get("target_accession"):
                extracted.append(
                    ProteinRecord(
                        accession=record.get("target_accession"),
                        source="tool_output",
                        status="active",
                        notes="Target accession from disease-context summary",
                    ).model_dump()
                )

    merged = merge_unique_records([item.model_dump() for item in existing], extracted, ("accession",))
    return [ProteinRecord.model_validate(item) for item in merged]


def extract_sequences(messages: list[Any], existing: list[SequenceRecord]) -> list[SequenceRecord]:
    extracted: list[dict[str, Any]] = []
    for message in messages:
        text = get_message_text(message)
        role = get_message_role(message)
        for raw_sequence in AMINO_ACID_SEQUENCE_RE.findall(text):
            normalized = raw_sequence.upper()
            extracted.append(
                SequenceRecord(
                    sequence_id=f"seq_{uuid.uuid5(uuid.NAMESPACE_OID, normalized).hex[:12]}",
                    sequence_type="protein",
                    raw_sequence=normalized,
                    label=f"{role}_sequence",
                    source=role,
                ).model_dump()
            )

    merged = merge_unique_records([item.model_dump() for item in existing], extracted, ("sequence_id",))
    return [SequenceRecord.model_validate(item) for item in merged]


def summarize_text(text: str, limit: int = 800) -> str | None:
    compact = " ".join(text.strip().split())
    if not compact:
        return None
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def derive_session_patch(state: dict[str, Any]) -> dict[str, Any]:
    view = SessionStateView.model_validate(state)
    proteins = trim_tail(extract_proteins(view.messages, view.proteins), MAX_TRACKED_PROTEINS)
    sequences = trim_tail(extract_sequences(view.messages, view.sequences), MAX_TRACKED_SEQUENCES)

    ai_messages = [message for message in view.messages if get_message_role(message) == "ai"]
    tool_messages = [message for message in view.messages if get_message_role(message) == "tool"]
    last_ai_text = get_message_text(ai_messages[-1]) if ai_messages else ""
    last_tool_text = get_message_text(tool_messages[-1]) if tool_messages else ""

    patch = SessionPatch(
        session_summary=summarize_text(last_ai_text),
        proteins=proteins,
        sequences=sequences,
        working_memory={
            **view.working_memory,
            "message_count": len(view.messages),
            "last_sync_source": "session_agent",
        },
        active_sequence_id=sequences[-1].sequence_id if sequences else view.active_sequence_id,
        active_accession=proteins[-1].accession if proteins else view.active_accession,
        last_analysis_summary=summarize_text(last_ai_text, limit=400),
        working_set_ids=trim_tail(
            [
                *[protein.accession for protein in proteins if protein.accession],
                *[sequence.sequence_id for sequence in sequences],
            ],
            MAX_WORKING_SET_IDS,
        ),
        current_mode=view.current_mode or "graph_analysis",
        last_tool_results_summary=summarize_text(last_tool_text, limit=400),
    )
    return patch.model_dump()

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from backend.agents_core.shared.models import AppContext, PersistenceResources, SessionPatch
from backend.agents_core.shared.services.session_state import serialize_message


class RuntimeSessionState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    current_mode: str | None
    working_memory: dict[str, Any]
    active_accession: str | None
    active_sequence_id: str | None
    proteins: list[dict[str, Any]]
    sequences: list[dict[str, Any]]


class BioSeqRuntimeSessionAgent:
    """Small LangGraph session agent for the runtime bioseq_retriever backend.

    Retrieval itself lives in ``BioSeqRetrieverPipeline``. This agent owns the
    chat/session boundary: thread-scoped LangGraph state plus compact
    ``public.chat_sessions`` synchronization for the UI.
    """

    def __init__(self, persistence: PersistenceResources) -> None:
        self._persistence = persistence
        builder = StateGraph(RuntimeSessionState)
        builder.add_node("respond", self._respond)
        builder.set_entry_point("respond")
        builder.add_edge("respond", END)
        self._graph = builder.compile(checkpointer=persistence.checkpointer)

    @property
    def warnings(self) -> list[str]:
        return self._persistence.warnings

    @property
    def persistence_mode(self) -> str:
        return self._persistence.mode

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]:
        config = _config(context)
        result = self._graph.invoke(
            {
                "messages": [HumanMessage(content=message)],
                "current_mode": "bioseq_runtime_session",
                "working_memory": {"last_sync_source": "bioseq_runtime_session"},
            },
            config=config,
        )
        current_state = dict(self._graph.get_state(config).values)
        self._sync_session(context, current_state)
        return result, current_state

    def get_current_state(self, context: AppContext) -> dict[str, Any]:
        config = _config(context)
        return dict(self._graph.get_state(config).values)

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]:
        config = _config(context)
        self._graph.update_state(config, _normalize_state_patch(patch))
        current_state = dict(self._graph.get_state(config).values)
        self._sync_session(context, current_state)
        return current_state

    def get_message_history(self, context: AppContext) -> list[dict[str, Any]]:
        return [serialize_message(message) for message in self.get_current_state(context).get("messages", [])]

    def _respond(self, state: RuntimeSessionState) -> dict[str, Any]:
        active_accession = state.get("active_accession")
        if active_accession:
            text = f"Current selected protein is {active_accession}."
        else:
            text = "Send a DNA or protein sequence and I will run bioseq_retriever against the local runtime index."
        return {
            "messages": [AIMessage(content=text)],
            "current_mode": "bioseq_runtime_session",
            "working_memory": {
                **(state.get("working_memory") or {}),
                "last_sync_source": "bioseq_runtime_session",
            },
        }

    def _sync_session(self, context: AppContext, current_state: dict[str, Any]) -> None:
        saved_session = self._persistence.session_repository.get_session(context.session_id)
        patch = _merge_session_patch(saved_session, _derive_session_patch(current_state))
        self._persistence.session_repository.upsert_session(context, patch)


def _config(context: AppContext) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": context.session_id}}


def _normalize_state_patch(patch: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(patch)
    for key in ("proteins", "sequences"):
        if key in normalized:
            normalized[key] = _model_dump_list(normalized[key])
    if "working_memory" in normalized:
        normalized["working_memory"] = dict(normalized.get("working_memory") or {})
    return normalized


def _derive_session_patch(state: dict[str, Any]) -> dict[str, Any]:
    proteins = _model_dump_list(state.get("proteins", []))
    sequences = _model_dump_list(state.get("sequences", []))
    active_accession = state.get("active_accession") or _last_value(proteins, "accession")
    active_sequence_id = state.get("active_sequence_id") or _last_value(sequences, "sequence_id")
    working_set_ids = _unique_tail(
        [
            *[item.get("accession") for item in proteins if item.get("accession")],
            *[item.get("sequence_id") for item in sequences if item.get("sequence_id")],
        ],
        limit=40,
    )
    message_count = len(state.get("messages") or [])
    summary = _summary(active_accession, len(proteins), len(sequences))
    return SessionPatch(
        session_summary=summary,
        proteins=proteins,
        sequences=sequences,
        working_memory={
            **(state.get("working_memory") or {}),
            "message_count": message_count,
            "last_sync_source": "bioseq_runtime_session",
        },
        active_sequence_id=active_sequence_id,
        active_accession=active_accession,
        last_analysis_summary=summary,
        working_set_ids=working_set_ids,
        current_mode=state.get("current_mode") or "bioseq_runtime_session",
        last_tool_results_summary=summary,
    ).model_dump()


def _merge_session_patch(saved: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if not saved:
        return incoming
    proteins = _merge_by_key(saved.get("proteins") or [], incoming.get("proteins") or [], "accession")
    sequences = _merge_by_key(saved.get("sequences") or [], incoming.get("sequences") or [], "sequence_id")
    working_memory = {
        **(saved.get("working_memory") or {}),
        **(incoming.get("working_memory") or {}),
    }
    working_set_ids = _unique_tail(
        [
            *(saved.get("working_set_ids") or []),
            *(incoming.get("working_set_ids") or []),
        ],
        limit=40,
    )
    return {
        **incoming,
        "proteins": proteins,
        "sequences": sequences,
        "working_memory": working_memory,
        "active_sequence_id": incoming.get("active_sequence_id") or saved.get("active_sequence_id"),
        "active_accession": incoming.get("active_accession") or saved.get("active_accession"),
        "working_set_ids": working_set_ids,
    }


def _model_dump_list(items: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in items or []:
        if hasattr(item, "model_dump"):
            output.append(item.model_dump())
        elif isinstance(item, dict):
            output.append(dict(item))
    return output


def _merge_by_key(existing: list[Any], incoming: list[Any], key: str) -> list[dict[str, Any]]:
    merged: dict[Any, dict[str, Any]] = {}
    for item in [*existing, *incoming]:
        payload = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        value = payload.get(key)
        if not value:
            continue
        previous = merged.get(value, {})
        merged[value] = {**previous, **{name: value for name, value in payload.items() if value is not None}}
    return list(merged.values())


def _last_value(items: list[dict[str, Any]], key: str) -> str | None:
    for item in reversed(items):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _unique_tail(items: list[Any], limit: int) -> list[str]:
    unique: list[str] = []
    for item in items:
        value = str(item) if item else ""
        if value and value not in unique:
            unique.append(value)
    return unique[-limit:]


def _summary(active_accession: str | None, protein_count: int, sequence_count: int) -> str:
    if active_accession:
        return f"bioseq_retriever runtime selected {active_accession}; proteins={protein_count}, sequences={sequence_count}."
    return f"bioseq_retriever runtime session; proteins={protein_count}, sequences={sequence_count}."

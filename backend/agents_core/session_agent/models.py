from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware.types import AgentState
from pydantic import BaseModel, ConfigDict, Field


class AppContext(BaseModel):
    user_id: str
    session_id: str
    workspace_id: str | None = None
    user_role: str | None = None


class ProteinRecord(BaseModel):
    accession: str | None = None
    gene_name: str | None = None
    protein_name: str | None = None
    source: str | None = None
    status: str | None = None
    notes: str | None = None


class SequenceRecord(BaseModel):
    sequence_id: str
    sequence_type: str
    raw_sequence: str
    label: str
    source: str
    linked_accession: str | None = None


class SessionPatch(BaseModel):
    session_summary: str | None = None
    proteins: list[ProteinRecord] = Field(default_factory=list)
    sequences: list[SequenceRecord] = Field(default_factory=list)
    working_memory: dict[str, Any] = Field(default_factory=dict)
    active_sequence_id: str | None = None
    active_accession: str | None = None
    last_analysis_summary: str | None = None
    working_set_ids: list[str] = Field(default_factory=list)
    current_mode: str | None = None
    last_tool_results_summary: str | None = None


class SessionRow(BaseModel):
    session_id: str
    thread_id: str
    user_id: str
    workspace_id: str | None = None
    user_role: str | None = None
    session_summary: str | None = None
    proteins: list[ProteinRecord] = Field(default_factory=list)
    sequences: list[SequenceRecord] = Field(default_factory=list)
    working_memory: dict[str, Any] = Field(default_factory=dict)
    active_sequence_id: str | None = None
    active_accession: str | None = None
    last_analysis_summary: str | None = None
    working_set_ids: list[str] = Field(default_factory=list)
    current_mode: str | None = None
    last_tool_results_summary: str | None = None

    model_config = ConfigDict(extra="ignore")

    def proteins_payload(self) -> list[dict[str, Any]]:
        return [item.model_dump() for item in self.proteins]

    def sequences_payload(self) -> list[dict[str, Any]]:
        return [item.model_dump() for item in self.sequences]


class SessionStateView(BaseModel):
    messages: list[Any] = Field(default_factory=list)
    proteins: list[ProteinRecord] = Field(default_factory=list)
    sequences: list[SequenceRecord] = Field(default_factory=list)
    working_memory: dict[str, Any] = Field(default_factory=dict)
    active_sequence_id: str | None = None
    active_accession: str | None = None
    current_mode: str | None = None

    model_config = ConfigDict(extra="ignore")


class SessionAgentState(AgentState[None], total=False):
    session_summary: str | None
    proteins: list[dict[str, Any]]
    sequences: list[dict[str, Any]]
    working_memory: dict[str, Any]
    active_sequence_id: str | None
    active_accession: str | None
    last_analysis_summary: str | None
    working_set_ids: list[str]
    current_mode: str | None
    last_tool_results_summary: str | None


@dataclass
class PersistenceResources:
    checkpointer: Any
    store: Any
    session_repository: Any
    mode: str
    warnings: list[str]

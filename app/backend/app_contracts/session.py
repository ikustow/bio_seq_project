from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SessionSnapshot(BaseModel):
    session_id: str
    user_id: str = "anonymous"
    workspace_id: str | None = None
    user_role: str | None = None
    active_accession: str | None = None
    active_sequence_id: str | None = None
    current_mode: str | None = None
    proteins: list[dict[str, Any]] = Field(default_factory=list)
    sequences: list[dict[str, Any]] = Field(default_factory=list)
    working_memory: dict[str, Any] = Field(default_factory=dict)
    message_history: list[dict[str, Any]] = Field(default_factory=list)

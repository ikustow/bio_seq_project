from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .pipeline import BioSeqPipelineSnapshot
from .protein_view import CandidateView
from .session import SessionSnapshot


class ChatTurnRequest(BaseModel):
    message: str
    session_id: str
    user_id: str = "anonymous"
    workspace_id: str | None = None
    user_role: str | None = None
    selected_accession: str | None = None
    selected_candidate_index: int | None = None
    search_algorithm: str | None = None
    think_mode: bool = False
    ui_context: dict[str, Any] = Field(default_factory=dict)


class ChatTurnResult(BaseModel):
    session_id: str
    assistant_message: str
    candidates: list[CandidateView] = Field(default_factory=list)
    selected_candidate_index: int = 0
    revealed_sections: set[str] = Field(default_factory=set)
    session: SessionSnapshot
    pipeline: BioSeqPipelineSnapshot | None = None
    warnings: list[str] = Field(default_factory=list)
    # Defaults preserve retriever-turn behaviour: replace the card, no provider info.
    update_card: bool = True
    current_mode: str | None = None
    provider: str | None = None
    provider_model: str | None = None
    suggested_questions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

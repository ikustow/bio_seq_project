from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .pipeline import BioSeqPipelineSnapshot
from .protein_view import CandidateView
from .session import SessionSnapshot


class ObjectsPatch(BaseModel):
    """Structured patch applied to the frontend object registry.

    Three operations:

    - ``upsert`` — keyed by ``object_id`` (``seq_A``, ``protein_O95185``).
      The value is a full or partial object. Missing fields are kept;
      provided top-level keys overwrite. ``matches`` is sent as the full
      list, never as deltas.
    - ``remove`` — object ids to delete.
    - ``set_selected`` — new selected object id, or ``None`` to leave the
      frontend's current selection alone.
    """

    upsert: dict[str, Any] = Field(default_factory=dict)
    remove: list[str] = Field(default_factory=list)
    set_selected: str | None = None


class ObjectMention(BaseModel):
    """A ``@<label>`` reference recognised by the frontend in user text."""

    token: str
    object_id: str | None = None
    label: str | None = None
    kind: str | None = None  # "sequence" | "protein"


class UploadedFile(BaseModel):
    """Metadata for a file the user attached via the composer."""

    file_name: str
    size: int | None = None
    mime_type: str | None = None
    sequence_ids: list[str] = Field(default_factory=list)


class ChatTurnRequest(BaseModel):
    message: str
    session_id: str
    user_id: str = "anonymous"
    workspace_id: str | None = None
    user_role: str | None = None
    # Legacy single-selection fields (kept for backward compatibility while
    # the registry rollout proceeds — service code prefers ``selected_object_id``).
    selected_accession: str | None = None
    selected_candidate_index: int | None = None
    selected_object_id: str | None = None
    objects: dict[str, Any] = Field(default_factory=dict)
    object_mentions: list[ObjectMention] = Field(default_factory=list)
    uploaded_files: list[UploadedFile] = Field(default_factory=list)
    search_algorithm: str | None = None
    think_mode: bool = False
    ui_context: dict[str, Any] = Field(default_factory=dict)


class ChatTurnResult(BaseModel):
    session_id: str
    assistant_message: str
    # Optional follow-up reply produced by the Chat-LLM (Gemini/OpenAI) right
    # after a retriever hit, rendered as a second assistant bubble. ``None``
    # means no follow-up was generated for this turn (e.g. retriever miss,
    # follow-up turn that already came from Chat-LLM, or LLM call failed).
    secondary_assistant_message: str | None = None
    secondary_provider: str | None = None
    secondary_provider_model: str | None = None
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
    # Object registry contract.
    objects_patch: ObjectsPatch = Field(default_factory=ObjectsPatch)
    selected_object_id: str | None = None

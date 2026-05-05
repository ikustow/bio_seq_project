from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BioSeqInputExtraction(BaseModel):
    sequence_or_path: str | None = Field(default=None)
    input_type: Literal["SEQUENCE", "FILEPATH", "TEXT"] = "TEXT"
    context: str = ""
    sequence_type: Literal["DNA", "PROTEIN", "UNKNOWN"] = "UNKNOWN"
    is_confident: bool = False
    reasoning: str = ""


class BioSeqPipelineSnapshot(BaseModel):
    prompt: str
    sequence_or_path: str | None = None
    input_type: Literal["SEQUENCE", "FILEPATH", "TEXT"] = "TEXT"
    context: str = ""
    sequence: str | None = None
    sequence_type: Literal["DNA", "PROTEIN", "UNKNOWN"] = "UNKNOWN"
    protein_sequence: str | None = None
    is_confident: bool = False
    active_accession: str | None = None
    controlled_miss: bool = False
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)

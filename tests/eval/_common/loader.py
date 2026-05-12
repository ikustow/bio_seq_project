"""YAML loaders for the three eval datasets.

Returns plain dicts (no dataclasses for now — keeps the harness easy to evolve
when scenario fields change). Each loader is responsible only for parsing and
shape sanity-checks; semantic validation lives in `validate_data.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PROTEINS_YAML = DATA_DIR / "proteins.yaml"
LLM_SCENARIOS_YAML = DATA_DIR / "llm_scenarios.yaml"
E2E_YAML = DATA_DIR / "end_to_end.yaml"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Dataset file is missing: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_proteins() -> dict[str, Any]:
    return _load(PROTEINS_YAML)


def load_llm_scenarios() -> dict[str, Any]:
    return _load(LLM_SCENARIOS_YAML)


def load_e2e() -> dict[str, Any]:
    return _load(E2E_YAML)


def clean_sequence(seq: str) -> str:
    """Strip whitespace and newlines from a sequence block (YAML `|` form)."""
    return "".join(seq.split())

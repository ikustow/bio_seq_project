"""Adapter from the Streamlit UI to the bioseq_retriever pipeline.

Calls `run_bioseq_pipeline(prompt)` and translates its `final_results`
(UniProt JSON dicts) into the UI's `Candidate` view-model list.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root must be on sys.path so `bioseq_retriever` is importable when
# Streamlit launches `streamlit_ui/app.py` directly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from bioseq_retriever.src.pipeline import run_bioseq_pipeline  # noqa: E402

from mock.protein_loader import Candidate, from_dict  # noqa: E402


def run_search(prompt: str) -> list[Candidate]:
    """Run the bioseq pipeline and return UI-ready Candidate list.

    Score is a placeholder (0.0) — the rerank step currently drops scores.
    The UI should render a neutral "match-confidence unavailable" badge.
    """
    result = run_bioseq_pipeline(prompt)

    error = result.get("error")
    if error:
        raise RuntimeError(error)

    out: list[Candidate] = []
    for record in result.get("final_results") or []:
        out.append(Candidate(protein=from_dict(record), match_score=0.0))
    return out

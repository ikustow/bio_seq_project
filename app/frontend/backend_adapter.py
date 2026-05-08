"""Adapter from the Streamlit UI to the bioseq_retriever pipeline.

Calls `run_bioseq_pipeline(prompt)` and translates its `final_results`
(UniProt JSON dicts) into the UI's `Candidate` view-model list.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Project root must be on sys.path so `bioseq_retriever` is importable when
# Streamlit launches `app/frontend/app.py` directly.
_FRONTEND_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RETRIEVER_ROOT = _PROJECT_ROOT / "bioseq_retriever"

for path in (_FRONTEND_ROOT, _PROJECT_ROOT, _RETRIEVER_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

_DATA_DIR = _RETRIEVER_ROOT / "data"
os.environ.setdefault("BIOSEQ_H5_PATH", str(_DATA_DIR / "per-protein.h5"))
os.environ.setdefault("BIOSEQ_INDEX_PATH", str(_DATA_DIR / "per-protein.index"))
# bioseq_retriever switched the accessions cache from pickle to JSON; default
# the path extension to match (was ``.pkl``).
os.environ.setdefault(
    "BIOSEQ_ACCESSIONS_CACHE_PATH",
    str(_DATA_DIR / "per-protein.accessions.json"),
)
# Default-off the new microservices mode; we run ProtT5+FAISS in-process.
os.environ.setdefault("BIOSEQ_USE_SERVICES", "false")

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

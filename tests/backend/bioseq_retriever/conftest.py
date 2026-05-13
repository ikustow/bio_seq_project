"""Pytest path setup for backend bioseq_retriever tests."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BIOSEQ_RETRIEVER_ROOT = REPO_ROOT / "app" / "backend" / "bioseq_retriever"

if str(BIOSEQ_RETRIEVER_ROOT) not in sys.path:
    sys.path.insert(0, str(BIOSEQ_RETRIEVER_ROOT))

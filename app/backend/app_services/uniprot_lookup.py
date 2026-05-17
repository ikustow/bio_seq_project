"""Direct UniProt accession / mnemonic-ID lookup.

Used when the user types an explicit identifier (e.g. ``O95185`` or
``UNC5C_HUMAN``) in the chat. Skips the embedding/rerank pipeline — the
user has already committed to one protein, so showing top-5 similar
candidates would be noise.

Resolution order:

1. Local cache of ``test_data_from_database/<ACCESSION>.json`` (mock mode
   and offline development).
2. UniProt REST API (when network is available and the bioseq_retriever
   ``data_fetcher`` succeeds).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from backend.app_contracts import ProteinView

from .protein_view_mapper import uniprot_record_to_view


_TEST_DATA_ROOT = (
    Path(__file__).resolve().parents[2] / "frontend" / "test_data_from_database"
)


def lookup_protein_view(identifier: str) -> ProteinView | None:
    """Resolve a UniProt accession or mnemonic ID into a ProteinView.

    Returns ``None`` if no record could be loaded from either local cache
    or the remote API.
    """
    identifier = (identifier or "").strip().upper()
    if not identifier:
        return None

    record = _lookup_local(identifier) or _lookup_remote(identifier)
    if not record:
        return None

    try:
        return uniprot_record_to_view(record)
    except Exception:
        return None


def _lookup_local(identifier: str) -> dict[str, Any] | None:
    """Look up an accession in the local test_data_from_database cache."""
    if not _TEST_DATA_ROOT.exists():
        return None
    candidate = _TEST_DATA_ROOT / f"{identifier}.json"
    if not candidate.exists():
        return None
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except Exception:
        return None


def _lookup_remote(identifier: str) -> dict[str, Any] | None:
    """Look up an accession / mnemonic via the UniProt REST API.

    Reuses the ``data_fetcher`` from bioseq_retriever so we share the
    user-agent / retry behaviour configured there.
    """
    backend_retriever_root = Path(__file__).resolve().parents[1] / "bioseq_retriever"
    root_text = str(backend_retriever_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    try:
        from src.data_fetcher import get_uniprot_records  # type: ignore
    except Exception:
        return None

    try:
        records = get_uniprot_records([identifier])
    except Exception:
        return None
    if not records:
        return None
    for record in records:
        primary = (record.get("primaryAccession") or "").upper()
        if primary == identifier:
            return record
        names = record.get("uniProtkbId") or ""
        if isinstance(names, str) and names.upper() == identifier:
            return record
    # Fall back to the first record if the API returned anything at all.
    return records[0]

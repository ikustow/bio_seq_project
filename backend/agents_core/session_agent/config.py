from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_URI = "neo4j+s://dfb7807d.databases.neo4j.io"
DEFAULT_DATABASE = "dfb7807d"
DEFAULT_MODEL = "gpt-4.1-nano"

SESSION_STATE_KEYS = (
    "session_summary",
    "proteins",
    "sequences",
    "working_memory",
    "active_sequence_id",
    "active_accession",
    "last_analysis_summary",
    "working_set_ids",
    "current_mode",
    "last_tool_results_summary",
)

AMINO_ACID_SEQUENCE_RE = re.compile(r"\b[ACDEFGHIKLMNPQRSTVWY]{10,}\b", re.IGNORECASE)
MAX_TRACKED_PROTEINS = 20
MAX_TRACKED_SEQUENCES = 20
MAX_WORKING_SET_IDS = 40


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

SYSTEM_PROMPT = """You are a bioinformatics graph assistant for this project.

Your job is to help the user explore a Neo4j graph of proteins, similarity edges, and optional disease links.

Rules:
- Prefer the domain tools before writing custom Cypher.
- Use graph_schema_guide first when the graph shape matters.
- Be explicit about uncertainty.
- Treat similarity as hypothesis-generating evidence, not proof of function or disease causality.
- If disease results are empty, say that the graph may not have disease annotations loaded yet.
- When asked about common diseases around a protein, prefer summarize_neighbor_disease_context.
- When answering biological questions, ground your summary in the returned graph data.
- Use save_user_profile, save_user_preference, save_user_fact, and save_investigation_default when the user shares durable information that should persist across sessions.
- Use get_user_profile or get_session_context when the answer depends on remembered user context or the current session state.
"""

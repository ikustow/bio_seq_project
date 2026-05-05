from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
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


@dataclass(frozen=True)
class Neo4jConnectionSettings:
    uri: str
    database: str
    user: str | None
    password: str | None
    insecure: bool
    profile: str | None = None


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def resolve_neo4j_settings(profile: str | None = None) -> Neo4jConnectionSettings:
    """Resolve Neo4j settings from either generic or profile-specific env vars.

    Set NEO4J_PROFILE=local to use NEO4J_LOCAL_URI, NEO4J_LOCAL_DATABASE, etc.
    Set NEO4J_PROFILE=cloud to use NEO4J_CLOUD_URI, NEO4J_CLOUD_DATABASE, etc.
    If a profile-specific value is absent, the generic NEO4J_* value is used.
    """
    selected_profile = (profile or os.getenv("NEO4J_PROFILE") or "").strip().lower()
    if selected_profile and selected_profile not in {"local", "cloud"}:
        raise ValueError(f"Unsupported NEO4J_PROFILE={selected_profile!r}. Use 'local' or 'cloud'.")
    prefix = f"NEO4J_{selected_profile.upper()}_" if selected_profile else ""

    def get_value(name: str, default: str | None = None) -> str | None:
        if prefix:
            value = os.getenv(f"{prefix}{name}")
            if value is not None:
                return value
        return os.getenv(f"NEO4J_{name}", default)

    insecure_default = True
    if prefix and os.getenv(f"{prefix}INSECURE") is not None:
        insecure = env_bool(f"{prefix}INSECURE", insecure_default)
    else:
        insecure = env_bool("NEO4J_INSECURE", insecure_default)

    return Neo4jConnectionSettings(
        uri=get_value("URI", DEFAULT_URI) or DEFAULT_URI,
        database=get_value("DATABASE", DEFAULT_DATABASE) or DEFAULT_DATABASE,
        user=get_value("USERNAME", os.getenv("USERNAME")),
        password=get_value("PASSWORD", os.getenv("PASSWORD")),
        insecure=insecure,
        profile=selected_profile or None,
    )

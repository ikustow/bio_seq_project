"""Load environment variables from `.env` at the repo root.

Each eval entry-point calls `load_env()` before doing anything that reads
from the environment (API keys, proxy URLs). Idempotent — safe to call
multiple times. Silently no-ops if `python-dotenv` is not installed.
"""

from __future__ import annotations

from pathlib import Path

from tests.eval._common.run_dir import REPO_ROOT


_ENV_FILE = REPO_ROOT / ".env"


def load_env() -> bool:
    """Load `<repo>/.env` into os.environ. Returns True if the file was read."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    if not _ENV_FILE.exists():
        return False
    return bool(load_dotenv(dotenv_path=_ENV_FILE, override=False))

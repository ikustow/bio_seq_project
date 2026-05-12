"""Load environment variables from `.env` at the repo root.

Each eval entry-point calls `load_env()` before doing anything that reads
from the environment (API keys, proxy URLs). Idempotent — safe to call
multiple times. Silently no-ops if `python-dotenv` is not installed.

Also installs a Windows-specific OpenMP duplicate-runtime workaround at
import time (before torch/faiss/MKL are imported elsewhere). On Windows
the conda/pip combo of torch + faiss-cpu often bundles two OpenMP DLLs,
and OMP's runtime check aborts the process. The workaround is documented
in the OMP error message itself; safe here because our harness is a
single short-lived process doing deterministic eval, not multi-threaded
shared-state code.
"""

from __future__ import annotations

import os
from pathlib import Path

from tests.eval._common.run_dir import REPO_ROOT


_ENV_FILE = REPO_ROOT / ".env"


# Set BEFORE any heavy import (torch, faiss, transformers) — these DLLs
# read KMP_DUPLICATE_LIB_OK at C-runtime initialization. `setdefault`
# means we don't override a user-provided value.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def load_env() -> bool:
    """Load `<repo>/.env` into os.environ. Returns True if the file was read."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    if not _ENV_FILE.exists():
        return False
    return bool(load_dotenv(dotenv_path=_ENV_FILE, override=False))

"""Run directory helpers — every eval invocation creates an isolated dir."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS_ROOT = REPO_ROOT / "tests" / "eval" / "runs"


def make_run_dir(suite: str, *, root: Path | None = None) -> Path:
    """Create runs/<ISO-timestamp>-<suite>/ and return it."""
    root = root or RUNS_ROOT
    stamp = _dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    out = root / f"{stamp}-{suite}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def latest_run_dir(suite_prefix: str | None = None, *, root: Path | None = None) -> Path | None:
    """Return the most recently modified run dir (optionally filtered by suffix)."""
    root = root or RUNS_ROOT
    if not root.exists():
        return None
    candidates = [p for p in root.iterdir() if p.is_dir() and p.name != "baseline"]
    if suite_prefix:
        candidates = [p for p in candidates if p.name.endswith(f"-{suite_prefix}")]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)

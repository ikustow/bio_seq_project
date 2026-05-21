"""Spawn the heavy ProtT5/FAISS search gateway as a child process.

Why this module exists
----------------------
After the May-2026 rewrite the runtime retriever no longer runs the ProtT5 +
FAISS engine in-process. ``app/backend/bioseq_retriever/src/search.py`` is a
thin HTTP client that talks to the unified gateway
(``app/backend/bioseq_retriever/services/search_service.py``) on
``BIOSEQ_SEARCH_SERVICE_URL`` (default ``http://localhost:8002``). Locally you
start that gateway in a second terminal.

A Hugging Face *Streamlit* Space has a single entry point
(``streamlit run app/frontend/app.py``) — there is no second terminal. This
module lets the Streamlit process itself bring up the gateway, so one
``streamlit run`` boots both halves.

Design
------
* **Opt-in.** Does nothing unless ``BIOSEQ_SPAWN_GATEWAY`` is truthy. Local
  two-terminal dev is therefore never disrupted; set the variable only on the
  Space.
* **Idempotent.** If something is already listening on the gateway port (a
  manually started gateway, or a previous spawn that survived a Streamlit
  script rerun) it is a no-op.
* **Non-blocking.** The actual work — optional data download + model load +
  index build, several minutes — happens in a daemon thread and in the child
  process, never on Streamlit's script thread. The retriever's own fail-fast
  liveness probe surfaces "gateway not reachable yet" cleanly during warmup.
* **Hands off ``main``.** This is deploy glue on the frontend side; it does not
  modify the retriever or the gateway. It launches ``bootstrap.py`` and
  ``search_service.py`` exactly the way the docs say to launch them by hand.

Data prerequisites (see DEPLOY_HF_SINGLE_PROCESS.md): the gateway loads BOTH a
protein and a DNA FAISS index at startup. ``bootstrap`` now fetches both
``per-protein.h5`` and ``per-gene.h5`` from the dataset — the operator only has
to upload ``per-gene.h5`` there once, or the gateway exits on boot.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RETRIEVER = _REPO_ROOT / "app" / "backend" / "bioseq_retriever"
_GATEWAY_SCRIPT = _RETRIEVER / "services" / "search_service.py"
_BOOTSTRAP_SCRIPT = _RETRIEVER / "src" / "bootstrap.py"

_SERVICE_URL = os.getenv("BIOSEQ_SEARCH_SERVICE_URL", "http://localhost:8002")

# Module-level guard so a single process never starts two children even if the
# st.cache_resource layer is bypassed.
_started = False
_lock = threading.Lock()


def _log(msg: str) -> None:
    print(f"[gateway_supervisor] {msg}", flush=True)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def port_is_open(url: str = _SERVICE_URL, timeout: float = 0.5) -> bool:
    """True if a TCP connection to the gateway's host:port succeeds."""
    host, port = _host_port(url)
    # The gateway binds 0.0.0.0; probing 127.0.0.1 still reaches it.
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    try:
        with socket.create_connection((probe_host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _spawn_worker() -> None:
    """Download protein data (best-effort), then launch the gateway."""
    child_env = os.environ.copy()
    # Don't let the child re-trigger this supervisor logic.
    child_env.pop("BIOSEQ_SPAWN_GATEWAY", None)

    # 1) Best-effort data bootstrap (protein only). Reuses the retriever's own
    #    bootstrap script via its __main__; same cwd/env as the gateway so the
    #    relative data dir resolves to the same place. Failure is non-fatal —
    #    the gateway will report missing data in its own logs.
    if _truthy(os.getenv("BIOSEQ_BOOTSTRAP_DATA", "true")) and _BOOTSTRAP_SCRIPT.exists():
        try:
            _log(f"running data bootstrap: {_BOOTSTRAP_SCRIPT.name}")
            subprocess.run(
                [sys.executable, str(_BOOTSTRAP_SCRIPT)],
                cwd=str(_REPO_ROOT),
                env=child_env,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001 - never let glue crash the UI
            _log(f"data bootstrap failed (continuing anyway): {exc!r}")

    # 2) Launch the long-running gateway. stdout/stderr default to the parent's
    #    real file descriptors, so the child's progress lands in the Space logs.
    if not _GATEWAY_SCRIPT.exists():
        _log(f"ERROR gateway script not found: {_GATEWAY_SCRIPT}")
        return
    _log(f"launching gateway: {_GATEWAY_SCRIPT}")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(_GATEWAY_SCRIPT)],
            cwd=str(_REPO_ROOT),
            env=child_env,
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"ERROR failed to launch gateway: {exc!r}")
        return

    # Catch an immediate crash (e.g. missing fastapi/uvicorn, missing data) so
    # it shows up in logs at once instead of as a silent never-ready gateway.
    # We do NOT wait for full warmup — model load + index build takes minutes.
    time.sleep(2.0)
    rc = proc.poll()
    if rc is not None:
        _log(f"ERROR gateway exited immediately (code {rc}) — check logs above")
    else:
        _log(f"gateway running (pid {proc.pid}); warming up (models + FAISS)…")


def ensure_gateway() -> dict:
    """Start the search gateway in the background if needed. Idempotent.

    Returns a small status dict for logging / the debug panel. Never raises:
    deploy glue must not be able to take the UI down.
    """
    global _started

    if not _truthy(os.getenv("BIOSEQ_SPAWN_GATEWAY")):
        return {"spawned": False, "reason": "BIOSEQ_SPAWN_GATEWAY not enabled"}

    host, _ = _host_port(_SERVICE_URL)
    if host not in ("localhost", "127.0.0.1", "0.0.0.0"):
        # Split deploy: the gateway is a separate host; nothing to spawn here.
        return {"spawned": False, "reason": f"gateway is remote ({_SERVICE_URL})"}

    with _lock:
        if _started:
            return {"spawned": False, "reason": "already started this process"}
        if port_is_open(_SERVICE_URL):
            return {"spawned": False, "reason": "gateway already listening"}
        _started = True

    threading.Thread(target=_spawn_worker, name="gateway-spawn", daemon=True).start()
    return {"spawned": True, "reason": "launching in background"}

"""First-boot data bootstrap for the bioseq_retriever pipeline.

Downloads the embeddings the unified gateway loads at startup:

  * per-protein.h5  (~1.3 GB, protein FAISS source) — required.
  * per-gene.h5     (DNA/gene FAISS source)         — required: the
    gateway loads the DNA index at boot and EXITS if it is missing.

plus, when present, the matching pre-built FAISS index + JSON accession
cache (<name>.index / <name>.accessions.json) so the gateway skips the
slow rebuild. Files land in BIOSEQ_DATA_DIR. Idempotent: a second call
is a no-op when the files are already on disk.

Source is selected by `BIOSEQ_DATA_SOURCE`:

  "uniprot"               UniProt FTP — protein only (no per-gene.h5).
  "hf:OWNER/DATASET_REPO" HF Hub dataset (fast; the operator uploads
                          per-protein.h5 + per-gene.h5 [+ optional
                          .index / .accessions.json] there once).

Default is "uniprot" so that the Space boots without extra setup, even
if cold starts take ~10 min on the embeddings download.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_UNIPROT_H5_URL = (
    "https://ftp.uniprot.org/pub/databases/uniprot/current_release/"
    "knowledgebase/embeddings/uniprot_sprot/per-protein.h5"
)

# A non-empty .h5 must be at least this big — guards against half-downloaded
# files left behind by a killed container. Anything smaller is treated as
# missing and re-downloaded.
_H5_MIN_BYTES = 1_000_000_000  # 1.0 GB; real file is ~1.38 GB

# per-gene.h5 (DNA) is much smaller and its size depends on how many SwissProt
# entries got a DNA mapping, so we only guard against an empty / truncated file
# rather than asserting a specific size.
_GENE_H5_MIN_BYTES = 1_000_000  # 1 MB


def _data_dir() -> Path:
    return Path(
        os.getenv("BIOSEQ_DATA_DIR", os.path.join("bioseq_retriever", "data"))
    )


def _file_ok(path: Path, min_bytes: int = 1) -> bool:
    return path.exists() and path.stat().st_size >= min_bytes


def _log(msg: str) -> None:
    print(f"[bioseq.bootstrap] {msg}", flush=True)


def _download_url(url: str, dest: Path) -> None:
    """Stream a URL to disk with progress lines (HF Space logs friendly)."""
    import requests

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    _log(f"GET {url}")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        bytes_done = 0
        next_log_at = 50 * 1024 * 1024  # log every ~50 MB
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                bytes_done += len(chunk)
                if bytes_done >= next_log_at:
                    pct = (bytes_done / total * 100) if total else 0
                    _log(f"  {bytes_done / 1e9:.2f} GB / {total / 1e9:.2f} GB ({pct:.0f}%)")
                    next_log_at += 50 * 1024 * 1024
    tmp.replace(dest)
    _log(f"saved {dest} ({dest.stat().st_size:,} bytes)")


def _download_hf(repo_id: str, filename: str, dest: Path) -> None:
    """Pull `filename` from a HF Hub dataset repo into `dest`."""
    from huggingface_hub import hf_hub_download  # lazy import

    _log(f"HF Hub: {repo_id}::{filename}")
    cached = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        repo_type="dataset",
        local_dir=str(dest.parent),
    )
    cached_path = Path(cached)
    if cached_path != dest:
        if dest.exists():
            dest.unlink()
        cached_path.replace(dest)
    _log(f"saved {dest} ({dest.stat().st_size:,} bytes)")


def _ensure_h5(source: str, filename: str, dest: Path, min_bytes: int,
               uniprot_url: str | None = None) -> bool:
    """Ensure a (large) .h5 embeddings file is on disk. Returns True if present.

    HF source pulls ``filename`` from the dataset repo. ``uniprot`` source only
    works when a ``uniprot_url`` is supplied (protein only) — there is no
    UniProt FTP location for the DNA ``per-gene.h5``.
    """
    if _file_ok(dest, min_bytes):
        _log(f"{filename} already present at {dest}, skipping download")
        return True

    _log(f"{filename} missing or undersized at {dest}")
    if source.startswith("hf:"):
        repo_id = source.split(":", 1)[1]
        _download_hf(repo_id, filename, dest)
        return True
    if source == "uniprot":
        if uniprot_url is None:
            _log(f"source=uniprot has no location for {filename}; skipping")
            return False
        _download_url(uniprot_url, dest)
        return True
    raise RuntimeError(
        f"Unknown BIOSEQ_DATA_SOURCE={source!r}. Use 'uniprot' or 'hf:OWNER/DATASET'."
    )


def _pkl_to_json_accessions(pkl_path: Path, json_path: Path) -> int:
    """Convert a legacy pickled accession list to the JSON the gateway reads.

    Older dataset snapshots shipped ``<prefix>.accessions.pkl`` (a pickled list
    of UniProt accessions in FAISS-row order). The gateway only reads
    ``.accessions.json`` (via ``json.load``), so we translate in place rather
    than force a dataset re-upload. Returns the number of accessions written.
    """
    import json
    import pickle

    with pkl_path.open("rb") as fh:
        accessions = pickle.load(fh)  # trusted: operator's own dataset
    if not isinstance(accessions, list):
        accessions = list(accessions)
    accessions = [a.decode() if isinstance(a, bytes) else str(a) for a in accessions]
    with json_path.open("w") as fh:
        json.dump(accessions, fh)
    return len(accessions)


def _ensure_prebuilt_index(source: str, data_dir: Path, prefix: str) -> None:
    """Best-effort fetch of a pre-built FAISS index + JSON accession cache.

    The gateway's ``load_or_create_index`` uses the pre-built pair only when
    BOTH ``<prefix>.index`` and ``<prefix>.accessions.json`` are on disk; with
    either missing it rebuilds from the ``.h5`` (a 5–15 min one-time cold-start
    cost). The cache must be ``.json`` (the gateway reads it with ``json.load``).
    We prefer a shipped ``.json`` but fall back to downloading and converting a
    legacy ``.accessions.pkl`` so a dataset that only has ``.pkl`` still yields a
    usable cache without a re-upload. Missing files here are non-fatal.
    """
    if not source.startswith("hf:"):
        return  # UniProt FTP hosts no pre-built index; the gateway builds it.
    repo_id = source.split(":", 1)[1]
    index_path = data_dir / f"{prefix}.index"
    cache_json = data_dir / f"{prefix}.accessions.json"
    if _file_ok(index_path) and _file_ok(cache_json):
        _log(f"{prefix}: pre-built index + accessions.json already present")
        return

    # 1) The FAISS index itself. No index → nothing to pair a cache with.
    if not _file_ok(index_path):
        try:
            _download_hf(repo_id, f"{prefix}.index", index_path)
        except Exception as exc:  # noqa: BLE001
            _log(f"{prefix}: pre-built .index not in dataset ({exc}); "
                 "gateway will rebuild from .h5 (one-time cold-start cost)")
            return

    # 2) The accession cache the gateway reads (.json). Prefer a shipped .json;
    #    otherwise convert a legacy .pkl.
    if _file_ok(cache_json):
        _log(f"{prefix}: pre-built index + accessions.json ready — skipping rebuild")
        return
    try:
        _download_hf(repo_id, f"{prefix}.accessions.json", cache_json)
        _log(f"{prefix}: pre-built FAISS index pulled from HF — skipping rebuild")
        return
    except Exception:  # noqa: BLE001
        pass  # fall through to the .pkl fallback
    try:
        pkl_path = data_dir / f"{prefix}.accessions.pkl"
        _download_hf(repo_id, f"{prefix}.accessions.pkl", pkl_path)
        n = _pkl_to_json_accessions(pkl_path, cache_json)
        _log(f"{prefix}: converted legacy .accessions.pkl -> .accessions.json "
             f"({n} entries) — skipping rebuild")
    except Exception as exc:  # noqa: BLE001
        _log(f"{prefix}: no .accessions.json/.pkl in dataset ({exc}); "
             "gateway will rebuild from .h5 (one-time cold-start cost)")


def ensure_data() -> None:
    """Make sure the gateway's embeddings files are on disk before it boots.

    Fetches per-protein.h5 and per-gene.h5 (both loaded by the gateway at
    startup) plus, when available, their pre-built .index/.accessions.json.
    Reads BIOSEQ_DATA_SOURCE to pick the source. Safe to call multiple times —
    a second call is a no-op when files are already present.
    """
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    source = os.getenv("BIOSEQ_DATA_SOURCE", "uniprot").strip()

    # --- Protein FAISS source (required) ---
    _ensure_h5(source, "per-protein.h5", data_dir / "per-protein.h5",
               _H5_MIN_BYTES, uniprot_url=_UNIPROT_H5_URL)
    _ensure_prebuilt_index(source, data_dir, "per-protein")

    # --- DNA/gene FAISS source (required: the gateway loads the DNA index at
    #     startup and EXITS without it). HF-only — no UniProt FTP fallback. ---
    gene_ok = False
    try:
        gene_ok = _ensure_h5(source, "per-gene.h5", data_dir / "per-gene.h5",
                             _GENE_H5_MIN_BYTES)
    except Exception as exc:  # noqa: BLE001
        _log(f"ERROR fetching per-gene.h5: {exc!r}")
    if gene_ok:
        _ensure_prebuilt_index(source, data_dir, "per-gene")
    else:
        _log(
            "WARNING per-gene.h5 unavailable — the gateway loads the DNA index "
            "at startup and will EXIT without it. Upload per-gene.h5 to the "
            f"dataset (source={source!r}) or make the DNA index optional in "
            "services/search_service.py."
        )


if __name__ == "__main__":
    ensure_data()
    sys.exit(0)

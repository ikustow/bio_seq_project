"""One-time upload of the embeddings + FAISS index to a HF Dataset.

Run from the project root after `per-protein.h5`, `per-protein.index`,
and `per-protein.accessions.pkl` exist in `bioseq_retriever/data/`.

Usage (PowerShell on the strong laptop):

    cd D:\\Alina_data_Sanity\\bio_seq_project
    $env:HF_TOKEN = "<paste your HF write token>"
    .\\.venv\\python.exe scripts\\upload_to_hf_dataset.py

Re-running is safe — HF dedups uploaded blobs via xet, so a second
run mostly verifies hashes and finishes quickly.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ID = "radda-i/bioseq-data"

FILES = [
    "bioseq_retriever/data/per-protein.h5",
    "bioseq_retriever/data/per-protein.index",
    "bioseq_retriever/data/per-protein.accessions.pkl",
]


def main() -> int:
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("ERROR: HF_TOKEN env var is not set.", file=sys.stderr)
        print(
            "Set it in PowerShell before running: $env:HF_TOKEN = \"<token>\"",
            file=sys.stderr,
        )
        return 2

    # Validate files exist before talking to the network.
    missing = [p for p in FILES if not Path(p).exists()]
    if missing:
        print("ERROR: files missing on disk:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return 3

    # Lazy import so the file-existence error above is fast.
    from huggingface_hub import HfApi

    api = HfApi(token=token)

    print(f"Target dataset: {REPO_ID}")
    print(f"{len(FILES)} file(s) to upload:")
    for src in FILES:
        size_mb = Path(src).stat().st_size / 1e6
        print(f"  {src}  ({size_mb:,.1f} MB)")
    print()

    for src in FILES:
        size_mb = Path(src).stat().st_size / 1e6
        print(f"--- {src} ({size_mb:,.1f} MB) ---")
        t0 = time.time()
        api.upload_file(
            path_or_fileobj=src,
            path_in_repo=Path(src).name,
            repo_id=REPO_ID,
            repo_type="dataset",
        )
        print(f"  done in {time.time() - t0:,.0f}s")

    print()
    print(f"All uploads complete. Browse: https://huggingface.co/datasets/{REPO_ID}/tree/main")
    return 0


if __name__ == "__main__":
    sys.exit(main())

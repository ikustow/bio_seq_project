"""Build prebuilt FAISS indexes + accession caches for the unified gateway.

Run this LOCALLY (fast disk/CPU) to produce the artifacts the gateway LOADS at
startup, so it never rebuilds from the per-*.h5 on the HF Space. A rebuild there
is impractically slow: the UniProt-style .h5 stores one tiny dataset per
accession, so building the index is hundreds of thousands of random reads —
minutes-to-hours on the free CPU/IO tier (and it repeats on every cold start,
since the free tier has no persistent disk).

Usage:
    python data_prep/build_faiss_indexes.py path/to/per-protein.h5 [path/to/per-gene.h5 ...]

For each <name>.h5 it writes, alongside it:
    <name>.index             FAISS IndexFlatIP over L2-normalized vectors
    <name>.accessions.json   JSON list of accessions in FAISS-row order

These match exactly what services/search_service.py::load_or_create_index reads
(inner product over L2-normalized vectors == cosine, same as the gateway's query
path). Upload <name>.index + <name>.accessions.json to the HF dataset next to the
.h5 and the gateway will load instead of build.

    hf upload radda-i/bioseq-data .\\per-protein.index per-protein.index --repo-type=dataset
    hf upload radda-i/bioseq-data .\\per-protein.accessions.json per-protein.accessions.json --repo-type=dataset

Requires: pip install faiss-cpu h5py numpy
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import faiss
import h5py
import numpy as np

BATCH = 1000  # matches search_service.H5_BATCH_SIZE


def build(h5_path: Path) -> None:
    if not h5_path.exists():
        raise SystemExit(f"not found: {h5_path}")
    base = str(h5_path)[: -len(".h5")] if str(h5_path).endswith(".h5") else str(h5_path)
    index_path = Path(base + ".index")
    json_path = Path(base + ".accessions.json")

    print(f"[build] {h5_path}")
    t0 = time.perf_counter()
    with h5py.File(h5_path, "r", libver="latest") as f:
        # Same filtering the gateway uses; compute the order ONCE so the index
        # rows and the accessions list stay aligned.
        accessions = [k for k in f.keys() if isinstance(f[k], h5py.Dataset)]
        n = len(accessions)
        if n == 0:
            raise SystemExit(f"{h5_path}: contains no datasets")
        dim = f[accessions[0]].shape[0]
        print(f"  {n:,} vectors, dim={dim}")

        index = faiss.IndexFlatIP(dim)
        for i in range(0, n, BATCH):
            batch_accs = accessions[i : i + BATCH]
            arr = np.zeros((len(batch_accs), dim), dtype=np.float32)
            for j, acc in enumerate(batch_accs):
                arr[j] = f[acc][:]
            faiss.normalize_L2(arr)
            index.add(arr)
            if (i // BATCH) % 25 == 0:
                done = min(i + BATCH, n)
                print(f"  {done:,}/{n:,} ({done * 100 // n}%) "
                      f"[{time.perf_counter() - t0:.0f}s]", flush=True)

    faiss.write_index(index, str(index_path))
    with json_path.open("w") as fh:
        json.dump(accessions, fh)
    print(f"  done in {time.perf_counter() - t0:.0f}s -> "
          f"{index_path.name} ({index_path.stat().st_size / 1e9:.2f} GB) + "
          f"{json_path.name} ({n:,} accessions)\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: build_faiss_indexes.py <h5> [<h5> ...]")
    for p in sys.argv[1:]:
        build(Path(p))
    print("All done. Upload the .index + .accessions.json files to the dataset.")

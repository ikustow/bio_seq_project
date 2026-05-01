import h5py
import numpy as np
import pandas as pd
from pathlib import Path
from tqdm import tqdm
import argparse

DEFAULT_INPUT = "graph_core/data/per-protein.h5"
DEFAULT_OUTDIR = "graph_core/output"
DEFAULT_DATASET = "UP000005640_9606"


def decode_if_bytes(x):
    if isinstance(x, bytes):
        return x.decode("utf-8")
    return str(x)


def try_find_array_pair(h5file):
    """
    Ищем типичный сценарий:
    - ids/accessions/names
    - embeddings/vectors/features
    """
    candidate_id_keys = []
    candidate_emb_keys = []

    def visitor(name, obj):
        if isinstance(obj, h5py.Dataset):
            lname = name.lower()
            if any(k in lname for k in ["id", "accession", "name", "protein"]):
                candidate_id_keys.append(name)
            if any(k in lname for k in ["embed", "vector", "feature", "repr"]):
                candidate_emb_keys.append(name)

    h5file.visititems(visitor)
    return candidate_id_keys, candidate_emb_keys


def load_case_shared_arrays(h5file):
    id_keys, emb_keys = try_find_array_pair(h5file)

    for ik in id_keys:
        for ek in emb_keys:
            ids = h5file[ik]
            embs = h5file[ek]
            if len(ids.shape) == 1 and len(embs.shape) == 2 and ids.shape[0] == embs.shape[0]:
                accessions = [decode_if_bytes(x) for x in ids[:]]
                vectors = np.asarray(embs[:], dtype=np.float32)
                return accessions, vectors, {"mode": "shared_arrays", "id_key": ik, "emb_key": ek}

    return None, None, None


def load_case_one_dataset_per_accession(h5file):
    """
    Сценарий:
      root/
        P12345 -> [dim]
        Q8XYZ1 -> [dim]
    или внутри группы.
    """
    items = []

    def visitor(name, obj):
        if isinstance(obj, h5py.Dataset):
            if len(obj.shape) == 1 and obj.shape[0] > 8:
                items.append(name)

    h5file.visititems(visitor)

    if not items:
        return None, None, None

    accessions = []
    vectors = []

    for ds_name in tqdm(items, desc="Reading per-accession datasets"):
        arr = np.asarray(h5file[ds_name][:], dtype=np.float32)
        if arr.ndim != 1:
            continue

        accession = Path(ds_name).name
        accessions.append(accession)
        vectors.append(arr)

    if not vectors:
        return None, None, None

    dim0 = len(vectors[0])
    filtered = [(a, v) for a, v in zip(accessions, vectors) if len(v) == dim0]
    accessions = [x[0] for x in filtered]
    vectors = np.stack([x[1] for x in filtered]).astype(np.float32)

    return accessions, vectors, {"mode": "one_dataset_per_accession", "num_items": len(accessions)}


def resolve_input_path(input_value: str) -> Path:
    input_path = Path(input_value)
    if input_path.is_absolute() and input_path.exists():
        return input_path

    cwd = Path.cwd()
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent

    candidates = [
        cwd / input_path,
        script_dir / input_path,
        project_root / input_path,
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    if input_path.name == "per-protein.h5":
        found = list(project_root.glob("**/per-protein.h5"))
        if len(found) == 1:
            return found[0]

    tried = "\n".join(str(p) for p in candidates)
    raise FileNotFoundError(
        f"Could not find input file '{input_value}'. Tried:\n{tried}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help="Path to per-protein.h5. Relative paths are tried from the current working directory, script directory, and project root.",
    )
    parser.add_argument(
        "--outdir",
        default=DEFAULT_OUTDIR,
        help="Output directory (default: graph_core/output)",
    )
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        help=f"Dataset name, e.g. {DEFAULT_DATASET}",
    )
    args = parser.parse_args()

    input_path = resolve_input_path(args.input)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    with h5py.File(input_path, "r") as f:
        accessions, vectors, meta = load_case_shared_arrays(f)

        if accessions is None:
            accessions, vectors, meta = load_case_one_dataset_per_accession(f)

        if accessions is None:
            raise RuntimeError(
                "Could not automatically detect HDF5 layout. "
                "Run inspect_h5.py and adapt loader to the actual structure."
            )

    df = pd.DataFrame({
        "accession": accessions,
        "dataset": args.dataset,
        "row_id": np.arange(len(accessions), dtype=np.int64),
    })

    df.to_parquet(outdir / "proteins.parquet", index=False)
    np.save(outdir / "embeddings.npy", vectors)

    with open(outdir / "meta.txt", "w", encoding="utf-8") as f:
        f.write(str(meta) + "\n")
        f.write(f"num_proteins={len(accessions)}\n")
        f.write(f"embedding_dim={vectors.shape[1]}\n")

    print("Saved:")
    print(outdir / "proteins.parquet")
    print(outdir / "embeddings.npy")
    print("shape:", vectors.shape)
    print("meta:", meta)


if __name__ == "__main__":
    main()
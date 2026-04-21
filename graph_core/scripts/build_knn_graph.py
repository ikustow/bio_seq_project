import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import faiss
from tqdm import tqdm

DEFAULT_VECTORS = "graph_core/output/embeddings_l2.npy"
DEFAULT_PROTEINS = "graph_core/output/proteins.parquet"
DEFAULT_OUTDIR = "graph_core/output"
DEFAULT_K = 20
DEFAULT_MIN_SIM = 0.70
DEFAULT_BATCH_SIZE = 10000


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vectors", default=DEFAULT_VECTORS, help="Normalized vectors .npy")
    parser.add_argument("--proteins", default=DEFAULT_PROTEINS, help="proteins.parquet")
    parser.add_argument("--outdir", default=DEFAULT_OUTDIR)
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="Top-k neighbors including self")
    parser.add_argument("--min-sim", type=float, default=DEFAULT_MIN_SIM, help="Minimum cosine similarity")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    X = np.load(args.vectors).astype(np.float32)
    proteins = pd.read_parquet(args.proteins)

    n, dim = X.shape
    print(f"Loaded vectors: n={n}, dim={dim}")

    index = faiss.IndexFlatIP(dim)
    index.add(X)

    edges_src = []
    edges_dst = []
    edges_sim = []

    for start in tqdm(range(0, n, args.batch_size), desc="Searching kNN"):
        end = min(start + args.batch_size, n)
        sims, idxs = index.search(X[start:end], args.k)

        for i in range(end - start):
            src = start + i
            for sim, dst in zip(sims[i], idxs[i]):
                if dst == src:
                    continue
                if sim < args.min_sim:
                    continue
                edges_src.append(src)
                edges_dst.append(int(dst))
                edges_sim.append(float(sim))

    edges = pd.DataFrame({
        "src_row_id": edges_src,
        "dst_row_id": edges_dst,
        "cosine_sim": edges_sim,
    })

    # Чтобы не держать дубли A->B и B->A как две независимые связи, можно канонизировать
    edges["a"] = edges[["src_row_id", "dst_row_id"]].min(axis=1)
    edges["b"] = edges[["src_row_id", "dst_row_id"]].max(axis=1)
    edges = (
        edges.groupby(["a", "b"], as_index=False)["cosine_sim"]
        .max()
        .rename(columns={"a": "src_row_id", "b": "dst_row_id"})
    )

    edges.to_parquet(outdir / "knn_edges.parquet", index=False)

    print("Saved:", outdir / "knn_edges.parquet")
    print("Num edges:", len(edges))
    print(edges.head())


if __name__ == "__main__":
    main()
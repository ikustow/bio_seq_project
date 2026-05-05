import argparse
import os
from pathlib import Path
import numpy as np
import pandas as pd
import faiss
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VECTORS = ROOT / "output" / "embeddings_l2.npy"
DEFAULT_PROTEINS = ROOT / "output" / "proteins.parquet"
DEFAULT_OUTDIR = ROOT / "output"
DEFAULT_K = 3
DEFAULT_MIN_SIM = 0.70
DEFAULT_BATCH_SIZE = 10000
DEFAULT_INDEX = os.getenv("BIOSEQ_GRAPH_KNN_INDEX", "hnsw")
DEFAULT_HNSW_M = int(os.getenv("BIOSEQ_GRAPH_HNSW_M", "32"))
DEFAULT_HNSW_EF_CONSTRUCTION = int(os.getenv("BIOSEQ_GRAPH_HNSW_EF_CONSTRUCTION", "80"))
DEFAULT_HNSW_EF_SEARCH = int(os.getenv("BIOSEQ_GRAPH_HNSW_EF_SEARCH", "80"))
DEFAULT_FLUSH_EDGES = int(os.getenv("BIOSEQ_GRAPH_EDGE_FLUSH_ROWS", "1000000"))


def create_index(dim: int, args):
    if args.index == "flat":
        return faiss.IndexFlatIP(dim)
    if args.index == "hnsw":
        index = faiss.IndexHNSWFlat(dim, args.hnsw_m, faiss.METRIC_INNER_PRODUCT)
        index.hnsw.efConstruction = args.hnsw_ef_construction
        index.hnsw.efSearch = args.hnsw_ef_search
        return index
    raise ValueError(f"Unsupported index type: {args.index}")


def flush_edges(outdir: Path, part_num: int, edges_src, edges_dst, edges_sim) -> int:
    if not edges_src:
        return part_num
    edges = pd.DataFrame({
        "src_row_id": edges_src,
        "dst_row_id": edges_dst,
        "cosine_sim": edges_sim,
    })
    edges = edges.drop_duplicates(subset=["src_row_id", "dst_row_id"], keep="first")
    edges = edges.sort_values(["src_row_id", "cosine_sim"], ascending=[True, False])
    edges["rank"] = edges.groupby("src_row_id").cumcount() + 1
    path = outdir / f"knn_edges_part_{part_num:05d}.parquet"
    edges.to_parquet(path, index=False)
    print(f"Saved edge part {part_num}: {path} rows={len(edges)}")
    return part_num + 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vectors", default=str(DEFAULT_VECTORS), help="Normalized vectors .npy")
    parser.add_argument("--proteins", default=str(DEFAULT_PROTEINS), help="proteins.parquet")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="Top-k neighbors including self")
    parser.add_argument("--min-sim", type=float, default=DEFAULT_MIN_SIM, help="Minimum cosine similarity")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--index", choices=["hnsw", "flat"], default=DEFAULT_INDEX)
    parser.add_argument("--hnsw-m", type=int, default=DEFAULT_HNSW_M)
    parser.add_argument("--hnsw-ef-construction", type=int, default=DEFAULT_HNSW_EF_CONSTRUCTION)
    parser.add_argument("--hnsw-ef-search", type=int, default=DEFAULT_HNSW_EF_SEARCH)
    parser.add_argument("--flush-edges", type=int, default=DEFAULT_FLUSH_EDGES)
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    X = np.load(args.vectors, mmap_mode="r")
    proteins = pd.read_parquet(args.proteins)

    n, dim = X.shape
    print(f"Loaded vectors: n={n}, dim={dim}")
    print(f"Using FAISS index: {args.index}")

    index = create_index(dim, args)
    index.add(np.asarray(X, dtype=np.float32))

    edges_src = []
    edges_dst = []
    edges_sim = []
    part_num = 0

    for start in tqdm(range(0, n, args.batch_size), desc="Searching kNN"):
        end = min(start + args.batch_size, n)
        sims, idxs = index.search(np.asarray(X[start:end], dtype=np.float32), args.k)

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

        if len(edges_src) >= args.flush_edges:
            part_num = flush_edges(outdir, part_num, edges_src, edges_dst, edges_sim)
            edges_src.clear()
            edges_dst.clear()
            edges_sim.clear()

    part_num = flush_edges(outdir, part_num, edges_src, edges_dst, edges_sim)

    part_paths = sorted(outdir.glob("knn_edges_part_*.parquet"))
    if part_paths:
        edges = pd.concat([pd.read_parquet(path) for path in part_paths], ignore_index=True)
    else:
        edges = pd.DataFrame(columns=["src_row_id", "dst_row_id", "cosine_sim"])
    edges = edges.drop_duplicates(subset=["src_row_id", "dst_row_id"], keep="first")
    edges = edges.sort_values(["src_row_id", "cosine_sim"], ascending=[True, False])
    edges["rank"] = edges.groupby("src_row_id").cumcount() + 1
    edges.to_parquet(outdir / "knn_edges.parquet", index=False)

    print("Saved:", outdir / "knn_edges.parquet")
    print("Num edges:", len(edges))
    print(edges.head())


if __name__ == "__main__":
    main()

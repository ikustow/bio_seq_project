import argparse
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROTEINS = ROOT / "output" / "proteins.parquet"
DEFAULT_EDGES = ROOT / "output" / "knn_edges.parquet"
DEFAULT_EDGE_BATCH_SIZE = 500_000


class DisjointSet:
    def __init__(self, items):
        self.parent = {int(item): int(item) for item in items}
        self.size = {int(item): 1 for item in items}

    def find(self, item):
        item = int(item)
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left, right):
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        if self.size[root_left] < self.size[root_right]:
            root_left, root_right = root_right, root_left
        self.parent[root_right] = root_left
        self.size[root_left] += self.size[root_right]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--proteins", default=str(DEFAULT_PROTEINS), help="proteins.parquet path")
    parser.add_argument("--edges", default=str(DEFAULT_EDGES), help="knn_edges.parquet path")
    parser.add_argument("--edge-batch-size", type=int, default=DEFAULT_EDGE_BATCH_SIZE)
    args = parser.parse_args()

    proteins = pd.read_parquet(args.proteins)
    row_ids = proteins["row_id"].astype(int).tolist()
    dsu = DisjointSet(row_ids)
    degree = {row_id: 0 for row_id in row_ids}
    undirected_edges = set()

    parquet_file = pq.ParquetFile(args.edges)
    for batch in parquet_file.iter_batches(
        columns=["src_row_id", "dst_row_id"],
        batch_size=args.edge_batch_size,
    ):
        edges = batch.to_pandas()
        for src, dst in edges[["src_row_id", "dst_row_id"]].itertuples(index=False, name=None):
            src = int(src)
            dst = int(dst)
            edge = (src, dst) if src <= dst else (dst, src)
            if edge in undirected_edges:
                continue
            undirected_edges.add(edge)
            degree[src] += 1
            degree[dst] += 1
            dsu.union(src, dst)

    component_sizes = {}
    for row_id in row_ids:
        root = dsu.find(row_id)
        component_sizes[root] = component_sizes.get(root, 0) + 1

    num_nodes = len(row_ids)
    num_edges = len(undirected_edges)
    degrees = list(degree.values())
    largest_cc = max(component_sizes.values()) if component_sizes else 0

    print("num_nodes:", num_nodes)
    print("num_edges:", num_edges)
    print("avg_degree:", sum(degrees) / len(degrees) if degrees else 0)
    print("num_components:", len(component_sizes))
    print("largest_component_size:", largest_cc)


if __name__ == "__main__":
    main()

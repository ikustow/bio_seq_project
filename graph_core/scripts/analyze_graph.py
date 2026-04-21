import argparse
import pandas as pd
import networkx as nx

DEFAULT_PROTEINS = "graph_core/output/proteins.parquet"
DEFAULT_EDGES = "graph_core/output/knn_edges.parquet"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--proteins", default=DEFAULT_PROTEINS, help="proteins.parquet path")
    parser.add_argument("--edges", default=DEFAULT_EDGES, help="knn_edges.parquet path")
    args = parser.parse_args()

    proteins = pd.read_parquet(args.proteins)
    edges = pd.read_parquet(args.edges)

    G = nx.Graph()
    G.add_nodes_from(proteins["row_id"].tolist())
    G.add_edges_from(edges[["src_row_id", "dst_row_id"]].itertuples(index=False, name=None))

    num_nodes = G.number_of_nodes()
    num_edges = G.number_of_edges()
    degrees = [d for _, d in G.degree()]
    cc = list(nx.connected_components(G))
    largest_cc = max(len(c) for c in cc) if cc else 0

    print("num_nodes:", num_nodes)
    print("num_edges:", num_edges)
    print("avg_degree:", sum(degrees) / len(degrees) if degrees else 0)
    print("num_components:", len(cc))
    print("largest_component_size:", largest_cc)


if __name__ == "__main__":
    main()
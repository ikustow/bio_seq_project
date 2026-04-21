from pathlib import Path
from pyvis.network import Network
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

edges = pd.read_parquet(OUTPUT_DIR / "knn_edges.parquet")
proteins = pd.read_parquet(OUTPUT_DIR / "proteins.parquet")

net = Network(height="800px", width="100%", notebook=False)
output_html = OUTPUT_DIR / "graph.html"
edge_subset = edges.head(2000)
node_ids = set(edge_subset["src_row_id"]) | set(edge_subset["dst_row_id"])

# добавляем узлы, участвующие в выбранных рёбрах
for row in proteins[proteins["row_id"].isin(node_ids)].itertuples(index=False):
    net.add_node(int(row.row_id), label=row.accession)

# добавляем рёбра
for row in edge_subset.itertuples(index=False):
    net.add_edge(
        int(row.src_row_id),
        int(row.dst_row_id),
        value=row.cosine_sim
    )

net.write_html(str(output_html))
print(f"Saved graph visualization to: {output_html}")
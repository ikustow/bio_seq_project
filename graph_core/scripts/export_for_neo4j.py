import argparse
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
DEFAULT_PROTEINS = OUTPUT_DIR / "proteins.parquet"
DEFAULT_EDGES = OUTPUT_DIR / "knn_edges.parquet"
DEFAULT_OUTDIR = OUTPUT_DIR / "neo4j"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--proteins",
        default=str(DEFAULT_PROTEINS),
        help=f"Path to proteins.parquet (default: {DEFAULT_PROTEINS})",
    )
    parser.add_argument(
        "--edges",
        default=str(DEFAULT_EDGES),
        help=f"Path to knn_edges.parquet (default: {DEFAULT_EDGES})",
    )
    parser.add_argument(
        "--outdir",
        default=str(DEFAULT_OUTDIR),
        help=f"Output directory for Neo4j CSV export (default: {DEFAULT_OUTDIR})",
    )
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    proteins = pd.read_parquet(args.proteins)
    edges = pd.read_parquet(args.edges)

    proteins_neo = proteins.rename(columns={
        "row_id": "row_id:ID(Protein)",
        "accession": "accession",
        "dataset": "dataset"
    })
    proteins_neo[":LABEL"] = "Protein"

    edges_neo = edges.rename(columns={
        "src_row_id": ":START_ID(Protein)",
        "dst_row_id": ":END_ID(Protein)",
        "cosine_sim": "cosine_sim:float"
    })
    edges_neo[":TYPE"] = "SIMILAR_TO"

    proteins_neo.to_csv(outdir / "proteins.csv", index=False)
    edges_neo.to_csv(outdir / "edges.csv", index=False)

    print("Saved:")
    print(outdir / "proteins.csv")
    print(outdir / "edges.csv")


if __name__ == "__main__":
    main()

import argparse
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
DEFAULT_PROTEINS = OUTPUT_DIR / "proteins_annotated.parquet"
DEFAULT_EDGES = OUTPUT_DIR / "knn_edges.parquet"
DEFAULT_DISEASES = OUTPUT_DIR / "protein_diseases.parquet"
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
    parser.add_argument(
        "--diseases",
        default=str(DEFAULT_DISEASES),
        help=f"Path to protein_diseases.parquet (default: {DEFAULT_DISEASES})",
    )
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    proteins_path = Path(args.proteins)
    if not proteins_path.exists() and proteins_path.name == "proteins_annotated.parquet":
        proteins_path = OUTPUT_DIR / "proteins.parquet"

    proteins = pd.read_parquet(proteins_path)
    edges = pd.read_parquet(args.edges)
    diseases_path = Path(args.diseases)
    diseases = pd.read_parquet(diseases_path) if diseases_path.exists() else None

    proteins_neo = proteins.rename(columns={"row_id": "row_id:ID(Protein)"})
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

    if diseases is not None and not diseases.empty:
        disease_nodes = (
            diseases[
                [
                    "disease_accession",
                    "disease_id",
                    "disease_acronym",
                    "disease_description",
                    "disease_xref_db",
                    "disease_xref_id",
                    "association_source",
                ]
            ]
            .drop_duplicates()
            .rename(columns={"disease_accession": "disease_accession:ID(Disease)"})
        )
        disease_nodes[":LABEL"] = "Disease"

        disease_edges = diseases.rename(
            columns={
                "row_id": ":START_ID(Protein)",
                "disease_accession": ":END_ID(Disease)",
            }
        )[
            [
                ":START_ID(Protein)",
                ":END_ID(Disease)",
                "association_note",
                "association_source",
            ]
        ]
        disease_edges[":TYPE"] = "ASSOCIATED_WITH"

        disease_nodes.to_csv(outdir / "diseases.csv", index=False)
        disease_edges.to_csv(outdir / "protein_disease_edges.csv", index=False)

        print(outdir / "diseases.csv")
        print(outdir / "protein_disease_edges.csv")


if __name__ == "__main__":
    main()

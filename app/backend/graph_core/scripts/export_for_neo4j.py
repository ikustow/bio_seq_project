import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
DEFAULT_PROTEINS = OUTPUT_DIR / "proteins_annotated.parquet"
DEFAULT_EDGES = OUTPUT_DIR / "knn_edges.parquet"
DEFAULT_DISEASES = OUTPUT_DIR / "protein_diseases.parquet"
DEFAULT_OUTDIR = OUTPUT_DIR / "neo4j"
DEFAULT_EDGE_BATCH_SIZE = 500_000


def normalize_sequence(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return "".join(str(value).upper().split())


def sequence_hash(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("utf-8")).hexdigest()


def split_list(value: Any) -> list[str]:
    if value is None or pd.isna(value):
        return []
    text = str(value).strip().strip(";")
    if not text:
        return []
    separators = [";", ","]
    parts = [text]
    for separator in separators:
        if separator in text:
            parts = text.split(separator)
            break
    return [part.strip() for part in parts if part.strip()]


def json_list(value: Any) -> str:
    return json.dumps(split_list(value), ensure_ascii=False)


def domains_json(value: Any) -> str:
    return json.dumps([{"name": item} for item in split_list(value)], ensure_ascii=False)


def xrefs_json(row: pd.Series) -> str:
    payload: dict[str, str] = {}
    ensembl = split_list(row.get("ensembl_ids"))
    if ensembl:
        payload["Ensembl"] = ";".join(ensembl)
    alphafold = split_list(row.get("alphafold_accession"))
    if alphafold:
        payload["AlphaFoldDB"] = alphafold[0]
    return json.dumps(payload, ensure_ascii=False)


def ensure_column(df: pd.DataFrame, column: str, default: Any = pd.NA) -> None:
    if column not in df.columns:
        df[column] = default


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
    parser.add_argument(
        "--edge-batch-size",
        type=int,
        default=DEFAULT_EDGE_BATCH_SIZE,
        help="Rows per batch when streaming edges parquet to CSV.",
    )
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    proteins_path = Path(args.proteins)
    if not proteins_path.exists() and proteins_path.name == "proteins_annotated.parquet":
        proteins_path = OUTPUT_DIR / "proteins.parquet"

    proteins = pd.read_parquet(proteins_path)
    diseases_path = Path(args.diseases)
    diseases = pd.read_parquet(diseases_path) if diseases_path.exists() else None

    for column in [
        "entry_name",
        "protein_name",
        "gene_primary",
        "organism_name",
        "taxon_id",
        "sequence_length",
        "mol_weight",
        "reviewed",
        "annotation_score",
        "protein_existence",
        "ensembl_ids",
        "protein_sequence",
        "function_text",
        "subcellular_locations_text",
        "keywords_text",
        "go_terms_text",
        "pubmed_ids_text",
        "alphafold_accession",
        "domains_text",
    ]:
        ensure_column(proteins, column)

    proteins["protein_sequence"] = proteins["protein_sequence"].map(normalize_sequence)
    proteins["sequence_hash"] = proteins["protein_sequence"].map(lambda item: sequence_hash(item) if item else pd.NA)
    proteins["keywords_json"] = proteins["keywords_text"].map(json_list)
    proteins["go_terms_json"] = proteins["go_terms_text"].map(json_list)
    proteins["pubmed_ids_json"] = proteins["pubmed_ids_text"].map(json_list)
    proteins["subcellular_locations_json"] = proteins["subcellular_locations_text"].map(json_list)
    proteins["domains_json"] = proteins["domains_text"].map(domains_json)
    proteins["alt_names_json"] = "[]"
    proteins["xrefs_json"] = proteins.apply(xrefs_json, axis=1)
    proteins["organism_common"] = ""
    proteins["embedding_model"] = "ProtT5"
    proteins["embedding_release"] = "offline"

    proteins_neo = proteins.rename(columns={"row_id": "row_id:ID(Protein)"})
    proteins_neo[":LABEL"] = "Protein"
    protein_columns = [
        "row_id:ID(Protein)",
        "accession",
        "dataset",
        "entry_name",
        "protein_name",
        "gene_primary",
        "organism_name",
        "organism_common",
        "taxon_id",
        "sequence_length",
        "mol_weight",
        "reviewed",
        "annotation_score",
        "protein_existence",
        "ensembl_ids",
        "protein_sequence",
        "sequence_hash",
        "embedding_model",
        "embedding_release",
        "function_text",
        "keywords_json",
        "go_terms_json",
        "pubmed_ids_json",
        "xrefs_json",
        "domains_json",
        "alt_names_json",
        "subcellular_locations_json",
        "alphafold_accession",
        ":LABEL",
    ]
    proteins_neo = proteins_neo[[column for column in protein_columns if column in proteins_neo.columns]]

    sequence_source = proteins[proteins["sequence_hash"].notna() & (proteins["protein_sequence"] != "")].copy()
    sequences_neo = pd.DataFrame(
        {
            "sequence_hash:ID(Sequence)": sequence_source["sequence_hash"],
            "sequence_type": "protein",
            "raw_sequence": sequence_source["protein_sequence"],
            "normalized_sequence": sequence_source["protein_sequence"],
            "protein_sequence": sequence_source["protein_sequence"],
            "length": sequence_source["protein_sequence"].str.len(),
            "source": "UniProt",
            "source_id": sequence_source["accession"],
            ":LABEL": "Sequence",
        }
    ).drop_duplicates(subset=["sequence_hash:ID(Sequence)"])
    sequence_edges_neo = pd.DataFrame(
        {
            ":START_ID(Sequence)": sequence_source["sequence_hash"],
            ":END_ID(Protein)": sequence_source["row_id"],
            ":TYPE": "ENCODES",
        }
    )

    proteins_neo.to_csv(outdir / "proteins.csv", index=False)

    edges_path = outdir / "edges.csv"
    edge_columns = [":START_ID(Protein)", ":END_ID(Protein)", "cosine_sim:float", "rank:int", ":TYPE"]
    first_edge_batch = True
    edge_rows = 0
    parquet_file = pq.ParquetFile(args.edges)
    for batch in parquet_file.iter_batches(batch_size=args.edge_batch_size):
        edges_neo = batch.to_pandas().rename(columns={
            "src_row_id": ":START_ID(Protein)",
            "dst_row_id": ":END_ID(Protein)",
            "cosine_sim": "cosine_sim:float",
            "rank": "rank:int",
        })
        if "rank:int" not in edges_neo.columns:
            edges_neo = edges_neo.sort_values([":START_ID(Protein)", "cosine_sim:float"], ascending=[True, False])
            edges_neo["rank:int"] = edges_neo.groupby(":START_ID(Protein)").cumcount() + 1
        edges_neo[":TYPE"] = "SIMILAR_TO"
        edges_neo = edges_neo[edge_columns]
        edges_neo.to_csv(edges_path, index=False, mode="w" if first_edge_batch else "a", header=first_edge_batch)
        first_edge_batch = False
        edge_rows += len(edges_neo)
        print(f"Exported edge rows: {edge_rows}")

    sequences_neo.to_csv(outdir / "sequences.csv", index=False)
    sequence_edges_neo.to_csv(outdir / "sequence_protein_edges.csv", index=False)

    print("Saved:")
    print(outdir / "proteins.csv")
    print(edges_path)
    print(outdir / "sequences.csv")
    print(outdir / "sequence_protein_edges.csv")

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

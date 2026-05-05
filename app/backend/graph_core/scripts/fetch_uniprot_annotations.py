import argparse
import subprocess
import time
from io import StringIO
from pathlib import Path
from urllib.parse import quote

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
DEFAULT_PROTEINS = OUTPUT_DIR / "proteins.parquet"
DEFAULT_OUTDIR = OUTPUT_DIR
DEFAULT_ANNOTATIONS = OUTPUT_DIR / "protein_annotations.parquet"
DEFAULT_MERGED = OUTPUT_DIR / "proteins_annotated.parquet"
DEFAULT_BATCH_SIZE = 100
DEFAULT_SLEEP_SECONDS = 0.05
UNIPROT_BASE_URL = "https://rest.uniprot.org/uniprotkb/search"
UNIPROT_FIELDS = [
    "accession",
    "id",
    "protein_name",
    "gene_primary",
    "organism_name",
    "organism_id",
    "length",
    "mass",
    "reviewed",
    "annotation_score",
    "protein_existence",
    "xref_ensembl",
    "sequence",
    "cc_function",
    "cc_subcellular_location",
    "keyword",
    "go_id",
    "lit_pubmed_id",
    "xref_alphafolddb",
    "ft_domain",
]


def fetch_batch(accessions):
    query = " OR ".join(f"accession:{accession}" for accession in accessions)
    params = (
        f"query={quote(query)}"
        f"&format=tsv&size={len(accessions)}"
        f"&fields={','.join(UNIPROT_FIELDS)}"
    )
    result = subprocess.run(
        ["curl", "-fsSL", f"{UNIPROT_BASE_URL}?{params}"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = result.stdout
    return pd.read_csv(StringIO(payload), sep="\t")


def normalize_annotations(df):
    rename_map = {
        "Entry": "accession",
        "Entry Name": "entry_name",
        "Protein names": "protein_name",
        "Gene Names (primary)": "gene_primary",
        "Organism": "organism_name",
        "Organism (ID)": "taxon_id",
        "Length": "sequence_length",
        "Mass": "mol_weight",
        "Reviewed": "reviewed",
        "Annotation": "annotation_score",
        "Protein existence": "protein_existence",
        "Ensembl": "ensembl_ids",
        "Sequence": "protein_sequence",
        "Function [CC]": "function_text",
        "Subcellular location [CC]": "subcellular_locations_text",
        "Keywords": "keywords_text",
        "Gene Ontology IDs": "go_terms_text",
        "PubMed ID": "pubmed_ids_text",
        "AlphaFoldDB": "alphafold_accession",
        "Domain [FT]": "domains_text",
    }
    df = df.rename(columns=rename_map)

    if "reviewed" in df.columns:
        df["reviewed"] = df["reviewed"].map({"reviewed": True, "unreviewed": False})
    if "sequence_length" in df.columns:
        df["sequence_length"] = pd.to_numeric(df["sequence_length"], errors="coerce")
    if "annotation_score" in df.columns:
        df["annotation_score"] = pd.to_numeric(df["annotation_score"], errors="coerce")
    if "taxon_id" in df.columns:
        df["taxon_id"] = pd.to_numeric(df["taxon_id"], errors="coerce")
    if "mol_weight" in df.columns:
        df["mol_weight"] = pd.to_numeric(df["mol_weight"], errors="coerce")
    if "ensembl_ids" in df.columns:
        df["ensembl_ids"] = df["ensembl_ids"].fillna("").str.rstrip(";")
        df.loc[df["ensembl_ids"] == "", "ensembl_ids"] = pd.NA
    if "alphafold_accession" in df.columns:
        df["alphafold_accession"] = df["alphafold_accession"].fillna("").str.rstrip(";")
        df.loc[df["alphafold_accession"] == "", "alphafold_accession"] = pd.NA

    for column in df.select_dtypes(include=["object"]).columns:
        df[column] = df[column].map(lambda value: pd.NA if pd.isna(value) else str(value))

    return df


def batch_iter(items, size):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--proteins",
        default=str(DEFAULT_PROTEINS),
        help=f"Path to proteins.parquet (default: {DEFAULT_PROTEINS})",
    )
    parser.add_argument(
        "--outdir",
        default=str(DEFAULT_OUTDIR),
        help=f"Output directory (default: {DEFAULT_OUTDIR})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"UniProt request batch size (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help="Small delay between UniProt requests to reduce throttling risk.",
    )
    args = parser.parse_args()

    proteins = pd.read_parquet(args.proteins)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    accessions = proteins["accession"].dropna().astype(str).unique().tolist()
    annotation_frames = []
    batches_dir = outdir / "uniprot_annotation_batches"
    batches_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching UniProt annotations for {len(accessions)} proteins...")
    for batch_num, batch in enumerate(batch_iter(accessions, args.batch_size), start=1):
        batch_path = batches_dir / f"batch_{batch_num:06d}.parquet"
        if batch_path.exists():
            print(f"Batch {batch_num}: cached {batch_path}")
            annotation_frames.append(pd.read_parquet(batch_path))
            continue
        print(f"Batch {batch_num}: {len(batch)} accessions")
        frame = fetch_batch(batch)
        frame.to_parquet(batch_path, index=False)
        annotation_frames.append(frame)
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    annotations = normalize_annotations(pd.concat(annotation_frames, ignore_index=True))
    annotations = annotations.drop_duplicates(subset=["accession"])

    merged = proteins.merge(annotations, on="accession", how="left")

    annotations_path = outdir / DEFAULT_ANNOTATIONS.name
    merged_path = outdir / DEFAULT_MERGED.name
    annotations.to_parquet(annotations_path, index=False)
    merged.to_parquet(merged_path, index=False)

    print("Saved:")
    print(annotations_path)
    print(merged_path)
    print(f"Annotated proteins: {merged['protein_name'].notna().sum()} / {len(merged)}")


if __name__ == "__main__":
    main()

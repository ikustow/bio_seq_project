import argparse
import subprocess
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
UNIPROT_BASE_URL = "https://rest.uniprot.org/uniprotkb/search"
UNIPROT_FIELDS = [
    "accession",
    "id",
    "protein_name",
    "gene_primary",
    "organism_name",
    "length",
    "reviewed",
    "annotation_score",
    "protein_existence",
    "xref_ensembl",
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
        "Length": "sequence_length",
        "Reviewed": "reviewed",
        "Annotation": "annotation_score",
        "Protein existence": "protein_existence",
        "Ensembl": "ensembl_ids",
    }
    df = df.rename(columns=rename_map)

    if "reviewed" in df.columns:
        df["reviewed"] = df["reviewed"].map({"reviewed": True, "unreviewed": False})
    if "sequence_length" in df.columns:
        df["sequence_length"] = pd.to_numeric(df["sequence_length"], errors="coerce")
    if "annotation_score" in df.columns:
        df["annotation_score"] = pd.to_numeric(df["annotation_score"], errors="coerce")
    if "ensembl_ids" in df.columns:
        df["ensembl_ids"] = df["ensembl_ids"].fillna("").str.rstrip(";")
        df.loc[df["ensembl_ids"] == "", "ensembl_ids"] = pd.NA

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
    args = parser.parse_args()

    proteins = pd.read_parquet(args.proteins)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    accessions = proteins["accession"].dropna().astype(str).unique().tolist()
    annotation_frames = []

    print(f"Fetching UniProt annotations for {len(accessions)} proteins...")
    for batch_num, batch in enumerate(batch_iter(accessions, args.batch_size), start=1):
        print(f"Batch {batch_num}: {len(batch)} accessions")
        annotation_frames.append(fetch_batch(batch))

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

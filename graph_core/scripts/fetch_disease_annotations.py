import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
DEFAULT_PROTEINS = OUTPUT_DIR / "proteins_annotated.parquet"
DEFAULT_OUTDIR = OUTPUT_DIR
DEFAULT_DISEASES = OUTPUT_DIR / "protein_diseases.parquet"
DEFAULT_SUMMARY = OUTPUT_DIR / "protein_disease_summary.parquet"
DEFAULT_BATCH_SIZE = 100
UNIPROT_BASE_URL = "https://rest.uniprot.org/uniprotkb/search"


def infer_disease_name(note: str) -> str | None:
    if not note:
        return None

    patterns = [
        r"cause of ([^.]+)",
        r"responsible for ([^.]+)",
        r"characterized by ([^.]+)",
        r"diseases such as ([^.]+)",
        r"involved in ([^.]+)",
        r"results in ([^.]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, note, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip(" ,;:")
            if value:
                return value[:200]

    sentence = note.split(".", 1)[0].strip(" ,;:")
    return sentence[:200] if sentence else None


def make_disease_accession(
    disease_accession: str | None,
    xref_db: str | None,
    xref_id: str | None,
    disease_id: str | None,
) -> str | None:
    if disease_accession:
        return disease_accession
    if xref_db and xref_id:
        return f"{xref_db}:{xref_id}"
    if disease_id:
        slug = re.sub(r"[^a-z0-9]+", "-", disease_id.lower()).strip("-")
        if slug:
            return f"SYN:{slug[:80]}"
    return None


def fetch_batch(accessions):
    query = " OR ".join(f"accession:{accession}" for accession in accessions)
    url = (
        f"{UNIPROT_BASE_URL}?query={quote(query)}"
        f"&format=json&size={len(accessions)}"
    )
    result = subprocess.run(
        ["curl", "-fsSL", url],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def extract_disease_rows(entry):
    accession = entry.get("primaryAccession")
    rows = []

    for comment in entry.get("comments", []):
        if comment.get("commentType") != "DISEASE":
            continue

        disease = comment.get("disease", {})
        note_texts = comment.get("note", {}).get("texts", [])
        note = " ".join(
            text.get("value", "").strip() for text in note_texts if text.get("value")
        )
        xref = disease.get("diseaseCrossReference", {})
        disease_id = disease.get("diseaseId") or infer_disease_name(note)
        disease_description = disease.get("description") or note or pd.NA
        disease_accession = make_disease_accession(
            disease.get("diseaseAccession"),
            xref.get("database"),
            xref.get("id"),
            disease_id,
        )

        if not disease_accession and note:
            digest = hashlib.sha1(note.encode("utf-8")).hexdigest()[:12]
            disease_accession = f"NOTE:{digest}"
        if not disease_id and disease_accession:
            disease_id = disease_accession

        rows.append(
            {
                "accession": accession,
                "disease_accession": disease_accession,
                "disease_id": disease_id,
                "disease_acronym": disease.get("acronym"),
                "disease_description": disease_description,
                "disease_xref_db": xref.get("database"),
                "disease_xref_id": xref.get("id"),
                "association_note": note or pd.NA,
                "association_source": "UniProt",
            }
        )

    return rows


def batch_iter(items, size):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--proteins",
        default=str(DEFAULT_PROTEINS),
        help=f"Path to proteins_annotated.parquet (default: {DEFAULT_PROTEINS})",
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

    proteins_path = Path(args.proteins)
    if not proteins_path.exists() and proteins_path.name == "proteins_annotated.parquet":
        proteins_path = OUTPUT_DIR / "proteins.parquet"

    proteins = pd.read_parquet(proteins_path)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    accessions = proteins["accession"].dropna().astype(str).unique().tolist()
    disease_rows = []

    print(f"Fetching UniProt disease annotations for {len(accessions)} proteins...")
    for batch_num, batch in enumerate(batch_iter(accessions, args.batch_size), start=1):
        print(f"Batch {batch_num}: {len(batch)} accessions")
        payload = fetch_batch(batch)
        for entry in payload.get("results", []):
            disease_rows.extend(extract_disease_rows(entry))

    diseases = pd.DataFrame(
        disease_rows,
        columns=[
            "accession",
            "disease_accession",
            "disease_id",
            "disease_acronym",
            "disease_description",
            "disease_xref_db",
            "disease_xref_id",
            "association_note",
            "association_source",
        ],
    )

    if not diseases.empty:
        diseases = proteins[["row_id", "accession"]].merge(
            diseases, on="accession", how="inner"
        )
        diseases = diseases[diseases["disease_accession"].notna()].copy()
        diseases = diseases.drop_duplicates(
            subset=["row_id", "disease_accession", "disease_id"]
        )
    else:
        diseases = pd.DataFrame(
            columns=[
                "row_id",
                "accession",
                "disease_accession",
                "disease_id",
                "disease_acronym",
                "disease_description",
                "disease_xref_db",
                "disease_xref_id",
                "association_note",
                "association_source",
            ]
        )

    summary = (
        diseases.groupby(["row_id", "accession"], as_index=False)
        .agg(
            disease_count=("disease_id", "count"),
            disease_names=("disease_id", lambda s: " | ".join(sorted(set(s.dropna())))),
        )
        if not diseases.empty
        else pd.DataFrame(
            columns=["row_id", "accession", "disease_count", "disease_names"]
        )
    )

    diseases_path = outdir / DEFAULT_DISEASES.name
    summary_path = outdir / DEFAULT_SUMMARY.name
    diseases.to_parquet(diseases_path, index=False)
    summary.to_parquet(summary_path, index=False)

    print("Saved:")
    print(diseases_path)
    print(summary_path)
    print(f"Proteins with disease annotations: {summary['row_id'].nunique() if not summary.empty else 0}")
    print(f"Protein-disease links: {len(diseases)}")


if __name__ == "__main__":
    main()

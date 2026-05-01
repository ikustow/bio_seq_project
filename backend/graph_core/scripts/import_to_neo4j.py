import argparse
import os
from pathlib import Path

import pandas as pd
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable

ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT.parent
DEFAULT_INPUT_DIR = ROOT / "output" / "neo4j"
DEFAULT_PROTEINS = DEFAULT_INPUT_DIR / "proteins.csv"
DEFAULT_EDGES = DEFAULT_INPUT_DIR / "edges.csv"
DEFAULT_DISEASES = DEFAULT_INPUT_DIR / "diseases.csv"
DEFAULT_PROTEIN_DISEASE_EDGES = DEFAULT_INPUT_DIR / "protein_disease_edges.csv"
DEFAULT_BATCH_SIZE = 500


def load_env_file(env_path):
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file(PROJECT_ROOT / ".env")

DEFAULT_URI = os.getenv("NEO4J_URI", "neo4j+s://dfb7807d.databases.neo4j.io")
DEFAULT_DATABASE = os.getenv("NEO4J_DATABASE", "dfb7807d")
DEFAULT_USER = os.getenv("NEO4J_USERNAME", os.getenv("USERNAME"))
DEFAULT_PASSWORD = os.getenv("NEO4J_PASSWORD", os.getenv("PASSWORD"))


CLEAR_DB_QUERY = "MATCH (n) DETACH DELETE n"
CREATE_CONSTRAINT_QUERY = """
CREATE CONSTRAINT protein_id IF NOT EXISTS
FOR (p:Protein) REQUIRE p.row_id IS UNIQUE
"""
CREATE_DISEASE_CONSTRAINT_QUERY = """
CREATE CONSTRAINT disease_id IF NOT EXISTS
FOR (d:Disease) REQUIRE d.disease_accession IS UNIQUE
"""
LOAD_PROTEINS_QUERY = """
UNWIND $rows AS row
MERGE (p:Protein {row_id: row.row_id})
SET p += row.props
"""
LOAD_EDGES_QUERY = """
UNWIND $rows AS row
MATCH (a:Protein {row_id: row.src})
MATCH (b:Protein {row_id: row.dst})
MERGE (a)-[r:SIMILAR_TO]->(b)
SET r.cosine_sim = row.sim
"""
LOAD_DISEASES_QUERY = """
UNWIND $rows AS row
MERGE (d:Disease {disease_accession: row.disease_accession})
SET d += row.props
"""
LOAD_PROTEIN_DISEASE_QUERY = """
UNWIND $rows AS row
MATCH (p:Protein {row_id: row.row_id})
MATCH (d:Disease {disease_accession: row.disease_accession})
MERGE (p)-[r:ASSOCIATED_WITH]->(d)
SET r += row.props
"""


def batch_iter(rows, size):
    for i in range(0, len(rows), size):
        batch = rows[i : i + size]
        if isinstance(batch, pd.DataFrame):
            yield batch.to_dict("records")
        else:
            yield batch


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--proteins",
        default=str(DEFAULT_PROTEINS),
        help=f"Path to proteins.csv (default: {DEFAULT_PROTEINS})",
    )
    parser.add_argument(
        "--edges",
        default=str(DEFAULT_EDGES),
        help=f"Path to edges.csv (default: {DEFAULT_EDGES})",
    )
    parser.add_argument(
        "--diseases",
        default=str(DEFAULT_DISEASES),
        help=f"Path to diseases.csv (default: {DEFAULT_DISEASES})",
    )
    parser.add_argument(
        "--protein-disease-edges",
        default=str(DEFAULT_PROTEIN_DISEASE_EDGES),
        help=f"Path to protein_disease_edges.csv (default: {DEFAULT_PROTEIN_DISEASE_EDGES})",
    )
    parser.add_argument(
        "--uri",
        default=DEFAULT_URI,
        help=f"Neo4j URI (default: {DEFAULT_URI})",
    )
    parser.add_argument(
        "--database",
        default=DEFAULT_DATABASE,
        help=f"Neo4j database name (default: {DEFAULT_DATABASE})",
    )
    parser.add_argument(
        "--user",
        default=DEFAULT_USER,
        help="Neo4j username. Falls back to NEO4J_USERNAME or USERNAME.",
    )
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help="Neo4j password. Falls back to NEO4J_PASSWORD or PASSWORD.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Allow self-signed certificates by switching neo4j+s:// to neo4j+ssc://.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Batch size for inserts (default: {DEFAULT_BATCH_SIZE})",
    )
    return parser.parse_args()


def resolve_driver_uri(uri, insecure):
    if not insecure:
        return uri
    if uri.startswith("neo4j+s://"):
        return "neo4j+ssc://" + uri[len("neo4j+s://") :]
    if uri.startswith("bolt+s://"):
        return "bolt+ssc://" + uri[len("bolt+s://") :]
    return uri


def is_tls_cert_error(exc):
    seen = set()
    stack = [exc]

    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        if "CERTIFICATE_VERIFY_FAILED" in str(current):
            return True

        cause = getattr(current, "__cause__", None)
        if cause is not None:
            stack.append(cause)

        context = getattr(current, "__context__", None)
        if context is not None:
            stack.append(context)

        for nested in getattr(current, "exceptions", ()):
            stack.append(nested)

    return False


def run_import(driver_uri, args, proteins, edges, diseases, protein_disease_edges):
    print(f"Connecting to: {driver_uri}")
    print(f"Using database: {args.database}")

    with GraphDatabase.driver(driver_uri, auth=(args.user, args.password)) as driver:
        print("Verifying connectivity...")
        driver.verify_connectivity()

        print("Clearing DB...")
        driver.execute_query(CLEAR_DB_QUERY, database_=args.database)

        print("Creating constraint...")
        driver.execute_query(CREATE_CONSTRAINT_QUERY, database_=args.database)
        driver.execute_query(CREATE_DISEASE_CONSTRAINT_QUERY, database_=args.database)

        print("Loading proteins...")
        for batch in batch_iter(proteins, args.batch_size):
            driver.execute_query(
                LOAD_PROTEINS_QUERY,
                rows=batch,
                database_=args.database,
            )

        print("Loading edges...")
        for batch in batch_iter(edges, args.batch_size):
            driver.execute_query(
                LOAD_EDGES_QUERY,
                rows=batch,
                database_=args.database,
            )

        if diseases:
            print("Loading diseases...")
            for batch in batch_iter(diseases, args.batch_size):
                driver.execute_query(
                    LOAD_DISEASES_QUERY,
                    rows=batch,
                    database_=args.database,
                )

        if protein_disease_edges:
            print("Loading protein-disease links...")
            for batch in batch_iter(protein_disease_edges, args.batch_size):
                driver.execute_query(
                    LOAD_PROTEIN_DISEASE_QUERY,
                    rows=batch,
                    database_=args.database,
                )


def main():
    args = parse_args()

    proteins_path = Path(args.proteins)
    edges_path = Path(args.edges)
    diseases_path = Path(args.diseases)
    protein_disease_edges_path = Path(args.protein_disease_edges)

    if not proteins_path.exists():
        raise FileNotFoundError(f"Proteins CSV not found: {proteins_path}")
    if not edges_path.exists():
        raise FileNotFoundError(f"Edges CSV not found: {edges_path}")
    if not args.user:
        raise ValueError("Neo4j username is missing. Set NEO4J_USERNAME or USERNAME.")
    if not args.password:
        raise ValueError("Neo4j password is missing. Set NEO4J_PASSWORD or PASSWORD.")

    proteins = pd.read_csv(proteins_path).rename(columns={"row_id:ID(Protein)": "row_id"})
    edges = pd.read_csv(edges_path).rename(
        columns={
            ":START_ID(Protein)": "src",
            ":END_ID(Protein)": "dst",
            "cosine_sim:float": "sim",
        }
    )
    diseases = pd.read_csv(diseases_path) if diseases_path.exists() else pd.DataFrame()
    protein_disease_edges = (
        pd.read_csv(protein_disease_edges_path)
        if protein_disease_edges_path.exists()
        else pd.DataFrame()
    )

    proteins["row_id"] = proteins["row_id"].astype(int)
    edges["src"] = edges["src"].astype(int)
    edges["dst"] = edges["dst"].astype(int)
    edges["sim"] = edges["sim"].astype(float)

    protein_rows = []
    for row in proteins.to_dict("records"):
        row_id = int(row.pop("row_id"))
        row.pop(":LABEL", None)
        props = {}
        for key, value in row.items():
            if pd.isna(value):
                continue
            props[key] = value.item() if hasattr(value, "item") else value
        protein_rows.append({"row_id": row_id, "props": props})

    disease_rows = []
    if not diseases.empty:
        diseases = diseases.rename(
            columns={"disease_accession:ID(Disease)": "disease_accession"}
        )
        for row in diseases.to_dict("records"):
            disease_accession = row.pop("disease_accession")
            row.pop(":LABEL", None)
            props = {}
            for key, value in row.items():
                if pd.isna(value):
                    continue
                props[key] = value.item() if hasattr(value, "item") else value
            disease_rows.append(
                {"disease_accession": disease_accession, "props": props}
            )

    protein_disease_rows = []
    if not protein_disease_edges.empty:
        protein_disease_edges = protein_disease_edges.rename(
            columns={
                ":START_ID(Protein)": "row_id",
                ":END_ID(Disease)": "disease_accession",
            }
        )
        for row in protein_disease_edges.to_dict("records"):
            row.pop(":TYPE", None)
            props = {}
            for key in ["association_note", "association_source"]:
                value = row.get(key)
                if pd.isna(value):
                    continue
                props[key] = value.item() if hasattr(value, "item") else value
            protein_disease_rows.append(
                {
                    "row_id": int(row["row_id"]),
                    "disease_accession": row["disease_accession"],
                    "props": props,
                }
            )

    print(f"Using proteins: {proteins_path}")
    print(f"Using edges: {edges_path}")
    if diseases_path.exists():
        print(f"Using diseases: {diseases_path}")
    if protein_disease_edges_path.exists():
        print(f"Using protein-disease edges: {protein_disease_edges_path}")

    driver_uri = resolve_driver_uri(args.uri, args.insecure)

    try:
        run_import(
            driver_uri,
            args,
            protein_rows,
            edges,
            disease_rows,
            protein_disease_rows,
        )
    except ServiceUnavailable as exc:
        if is_tls_cert_error(exc) and not args.insecure:
            fallback_uri = resolve_driver_uri(args.uri, insecure=True)
            print(
                "TLS certificate verification failed for the default secure connection."
            )
            print(f"Retrying with self-signed certificate mode: {fallback_uri}")
            run_import(
                fallback_uri,
                args,
                protein_rows,
                edges,
                disease_rows,
                protein_disease_rows,
            )
        else:
            raise

    print("Done!")


if __name__ == "__main__":
    main()

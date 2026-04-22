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
DEFAULT_BATCH_SIZE = 1000


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
LOAD_PROTEINS_QUERY = """
UNWIND $rows AS row
MERGE (p:Protein {row_id: row.row_id})
SET p.accession = row.accession,
    p.dataset = row.dataset
"""
LOAD_EDGES_QUERY = """
UNWIND $rows AS row
MATCH (a:Protein {row_id: row.src})
MATCH (b:Protein {row_id: row.dst})
MERGE (a)-[r:SIMILAR_TO]-(b)
SET r.cosine_sim = row.sim
"""


def batch_iter(df, size):
    for i in range(0, len(df), size):
        yield df.iloc[i : i + size].to_dict("records")


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


def run_import(driver_uri, args, proteins, edges):
    print(f"Connecting to: {driver_uri}")
    print(f"Using database: {args.database}")

    with GraphDatabase.driver(driver_uri, auth=(args.user, args.password)) as driver:
        print("Verifying connectivity...")
        driver.verify_connectivity()

        print("Clearing DB...")
        driver.execute_query(CLEAR_DB_QUERY, database_=args.database)

        print("Creating constraint...")
        driver.execute_query(CREATE_CONSTRAINT_QUERY, database_=args.database)

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


def main():
    args = parse_args()

    proteins_path = Path(args.proteins)
    edges_path = Path(args.edges)

    if not proteins_path.exists():
        raise FileNotFoundError(f"Proteins CSV not found: {proteins_path}")
    if not edges_path.exists():
        raise FileNotFoundError(f"Edges CSV not found: {edges_path}")
    if not args.user:
        raise ValueError("Neo4j username is missing. Set NEO4J_USERNAME or USERNAME.")
    if not args.password:
        raise ValueError("Neo4j password is missing. Set NEO4J_PASSWORD or PASSWORD.")

    proteins = pd.read_csv(proteins_path).rename(
        columns={"row_id:ID(Protein)": "row_id"}
    )
    edges = pd.read_csv(edges_path).rename(
        columns={
            ":START_ID(Protein)": "src",
            ":END_ID(Protein)": "dst",
            "cosine_sim:float": "sim",
        }
    )

    proteins["row_id"] = proteins["row_id"].astype(int)
    edges["src"] = edges["src"].astype(int)
    edges["dst"] = edges["dst"].astype(int)
    edges["sim"] = edges["sim"].astype(float)

    print(f"Using proteins: {proteins_path}")
    print(f"Using edges: {edges_path}")

    driver_uri = resolve_driver_uri(args.uri, args.insecure)

    try:
        run_import(driver_uri, args, proteins, edges)
    except ServiceUnavailable as exc:
        if is_tls_cert_error(exc) and not args.insecure:
            fallback_uri = resolve_driver_uri(args.uri, insecure=True)
            print(
                "TLS certificate verification failed for the default secure connection."
            )
            print(f"Retrying with self-signed certificate mode: {fallback_uri}")
            run_import(fallback_uri, args, proteins, edges)
        else:
            raise

    print("Done!")


if __name__ == "__main__":
    main()

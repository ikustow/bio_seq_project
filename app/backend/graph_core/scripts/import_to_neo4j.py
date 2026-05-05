import argparse
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT.parents[2]
APP_ROOT = PROJECT_ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.agents_core.shared.config import (  # noqa: E402
    resolve_neo4j_settings,
)
DEFAULT_INPUT_DIR = ROOT / "output" / "neo4j"
DEFAULT_PROTEINS = DEFAULT_INPUT_DIR / "proteins.csv"
DEFAULT_EDGES = DEFAULT_INPUT_DIR / "edges.csv"
DEFAULT_DISEASES = DEFAULT_INPUT_DIR / "diseases.csv"
DEFAULT_PROTEIN_DISEASE_EDGES = DEFAULT_INPUT_DIR / "protein_disease_edges.csv"
DEFAULT_SEQUENCES = DEFAULT_INPUT_DIR / "sequences.csv"
DEFAULT_SEQUENCE_PROTEIN_EDGES = DEFAULT_INPUT_DIR / "sequence_protein_edges.csv"
DEFAULT_BATCH_SIZE = 500
DEFAULT_CSV_CHUNK_SIZE = 100_000
DEFAULT_MAX_EDGE_RANK = 2
DEFAULT_MAX_RELATIONSHIPS = 400_000


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

DEFAULT_NEO4J = resolve_neo4j_settings()
DEFAULT_URI = DEFAULT_NEO4J.uri
DEFAULT_DATABASE = DEFAULT_NEO4J.database
DEFAULT_USER = DEFAULT_NEO4J.user
DEFAULT_PASSWORD = DEFAULT_NEO4J.password


CLEAR_DB_QUERY = "MATCH (n) DETACH DELETE n"
CREATE_PROTEIN_ROW_ID_CONSTRAINT_QUERY = """
CREATE CONSTRAINT protein_row_id IF NOT EXISTS
FOR (p:Protein) REQUIRE p.row_id IS UNIQUE
"""
CREATE_PROTEIN_ACCESSION_CONSTRAINT_QUERY = """
CREATE CONSTRAINT protein_accession IF NOT EXISTS
FOR (p:Protein) REQUIRE p.accession IS UNIQUE
"""
CREATE_PROTEIN_GENE_INDEX_QUERY = """
CREATE INDEX protein_gene IF NOT EXISTS
FOR (p:Protein) ON (p.gene_primary)
"""
CREATE_PROTEIN_ENTRY_NAME_INDEX_QUERY = """
CREATE INDEX protein_entry_name IF NOT EXISTS
FOR (p:Protein) ON (p.entry_name)
"""
CREATE_PROTEIN_SEQUENCE_HASH_INDEX_QUERY = """
CREATE INDEX protein_sequence_hash IF NOT EXISTS
FOR (p:Protein) ON (p.sequence_hash)
"""
CREATE_DISEASE_CONSTRAINT_QUERY = """
CREATE CONSTRAINT disease_accession IF NOT EXISTS
FOR (d:Disease) REQUIRE d.disease_accession IS UNIQUE
"""
CREATE_SEQUENCE_CONSTRAINT_QUERY = """
CREATE CONSTRAINT sequence_hash IF NOT EXISTS
FOR (s:Sequence) REQUIRE s.sequence_hash IS UNIQUE
"""
CREATE_PROTEIN_TEXT_INDEX_QUERY = """
CREATE FULLTEXT INDEX protein_text IF NOT EXISTS
FOR (p:Protein)
ON EACH [p.protein_name, p.gene_primary, p.organism_name, p.function_text, p.keywords_json]
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
SET r.cosine_sim = row.sim,
    r.rank = row.rank,
    r.method = coalesce(row.method, "precomputed_knn"),
    r.embedding_model = coalesce(row.embedding_model, "ProtT5"),
    r.embedding_release = coalesce(row.embedding_release, "offline")
"""
LOAD_SEQUENCES_QUERY = """
UNWIND $rows AS row
MERGE (s:Sequence {sequence_hash: row.sequence_hash})
SET s += row.props
"""
LOAD_SEQUENCE_PROTEIN_QUERY = """
UNWIND $rows AS row
MATCH (s:Sequence {sequence_hash: row.sequence_hash})
MATCH (p:Protein {row_id: row.row_id})
MERGE (s)-[:ENCODES]->(p)
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
    if not isinstance(rows, (pd.DataFrame, list, tuple)):
        yield from rows
        return
    for i in range(0, len(rows), size):
        batch = rows[i : i + size]
        if isinstance(batch, pd.DataFrame):
            yield batch.to_dict("records")
        else:
            yield batch


def normalize_edges_frame(edges, max_edge_rank=None):
    edges = edges.rename(
        columns={
            ":START_ID(Protein)": "src",
            ":END_ID(Protein)": "dst",
            "cosine_sim:float": "sim",
            "rank:int": "rank",
        }
    )
    edges["src"] = edges["src"].astype(int)
    edges["dst"] = edges["dst"].astype(int)
    edges["sim"] = edges["sim"].astype(float)
    if "rank" not in edges.columns:
        edges = edges.sort_values(["src", "sim"], ascending=[True, False])
        edges["rank"] = edges.groupby("src").cumcount() + 1
    edges["rank"] = edges["rank"].astype(int)
    if max_edge_rank is not None:
        edges = edges[edges["rank"] <= max_edge_rank].copy()
    edges["method"] = edges.get("method", "precomputed_knn")
    edges["embedding_model"] = edges.get("embedding_model", "ProtT5")
    edges["embedding_release"] = edges.get("embedding_release", "offline")
    return edges


def iter_edge_record_batches(edges_path, max_edge_rank=None, csv_chunk_size=DEFAULT_CSV_CHUNK_SIZE):
    for chunk in pd.read_csv(edges_path, chunksize=csv_chunk_size):
        edges = normalize_edges_frame(chunk, max_edge_rank=max_edge_rank)
        if edges.empty:
            continue
        yield edges.to_dict("records")


def count_csv_rows(path, chunksize=DEFAULT_CSV_CHUNK_SIZE):
    total = 0
    for chunk in pd.read_csv(path, chunksize=chunksize):
        total += len(chunk)
    return total


def count_edge_rows(path, max_edge_rank=None, chunksize=DEFAULT_CSV_CHUNK_SIZE):
    total = 0
    for chunk in pd.read_csv(path, chunksize=chunksize):
        total += len(normalize_edges_frame(chunk, max_edge_rank=max_edge_rank))
    return total


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
        "--sequences",
        default=str(DEFAULT_SEQUENCES),
        help=f"Path to sequences.csv (default: {DEFAULT_SEQUENCES})",
    )
    parser.add_argument(
        "--sequence-protein-edges",
        default=str(DEFAULT_SEQUENCE_PROTEIN_EDGES),
        help=f"Path to sequence_protein_edges.csv (default: {DEFAULT_SEQUENCE_PROTEIN_EDGES})",
    )
    parser.add_argument(
        "--neo4j-profile",
        default=DEFAULT_NEO4J.profile,
        help="Neo4j env profile: local or cloud. Uses NEO4J_LOCAL_* or NEO4J_CLOUD_* when set.",
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
        default=DEFAULT_NEO4J.insecure,
        help="Allow self-signed certificates by switching neo4j+s:// to neo4j+ssc://.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Batch size for inserts (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--no-clear",
        action="store_true",
        help="Do not clear the target database before importing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate input CSVs and print row counts without connecting to Neo4j.",
    )
    parser.add_argument(
        "--max-edge-rank",
        type=int,
        default=DEFAULT_MAX_EDGE_RANK,
        help=(
            "Only import SIMILAR_TO edges with rank <= this value. "
            f"Default: {DEFAULT_MAX_EDGE_RANK}."
        ),
    )
    parser.add_argument(
        "--max-relationships",
        type=int,
        default=DEFAULT_MAX_RELATIONSHIPS,
        help=(
            "Fail before importing if total relationships would exceed this limit. "
            f"Default: {DEFAULT_MAX_RELATIONSHIPS}. Use 0 to disable."
        ),
    )
    parser.add_argument(
        "--csv-chunk-size",
        type=int,
        default=DEFAULT_CSV_CHUNK_SIZE,
        help=f"Rows per pandas CSV chunk for large imports (default: {DEFAULT_CSV_CHUNK_SIZE}).",
    )
    args = parser.parse_args()
    if args.neo4j_profile != DEFAULT_NEO4J.profile:
        selected_neo4j = resolve_neo4j_settings(args.neo4j_profile)
        if args.uri == DEFAULT_NEO4J.uri:
            args.uri = selected_neo4j.uri
        if args.database == DEFAULT_NEO4J.database:
            args.database = selected_neo4j.database
        if args.user == DEFAULT_NEO4J.user:
            args.user = selected_neo4j.user
        if args.password == DEFAULT_NEO4J.password:
            args.password = selected_neo4j.password
        if args.insecure == DEFAULT_NEO4J.insecure:
            args.insecure = selected_neo4j.insecure
    return args


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


def run_import(driver_uri, args, proteins, edges, sequences, sequence_protein_edges, diseases, protein_disease_edges):
    from neo4j import GraphDatabase

    print(f"Connecting to: {driver_uri}")
    print(f"Using database: {args.database}")

    with GraphDatabase.driver(driver_uri, auth=(args.user, args.password)) as driver:
        print("Verifying connectivity...")
        driver.verify_connectivity()

        if not args.no_clear:
            print("Clearing DB...")
            driver.execute_query(CLEAR_DB_QUERY, database_=args.database)

        print("Creating constraints and indexes...")
        for query in [
            CREATE_PROTEIN_ROW_ID_CONSTRAINT_QUERY,
            CREATE_PROTEIN_ACCESSION_CONSTRAINT_QUERY,
            CREATE_PROTEIN_GENE_INDEX_QUERY,
            CREATE_PROTEIN_ENTRY_NAME_INDEX_QUERY,
            CREATE_PROTEIN_SEQUENCE_HASH_INDEX_QUERY,
            CREATE_SEQUENCE_CONSTRAINT_QUERY,
            CREATE_DISEASE_CONSTRAINT_QUERY,
            CREATE_PROTEIN_TEXT_INDEX_QUERY,
        ]:
            driver.execute_query(query, database_=args.database)

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
                rows=batch.to_dict("records") if isinstance(batch, pd.DataFrame) else batch,
                database_=args.database,
            )

        if sequences:
            print("Loading sequences...")
            for batch in batch_iter(sequences, args.batch_size):
                driver.execute_query(
                    LOAD_SEQUENCES_QUERY,
                    rows=batch,
                    database_=args.database,
                )

        if sequence_protein_edges:
            print("Loading sequence-protein links...")
            for batch in batch_iter(sequence_protein_edges, args.batch_size):
                driver.execute_query(
                    LOAD_SEQUENCE_PROTEIN_QUERY,
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
    sequences_path = Path(args.sequences)
    sequence_protein_edges_path = Path(args.sequence_protein_edges)

    if not proteins_path.exists():
        raise FileNotFoundError(f"Proteins CSV not found: {proteins_path}")
    if not edges_path.exists():
        raise FileNotFoundError(f"Edges CSV not found: {edges_path}")
    if not args.dry_run and not args.user:
        raise ValueError("Neo4j username is missing. Set NEO4J_USERNAME or USERNAME.")
    if not args.dry_run and not args.password:
        raise ValueError("Neo4j password is missing. Set NEO4J_PASSWORD or PASSWORD.")

    proteins = pd.read_csv(proteins_path).rename(columns={"row_id:ID(Protein)": "row_id"})
    diseases = pd.read_csv(diseases_path) if diseases_path.exists() else pd.DataFrame()
    protein_disease_edges = (
        pd.read_csv(protein_disease_edges_path)
        if protein_disease_edges_path.exists()
        else pd.DataFrame()
    )
    sequences = pd.read_csv(sequences_path) if sequences_path.exists() else pd.DataFrame()
    sequence_protein_edges = (
        pd.read_csv(sequence_protein_edges_path)
        if sequence_protein_edges_path.exists()
        else pd.DataFrame()
    )

    proteins["row_id"] = proteins["row_id"].astype(int)
    edge_rows_count = count_edge_rows(
        edges_path,
        max_edge_rank=args.max_edge_rank,
        chunksize=args.csv_chunk_size,
    )
    edge_batches = iter_edge_record_batches(
        edges_path,
        max_edge_rank=args.max_edge_rank,
        csv_chunk_size=args.csv_chunk_size,
    )

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

    sequence_rows = []
    if not sequences.empty:
        sequences = sequences.rename(columns={"sequence_hash:ID(Sequence)": "sequence_hash"})
        for row in sequences.to_dict("records"):
            sequence_hash = row.pop("sequence_hash")
            row.pop(":LABEL", None)
            props = {}
            for key, value in row.items():
                if pd.isna(value):
                    continue
                props[key] = value.item() if hasattr(value, "item") else value
            sequence_rows.append({"sequence_hash": sequence_hash, "props": props})

    sequence_protein_rows = []
    if not sequence_protein_edges.empty:
        sequence_protein_edges = sequence_protein_edges.rename(
            columns={
                ":START_ID(Sequence)": "sequence_hash",
                ":END_ID(Protein)": "row_id",
            }
        )
        for row in sequence_protein_edges.to_dict("records"):
            sequence_protein_rows.append(
                {
                    "sequence_hash": row["sequence_hash"],
                    "row_id": int(row["row_id"]),
                }
            )

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
    if sequences_path.exists():
        print(f"Using sequences: {sequences_path}")
    if sequence_protein_edges_path.exists():
        print(f"Using sequence-protein edges: {sequence_protein_edges_path}")
    print(
        "Rows: "
        f"proteins={len(protein_rows)}, "
        f"similarity_edges={edge_rows_count}, "
        f"sequences={len(sequence_rows)}, "
        f"sequence_edges={len(sequence_protein_rows)}, "
        f"diseases={len(disease_rows)}, "
        f"protein_disease_edges={len(protein_disease_rows)}"
    )
    total_relationships = (
        edge_rows_count
        + len(sequence_protein_rows)
        + len(protein_disease_rows)
    )
    print(
        f"Relationships: total={total_relationships}, "
        f"limit={args.max_relationships or 'disabled'}"
    )

    if args.max_relationships > 0 and total_relationships > args.max_relationships:
        raise ValueError(
            "Relationship limit exceeded: "
            f"{total_relationships} > {args.max_relationships}. "
            "Rebuild a smaller dataset with lower DEFAULT_MAX_PROTEINS, "
            "lower DEFAULT_K, or lower --max-edge-rank."
        )

    if args.dry_run:
        print("Dry run complete; no Neo4j connection was opened.")
        return

    driver_uri = resolve_driver_uri(args.uri, args.insecure)

    try:
        run_import(
            driver_uri,
            args,
            protein_rows,
            edge_batches,
            sequence_rows,
            sequence_protein_rows,
            disease_rows,
            protein_disease_rows,
        )
    except Exception as exc:
        from neo4j.exceptions import ServiceUnavailable

        if not isinstance(exc, ServiceUnavailable):
            raise
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
            iter_edge_record_batches(
                edges_path,
                max_edge_rank=args.max_edge_rank,
                csv_chunk_size=args.csv_chunk_size,
            ),
                sequence_rows,
                sequence_protein_rows,
                disease_rows,
                protein_disease_rows,
            )
        else:
            raise

    print("Done!")


if __name__ == "__main__":
    main()

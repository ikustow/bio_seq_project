import argparse
import json
import os
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_URI = "neo4j+s://dfb7807d.databases.neo4j.io"
DEFAULT_DATABASE = "dfb7807d"
DEFAULT_MODEL = "gpt-4.1-mini"


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(DEFAULT_ENV_PATH)


def resolve_driver_uri(uri: str, insecure: bool) -> str:
    if not insecure:
        return uri
    if uri.startswith("neo4j+s://"):
        return "neo4j+ssc://" + uri[len("neo4j+s://") :]
    if uri.startswith("bolt+s://"):
        return "bolt+ssc://" + uri[len("bolt+s://") :]
    return uri


def is_tls_cert_error(exc: BaseException) -> bool:
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


class Neo4jGraphClient:
    def __init__(self, uri: str, user: str, password: str, database: str) -> None:
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database

    def _execute(self, query: str, **params: Any) -> dict[str, Any]:
        try:
            return self._execute_once(self.uri, query, **params)
        except ServiceUnavailable as exc:
            fallback_uri = resolve_driver_uri(self.uri, insecure=True)
            if fallback_uri != self.uri:
                return self._execute_once(fallback_uri, query, **params)
            raise

    def _execute_once(self, uri: str, query: str, **params: Any) -> dict[str, Any]:
        with GraphDatabase.driver(uri, auth=(self.user, self.password)) as driver:
            records, summary, keys = driver.execute_query(
                query,
                database_=self.database,
                **params,
            )
        return {
            "keys": list(keys),
            "records": [record.data() for record in records],
            "query": summary.query,
        }


def ensure_read_only_cypher(query: str) -> str:
    normalized = " ".join(query.strip().split())
    lowered = normalized.lower()
    forbidden = [
        " create ",
        " merge ",
        " delete ",
        " detach ",
        " set ",
        " remove ",
        " drop ",
        " load csv ",
        " call dbms ",
        " call apoc.",
    ]
    wrapped = f" {lowered} "
    if any(token in wrapped for token in forbidden):
        raise ValueError("Only read-only Cypher queries are allowed.")
    if not lowered.startswith(("match", "optional match", "with", "call")):
        raise ValueError("Cypher query must start with MATCH, OPTIONAL MATCH, WITH, or CALL.")
    return normalized


def build_tools(client: Neo4jGraphClient):
    @tool
    def graph_schema_guide() -> str:
        """Return the graph schema and query-writing guidance for this Neo4j database."""
        return (
            "Graph schema:\n"
            "- Nodes:\n"
            "  Protein {row_id, accession, dataset, entry_name, protein_name, gene_primary, "
            "organism_name, sequence_length, reviewed, annotation_score, protein_existence, "
            "ensembl_ids, disease_count?, disease_names?}\n"
            "  Disease {disease_accession, disease_id, disease_acronym, disease_description, "
            "disease_xref_db, disease_xref_id, association_source}\n"
            "- Relationships:\n"
            "  (:Protein)-[:SIMILAR_TO {cosine_sim}]->(:Protein)\n"
            "  (:Protein)-[:ASSOCIATED_WITH {association_note, association_source}]->(:Disease)\n"
            "- Query guidance:\n"
            "  Use accession or gene_primary to find proteins.\n"
            "  Use cosine_sim DESC for strongest neighbors.\n"
            "  Disease nodes may be absent if no disease annotations were loaded.\n"
            "  Always prefer read-only MATCH/OPTIONAL MATCH/RETURN queries."
        )

    @tool
    def find_proteins(search_text: str, limit: int = 10) -> str:
        """Find proteins by accession, gene name, entry name, or protein name."""
        result = client._execute(
            """
            MATCH (p:Protein)
            WHERE toLower(p.accession) CONTAINS toLower($search_text)
               OR toLower(coalesce(p.gene_primary, "")) CONTAINS toLower($search_text)
               OR toLower(coalesce(p.entry_name, "")) CONTAINS toLower($search_text)
               OR toLower(coalesce(p.protein_name, "")) CONTAINS toLower($search_text)
            RETURN p.row_id AS row_id,
                   p.accession AS accession,
                   p.gene_primary AS gene_primary,
                   p.entry_name AS entry_name,
                   p.protein_name AS protein_name,
                   p.organism_name AS organism_name
            ORDER BY p.reviewed DESC, p.annotation_score DESC, p.accession ASC
            LIMIT $limit
            """,
            search_text=search_text,
            limit=limit,
        )
        return json.dumps(result["records"], ensure_ascii=False, indent=2)

    @tool
    def get_protein_neighbors(accession: str, limit: int = 10) -> str:
        """Get the most similar neighboring proteins for a given accession."""
        result = client._execute(
            """
            MATCH (p:Protein {accession: $accession})-[r:SIMILAR_TO]->(n:Protein)
            RETURN p.accession AS accession,
                   n.accession AS neighbor_accession,
                   n.gene_primary AS neighbor_gene,
                   n.entry_name AS neighbor_entry_name,
                   n.protein_name AS neighbor_protein_name,
                   n.organism_name AS neighbor_organism,
                   r.cosine_sim AS cosine_sim
            ORDER BY r.cosine_sim DESC
            LIMIT $limit
            """,
            accession=accession,
            limit=limit,
        )
        return json.dumps(result["records"], ensure_ascii=False, indent=2)

    @tool
    def get_neighbor_diseases(accession: str, neighbor_limit: int = 15, disease_limit: int = 20) -> str:
        """Aggregate diseases observed among the nearest neighbors of a protein."""
        result = client._execute(
            """
            MATCH (:Protein {accession: $accession})-[:SIMILAR_TO]->(n:Protein)
            WITH n
            ORDER BY n.annotation_score DESC
            LIMIT $neighbor_limit
            OPTIONAL MATCH (n)-[rel:ASSOCIATED_WITH]->(d:Disease)
            WITH n, d, rel
            WHERE d IS NOT NULL
            RETURN d.disease_id AS disease_id,
                   d.disease_accession AS disease_accession,
                   count(DISTINCT n) AS neighbor_hits,
                   collect(DISTINCT n.accession)[0..5] AS example_neighbors,
                   collect(DISTINCT rel.association_source)[0..3] AS sources
            ORDER BY neighbor_hits DESC, disease_id ASC
            LIMIT $disease_limit
            """,
            accession=accession,
            neighbor_limit=neighbor_limit,
            disease_limit=disease_limit,
        )
        return json.dumps(result["records"], ensure_ascii=False, indent=2)

    @tool
    def summarize_neighbor_disease_context(accession: str, neighbor_limit: int = 15, disease_limit: int = 10) -> str:
        """Summarize diseases shared across the nearest neighbors of a protein, with example proteins."""
        result = client._execute(
            """
            MATCH (p:Protein {accession: $accession})-[:SIMILAR_TO]->(n:Protein)
            WITH p, n
            ORDER BY n.annotation_score DESC
            LIMIT $neighbor_limit
            OPTIONAL MATCH (n)-[rel:ASSOCIATED_WITH]->(d:Disease)
            WITH p, n, rel, d
            WHERE d IS NOT NULL
            RETURN p.accession AS target_accession,
                   d.disease_id AS disease_id,
                   d.disease_accession AS disease_accession,
                   d.disease_description AS disease_description,
                   count(DISTINCT n) AS neighbor_hits,
                   collect(DISTINCT {
                       accession: n.accession,
                       gene_primary: n.gene_primary,
                       protein_name: n.protein_name
                   })[0..5] AS example_neighbors,
                   collect(DISTINCT rel.association_note)[0..3] AS example_notes
            ORDER BY neighbor_hits DESC, disease_id ASC
            LIMIT $disease_limit
            """,
            accession=accession,
            neighbor_limit=neighbor_limit,
            disease_limit=disease_limit,
        )
        return json.dumps(result["records"], ensure_ascii=False, indent=2)

    @tool
    def run_read_cypher(query: str) -> str:
        """Run a custom read-only Cypher query against the graph."""
        safe_query = ensure_read_only_cypher(query)
        result = client._execute(safe_query)
        return json.dumps(result["records"], ensure_ascii=False, indent=2)

    return [
        graph_schema_guide,
        find_proteins,
        get_protein_neighbors,
        get_neighbor_diseases,
        summarize_neighbor_disease_context,
        run_read_cypher,
    ]


SYSTEM_PROMPT = """You are a bioinformatics graph assistant for this project.

Your job is to help the user explore a Neo4j graph of proteins, similarity edges, and optional disease links.

Rules:
- Prefer the domain tools before writing custom Cypher.
- Use graph_schema_guide first when the graph shape matters.
- Be explicit about uncertainty.
- Treat similarity as hypothesis-generating evidence, not proof of function or disease causality.
- If disease results are empty, say that the graph may not have disease annotations loaded yet.
- When asked about common diseases around a protein, prefer summarize_neighbor_disease_context.
- When answering biological questions, ground your summary in the returned graph data.
"""


def build_agent(model_name: str, client: Neo4jGraphClient):
    model = ChatOpenAI(model=model_name, temperature=0)
    tools = build_tools(client)
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", help="Single message to send to the agent.")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--uri", default=os.getenv("NEO4J_URI", DEFAULT_URI))
    parser.add_argument("--database", default=os.getenv("NEO4J_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--user", default=os.getenv("NEO4J_USERNAME", os.getenv("USERNAME")))
    parser.add_argument("--password", default=os.getenv("NEO4J_PASSWORD", os.getenv("PASSWORD")))
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=os.getenv("NEO4J_INSECURE", "1").lower() not in {"0", "false", "no"},
        help="Use neo4j+ssc:// or bolt+ssc:// for environments with self-signed TLS chains.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is missing in the environment or .env file.")
    if not args.user or not args.password:
        raise ValueError("Neo4j credentials are missing in the environment or .env file.")

    client = Neo4jGraphClient(
        uri=resolve_driver_uri(args.uri, insecure=args.insecure),
        user=args.user,
        password=args.password,
        database=args.database,
    )
    agent = build_agent(args.model, client)

    if args.message:
        result = agent.invoke({"messages": [{"role": "user", "content": args.message}]})
        print(result["messages"][-1].content)
        return

    print("Simple graph agent is ready. Type 'exit' to stop.")
    while True:
        try:
            user_input = input("\nYou> ").strip()
        except EOFError:
            print()
            break
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            break

        result = agent.invoke({"messages": [{"role": "user", "content": user_input}]})
        print(f"\nAgent> {result['messages'][-1].content}")


if __name__ == "__main__":
    main()

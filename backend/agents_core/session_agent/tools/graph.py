from __future__ import annotations

from langchain.tools import tool

from ..services.graph import Neo4jGraphClient, ensure_read_only_cypher
from .base import dump_json


def build_graph_tools(client: Neo4jGraphClient) -> list:
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
        result = client.execute(
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
        return dump_json(result["records"])

    @tool
    def get_protein_neighbors(accession: str, limit: int = 10) -> str:
        """Get the most similar neighboring proteins for a given accession."""
        result = client.execute(
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
        return dump_json(result["records"])

    @tool
    def get_neighbor_diseases(accession: str, neighbor_limit: int = 15, disease_limit: int = 20) -> str:
        """Aggregate diseases observed among the nearest neighbors of a protein."""
        result = client.execute(
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
        return dump_json(result["records"])

    @tool
    def summarize_neighbor_disease_context(accession: str, neighbor_limit: int = 15, disease_limit: int = 10) -> str:
        """Summarize diseases shared across the nearest neighbors of a protein, with example proteins."""
        result = client.execute(
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
        return dump_json(result["records"])

    @tool
    def run_read_cypher(query: str) -> str:
        """Run a custom read-only Cypher query against the graph."""
        result = client.execute(ensure_read_only_cypher(query))
        return dump_json(result["records"])

    return [
        graph_schema_guide,
        find_proteins,
        get_protein_neighbors,
        get_neighbor_diseases,
        summarize_neighbor_disease_context,
        run_read_cypher,
    ]

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.app_contracts import CandidateView, ProteinView

from .protein_view_mapper import neighbor_record_to_candidate, protein_record_to_view

if TYPE_CHECKING:
    from backend.agents_core.shared.services.graph import Neo4jGraphClient

AMINO_ACID_RE = re.compile(r"^[ACDEFGHIKLMNPQRSTVWY]+$", re.IGNORECASE)


@dataclass(frozen=True)
class ProteinLookupHit:
    accession: str
    score: float = 1.0
    match_type: str = "accession"
    record: dict[str, Any] | None = None


def normalize_protein_sequence(sequence: str) -> str:
    lines = sequence.strip().splitlines()
    if lines and lines[0].startswith(">"):
        lines = [line for line in lines if not line.startswith(">")]
    return "".join("".join(lines).upper().split())


def sequence_hash(sequence: str) -> str:
    return hashlib.sha256(normalize_protein_sequence(sequence).encode("utf-8")).hexdigest()


class GraphRetrievalService:
    def __init__(self, client: "Neo4jGraphClient") -> None:
        self._client = client

    def resolve_input(self, text: str, limit: int = 5) -> list[ProteinLookupHit]:
        search_text = text.strip()
        if not search_text:
            return []
        result = self._client.execute(
            """
            MATCH (p:Protein)
            WHERE toLower(p.accession) = toLower($search_text)
               OR toLower(coalesce(p.gene_primary, "")) = toLower($search_text)
               OR toLower(coalesce(p.entry_name, "")) = toLower($search_text)
               OR toLower(coalesce(p.protein_name, "")) CONTAINS toLower($search_text)
               OR toLower(coalesce(p.accession, "")) CONTAINS toLower($search_text)
               OR toLower(coalesce(p.gene_primary, "")) CONTAINS toLower($search_text)
               OR toLower(coalesce(p.entry_name, "")) CONTAINS toLower($search_text)
            RETURN p {
                .*,
                accession: p.accession,
                protein_name: p.protein_name,
                gene_primary: p.gene_primary
            } AS protein,
            CASE
                WHEN toLower(p.accession) = toLower($search_text) THEN 1.0
                WHEN toLower(coalesce(p.gene_primary, "")) = toLower($search_text) THEN 0.95
                WHEN toLower(coalesce(p.entry_name, "")) = toLower($search_text) THEN 0.90
                ELSE 0.70
            END AS score
            ORDER BY score DESC, p.reviewed DESC, p.annotation_score DESC, p.accession ASC
            LIMIT $limit
            """,
            search_text=search_text,
            limit=limit,
        )
        hits: list[ProteinLookupHit] = []
        for item in result["records"]:
            record = dict(item.get("protein") or {})
            accession = record.get("accession")
            if accession:
                hits.append(ProteinLookupHit(accession=str(accession), score=float(item.get("score") or 0), record=record))
        return hits

    def find_by_sequence_hash(self, sequence: str) -> ProteinLookupHit | None:
        normalized = normalize_protein_sequence(sequence)
        if not normalized or not AMINO_ACID_RE.match(normalized):
            return None
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        result = self._client.execute(
            """
            MATCH (p:Protein {sequence_hash: $sequence_hash})
            RETURN p {.*} AS protein
            ORDER BY p.reviewed DESC, p.annotation_score DESC, p.accession ASC
            LIMIT 1
            """,
            sequence_hash=digest,
        )
        if not result["records"]:
            return None
        record = dict(result["records"][0].get("protein") or {})
        accession = record.get("accession")
        if not accession:
            return None
        return ProteinLookupHit(accession=str(accession), score=1.0, match_type="sequence_hash", record=record)

    def find_encoded_protein_by_sequence_hash(
        self,
        raw_sequence: str,
        translated_protein_sequence: str | None = None,
    ) -> ProteinLookupHit | None:
        raw_digest = sequence_hash(raw_sequence)
        result = self._client.execute(
            """
            MATCH (s:Sequence {sequence_hash: $sequence_hash})
            OPTIONAL MATCH (s)-[:ENCODES]->(encoded:Protein)
            OPTIONAL MATCH (s)-[:TRANSLATES_TO]->(:Sequence)-[:ENCODES]->(translated:Protein)
            WITH coalesce(encoded, translated) AS p
            WHERE p IS NOT NULL
            RETURN p {.*} AS protein
            ORDER BY p.reviewed DESC, p.annotation_score DESC, p.accession ASC
            LIMIT 1
            """,
            sequence_hash=raw_digest,
        )
        if result["records"]:
            record = dict(result["records"][0].get("protein") or {})
            accession = record.get("accession")
            if accession:
                return ProteinLookupHit(accession=str(accession), score=1.0, match_type="sequence_graph", record=record)
        if translated_protein_sequence:
            hit = self.find_by_sequence_hash(translated_protein_sequence)
            if hit:
                return ProteinLookupHit(
                    accession=hit.accession,
                    score=hit.score,
                    match_type="translated_protein_hash",
                    record=hit.record,
                )
        return None

    def retrieve_candidates(
        self,
        accession: str,
        limit: int = 5,
        neighbor_pool: int = 50,
        context: str | None = None,
    ) -> list[CandidateView]:
        target = self.get_protein_view(accession)
        result = self._client.execute(
            """
            MATCH (p:Protein {accession: $accession})-[r:SIMILAR_TO]->(n:Protein)
            RETURN n {.*} AS protein,
                   r.cosine_sim AS similarity_score,
                   r.rank AS graph_rank
            ORDER BY coalesce(r.rank, 999999) ASC, r.cosine_sim DESC, n.annotation_score DESC
            LIMIT $neighbor_pool
            """,
            accession=accession,
            neighbor_pool=neighbor_pool,
        )
        candidates = [
            CandidateView(
                protein=target,
                match_score=1.0,
                rank=0,
                similarity_score=1.0,
            )
        ]
        seen = {target.accession}
        for item in result["records"]:
            record = dict(item.get("protein") or {})
            record["similarity_score"] = item.get("similarity_score")
            candidate = neighbor_record_to_candidate(record, rank=len(candidates))
            if candidate.protein.accession in seen:
                continue
            seen.add(candidate.protein.accession)
            candidates.append(candidate)
            if len(candidates) >= limit:
                break
        return _rerank_candidates_by_context(candidates, context, limit)

    def get_protein_view(self, accession: str) -> ProteinView:
        result = self._client.execute(
            """
            MATCH (p:Protein {accession: $accession})
            RETURN p {.*} AS protein
            LIMIT 1
            """,
            accession=accession,
        )
        if not result["records"]:
            raise LookupError(f"Protein accession {accession} was not found in the prepared graph.")
        return protein_record_to_view(dict(result["records"][0].get("protein") or {}))

    def get_candidate_context(self, accession: str, limit: int = 5) -> list[dict[str, Any]]:
        result = self._client.execute(
            """
            MATCH (:Protein {accession: $accession})-[r:SIMILAR_TO]->(n:Protein)
            OPTIONAL MATCH (n)-[:ASSOCIATED_WITH]->(d:Disease)
            RETURN n.accession AS accession,
                   n.gene_primary AS gene,
                   n.protein_name AS protein_name,
                   r.cosine_sim AS cosine_sim,
                   collect(DISTINCT d.disease_id)[0..5] AS disease_ids
            ORDER BY r.cosine_sim DESC
            LIMIT $limit
            """,
            accession=accession,
            limit=limit,
        )
        return result["records"]


def _rerank_candidates_by_context(
    candidates: list[CandidateView],
    context: str | None,
    limit: int,
) -> list[CandidateView]:
    if not context or len(candidates) <= 2:
        return candidates[:limit]
    scored = []
    for candidate in candidates:
        context_score = _lexical_context_score(candidate, context)
        candidate.context_score = context_score
        scored.append(candidate)
    target = scored[0]
    neighbors = scored[1:]
    neighbors.sort(
        key=lambda item: (
            item.context_score or 0.0,
            item.similarity_score or item.match_score,
            -item.rank,
        ),
        reverse=True,
    )
    return [target, *neighbors][:limit]


def _lexical_context_score(candidate: CandidateView, context: str) -> float:
    query_terms = _terms(context)
    if not query_terms:
        return 0.0
    protein = candidate.protein
    haystack = " ".join(
        [
            protein.accession,
            protein.name,
            protein.gene,
            protein.organism_scientific,
            protein.function_text,
            " ".join(protein.keywords),
            " ".join(protein.go_terms),
        ]
    )
    document_terms = _terms(haystack)
    if not document_terms:
        return 0.0
    return len(query_terms & document_terms) / len(query_terms)


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9]{3,}", text.lower())}

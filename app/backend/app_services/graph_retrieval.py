from __future__ import annotations

import hashlib
import re
import os
import json
import numpy as np
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.app_contracts import CandidateView, ProteinView
from .protein_view_mapper import neighbor_record_to_candidate, protein_view_mapper, protein_record_to_view
from graph_core.scripts.anchor_indexer import GraphAnchorIndexer
from graph_core.scripts.reranker import LLMReranker

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

# Configuration constants
MINHASH_SIGNATURES_PATH = os.getenv("BIOSEQ_MINHASH_SIGNATURES_PATH", "app/backend/graph_core/data/minhash_signatures.json")

class GraphRetrievalService:
    def __init__(self, client: "Neo4jGraphClient") -> None:
        self._client = client
        self.minhasher = GraphAnchorIndexer()
        self.reranker = LLMReranker()
        
        # Load signatures for procedural anchor matching
        self.signatures_db = []
        if os.path.exists(MINHASH_SIGNATURES_PATH):
            with open(MINHASH_SIGNATURES_PATH, 'r') as f:
                self.signatures_db = json.load(f)
        else:
            print(f"Warning: MinHash signatures not found at {MINHASH_SIGNATURES_PATH}. Anchor fallback disabled.")


    def _find_anchor_procedurally(self, sequence: str) -> str | None:
        if not self.signatures_db: return None
        query_sig = np.array(self.minhasher.compute_signature(sequence))
        
        best_accession = None
        max_similarity = -1.0
        
        for item in self.signatures_db:
            target_sig = np.array(item["signature"])
            similarity = np.mean(query_sig == target_sig)
            if similarity > max_similarity:
                max_similarity = similarity
                best_accession = item["accession"]
        return best_accession

    def resolve_input(self, text: str, limit: int = 5) -> list[ProteinLookupHit]:
        search_text = text.strip()
        if not search_text: return []
        result = self._client.execute(
            """
            MATCH (p:Protein)
            WHERE toLower(p.accession) = toLower($search_text) OR toLower(coalesce(p.gene_primary, "")) = toLower($search_text)
            RETURN p {.*, accession: p.accession, protein_name: p.protein_name} AS protein,
            CASE WHEN toLower(p.accession) = toLower($search_text) THEN 1.0 ELSE 0.70 END AS score
            ORDER BY score DESC LIMIT $limit
            """,
            search_text=search_text, limit=limit,
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
        if not normalized or not AMINO_ACID_RE.match(normalized): return None
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        result = self._client.execute(
            "MATCH (p:Protein {sequence_hash: $sequence_hash}) RETURN p {.*} AS protein LIMIT 1",
            sequence_hash=digest,
        )
        if not result["records"]: return None
        record = dict(result["records"][0].get("protein") or {})
        return ProteinLookupHit(accession=str(record.get("accession")), score=1.0, match_type="sequence_hash", record=record)

    def retrieve_candidates(
        self,
        accession: str | None = None,
        sequence: str | None = None,
        limit: int = 5,
        neighbor_pool: int = 50,
        context: str | None = None,
    ) -> list[CandidateView]:
        if not accession and sequence:
            hit = self.find_by_sequence_hash(sequence)
            accession = hit.accession if hit else self._find_anchor_procedurally(sequence)
        
        if not accession: return []

        target = self.get_protein_view(accession)
        result = self._client.execute(
            """
            MATCH (p:Protein {accession: $accession})-[r:SIMILAR_TO]->(n:Protein)
            RETURN n {.*} AS protein, r.cosine_sim AS similarity_score
            ORDER BY r.cosine_sim DESC LIMIT $neighbor_pool
            """,
            accession=accession, neighbor_pool=neighbor_pool,
        )
        
        candidates = [CandidateView(protein=target, match_score=1.0, rank=0, similarity_score=1.0)]
        for item in result["records"]:
            record = dict(item.get("protein") or {})
            record["similarity_score"] = item.get("similarity_score")
            candidates.append(neighbor_record_to_candidate(record, rank=len(candidates)))
        
        return self._rerank_by_llm(candidates, context, limit)

    def _rerank_by_llm(self, candidates: list[CandidateView], context: str | None, limit: int) -> list[CandidateView]:
        if not context or not candidates: return candidates[:limit]
        records = [c.protein.__dict__ for c in candidates]
        reranked = self.reranker.rerank(records, context, top_n=limit)
        reranked_accessions = {r.get('accession') for r in reranked}
        return [c for c in candidates if c.protein.accession in reranked_accessions][:limit]

    def get_protein_view(self, accession: str) -> ProteinView:
        result = self._client.execute("MATCH (p:Protein {accession: $accession}) RETURN p {.*} AS protein LIMIT 1", accession=accession)
        if not result["records"]: raise LookupError(f"Accession {accession} not found.")
        return protein_record_to_view(dict(result["records"][0].get("protein") or {}))

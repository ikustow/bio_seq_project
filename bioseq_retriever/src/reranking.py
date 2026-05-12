import numpy as np
import torch
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass, field
import faiss
import re
import traceback
from transformers import AutoTokenizer, AutoModel

# --- Global Model Cache (Internal Singleton) ---
# We load the model here to prevent re-loading on every node invocation
_RERANK_MODEL = None
_RERANK_TOKENIZER = None
_DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

def _get_bio_embedder():
    global _RERANK_MODEL, _RERANK_TOKENIZER
    if _RERANK_MODEL is None:
        model_name = "intfloat/e5-large-v2" # BioE5 candidate: High performance for scientific retrieval
        print(f"Loading Biomedical Reranking Model ({model_name}) on {_DEVICE}...")
        _RERANK_TOKENIZER = AutoTokenizer.from_pretrained(model_name)
        _RERANK_MODEL = AutoModel.from_pretrained(model_name).to(_DEVICE)
        _RERANK_MODEL.eval()
    return _RERANK_MODEL, _RERANK_TOKENIZER

# --- Data Structures ---

@dataclass
class BiologicalQuery:
    """Parsed user intent."""
    raw_query: str
    taxonomic_hints: List[str] = field(default_factory=list)
    localization_hints: List[str] = field(default_factory=list)
    functional_terms: List[str] = field(default_factory=list)
    enzyme_ids: List[str] = field(default_factory=list) # EC numbers

@dataclass
class ScoringComponents:
    """Detailed breakdown for transparency."""
    sequence: float = 0.0
    semantic: float = 0.0
    taxonomy: float = 0.0
    localization: float = 0.0
    domain_architecture: float = 0.0
    total: float = 0.0
    explanation: List[str] = field(default_factory=list)

# --- Advanced Biological Reranker ---

class LocalReranker:
    """
    State-of-the-art biological reranker for Swiss-Prot.
    Uses BioE5 embeddings with multi-field taxonomic and functional scoring.
    """
    def __init__(self):
        self.model, self.tokenizer = _get_bio_embedder()
        self.weights = {
            "sequence": 0.20,
            "semantic": 0.35,
            "taxonomy": 0.15,
            "localization": 0.20,
            "domain": 0.10
        }

    def _parse_biological_intent(self, prompt: str) -> BiologicalQuery:
        """Parses biological entities and constraints from natural language."""
        query = BiologicalQuery(raw_query=prompt)
        prompt_lower = prompt.lower()
        
        # Taxonomic entities
        taxa_map = {"bacterial": "bacteria", "human": "homo sapiens", "mammal": "mammalia", "viral": "viruses"}
        query.taxonomic_hints = [v for k, v in taxa_map.items() if k in prompt_lower]
        
        # Localization entities
        locs = ["membrane", "secreted", "cytoplasm", "nucleus", "mitochondrion", "chloroplast", "extracellular"]
        query.localization_hints = [l for l in locs if l in prompt_lower]
        
        # Enzyme/EC Pattern (e.g., 2.7.1.1)
        ec_match = re.findall(r"\b\d+\.\d+\.\d+\.\d+\b", prompt)
        query.enzyme_ids = ec_match

        return query

    def _extract_rich_profile(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Synthesizes a deep biological profile from UniProt metadata."""
        profile = {
            "name": record.get('proteinDescription', {}).get('recommendedName', {}).get('fullName', {}).get('value', ''),
            "organism": record.get('organism', {}).get('scientificName', ''),
            "lineage": [t.get('scientificName', '').lower() for t in record.get('organism', {}).get('lineage', [])],
            "locations": [],
            "functions": [],
            "go_terms": [],
            "domains": [],
            "ec_numbers": []
        }

        # Subcellular Localization
        for comment in record.get('comments', []):
            if comment.get('commentType') == 'SUBCELLULAR_LOCATION':
                profile["locations"].extend([l.get('location', {}).get('value', '').lower() for l in comment.get('locations', [])])
            if comment.get('commentType') == 'FUNCTION':
                profile["functions"].extend([t.get('value', '') for t in comment.get('note', {}).get('texts', [])])

        # Cross-references (EC, Pfam, GO)
        for xref in record.get('uniProtKBCrossReferences', []):
            db = xref.get('database')
            if db == 'EC': profile["ec_numbers"].append(xref.get('id'))
            elif db == 'GO': profile["go_terms"].append(xref.get('properties', [{}])[0].get('value', ''))
            elif db in ['Pfam', 'InterPro']: profile["domains"].append(xref.get('properties', [{}])[0].get('value', ''))

        return profile

    def _embed_bio_texts(self, texts: List[str], is_query: bool = False) -> np.ndarray:
        """Generates BioE5 embeddings with appropriate task-specific prefixes."""
        # E5 requires "query: " and "passage: " prefixes
        prefix = "query: " if is_query else "passage: "
        prefixed_texts = [f"{prefix}{t}" for t in texts]
        
        inputs = self.tokenizer(prefixed_texts, padding=True, truncation=True, max_length=512, return_tensors="pt").to(_DEVICE)
        
        with torch.no_grad():
            outputs = self.model(**inputs)
            # Use mean pooling of the last hidden state
            embeddings = outputs.last_hidden_state.mean(dim=1)
            
        return embeddings.cpu().numpy().astype(np.float32)

    def _score_taxonomy(self, profile: Dict[str, Any], query: BiologicalQuery) -> float:
        if not query.taxonomic_hints: return 1.0
        match_count = 0
        for hint in query.taxonomic_hints:
            if hint in profile["organism"].lower() or any(hint in t for t in profile["lineage"]):
                match_count += 1
        return match_count / len(query.taxonomic_hints)

    def _score_localization(self, profile: Dict[str, Any], query: BiologicalQuery) -> float:
        if not query.localization_hints: return 1.0
        # Check for direct or substring matches in localization strings
        match_count = 0
        for hint in query.localization_hints:
            if any(hint in loc for l in profile["locations"] for loc in l.split()):
                match_count += 1
        return match_count / len(query.localization_hints)

    def rerank_by_context(
        self, 
        records: List[Dict[str, Any]], 
        context_query: str,
        top_n: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Executes hybrid biological reranking.
        """
        if not records: return []

        try:
            # 1. Parsing & Profiling
            bio_query = self._parse_biological_intent(context_query)
            profiles = [self._extract_rich_profile(rec) for rec in records]
            
            # 2. Semantic Scoring (BioE5)
            # Build dense biological documents for each candidate
            passages = [
                f"Protein: {p['name']}. Function: {' '.join(p['functions'])}. GO: {', '.join(p['go_terms'][:10])}."
                for p in profiles
            ]
            
            query_vec = self._embed_bio_texts([context_query], is_query=True)
            doc_vecs = self._embed_bio_texts(passages, is_query=False)
            
            # Cosine similarity via dot product (E5 vectors are normalized by model or manually)
            query_vec = query_vec / np.linalg.norm(query_vec, axis=1, keepdims=True)
            doc_vecs = doc_vecs / np.linalg.norm(doc_vecs, axis=1, keepdims=True)
            semantic_scores = np.dot(doc_vecs, query_vec.T).flatten()

            # 3. Multi-Field Fusion
            reranked_list = []
            for i, (record, profile) in enumerate(zip(records, profiles)):
                comp = ScoringComponents()
                
                # Sequence: based on initial retrieval rank
                comp.sequence = 1.0 - (i / len(records))
                comp.semantic = float(semantic_scores[i])
                comp.taxonomy = self._score_taxonomy(profile, bio_query)
                comp.localization = self._score_localization(profile, bio_query)
                
                # Domain Match (Soft boost for shared EC or domains)
                if bio_query.enzyme_ids and any(ec in profile["ec_numbers"] for ec in bio_query.enzyme_ids):
                    comp.domain_architecture = 1.0
                
                # Aggregation
                comp.total = (
                    self.weights["sequence"] * comp.sequence +
                    self.weights["semantic"] * comp.semantic +
                    self.weights["taxonomy"] * comp.taxonomy +
                    self.weights["localization"] * comp.localization +
                    self.weights["domain"] * comp.domain_architecture
                )

                # Generate Explanations
                if comp.localization > 0.8: comp.explanation.append("Subcellular alignment")
                if comp.taxonomy > 0.8: comp.explanation.append("Taxonomic match")
                if comp.domain_architecture > 0.5: comp.explanation.append("Specific Enzyme Class match")
                if comp.semantic > 0.85: comp.explanation.append("Exceptional functional relevance")

                record["_rerank_score"] = comp.total
                record["_rerank_explanation"] = " | ".join(comp.explanation)
                reranked_list.append(record)

            # 4. Final Sort
            reranked_list.sort(key=lambda x: x["_rerank_score"], reverse=True)
            return reranked_list[:top_n]

        except Exception as e:
            print(f"Reranking Failure: {str(e)}")
            traceback.print_exc()
            return records[:top_n]

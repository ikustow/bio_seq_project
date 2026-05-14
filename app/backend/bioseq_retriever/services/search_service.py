import os
import sys
import faiss
import json
import numpy as np
import h5py
import asyncio
import torch
import traceback
import re
from typing import List, Tuple, Generator, Dict, Any, Optional
from dataclasses import dataclass, field
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import T5EncoderModel, T5Tokenizer, AutoModel, AutoTokenizer
from concurrent.futures import ThreadPoolExecutor

# Add parent dir to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.config import (
    DEFAULT_H5_PATH, DEFAULT_INDEX_PATH, DEFAULT_CACHE_PATH,
    DNA_H5_PATH, DNA_INDEX_PATH, DNA_CACHE_PATH,
    HNSW_M, HNSW_EF_CONSTRUCTION, HNSW_EF_SEARCH, RANDOM_SEED,
    SEARCH_SERVICE_HOST, SEARCH_SERVICE_PORT,
    PROTEIN_MODEL_NAME, DNA_MODEL_NAME, RERANK_MODEL_NAME,
    DNA_MAX_LENGTH, DEFAULT_FAISS_THREADS
)

app = FastAPI(title="Unified BioSeq Gateway Service")
executor = ThreadPoolExecutor(max_workers=8)
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# Set seed for reproducibility
np.random.seed(RANDOM_SEED)

# =============================================================================
# DATA STRUCTURES
# =============================================================================

class SearchRequest(BaseModel):
    sequence: str
    k: int = 25

class RerankRequest(BaseModel):
    records: List[Dict[str, Any]]
    context_query: str
    top_n: int = 5

@dataclass
class BiologicalQuery:
    raw_query: str
    taxonomic_hints: List[str] = field(default_factory=list)
    localization_hints: List[str] = field(default_factory=list)
    enzyme_ids: List[str] = field(default_factory=list)

@dataclass
class ScoringComponents:
    sequence: float = 0.0
    semantic: float = 0.0
    taxonomy: float = 0.0
    localization: float = 0.0
    domain_architecture: float = 0.0
    total: float = 0.0
    explanation: List[str] = field(default_factory=list)

# =============================================================================
# MODEL INITIALIZATION
# =============================================================================

# Global references
protein_model = None
protein_tokenizer = None
dna_model = None
dna_tokenizer = None
rerank_model = None
rerank_tokenizer = None

def init_models():
    global protein_model, protein_tokenizer, dna_model, dna_tokenizer, rerank_model, rerank_tokenizer
    
    print(f"Loading Protein model: {PROTEIN_MODEL_NAME}...")
    protein_tokenizer = T5Tokenizer.from_pretrained(PROTEIN_MODEL_NAME, do_lower_case=False)
    protein_model = T5EncoderModel.from_pretrained(PROTEIN_MODEL_NAME).to(device)
    protein_model.eval()

    print(f"Loading DNA model: {DNA_MODEL_NAME}...")
    dna_tokenizer = AutoTokenizer.from_pretrained(DNA_MODEL_NAME, trust_remote_code=True)
    dna_model = AutoModel.from_pretrained(DNA_MODEL_NAME, trust_remote_code=True).to(device)
    dna_model.eval()

    print(f"Loading Reranking model: {RERANK_MODEL_NAME}...")
    rerank_tokenizer = AutoTokenizer.from_pretrained(RERANK_MODEL_NAME)
    rerank_model = AutoModel.from_pretrained(RERANK_MODEL_NAME).to(device)
    rerank_model.eval()
    
    print(f"All models loaded on {device}")

init_models()

# =============================================================================
# INDEX MANAGEMENT
# =============================================================================

def iter_embeddings(h5_path: str, batch_size: int = 1000) -> Generator[Tuple[np.ndarray, List[str]], None, None]:
    with h5py.File(h5_path, 'r') as f:
        accessions = list(f.keys())
        if not accessions: raise ValueError(f"HDF5 file {h5_path} is empty.")
        dim = f[accessions[0]].shape[0]
        for i in range(0, len(accessions), batch_size):
            batch_accs = accessions[i : i + batch_size]
            batch_embeddings = np.zeros((len(batch_accs), dim), dtype=np.float32)
            for j, acc in enumerate(batch_accs):
                batch_embeddings[j] = f[acc][:]
            yield batch_embeddings, batch_accs

def build_index(h5_path: str, index_path: str, name: str) -> faiss.IndexHNSWFlat:
    index = None
    for batch_embeddings, _ in iter_embeddings(h5_path):
        dim = batch_embeddings.shape[1]
        if index is None:
            print(f"Building {name} HNSW index (M={HNSW_M}, efC={HNSW_EF_CONSTRUCTION})...")
            index = faiss.IndexHNSWFlat(dim, HNSW_M, faiss.METRIC_INNER_PRODUCT)
            index.hnsw.efConstruction = HNSW_EF_CONSTRUCTION
        faiss.normalize_L2(batch_embeddings)
        index.add(batch_embeddings)
    if index_path: faiss.write_index(index, index_path)
    return index

def load_or_create_index(h5_path: str, index_path: str, cache_path: str, name: str) -> Tuple[faiss.IndexHNSWFlat, List[str]]:
    if os.path.exists(index_path) and os.path.exists(cache_path):
        print(f"Loading existing {name} index...")
        index = faiss.read_index(index_path)
        index.hnsw.efSearch = HNSW_EF_SEARCH
        with open(cache_path, 'r') as f: accessions = json.load(f)
        return index, accessions
    
    index = build_index(h5_path, index_path, name)
    index.hnsw.efSearch = HNSW_EF_SEARCH
    with h5py.File(h5_path, 'r') as f: accessions = list(f.keys())
    with open(cache_path, 'w') as f: json.dump(accessions, f)
    return index, accessions

print("Initializing FAISS indices...")
protein_index, protein_accessions = load_or_create_index(DEFAULT_H5_PATH, DEFAULT_INDEX_PATH, DEFAULT_CACHE_PATH, "Protein")
# DNA index is optional: many deployments only ship the protein corpus.
# We log a warning and disable the /search/dna endpoint instead of crashing
# the whole service when the .h5 isn't present.
if os.path.exists(DNA_H5_PATH) or (os.path.exists(DNA_INDEX_PATH) and os.path.exists(DNA_CACHE_PATH)):
    dna_index, dna_accessions = load_or_create_index(DNA_H5_PATH, DNA_INDEX_PATH, DNA_CACHE_PATH, "DNA")
else:
    print(f"DNA index disabled: {DNA_H5_PATH} not found. /search/dna will return 503.")
    dna_index, dna_accessions = None, []
print("Indices ready.")

# =============================================================================
# INTERNAL LOGIC (CORE)
# =============================================================================

# --- Embedding ---

def _embed_protein(sequence: str) -> np.ndarray:
    processed_seq = " ".join(list(sequence.upper()))
    inputs = protein_tokenizer(processed_seq, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = protein_model(**inputs)
        residue_embeddings = outputs.last_hidden_state.squeeze(0)
    return residue_embeddings.mean(dim=0).cpu().numpy().astype(np.float32)

def _embed_dna(sequence: str) -> np.ndarray:
    inputs = dna_tokenizer(sequence, return_tensors="pt", truncation=True, max_length=DNA_MAX_LENGTH).to(device)
    with torch.no_grad():
        outputs = dna_model(**inputs)
        mean_pooled = outputs.last_hidden_state.mean(dim=1).squeeze()
    return mean_pooled.cpu().numpy().astype(np.float32)

def _embed_rerank_texts(texts: List[str], is_query: bool = False) -> np.ndarray:
    prefix = "query: " if is_query else "passage: "
    prefixed_texts = [f"{prefix}{t}" for t in texts]
    inputs = rerank_tokenizer(prefixed_texts, padding=True, truncation=True, max_length=512, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = rerank_model(**inputs)
        embeddings = outputs.last_hidden_state.mean(dim=1)
    return embeddings.cpu().numpy().astype(np.float32)

# --- Search ---

def _perform_vector_search(index, query_emb: np.ndarray, k: int):
    query_vec = query_emb.reshape(1, -1)
    faiss.normalize_L2(query_vec)
    faiss.omp_set_num_threads(1) # Strict reproducibility
    try:
        distances, indices = index.search(query_vec, k)
    finally:
        faiss.omp_set_num_threads(DEFAULT_FAISS_THREADS)
    return distances, indices

# --- Reranking Helpers ---

def _parse_biological_intent(prompt: str) -> BiologicalQuery:
    query = BiologicalQuery(raw_query=prompt)
    prompt_lower = prompt.lower()
    taxa_map = {"bacterial": "bacteria", "human": "homo sapiens", "mammal": "mammalia", "viral": "viruses"}
    query.taxonomic_hints = [v for k, v in taxa_map.items() if k in prompt_lower]
    query.localization_hints = [l for l in ["membrane", "secreted", "cytoplasm", "nucleus", "mitochondrion"] if l in prompt_lower]
    query.enzyme_ids = re.findall(r"\b\d+\.\d+\.\d+\.\d+\b", prompt)
    return query

def _extract_rich_profile(record: Dict[str, Any]) -> Dict[str, Any]:
    lineage = []
    for taxon in record.get('organism', {}).get('lineage', []):
        if isinstance(taxon, dict):
            lineage.append(taxon.get('scientificName', '').lower())
        elif isinstance(taxon, str):
            lineage.append(taxon.lower())

    profile = {
        "name": record.get('proteinDescription', {}).get('recommendedName', {}).get('fullName', {}).get('value', ''),
        "organism": record.get('organism', {}).get('scientificName', ''),
        "lineage": lineage,
        "locations": [], "functions": [], "go_terms": [], "domains": [], "ec_numbers": []
    }
    for comment in record.get('comments', []):
        if comment.get('commentType') == 'SUBCELLULAR_LOCATION':
            profile["locations"].extend([l.get('location', {}).get('value', '').lower() for l in comment.get('locations', [])])
        if comment.get('commentType') == 'FUNCTION':
            profile["functions"].extend([t.get('value', '') for t in comment.get('note', {}).get('texts', [])])
    for xref in record.get('uniProtKBCrossReferences', []):
        db = xref.get('database')
        if db == 'EC': profile["ec_numbers"].append(xref.get('id'))
        elif db == 'GO': profile["go_terms"].append(xref.get('properties', [{}])[0].get('value', ''))
        elif db in ['Pfam', 'InterPro']: profile["domains"].append(xref.get('properties', [{}])[0].get('value', ''))
    return profile

# =============================================================================
# ENDPOINTS
# =============================================================================

@app.post("/search/protein")
async def search_protein(request: SearchRequest):
    try:
        loop = asyncio.get_event_loop()
        emb = await loop.run_in_executor(executor, _embed_protein, request.sequence)
        dist, idxs = await loop.run_in_executor(executor, _perform_vector_search, protein_index, emb, request.k)
        results = [{"accession": protein_accessions[i], "score": float(dist[0][j])} for j, i in enumerate(idxs[0]) if i != -1]
        return {"results": results}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/search/dna")
async def search_dna(request: SearchRequest):
    if dna_index is None:
        raise HTTPException(
            status_code=503,
            detail=f"DNA index not loaded ({DNA_H5_PATH} missing). Provide BIOSEQ_DNA_H5_PATH to enable.",
        )
    try:
        loop = asyncio.get_event_loop()
        emb = await loop.run_in_executor(executor, _embed_dna, request.sequence)
        dist, idxs = await loop.run_in_executor(executor, _perform_vector_search, dna_index, emb, request.k)
        results = [{"accession": dna_accessions[i], "score": float(dist[0][j])} for j, i in enumerate(idxs[0]) if i != -1]
        return {"results": results}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/rerank")
async def rerank(request: RerankRequest):
    try:
        loop = asyncio.get_event_loop()
        bio_query = _parse_biological_intent(request.context_query)
        profiles = [_extract_rich_profile(rec) for rec in request.records]
        
        passages = [f"Protein: {p['name']}. Function: {' '.join(p['functions'])}. GO: {', '.join(p['go_terms'][:10])}." for p in profiles]
        query_vec = await loop.run_in_executor(executor, _embed_rerank_texts, [request.context_query], True)
        doc_vecs = await loop.run_in_executor(executor, _embed_rerank_texts, passages, False)
        
        query_vec = query_vec / np.linalg.norm(query_vec, axis=1, keepdims=True)
        doc_vecs = doc_vecs / np.linalg.norm(doc_vecs, axis=1, keepdims=True)
        semantic_scores = np.dot(doc_vecs, query_vec.T).flatten()

        weights = {"sequence": 0.20, "semantic": 0.35, "taxonomy": 0.15, "localization": 0.20, "domain": 0.10}
        reranked_list = []
        for i, (record, profile) in enumerate(zip(request.records, profiles)):
            comp = ScoringComponents()
            comp.sequence = 1.0 - (i / len(request.records))
            comp.semantic = float(semantic_scores[i])
            
            if bio_query.taxonomic_hints:
                comp.taxonomy = sum(1 for h in bio_query.taxonomic_hints if h in profile["organism"].lower() or any(h in t for t in profile["lineage"])) / len(bio_query.taxonomic_hints)
            else: comp.taxonomy = 1.0
            
            if bio_query.localization_hints:
                comp.localization = sum(1 for h in bio_query.localization_hints if any(h in loc for l in profile["locations"] for loc in l.split())) / len(bio_query.localization_hints)
            else: comp.localization = 1.0
                
            if bio_query.enzyme_ids and any(ec in profile["ec_numbers"] for ec in bio_query.enzyme_ids):
                comp.domain_architecture = 1.0
                
            comp.total = sum(weights[k] * getattr(comp, k == "domain" and "domain_architecture" or k) for k in weights)
            
            if comp.localization > 0.8: comp.explanation.append("Subcellular alignment")
            if comp.taxonomy > 0.8: comp.explanation.append("Taxonomic match")
            if comp.domain_architecture > 0.5: comp.explanation.append("Specific Enzyme Class match")
            if comp.semantic > 0.85: comp.explanation.append("Exceptional functional relevance")

            record["_rerank_score"] = comp.total
            record["_rerank_explanation"] = " | ".join(comp.explanation)
            reranked_list.append(record)

        reranked_list.sort(key=lambda x: x["_rerank_score"], reverse=True)
        return {"results": reranked_list[:request.top_n]}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SEARCH_SERVICE_HOST, port=SEARCH_SERVICE_PORT)

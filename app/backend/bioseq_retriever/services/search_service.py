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
    DNA_MAX_LENGTH, DEFAULT_FAISS_THREADS, H5_BATCH_SIZE,
    RERANK_LAMBDA
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

def iter_embeddings(h5_path: str, batch_size: int = H5_BATCH_SIZE) -> Generator[Tuple[np.ndarray, List[str]], None, None]:
    # Use libver='latest' to handle modern HDF5 layout messages
    with h5py.File(h5_path, 'r', libver='latest') as f:
        # Filter keys to ensure we only process actual datasets
        all_keys = list(f.keys())
        accessions = [k for k in all_keys if isinstance(f[k], h5py.Dataset)]

        if not accessions:
            raise ValueError(f"HDF5 file {h5_path} contains no valid datasets.")

        # Infer dimension from the first valid dataset
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
    if index_path:
        faiss.write_index(index, index_path)
    return index

def load_or_create_index(h5_path: str, index_path: str, cache_path: str, name: str) -> Tuple[faiss.IndexHNSWFlat, List[str]]:
    if os.path.exists(index_path) and os.path.exists(cache_path):
        print(f"Loading existing {name} index...")
        index = faiss.read_index(index_path)
        index.hnsw.efSearch = HNSW_EF_SEARCH
        with open(cache_path, 'r') as f:
            accessions = json.load(f)
        return index, accessions
    
    # If index or cache missing, build it
    index = build_index(h5_path, index_path, name)
    index.hnsw.efSearch = HNSW_EF_SEARCH

    # Extract and cache accessions (ensuring we only cache dataset keys)
    with h5py.File(h5_path, 'r', libver='latest') as f:
        accessions = [k for k in f.keys() if isinstance(f[k], h5py.Dataset)]

    with open(cache_path, 'w') as f:
        json.dump(accessions, f)

    return index, accessions

print("Initializing FAISS indices...")
protein_index, protein_accessions = load_or_create_index(DEFAULT_H5_PATH, DEFAULT_INDEX_PATH, DEFAULT_CACHE_PATH, "Protein")
dna_index, dna_accessions = load_or_create_index(DNA_H5_PATH, DNA_INDEX_PATH, DNA_CACHE_PATH, "DNA")

print("Indices ready.")

# =============================================================================
# INTERNAL LOGIC (CORE)
# =============================================================================

# --- Embedding ---

def _embed_protein(sequence: str) -> np.ndarray:
    # ProtT5 reference recipe: substitute rare/ambiguous residues with X
    seq = re.sub(r"[UZOB]", "X", sequence.upper())
    processed_seq = " ".join(list(seq))
    
    inputs = protein_tokenizer(processed_seq, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = protein_model(**inputs)
        # Exclude the trailing </s> (EOS) token from the mean pool to match bio_embeddings distribution
        residue_embeddings = outputs.last_hidden_state[0, :len(seq), :]
        
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

def _format_record_for_embedding(record: Dict[str, Any]) -> str:
    """Creates a clean text summary of a UniProt record for semantic embedding."""
    name = record.get('proteinDescription', {}).get('recommendedName', {}).get('fullName', {}).get('value', 'N/A')
    organism = record.get('organism', {}).get('scientificName', 'N/A')
    
    # Extract function comments
    functions = []
    for comment in record.get('comments', []):
        if comment.get('commentType') == 'FUNCTION':
            functions.extend([t.get('value', '') for t in comment.get('texts', [])])
    func_text = " ".join(functions)
    
    # Extract keywords
    keywords = ", ".join([k.get('value', '') for k in record.get('keywords', [])])
    
    return f"Protein: {name}. Organism: {organism}. Function: {func_text}. Keywords: {keywords}."

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
        
        # 1. Prepare text passages for candidates
        passages = [_format_record_for_embedding(rec) for rec in request.records]
        
        # 2. Embed query and passages
        query_vec = await loop.run_in_executor(executor, _embed_rerank_texts, [request.context_query], True)
        doc_vecs = await loop.run_in_executor(executor, _embed_rerank_texts, passages, False)
        
        # 3. Calculate semantic scores (Cosine Similarity)
        query_vec = query_vec / (np.linalg.norm(query_vec, axis=1, keepdims=True) + 1e-9)
        doc_vecs = doc_vecs / (np.linalg.norm(doc_vecs, axis=1, keepdims=True) + 1e-9)
        semantic_scores = np.dot(doc_vecs, query_vec.T).flatten()

        # 4. Margin-Aware Adaptive Fusion
        # Retrieval scores are stored in '_search_score'. We assume records are sorted by this.
        retrieval_scores = np.array([r.get("_search_score", 0.0) for r in request.records])
        
        # Calculate local margins: gap between current and next candidate
        # For the last element, we repeat the previous margin or use 0
        margins = np.abs(np.diff(retrieval_scores))
        if len(margins) > 0:
            margins = np.append(margins, margins[-1])
        else:
            margins = np.array([0.0])
        
        # Adaptive weight logic: 
        # tau is derived from the mean of positive margins to calibrate to the current data distribution
        tau = np.mean(margins[margins > 0]) + 1e-6 if np.any(margins > 0) else 0.1
        
        # Exponential decay: large gaps -> small weights (preserves original ranking)
        # Small gaps (near-ties) -> large weights (allows reranker to break ties)
        adaptive_weights = np.exp(-margins / tau)
        adaptive_weights = np.clip(adaptive_weights, 0, 1)

        # Internal sensitivity constant for the rerank signal
        LAMBDA = RERANK_LAMBDA

        reranked_list = []
        for i, record in enumerate(request.records):
            # Combined score formula: Retrieval + gated Rerank signal
            # This approximates lexicographic ranking while allowing smooth tie-breaking
            final_score = retrieval_scores[i] + (LAMBDA * adaptive_weights[i] * semantic_scores[i])
            
            record["_search_score"] = float(final_score)
            reranked_list.append(record)

        # 5. Final Sort by the new margin-aware score
        reranked_list.sort(key=lambda x: x["_search_score"], reverse=True)
        return {"results": reranked_list[:request.top_n]}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SEARCH_SERVICE_HOST, port=SEARCH_SERVICE_PORT)

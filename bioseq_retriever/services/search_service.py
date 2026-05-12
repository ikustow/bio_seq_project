import os
import sys
import faiss
import json
import numpy as np
import h5py
import asyncio
import torch
import traceback
from typing import List, Tuple, Generator
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import T5EncoderModel, T5Tokenizer
from concurrent.futures import ThreadPoolExecutor

# Add parent dir to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.config import (
    DEFAULT_H5_PATH, DEFAULT_INDEX_PATH, DEFAULT_CACHE_PATH,
    HNSW_M, HNSW_EF_CONSTRUCTION, HNSW_EF_SEARCH, RANDOM_SEED,
    SEARCH_SERVICE_HOST, SEARCH_SERVICE_PORT, MODEL_NAME,
    DEFAULT_FAISS_THREADS
)

app = FastAPI(title="Unified BioSeq Search Service")
executor = ThreadPoolExecutor(max_workers=4)

# Set seed for reproducibility
np.random.seed(RANDOM_SEED)

# --- Model Initialization ---
print(f"Loading ProtT5 model: {MODEL_NAME}...")
tokenizer = T5Tokenizer.from_pretrained(MODEL_NAME, do_lower_case=False)
model = T5EncoderModel.from_pretrained(MODEL_NAME)
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
model.to(device)
model.eval()
print(f"Model loaded on {device}")

# --- Index Management Logic ---

def iter_embeddings(h5_path: str, batch_size: int = 1000) -> Generator[Tuple[np.ndarray, List[str]], None, None]:
    with h5py.File(h5_path, 'r') as f:
        accessions = list(f.keys())
        if not accessions:
            raise ValueError("The HDF5 file is empty.")
            
        dim = f[accessions[0]].shape[0]
        for i in range(0, len(accessions), batch_size):
            batch_accs = accessions[i : i + batch_size]
            batch_embeddings = np.zeros((len(batch_accs), dim), dtype=np.float32)
            for j, acc in enumerate(batch_accs):
                batch_embeddings[j] = f[acc][:]
            yield batch_embeddings, batch_accs

def build_index(h5_path: str, index_path: str = None) -> faiss.IndexHNSWFlat:
    index = None
    # Multi-threaded construction is allowed
    
    for batch_embeddings, _ in iter_embeddings(h5_path):
        dim = batch_embeddings.shape[1]
        if index is None:
            print(f"Initializing HNSW index (M={HNSW_M}, efC={HNSW_EF_CONSTRUCTION}) with dimension {dim}...")
            index = faiss.IndexHNSWFlat(dim, HNSW_M, faiss.METRIC_INNER_PRODUCT)
            index.hnsw.efConstruction = HNSW_EF_CONSTRUCTION
            
        faiss.normalize_L2(batch_embeddings)
        index.add(batch_embeddings)
    
    if index_path and index:
        faiss.write_index(index, index_path)
        
    return index

def get_or_create_index(h5_path: str, index_path: str, accessions_cache_path: str = None) -> Tuple[faiss.IndexHNSWFlat, List[str]]:
    if os.path.exists(index_path) and (accessions_cache_path and os.path.exists(accessions_cache_path)):
        print(f"Loading existing index from {index_path}...")
        index = faiss.read_index(index_path)
        index.hnsw.efSearch = HNSW_EF_SEARCH
        with open(accessions_cache_path, 'r') as f:
            accessions = json.load(f)
        return index, accessions
    
    index = build_index(h5_path, index_path)
    index.hnsw.efSearch = HNSW_EF_SEARCH
    
    with h5py.File(h5_path, 'r') as f:
        accessions = list(f.keys())
    
    if accessions_cache_path:
        with open(accessions_cache_path, 'w') as f:
            json.dump(accessions, f)
            
    return index, accessions

# --- Global Service State ---
print("Loading FAISS index and accessions...")
index, accessions = get_or_create_index(DEFAULT_H5_PATH, DEFAULT_INDEX_PATH, DEFAULT_CACHE_PATH)
print(f"Index loaded. {len(accessions)} records available.")

class SearchRequest(BaseModel):
    sequence: str
    k: int = 25

def _embed(sequence: str) -> np.ndarray:
    try:
        print(f"[DEBUG] Embedding request: length={len(sequence)}")
        processed_seq = " ".join(list(sequence.upper()))
        inputs = tokenizer(processed_seq, return_tensors="pt").to(device)
        with torch.no_grad():
            outputs = model(**inputs)
            residue_embeddings = outputs.last_hidden_state.squeeze(0)
        protein_embedding = residue_embeddings.mean(dim=0).cpu().numpy()
        print(f"[DEBUG] Embedding success: shape={protein_embedding.shape}")
        return protein_embedding.astype(np.float32)
    except Exception as e:
        print(f"[ERROR] Embedding failed: {str(e)}")
        traceback.print_exc()
        raise

def _perform_search(query_emb: np.ndarray, k: int):
    try:
        query_vec = query_emb.reshape(1, -1)
        faiss.normalize_L2(query_vec)
        
        print(f"[DEBUG] Search starting: query_vec.shape={query_vec.shape}, index.d={index.d}, k={k}")
        
        # Temporarily set to single thread for reproducibility
        faiss.omp_set_num_threads(1)
        try:
            distances, indices = index.search(query_vec, k)
        finally:
            # Restore to default FAISS threads
            faiss.omp_set_num_threads(DEFAULT_FAISS_THREADS)
            
        print(f"[DEBUG] Search complete: matches found={len(indices[0])}")
        return distances, indices
    except Exception as e:
        print(f"[ERROR] FAISS search failed: {str(e)}")
        traceback.print_exc()
        raise

@app.post("/search")
async def search(request: SearchRequest):
    try:
        loop = asyncio.get_event_loop()
        
        # 1. Embed
        query_emb = await loop.run_in_executor(executor, _embed, request.sequence)
        
        # 2. Search
        distances, indices = await loop.run_in_executor(executor, _perform_search, query_emb, request.k)
        
        results = [
            {"accession": accessions[idx], "score": float(distances[0][i])}
            for i, idx in enumerate(indices[0])
            if idx != -1
        ]
        return {"results": results}
    except Exception as e:
        print("--- UNIFIED SEARCH SERVICE EXCEPTION ---")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Search Service Error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SEARCH_SERVICE_HOST, port=SEARCH_SERVICE_PORT)

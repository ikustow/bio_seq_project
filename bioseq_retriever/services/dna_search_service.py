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
from transformers import AutoModel, AutoTokenizer
from concurrent.futures import ThreadPoolExecutor

# Add parent dir to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.config import (
    DNA_H5_PATH, DNA_INDEX_PATH, DNA_CACHE_PATH,
    HNSW_M, HNSW_EF_CONSTRUCTION, HNSW_EF_SEARCH, RANDOM_SEED,
    DNA_SEARCH_SERVICE_HOST, DNA_SEARCH_SERVICE_PORT, 
    DNA_MODEL_NAME, DNA_MAX_LENGTH, DEFAULT_FAISS_THREADS
)

app = FastAPI(title="Unified DNA Search Service")
executor = ThreadPoolExecutor(max_workers=4)

# Set seed for reproducibility
np.random.seed(RANDOM_SEED)

# --- Model Initialization ---
print(f"Loading HyenaDNA model: {DNA_MODEL_NAME}...")
tokenizer = AutoTokenizer.from_pretrained(DNA_MODEL_NAME, trust_remote_code=True)
model = AutoModel.from_pretrained(DNA_MODEL_NAME, trust_remote_code=True)
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
model.to(device)
model.eval()
print(f"Model loaded on {device}")

# --- DNA Index Management ---

def iterate_dna_embeddings(h5_source: str, chunk_size: int = 1000) -> Generator[Tuple[np.ndarray, List[str]], None, None]:
    """Generator to yield DNA embeddings and their accessions from HDF5."""
    with h5py.File(h5_source, 'r') as h5_file:
        keys = list(h5_file.keys())
        if not keys:
            raise RuntimeError("DNA HDF5 file contains no data.")
        
        vector_dim = h5_file[keys[0]].shape[0]
        for i in range(0, len(keys), chunk_size):
            slice_keys = keys[i : i + chunk_size]
            embeddings_batch = np.zeros((len(slice_keys), vector_dim), dtype=np.float32)
            for j, key in enumerate(slice_keys):
                embeddings_batch[j] = h5_file[key][:]
            yield embeddings_batch, slice_keys

def construct_dna_index(h5_source: str, output_path: str = None) -> faiss.IndexHNSWFlat:
    """Constructs a high-accuracy HNSW index for DNA sequences."""
    dna_idx = None
    # Use default threads (multi-threaded) for construction
    
    for batch_data, _ in iterate_dna_embeddings(h5_source):
        dim = batch_data.shape[1]
        if dna_idx is None:
            print(f"Constructing HNSW index (M={HNSW_M}, efC={HNSW_EF_CONSTRUCTION}) for DNA, dimension {dim}...")
            dna_idx = faiss.IndexHNSWFlat(dim, HNSW_M, faiss.METRIC_INNER_PRODUCT)
            dna_idx.hnsw.efConstruction = HNSW_EF_CONSTRUCTION
            
        faiss.normalize_L2(batch_data)
        dna_idx.add(batch_data)
        
    if output_path and dna_idx:
        faiss.write_index(dna_idx, output_path)
    return dna_idx

def load_or_init_dna_index(h5_path: str, idx_path: str, cache_path: str = None) -> Tuple[faiss.IndexHNSWFlat, List[str]]:
    """Loads existing DNA index or builds a new one."""
    if os.path.exists(idx_path) and (cache_path and os.path.exists(cache_path)):
        print(f"Loading existing DNA index: {idx_path}")
        dna_idx = faiss.read_index(idx_path)
        dna_idx.hnsw.efSearch = HNSW_EF_SEARCH
        with open(cache_path, 'r') as f:
            accessions = json.load(f)
        return dna_idx, accessions
    
    dna_idx = construct_dna_index(h5_path, idx_path)
    dna_idx.hnsw.efSearch = HNSW_EF_SEARCH
    
    with h5py.File(h5_path, 'r') as f:
        accessions = list(f.keys())
    
    if cache_path:
        with open(cache_path, 'w') as f:
            json.dump(accessions, f)
    return dna_idx, accessions

# --- Service Global State ---
print("Initializing DNA Search components...")
dna_index, dna_accessions = load_or_init_dna_index(DNA_H5_PATH, DNA_INDEX_PATH, DNA_CACHE_PATH)
print(f"DNA Retrieval System Ready. {len(dna_accessions)} genes indexed.")

class DNARequest(BaseModel):
    sequence: str
    k: int = 25

def _process_dna_embedding(dna_string: str) -> np.ndarray:
    """Computes a mean-pooled embedding for a DNA sequence using HyenaDNA."""
    try:
        print(f"[DEBUG] DNA Embedding: length={len(dna_string)}")
        encoded_input = tokenizer(
            dna_string, 
            return_tensors="pt", 
            truncation=True, 
            max_length=DNA_MAX_LENGTH, 
            padding=False
        ).to(device)
        
        with torch.no_grad():
            net_output = model(**encoded_input)
            # HyenaDNA output mean pooling across sequence dimension
            state_vecs = net_output.last_hidden_state
            mean_pooled = torch.mean(state_vecs, dim=1).squeeze()
            
        final_emb = mean_pooled.cpu().numpy().astype(np.float32)
        print(f"[DEBUG] DNA Embedding complete: shape={final_emb.shape}")
        return final_emb
    except Exception as e:
        print(f"[ERROR] DNA Embedding failed: {str(e)}")
        traceback.print_exc()
        raise

def _run_vector_search(query_vec: np.ndarray, top_k: int):
    """Executes a single-threaded FAISS search for DNA."""
    try:
        query_reshaped = query_vec.reshape(1, -1)
        faiss.normalize_L2(query_reshaped)
        
        print(f"[DEBUG] DNA Search: query_vec={query_reshaped.shape}, d={dna_index.d}, k={top_k}")
        
        # Enforce single-threaded for search phase
        faiss.omp_set_num_threads(1)
        try:
            scores, indices = dna_index.search(query_reshaped, top_k)
        finally:
            # Revert to system default for background tasks
            faiss.omp_set_num_threads(DEFAULT_FAISS_THREADS)
            
        print(f"[DEBUG] DNA Search done: results={len(indices[0])}")
        return scores, indices
    except Exception as e:
        print(f"[ERROR] DNA Vector Search failed: {str(e)}")
        traceback.print_exc()
        raise

@app.post("/search")
async def dna_search_endpoint(request: DNARequest):
    """Unified endpoint for DNA sequence retrieval."""
    try:
        event_loop = asyncio.get_event_loop()
        
        # 1. Generate Vector
        vector = await event_loop.run_in_executor(executor, _process_dna_embedding, request.sequence)
        
        # 2. Search FAISS
        distances, matches = await event_loop.run_in_executor(executor, _run_vector_search, vector, request.k)
        
        results_list = [
            {"accession": dna_accessions[idx], "score": float(distances[0][i])}
            for i, idx in enumerate(matches[0])
            if idx != -1
        ]
        return {"results": results_list}
    except Exception as exc:
        print("--- DNA SEARCH SERVICE EXCEPTION ---")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"DNA Search Error: {str(exc)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=DNA_SEARCH_SERVICE_HOST, port=DNA_SEARCH_SERVICE_PORT)

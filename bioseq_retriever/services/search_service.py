import os
import sys
import faiss
import json
import numpy as np
from typing import List, Tuple
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Add parent dir to path to import src if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import DEFAULT_H5_PATH, DEFAULT_INDEX_PATH, DEFAULT_CACHE_PATH
from src.embeddings import get_or_create_index

app = FastAPI(title="BioSeq Search Service")

class SearchRequest(BaseModel):
    embedding: List[float]
    k: int = 25

# --- Index Loading (Global Singleton in process) ---
print("Loading FAISS index and accessions...")
index, accessions = get_or_create_index(DEFAULT_H5_PATH, DEFAULT_INDEX_PATH, DEFAULT_CACHE_PATH)
print(f"Index loaded. {len(accessions)} records available.")

@app.post("/search")
async def search(request: SearchRequest):
    try:
        query_vec = np.array([request.embedding], dtype=np.float32)
        faiss.normalize_L2(query_vec)
        
        distances, indices = index.search(query_vec, request.k)
        
        results = [
            {"accession": accessions[idx], "score": float(distances[0][i])}
            for i, idx in enumerate(indices[0])
            if idx != -1
        ]
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)

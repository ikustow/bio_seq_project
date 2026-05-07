from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
from src.minhash import OptimizedMinHash
import json
import numpy as np

app = FastAPI(title="BioSeq MinHash Anchor Service")

# Load precomputed MinHash signatures from graph_core output
SIGNATURE_PATH = "data/minhash_signatures.json"

with open(SIGNATURE_PATH, 'r') as f:
    signatures_db = json.load(f)

minhasher = OptimizedMinHash()

class AnchorRequest(BaseModel):
    sequence: str

@app.post("/find_anchor")
async def find_anchor(request: AnchorRequest):
    query_sig = minhasher.compute_signature(request.sequence)
    query_sig_arr = np.array(query_sig)
    
    best_accession = None
    max_similarity = -1.0
    
    for item in signatures_db:
        # Calculate Jaccard similarity: intersection / union
        # For MinHash signatures of equal size, this is (matching elements) / num_perm
        target_sig_arr = np.array(item["signature"])
        similarity = np.mean(query_sig_arr == target_sig_arr)
        
        if similarity > max_similarity:
            max_similarity = similarity
            best_accession = item["accession"]
            
    return {"anchor_accession": best_accession, "similarity": float(max_similarity)}

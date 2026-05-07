import os
import sys
import torch
import numpy as np
from typing import List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import T5EncoderModel, T5Tokenizer

# Add parent dir to path to import src if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = FastAPI(title="BioSeq Embedding Service")

class SequenceRequest(BaseModel):
    sequence: str

class BatchSequenceRequest(BaseModel):
    sequences: List[str]

# --- Model Loading (Global Singleton in process) ---
MODEL_NAME = "Rostlab/prot_t5_xl_uniref50"
print(f"Loading ProtT5 model: {MODEL_NAME}...")
tokenizer = T5Tokenizer.from_pretrained(MODEL_NAME, do_lower_case=False)
model = T5EncoderModel.from_pretrained(MODEL_NAME)
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
model.to(device)
model.eval()
print(f"Model loaded on {device}")

def _embed(sequence: str) -> np.ndarray:
    processed_seq = " ".join(list(sequence.upper()))
    inputs = tokenizer(processed_seq, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model(**inputs)
        residue_embeddings = outputs.last_hidden_state.squeeze(0)
    return residue_embeddings.mean(dim=0).cpu().numpy().astype(np.float32)

@app.post("/embed")
async def embed_single(request: SequenceRequest):
    try:
        vec = _embed(request.sequence)
        return {"embedding": vec.tolist()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/embed_batch")
async def embed_batch(request: BatchSequenceRequest):
    try:
        results = [_embed(seq).tolist() for seq in request.sequences]
        return {"embeddings": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

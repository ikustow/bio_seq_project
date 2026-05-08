import faiss
import numpy as np
import torch
from typing import List, Tuple, Optional
from src.config import USE_SERVICES, EMBEDDING_SERVICE_URL, SEARCH_SERVICE_URL
from src.api_client import default_api_client

# Delay import to avoid memory usage if not using local mode
_model = None
_tokenizer = None
_device = None

def get_prottrans_embedder(model_name: str = "Rostlab/prot_t5_xl_uniref50"):
    global _model, _tokenizer, _device
    if USE_SERVICES:
        return None, None, None # Placeholders

    if _model is None:
        from transformers import T5EncoderModel, T5Tokenizer
        print(f"Loading local model {model_name}...")
        _tokenizer = T5Tokenizer.from_pretrained(model_name, do_lower_case=False)
        _model = T5EncoderModel.from_pretrained(model_name)
        _device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        _model.to(_device)
        _model.eval()
    
    return _model, _tokenizer, _device

def embed_sequence(sequence: str, model=None, tokenizer=None, device=None) -> np.ndarray:
    if USE_SERVICES:
        response = default_api_client.request_with_retry(
            "POST", f"{EMBEDDING_SERVICE_URL}/embed", 
            json={"sequence": sequence}
        )
        return np.array(response.json()["embedding"], dtype=np.float32)

    # Local mode (lazy loaded via get_prottrans_embedder)
    if model is None:
        model, tokenizer, device = get_prottrans_embedder()

    processed_seq = " ".join(list(sequence.upper()))
    inputs = tokenizer(processed_seq, return_tensors="pt").to(device)
    
    with torch.no_grad():
        outputs = model(**inputs)
        residue_embeddings = outputs.last_hidden_state.squeeze(0)
        
    protein_embedding = residue_embeddings.mean(dim=0).cpu().numpy()
    return protein_embedding.astype(np.float32)

def search_top_k(
    query_sequence: str, 
    embedder_tools: Optional[Tuple], 
    index: Optional[faiss.Index], 
    accession_list: Optional[List[str]], 
    k: int = 25
) -> List[Tuple[str, float]]:
    
    print(f"Embedding query sequence (length {len(query_sequence)})...")
    query_emb = embed_sequence(query_sequence, *embedder_tools if embedder_tools else (None, None, None))
    
    if USE_SERVICES:
        response = default_api_client.request_with_retry(
            "POST", f"{SEARCH_SERVICE_URL}/search",
            json={"embedding": query_emb.tolist(), "k": k}
        )
        results = response.json()["results"]
        return [(r["accession"], r["score"]) for r in results]

    # Local mode
    query_emb = query_emb.reshape(1, -1) 
    faiss.normalize_L2(query_emb)
    
    print(f"Searching index for top {k} matches...")

    # Ensure determinism by temporarily setting search to single thread.
    # The threading API requires OpenMP and is missing on some faiss-cpu
    # builds (notably older Windows wheels); degrade gracefully when so —
    # search still works, but multi-thread variance becomes possible.
    _get_threads = getattr(faiss, "get_num_threads", None)
    _set_threads = getattr(faiss, "set_num_threads", None)
    original_num_threads = _get_threads() if _get_threads else None
    if _set_threads:
        _set_threads(1)
    try:
        distances, indices = index.search(query_emb, k)
    finally:
        if _set_threads and original_num_threads is not None:
            _set_threads(original_num_threads)
    
    return [(accession_list[idx], float(distances[0][i])) for i, idx in enumerate(indices[0])]

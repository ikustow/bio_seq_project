import faiss
import numpy as np
from typing import List, Tuple, Any

def get_similarity_score(similarity_val: float) -> float:
    """
    Normalizes a similarity score to a 0-1 scale.
    """
    return float(max(0.0, min(1.0, similarity_val)))

def rank_sequences(matches: List[Tuple[Any, float]]) -> List[Tuple[Any, float]]:
    """
    Ranks items by similarity score.
    """
    return sorted(matches, key=lambda x: x[1], reverse=True)

def perform_similarity_search(
    query_emb: np.ndarray, 
    document_embs: np.ndarray, 
    top_k: int = 5
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Performs a local similarity search using FAISS.
    Expects normalized embeddings for cosine similarity.
    """
    dim = query_emb.shape[1]
    # Use FlatIP for exact search on small batches (like top 25)
    index = faiss.IndexFlatIP(dim)
    index.add(document_embs)
    
    distances, indices = index.search(query_emb, top_k)
    return distances[0], indices[0]

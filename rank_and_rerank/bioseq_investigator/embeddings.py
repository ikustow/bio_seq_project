import h5py
import faiss
import numpy as np
from typing import List, Tuple

def load_embeddings_and_build_index(h5_path: str) -> Tuple[faiss.IndexHNSWFlat, List[str]]:
    """
    Loads protein embeddings from an HDF5 file and inserts them into a FAISS HNSW index.
    Uses Cosine distance (Inner Product of normalized vectors).
    
    :param h5_path: Path to the .h5 file containing embeddings.
    :return: A tuple of (FAISS index, list of accession numbers).
    """
    print(f"Loading embeddings from {h5_path}...")
    with h5py.File(h5_path, 'r') as f:
        accessions = list(f.keys())
        if not accessions:
            raise ValueError("The HDF5 file is empty.")
            
        dim = f[accessions[0]].shape[0]
        print(f"Detected embedding dimension: {dim}")
        
        print(f"Reading {len(accessions)} embeddings into memory...")
        all_embeddings = np.zeros((len(accessions), dim), dtype=np.float32)
        for i, acc in enumerate(accessions):
            all_embeddings[i] = f[acc][:]
            
        print("Normalizing embeddings for cosine similarity...")
        faiss.normalize_L2(all_embeddings)
        
        # Initialize HNSW index with Inner Product
        index = faiss.IndexHNSWFlat(dim, 32, faiss.METRIC_INNER_PRODUCT)
        
        print("Adding embeddings to HNSW index...")
        index.add(all_embeddings)
        
    return index, accessions

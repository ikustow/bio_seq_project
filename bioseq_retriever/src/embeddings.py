import h5py
import faiss
import numpy as np
import os
import pickle
from typing import List, Tuple

def load_embeddings(h5_path: str) -> Tuple[np.ndarray, List[str]]:
    """
    Loads protein embeddings from an HDF5 file.
    
    :param h5_path: Path to the .h5 file containing embeddings.
    :return: A tuple of (embeddings as np.ndarray, list of accession numbers).
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
            
    return all_embeddings, accessions

def build_index(embeddings: np.ndarray, index_path: str = None) -> faiss.IndexHNSWFlat:
    """
    Builds a FAISS HNSW index from embeddings.
    Uses Cosine distance (Inner Product of normalized vectors).
    
    :param embeddings: Numpy array of embeddings.
    :param index_path: Optional path to save the built index.
    :return: FAISS index.
    """
    dim = embeddings.shape[1]
    print("Normalizing embeddings for cosine similarity...")
    # Work on a copy to avoid mutating the original embeddings if they are needed elsewhere
    norm_embeddings = embeddings.copy()
    faiss.normalize_L2(norm_embeddings)
    
    # Initialize HNSW index with Inner Product
    print(f"Initializing HNSW index with dimension {dim}...")
    index = faiss.IndexHNSWFlat(dim, 32, faiss.METRIC_INNER_PRODUCT)
    
    print("Adding embeddings to HNSW index...")
    index.add(norm_embeddings)
    
    if index_path:
        print(f"Saving index to {index_path}...")
        faiss.write_index(index, index_path)
        
    return index

def get_or_create_index(h5_path: str, index_path: str, accessions_cache_path: str = None) -> Tuple[faiss.IndexHNSWFlat, List[str]]:
    """
    Loads an existing FAISS index or builds a new one from HDF5 embeddings.
    
    :param h5_path: Path to the .h5 file.
    :param index_path: Path to the FAISS index file.
    :param accessions_cache_path: Optional path to save/load accession list.
    :return: A tuple of (FAISS index, list of accession numbers).
    """
    # We always need the accessions to map index IDs back to UniProt IDs
    # Accessions come from the H5 file keys.
    # To avoid opening H5 if index exists, we might want to cache accessions too.
    
    if os.path.exists(index_path) and (accessions_cache_path and os.path.exists(accessions_cache_path)):
        print(f"Loading existing index from {index_path}...")
        index = faiss.read_index(index_path)
        with open(accessions_cache_path, 'rb') as f:
            accessions = pickle.load(f)
        return index, accessions
    
    # If not existing, build it
    embeddings, accessions = load_embeddings(h5_path)
    index = build_index(embeddings, index_path)
    
    if accessions_cache_path:
        print(f"Caching accessions to {accessions_cache_path}...")
        with open(accessions_cache_path, 'wb') as f:
            pickle.dump(accessions, f)
            
    return index, accessions

def load_embeddings_and_build_index(h5_path: str) -> Tuple[faiss.IndexHNSWFlat, List[str]]:
    """
    Legacy wrapper for backward compatibility. 
    Does not persist the index.
    """
    embeddings, accessions = load_embeddings(h5_path)
    index = build_index(embeddings)
    return index, accessions

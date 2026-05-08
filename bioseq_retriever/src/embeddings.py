import h5py
import faiss
import numpy as np
import os
import json
from typing import List, Tuple, Generator

def iter_embeddings(h5_path: str, batch_size: int = 1000) -> Generator[Tuple[np.ndarray, List[str]], None, None]:
    """
    Generator that yields batches of protein embeddings from an HDF5 file.
    
    :param h5_path: Path to the .h5 file containing embeddings.
    :param batch_size: Number of embeddings to yield per batch.
    :yield: A tuple of (embeddings as np.ndarray, list of accession numbers).
    """
    print(f"Opening embeddings from {h5_path}...")
    with h5py.File(h5_path, 'r') as f:
        accessions = list(f.keys())
        if not accessions:
            raise ValueError("The HDF5 file is empty.")
            
        dim = f[accessions[0]].shape[0]
        print(f"Detected embedding dimension: {dim}")
        
        for i in range(0, len(accessions), batch_size):
            batch_accs = accessions[i : i + batch_size]
            batch_embeddings = np.zeros((len(batch_accs), dim), dtype=np.float32)
            for j, acc in enumerate(batch_accs):
                batch_embeddings[j] = f[acc][:]
            yield batch_embeddings, batch_accs

def build_index(h5_path: str, index_path: str = None) -> faiss.IndexHNSWFlat:
    """
    Builds a FAISS HNSW index from embeddings using a generator to save memory.
    Uses Cosine distance (Inner Product of normalized vectors).
    
    :param h5_path: Path to the .h5 file.
    :param index_path: Optional path to save the built index.
    :return: FAISS index.
    """
    index = None
    
    for batch_embeddings, _ in iter_embeddings(h5_path):
        dim = batch_embeddings.shape[1]
        
        if index is None:
            print(f"Initializing HNSW index with dimension {dim}...")
            # Initialize HNSW index with Inner Product
            index = faiss.IndexHNSWFlat(dim, 32, faiss.METRIC_INNER_PRODUCT)
            
        print(f"Processing batch of {len(batch_embeddings)} embeddings...")
        faiss.normalize_L2(batch_embeddings)
        index.add(batch_embeddings)
    
    if index_path and index:
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
    if os.path.exists(index_path) and (accessions_cache_path and os.path.exists(accessions_cache_path)):
        print(f"Loading existing index from {index_path}...")
        index = faiss.read_index(index_path)
        with open(accessions_cache_path, 'r') as f:
            accessions = json.load(f)
        return index, accessions
    
    # If not existing, build it
    index = build_index(h5_path, index_path)
    
    # Get all accessions for the cache
    with h5py.File(h5_path, 'r') as f:
        accessions = list(f.keys())
    
    if accessions_cache_path:
        print(f"Caching accessions to {accessions_cache_path}...")
        with open(accessions_cache_path, 'w') as f:
            json.dump(accessions, f)
            
    return index, accessions

def load_embeddings_and_build_index(h5_path: str) -> Tuple[faiss.IndexHNSWFlat, List[str]]:
    """
    Legacy wrapper for backward compatibility. 
    Does not persist the index.
    """
    index = build_index(h5_path)
    with h5py.File(h5_path, 'r') as f:
        accessions = list(f.keys())
    return index, accessions

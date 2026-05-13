import numpy as np
from typing import List, Tuple
from depricated.bioseq_retriever.src.config import SEARCH_SERVICE_URL, DNA_SEARCH_SERVICE_URL
from depricated.bioseq_retriever.src.api_client import default_api_client

def search_top_k(
    query_sequence: str, 
    k: int = 25
) -> List[Tuple[str, float]]:
    """
    Client function to call the unified protein search service.
    """
    print(f"Searching protein index for top {k} matches...")
    
    response = default_api_client.request_with_retry(
        "POST", f"{SEARCH_SERVICE_URL}/search/protein",
        json={"sequence": query_sequence, "k": k}
    )
    results = response.json()["results"]
    return [(r["accession"], r["score"]) for r in results]

def search_dna_top_k(
    query_sequence: str, 
    k: int = 25
) -> List[Tuple[str, float]]:
    """
    Client function to call the unified DNA search service.
    """
    print(f"Searching DNA index for top {k} matches...")
    
    response = default_api_client.request_with_retry(
        "POST", f"{SEARCH_SERVICE_URL}/search/dna",
        json={"sequence": query_sequence, "k": k}
    )
    results = response.json()["results"]
    return [(r["accession"], r["score"]) for r in results]

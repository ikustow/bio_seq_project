import numpy as np
from typing import List, Tuple
from src.config import SEARCH_SERVICE_URL
from src.api_client import default_api_client

def search_top_k(
    query_sequence: str, 
    k: int = 25
) -> List[Tuple[str, float]]:
    """
    Client function to call the unified search service.
    Sends a raw sequence directly to the service which handles embedding and search.
    """
    print(f"Searching index for top {k} matches for sequence (length {len(query_sequence)})...")
    
    response = default_api_client.request_with_retry(
        "POST", f"{SEARCH_SERVICE_URL}/search",
        json={"sequence": query_sequence, "k": k}
    )
    results = response.json()["results"]
    return [(r["accession"], r["score"]) for r in results]

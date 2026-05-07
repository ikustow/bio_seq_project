import httpx
from typing import List, Dict, Any
from src.config import FETCH_TIMEOUT

def get_uniprot_records(accessions: List[str]) -> List[Dict[str, Any]]:
    if not accessions:
        return []
        
    print(f"Fetching UniProt records for: {', '.join(accessions)}")
    
    ids_query = " OR ".join([f"accession:{acc}" for acc in accessions])
    url = "https://rest.uniprot.org/uniprotkb/search"
    params = {
        "query": ids_query,
        "format": "json",
        "size": len(accessions)
    }
    
    with httpx.Client(timeout=FETCH_TIMEOUT) as client:
        response = client.get(url, params=params)
        if response.status_code == 200:
            return response.json().get('results', [])
        else:
            response.raise_for_status()

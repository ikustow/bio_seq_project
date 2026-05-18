import os
import time
from typing import List, Tuple

import httpx

from src.config import SEARCH_SERVICE_URL
from src.api_client import default_api_client

def search_protein_top_k(
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


EBI_BLAST_BASE = "https://www.ebi.ac.uk/Tools/services/rest/ncbiblast"
BLAST_DEFAULT_EMAIL = "bioseq-investigator@example.com"
BLAST_FAST_POLL_INTERVAL_SECONDS = 1.0
BLAST_FAST_POLL_WINDOW_SECONDS = 10.0
BLAST_POLL_INTERVAL_SECONDS = 4.0
BLAST_MAX_WAIT_SECONDS = 180.0


def blast_search(
    query_sequence: str,
    k: int = 10,
    database: str = "uniprotkb_swissprot",
) -> List[Tuple[str, float]]:
    """Submit a protein BLAST job to EBI and return ranked hits.

    The returned score is percent identity normalized to 0..1 so it lines up
    with the cosine-similarity score returned by ``search_protein_top_k``.
    """
    email = os.getenv("BIOSEQ_BLAST_EMAIL", BLAST_DEFAULT_EMAIL)
    print(f"Submitting BLAST job: seq_len={len(query_sequence)}, db={database}, k={k}")

    try:
        submit_response = default_api_client.request_with_retry(
            "POST", f"{EBI_BLAST_BASE}/run",
            data={
                "email": email,
                "program": "blastp",
                "stype": "protein",
                "database": database,
                "sequence": query_sequence,
                "wordsize": "6",
                "exp": "1e-5",
                "scores": str(max(k, 10)),
                "alignments": str(max(k, 10)),
            },
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        body = (exc.response.text if exc.response is not None else "")[:500]
        raise RuntimeError(
            f"EBI BLAST submission failed ({status}): {body or '(empty body)'}"
        ) from exc
    job_id = submit_response.text.strip()
    print(f"BLAST job submitted: {job_id}")

    waited = 0.0
    while waited < BLAST_MAX_WAIT_SECONDS:
        status_response = default_api_client.request_with_retry(
            "GET", f"{EBI_BLAST_BASE}/status/{job_id}",
        )
        status = status_response.text.strip()
        print(f"BLAST job {job_id}: {status} (waited {waited:.0f}s)")
        if status == "FINISHED":
            break
        if status in ("FAILED", "ERROR", "NOT_FOUND"):
            raise RuntimeError(f"BLAST job {job_id} ended with status {status}")
        interval = (
            BLAST_FAST_POLL_INTERVAL_SECONDS
            if waited < BLAST_FAST_POLL_WINDOW_SECONDS
            else BLAST_POLL_INTERVAL_SECONDS
        )
        time.sleep(interval)
        waited += interval
    else:
        raise TimeoutError(
            f"BLAST job {job_id} did not finish within {BLAST_MAX_WAIT_SECONDS}s"
        )

    result_response = default_api_client.request_with_retry(
        "GET", f"{EBI_BLAST_BASE}/result/{job_id}/json",
    )
    payload = result_response.json()

    hits = payload.get("hits") or []
    results: List[Tuple[str, float]] = []
    for hit in hits[:k]:
        accession = hit.get("hit_acc")
        if not accession:
            continue
        hsps = hit.get("hit_hsps") or []
        identity_percent = float(hsps[0].get("hsp_identity", 0.0)) if hsps else 0.0
        results.append((accession, identity_percent / 100.0))

    print(f"BLAST returned {len(results)} hits")
    return results

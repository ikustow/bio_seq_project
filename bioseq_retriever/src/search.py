import os
import time
from typing import List, Tuple

import httpx

from src.config import SEARCH_SERVICE_URL
from src.api_client import default_api_client


def search_top_k(
    query_sequence: str,
    k: int = 25,
) -> List[Tuple[str, float]]:
    """
    Client function to call the unified search service (ProtT5 + FAISS).
    Sends a raw sequence directly to the service which handles embedding and search.
    """
    print(f"Searching index for top {k} matches for sequence (length {len(query_sequence)})...")

    response = default_api_client.request_with_retry(
        "POST", f"{SEARCH_SERVICE_URL}/search",
        json={"sequence": query_sequence, "k": k},
    )
    results = response.json()["results"]
    return [(r["accession"], r["score"]) for r in results]


# ---------------------------------------------------------------------------
# EBI REST BLAST client (alternative backend, no local data required)
#
# Submits a job to https://www.ebi.ac.uk/Tools/services/rest/ncbiblast/, polls
# until done, parses the JSON result. Returns the same ``(accession, score)``
# shape as ``search_top_k`` so ``rank_node`` does not care which backend ran.
#
# Email is required by EBI for abuse logging; they do not validate it for
# deliverability. Override via ``BIOSEQ_BLAST_EMAIL`` env var if you want
# your own address in their logs.
# ---------------------------------------------------------------------------

EBI_BLAST_BASE = "https://www.ebi.ac.uk/Tools/services/rest/ncbiblast"
# Must look like a real email — EBI rejects unknown TLDs (e.g. ".user"). The
# ``example.com`` domain is IANA-reserved for placeholders and accepted by
# every standard validator. EBI does not check deliverability.
BLAST_DEFAULT_EMAIL = "bioseq-investigator@example.com"
# Short jobs against SwissProt with blastp-fast often finish in 5-15s, so
# poll every 1s for the first ``BLAST_FAST_POLL_WINDOW_SECONDS`` then fall
# back to the slower cadence to be polite to EBI on longer jobs.
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
    with the cosine-similarity score returned by ``search_top_k``.
    """
    email = os.getenv("BIOSEQ_BLAST_EMAIL", BLAST_DEFAULT_EMAIL)
    print(f"Submitting BLAST job: seq_len={len(query_sequence)}, db={database}, k={k}")

    # Speed-tuned parameter set. EBI does the same work as the UniProt website
    # BLAST under the hood, so these are the only knobs available; there is no
    # "quick preview" mode in the REST API.
    try:
        submit_response = default_api_client.request_with_retry(
            "POST", f"{EBI_BLAST_BASE}/run",
            data={
                "email": email,
                "program": "blastp",
                "stype": "protein",
                "database": database,
                "sequence": query_sequence,
                # Word size 6 instead of the blastp default of 3 — this is
                # what NCBI's "blastp-fast" preset does internally. 2-5x
                # faster, slightly less sensitive. Note: EBI's "task" param
                # only accepts blastp/blastn/megablast, so we tune wordsize
                # directly here.
                "wordsize": "6",
                # Tighter E-value than the default of 10: server skips hits
                # unlikely to be biologically meaningful, reducing scoring
                # and formatting work.
                "exp": "1e-5",
                # Cap how many hits the server scores/aligns/formats. We
                # only ever read the top ``k`` (default 10) below, so asking
                # for the default 50 is wasted server work.
                "scores": str(max(k, 10)),
                "alignments": str(max(k, 10)),
            },
        )
    except httpx.HTTPStatusError as exc:
        # EBI puts the actual reason ("invalid email", "sequence contains
        # invalid characters", etc.) into the response body. The default
        # ``HTTPStatusError`` message only carries the status code, which
        # is useless for diagnosis. Surface the body so the chat reply
        # tells the user what's wrong.
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
        # Adaptive backoff: short jobs benefit from sub-4s detection;
        # longer jobs revert to the relaxed interval.
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

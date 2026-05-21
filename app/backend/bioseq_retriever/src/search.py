import os
import socket
import time
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

import httpx

from src.config import SEARCH_SERVICE_URL, SEARCH_PROBE_TIMEOUT
from src.api_client import default_api_client


def _search_service_alive(url: str = SEARCH_SERVICE_URL, timeout: float = SEARCH_PROBE_TIMEOUT) -> bool:
    """Quick TCP liveness probe for the local search gateway.

    The api_client's retry loop waits ~31s + jitter across 5 attempts before
    giving up — meaningful for transient 429 / 5xx but pure overhead when the
    port is simply closed. A 0.5s connect probe lets us fail fast with a
    clear message instead of making the user wait a minute for the inevitable.
    """
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def search_protein_top_k(
    query_sequence: str,
    k: int = 25
) -> List[Tuple[str, float]]:
    """
    Client function to call the unified protein search service.
    """
    print(f"Searching protein index for top {k} matches...")
    if not _search_service_alive():
        raise ConnectionError(
            f"Search service at {SEARCH_SERVICE_URL} is not reachable — "
            "is the gateway (services/search_service.py) running on the "
            "expected port?"
        )

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
    if not _search_service_alive():
        raise ConnectionError(
            f"Search service at {SEARCH_SERVICE_URL} is not reachable — "
            "is the gateway (services/search_service.py) running on the "
            "expected port?"
        )

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
    program: str = "blastp",
    stype: str = "protein",
) -> List[Tuple[str, float]]:
    """Submit a BLAST job to EBI and return ranked hits.

    The returned score is percent identity normalized to 0..1 so it lines up
    with the cosine-similarity score returned by ``search_protein_top_k``.

    ``program`` / ``stype`` are passed through to EBI — ``blastp`` + ``protein``
    by default, but the same submission/poll/parse flow works for ``blastx`` +
    ``dna`` (used by :func:`blastx_search_dna`).
    """
    email = os.getenv("BIOSEQ_BLAST_EMAIL", BLAST_DEFAULT_EMAIL)
    print(
        f"Submitting BLAST job: program={program}, stype={stype}, "
        f"seq_len={len(query_sequence)}, db={database}, k={k}"
    )

    try:
        submit_response = default_api_client.request_with_retry(
            "POST", f"{EBI_BLAST_BASE}/run",
            data={
                "email": email,
                "program": program,
                "stype": stype,
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
    # Tuple shape: (accession, identity_0..1, hit_info). ``hit_info`` carries
    # frame/range data that's only meaningful for blastx (DNA → 6-frame protein
    # search) — the caller in rank_dna_node uses it to translate the query in
    # the right frame for per-pair alignment. For blastp, frame is always 0.
    results: List[Tuple[str, float, Dict[str, Any]]] = []
    for hit in hits[:k]:
        accession = hit.get("hit_acc")
        if not accession:
            continue
        hsps = hit.get("hit_hsps") or []
        if hsps:
            first = hsps[0]
            identity_percent = float(first.get("hsp_identity", 0.0))
            hit_info: Dict[str, Any] = {
                "query_frame": first.get("hsp_query_frame"),
                "query_from": first.get("hsp_query_from"),
                "query_to": first.get("hsp_query_to"),
            }
        else:
            identity_percent = 0.0
            hit_info = {}
        results.append((accession, identity_percent / 100.0, hit_info))

    print(f"BLAST returned {len(results)} hits")
    return results


def blastx_search_dna(
    query_sequence: str,
    k: int = 10,
) -> List[Tuple[str, float]]:
    """Run BLAST for a DNA query via ``blastx`` against SwissProt.

    Что тут происходит, по-человечески:

    У нас нуклеотидный запрос, а сравнивать мы хотим с **белковой** базой
    (SwissProt) — потому что весь downstream-пайплайн (UniProt lookup, UI-карточка,
    rerank по контексту) завязан на UniProt-аксессии и работает именно с белками.
    Поэтому берём программу ``blastx``: она транслирует нашу ДНК **во всех 6
    рамках считывания** (три прямые рамки +1/+2/+3 и три обратно-комплементарные
    -1/-2/-3), и каждую трансляцию гоняет blastp-подобным поиском против
    SwissProt. Так мы а) не зависим от того, знаем ли мы заранее правильную
    рамку и направление цепи, и б) переживаем off-by-one в начале CDS и кейсы,
    когда последовательность пришла в reverse-complement.

    На выходе — те же ``(uniprot_accession, identity_percent / 100)``, что и у
    обычного blastp, так что вызывающий код в ``rank_dna_node`` может
    обрабатывать матчи ровно так же, как протеиновая BLAST-ветка.
    """
    return blast_search(
        query_sequence,
        k=k,
        database="uniprotkb_swissprot",
        program="blastx",
        stype="dna",
    )

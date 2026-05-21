# HF Spaces deploy — single-container (Option A)

Run the new split architecture (`main`) on **one** Hugging Face *Streamlit*
Space, where there is only one entry point (`streamlit run app/frontend/app.py`)
and no second terminal to start the gateway in.

## How it works

`app/frontend/gateway_supervisor.py` lets the Streamlit process spawn the heavy
search/rerank gateway (`app/backend/bioseq_retriever/services/search_service.py`)
as a child process at startup:

```
streamlit run app/frontend/app.py
        │
        ├─ main() → _ensure_gateway_once()  (cached: runs once per server)
        │            └─ background thread:
        │                 1. python …/src/bootstrap.py   (download protein data)
        │                 2. python …/services/search_service.py  (uvicorn :8002)
        └─ UI renders immediately; first queries fail-fast until :8002 warms up
```

* **Opt-in** via `BIOSEQ_SPAWN_GATEWAY=true`. Unset → no spawn (local
  two-terminal dev is unaffected).
* **Idempotent**: skips if something already listens on the gateway port, or if
  the gateway URL points at a remote host (split deploy).
* **Non-blocking**: download + model load + FAISS build (several minutes) run in
  a daemon thread / the child process, never on Streamlit's script thread. The
  retriever's TCP liveness probe shows a clean "gateway not reachable yet"
  message during warmup.

## HF Space settings

Streamlit SDK Space (`sdk: streamlit`, `app_file: app/frontend/app.py`),
installs the repo-root `requirements.txt` (now includes `fastapi` + `uvicorn`).

| Name | Type | Value | Notes |
|---|---|---|---|
| `BIOSEQ_SPAWN_GATEWAY` | Variable | `true` | **Enables Option A.** |
| `BIOSEQ_BACKEND` | Variable | `real` | Use the real retriever, not mock. |
| `BIOSEQ_DATA_SOURCE` | Variable | `hf:radda-i/bioseq-data` | Where bootstrap pulls embeddings from. |
| `MISTRAL_API_KEY` | Secret | … | Extract/classify + rerank. |
| `SUPABASE_DB_URL` | Secret | … | Chat-history persistence. |
| `BIOSEQ_LLM_PROXY_URL` / `BIOSEQ_LLM_PROXY_TOKEN` | Secret | … | Gemini follow-up via the Cloudflare Worker. |

Optional: `BIOSEQ_BOOTSTRAP_DATA=false` to skip the auto-download (if you bake
data into the image); `BIOSEQ_DATA_DIR` to relocate the data folder (the
gateway and bootstrap share it).

RAM note: only the gateway loads ProtT5 + FAISS now (Streamlit is a thin HTTP
client), so the footprint is ≈ the old in-process deploy — fits the free 16 GB
CPU tier.

## ⚠️ Remaining data gaps (not solved by Option A)

Option A handles **process startup**. Data provisioning is separate and still
needs attention before a cold boot succeeds end-to-end:

1. **DNA index is mandatory at boot.** `search_service` loads *both* a protein
   and a DNA FAISS index on startup, but `bootstrap.ensure_data()` only fetches
   the protein `per-protein.h5`. If `per-gene.h5` is absent the gateway exits
   immediately (the supervisor logs `gateway exited immediately`). Fix options:
   add `per-gene.h5` to the `radda-i/bioseq-data` dataset and extend
   `bootstrap.ensure_data()` to fetch it, **or** make the DNA index load lazy /
   optional in `search_service`. Both touch `main` — out of scope for this
   branch.
2. **Accessions cache format mismatch (non-fatal).** bootstrap downloads
   `per-protein.accessions.pkl`; the gateway reads `per-protein.accessions.json`.
   Result: the prebuilt index is ignored and rebuilt from the `.h5` (~5–15 min
   one-time cold-start cost). Align the dataset to ship `.accessions.json` to
   avoid the rebuild.

## Local smoke test

```powershell
$env:BIOSEQ_SPAWN_GATEWAY = "true"
$env:BIOSEQ_BACKEND = "real"
$env:BIOSEQ_DATA_SOURCE = "hf:radda-i/bioseq-data"
streamlit run app/frontend/app.py
# Watch the terminal for [gateway_supervisor] / [bioseq.bootstrap] lines and
# the gateway's "Uvicorn running on http://0.0.0.0:8002".
```

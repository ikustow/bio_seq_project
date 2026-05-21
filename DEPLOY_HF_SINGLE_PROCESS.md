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

## Data provisioning (what the gateway needs on disk)

Option A handles **process startup**. The gateway then loads *both* a protein
and a DNA FAISS index at boot, so the dataset behind
`BIOSEQ_DATA_SOURCE=hf:OWNER/DATASET` must provide both `.h5` files.
`bootstrap.ensure_data()` fetches them automatically before launching the
gateway:

| File | Required | Notes |
|---|---|---|
| `per-protein.h5` | **yes** | Protein FAISS source (~1.3 GB). |
| `per-gene.h5` | **yes** | DNA FAISS source. Without it the gateway **exits on boot** (it loads the DNA index eagerly); the supervisor logs `gateway exited immediately`. |
| `per-protein.index` + `per-protein.accessions.json` | optional | Pre-built protein index — skips a 5–15 min rebuild. Cache must be `.json` (the gateway reads it with `json.load`). |
| `per-gene.index` + `per-gene.accessions.json` | optional | Same, for DNA. |

If the optional `.index` / `.accessions.json` are absent the gateway rebuilds
the index from the `.h5` on first boot — correct, just slow, and repeated on
every cold start (the HF free tier has no persistent disk). To upload only the
`.h5` files is enough for a *working* (if slow-booting) deploy.

> **Previously known gaps, now fixed in `bootstrap.py`:** it used to fetch only
> `per-protein.h5` (so `per-gene.h5` was missing → gateway crashed) and to pull
> `per-protein.accessions.pkl` instead of the `.json` the gateway reads (so the
> prebuilt index was ignored). Both are resolved in code: bootstrap now fetches
> `per-gene.h5`, prefers a shipped `.accessions.json`, and — for the existing
> dataset that only carries the legacy `.accessions.pkl` — downloads and
> converts it to `.json` automatically. The **only** manual step left is
> uploading `per-gene.h5` to the dataset root. (The dataset as of 2026-05-31
> has `per-protein.h5` + `per-protein.index` + `per-protein.accessions.pkl`;
> the protein index now works as-is via the auto-conversion. DNA has no
> prebuilt index, so the gateway builds it once from `per-gene.h5` on first
> boot.)

## Local smoke test

```powershell
$env:BIOSEQ_SPAWN_GATEWAY = "true"
$env:BIOSEQ_BACKEND = "real"
$env:BIOSEQ_DATA_SOURCE = "hf:radda-i/bioseq-data"
streamlit run app/frontend/app.py
# Watch the terminal for [gateway_supervisor] / [bioseq.bootstrap] lines and
# the gateway's "Uvicorn running on http://0.0.0.0:8002".
```

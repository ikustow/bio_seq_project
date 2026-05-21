# HF Spaces deploy — single-container (Option A)

Run the split architecture (`main`) on **one** Hugging Face *Streamlit* Space,
where there is only one entry point (`streamlit run app/frontend/app.py`) and no
second terminal to start the gateway in.

## How it works

`app/frontend/gateway_supervisor.py` lets the Streamlit process spawn the heavy
search/rerank gateway (`app/backend/bioseq_retriever/services/search_service.py`)
as a child process at startup:

```
streamlit run app/frontend/app.py
        │
        ├─ main() → _ensure_gateway_once()  (cached: runs once per server)
        │            └─ background thread:
        │                 1. python …/src/bootstrap.py   (download data + indexes)
        │                 2. python …/services/search_service.py  (uvicorn :8002)
        └─ UI renders immediately; first queries fail-fast until :8002 warms up
```

* **Opt-in** via `BIOSEQ_SPAWN_GATEWAY=true`. Unset → no spawn (local
  two-terminal dev is unaffected).
* **Idempotent**: skips if something already listens on the gateway port, or if
  the gateway URL points at a remote host (split deploy).
* **Non-blocking**: download + model load + index load run in a daemon thread /
  the child process, never on Streamlit's script thread. The retriever's TCP
  liveness probe shows a clean "gateway not reachable yet" message during warmup.

## HF Space settings

Streamlit SDK Space (`sdk: streamlit`, `app_file: app/frontend/app.py`),
installs the repo-root `requirements.txt` (includes `fastapi` + `uvicorn` for the
gateway and `langgraph-checkpoint-postgres` for Supabase persistence).

| Name | Type | Value | Notes |
|---|---|---|---|
| `BIOSEQ_SPAWN_GATEWAY` | Variable | `true` | **Enables Option A.** |
| `BIOSEQ_FRONTEND_BACKEND` | Variable | `real` | Frontend selector: `app.py` takes the real-search path only when this is exactly `real` (else it shows mock data). |
| `BIOSEQ_BACKEND` | Variable | `runtime` | Backend service-factory mode: one of `runtime` / `bioseq` / `bioseq_retriever` (or `mock`). **Not `real`** — `service_factory.py` rejects it. |
| `BIOSEQ_DATA_SOURCE` | Variable | `hf:radda-i/bioseq-data` | Where bootstrap pulls embeddings + indexes from. |
| `STREAMLIT_LOGGER_LEVEL` | Variable | `error` | Quiets per-rerun Streamlit WARNING spam in the Space logs (also set in `.streamlit/config.toml`). |
| `MISTRAL_API_KEY` | Secret | … | Extract/classify + rerank. |
| `SUPABASE_DB_URL` | Secret | … | Chat-history persistence. |
| `BIOSEQ_LLM_PROXY_URL` / `BIOSEQ_LLM_PROXY_TOKEN` | Secret | … | Gemini follow-up via the Cloudflare Worker. |

> ⚠️ **Frontend and backend read DIFFERENT values.** The UI wants
> `BIOSEQ_FRONTEND_BACKEND=real`; the service factory wants `BIOSEQ_BACKEND=runtime`.
> A single `BIOSEQ_BACKEND=real` (the old deploy's value) makes the backend raise
> *"BIOSEQ_BACKEND must be one of: 'runtime', 'bioseq', 'bioseq_retriever', or
> 'mock'"* on the first user message. Set **both** vars as above.

Optional: `BIOSEQ_BOOTSTRAP_DATA=false` to skip the auto-download (if you bake
data into the image); `BIOSEQ_DATA_DIR` to relocate the data folder (the gateway
and bootstrap share it).

## Memory budget (free 16 GB CPU tier)

Two processes share the 16 GB: the Streamlit frontend (~1.5 GB, a thin HTTP
client) and the gateway (loads all models + indexes). Measured/derived, fp32 on
CPU (half precision is not viable on CPU):

| Component (gateway) | RAM |
|---|---|
| ProtT5 encoder (`T5EncoderModel`, encoder-only of an 11 GB .bin) | ~4.8 GB |
| Qwen3-Embedding-0.6B reranker (fp32) | ~2.4 GB |
| HyenaDNA (14 M params, d_model=256) | ~0.06 GB |
| Protein FAISS index (≈461 k × 1024 × 4) | ~1.9 GB |
| DNA FAISS index (≈547 k × 256 × 4) | ~0.56 GB |
| torch / faiss / python base | ~1.5 GB |
| **gateway total** | **~11.2 GB** |

Frontend ~1.5 GB → **~12.7 GB total, fits 16 GB with ~3 GB headroom** (both
indexes). Transient peak ~11 GB while ProtT5's single unsharded .bin loads — also
under 16 GB. The DNA index is small only because HyenaDNA's dim is 256; that is
what makes keeping both indexes viable on the free tier.

## Data provisioning (what the gateway needs on disk)

The gateway loads *both* a protein and a DNA FAISS index at boot.
`bootstrap.ensure_data()` fetches everything from the dataset behind
`BIOSEQ_DATA_SOURCE=hf:OWNER/DATASET` before launching the gateway:

| File | Required | Notes |
|---|---|---|
| `per-protein.h5` | **yes** | Protein embeddings source (~1.3 GB). |
| `per-gene.h5` | **yes** | DNA embeddings source. Without it the gateway **exits on boot** (loads the DNA index eagerly). |
| `per-protein.index` + `per-protein.accessions.json` | **yes, in practice** | Pre-built protein index. |
| `per-gene.index` + `per-gene.accessions.json` | **yes, in practice** | Pre-built DNA index. |

> 🔴 **Ship the pre-built `.index` + `.accessions.json` — do NOT rely on rebuild.**
> The `.h5` files store one tiny dataset per accession, so rebuilding an index is
> hundreds of thousands of random reads. On the free CPU/IO tier this takes
> **~40 min or effectively hangs** (and repeats every cold start — no persistent
> disk). With the pre-built pair present, the gateway *loads* in seconds
> (`Loading existing … index` instead of `Building … index`). Build them locally:
>
> ```powershell
> pip install faiss-cpu h5py numpy
> python data_prep/build_faiss_indexes.py <path>\per-protein.h5 <path>\per-gene.h5
> # writes <name>.index + <name>.accessions.json next to each .h5
> ```
>
> Then upload all four to the dataset root (LFS handles the size):
> ```powershell
> hf upload radda-i/bioseq-data .\per-protein.index per-protein.index --repo-type=dataset
> hf upload radda-i/bioseq-data .\per-protein.accessions.json per-protein.accessions.json --repo-type=dataset
> hf upload radda-i/bioseq-data .\per-gene.index per-gene.index --repo-type=dataset
> hf upload radda-i/bioseq-data .\per-gene.accessions.json per-gene.accessions.json --repo-type=dataset
> ```

The cache must be `.json` (the gateway reads it with `json.load`). `bootstrap.py`
prefers a shipped `.accessions.json` but falls back to downloading and converting
a legacy `.accessions.pkl`, so an older dataset still yields a usable cache. The
gateway loads the pre-built index only when **both** `<name>.index` and
`<name>.accessions.json` are on disk.

## Deploying to the Space (push)

A HF Space **is its own git repo** — there is **no GitHub→HF auto-sync**.
Deploy = a manual push to the `space` remote:

```
space → https://huggingface.co/spaces/radda-i/BioSeq_investigator   (branch main)
origin → github.com/ikustow/bio_seq_project                          (NOT linked to HF)
```

You **cannot** push `main` directly: its history carries >10 MiB blobs from the
old `backend/graph_core/` experiments, and HF rejects oversized blobs *anywhere*
in the pushed history. So a deploy is a fresh **orphan snapshot** (one commit, no
history) of the current tree.

Two HF push rules to satisfy — both handled by the recipe below:

1. **No blob >10 MiB in the pushed history.** The orphan snapshot strips the old
   graph_core blobs (the current tree's largest file is ~6.5 MiB).
2. **No binary file stored as a plain git blob** ("use Xet/LFS"). The UI assets
   (`*.png`, `*.psd` under `app/frontend/assets/`) must go through **LFS**. The
   old `deploy/hf-spaces` predates this policy, so it slipped through without it.

### Prerequisites

* Pre-built indexes uploaded to the `radda-i/bioseq-data` dataset (see *Data
  provisioning*).
* Space Variables/Secrets set (see *HF Space settings*) — crucially
  `BIOSEQ_SPAWN_GATEWAY=true`, `BIOSEQ_FRONTEND_BACKEND=real`,
  `BIOSEQ_BACKEND=runtime`, `BIOSEQ_DATA_SOURCE=hf:radda-i/bioseq-data`.
* `git lfs` installed locally (`git lfs version`).

### The `.gitattributes` (LFS) the snapshot needs

The snapshot must contain a repo-root `.gitattributes` tracking binaries via LFS.
It is **intentionally not committed on `main`** (to avoid changing the team's
normal git/LFS behaviour) — create it at deploy time with:

```
*.png  filter=lfs diff=lfs merge=lfs -text
*.psd  filter=lfs diff=lfs merge=lfs -text
*.jpg  filter=lfs diff=lfs merge=lfs -text
*.jpeg filter=lfs diff=lfs merge=lfs -text
*.gif  filter=lfs diff=lfs merge=lfs -text
*.ico  filter=lfs diff=lfs merge=lfs -text
*.webp filter=lfs diff=lfs merge=lfs -text
*.pdf  filter=lfs diff=lfs merge=lfs -text
*.zip  filter=lfs diff=lfs merge=lfs -text
*.h5   filter=lfs diff=lfs merge=lfs -text
*.bin  filter=lfs diff=lfs merge=lfs -text
*.pkl  filter=lfs diff=lfs merge=lfs -text
*.index filter=lfs diff=lfs merge=lfs -text
*.npy  filter=lfs diff=lfs merge=lfs -text
```

### Commands (run from a clean `main`)

```powershell
# 0. Make sure the .gitattributes block above exists at the repo root.

# 1. Fresh orphan snapshot of the current tree (no history → no big blobs)
git branch -D deploy/hf-space-snapshot   # first time this errors — ignore it
git checkout --orphan deploy/hf-space-snapshot
git add -A
git commit -m "deploy: HF single-process snapshot"

# 2. Convert binary UI assets to LFS pointers (HF rejects plain-blob binaries)
git lfs install --local
git add .gitattributes
git add --renormalize app/frontend/assets
git commit --amend --no-edit

# 3. Force-push the snapshot as the Space's main branch (uploads LFS objects too)
git push space deploy/hf-space-snapshot:main --force

# 4. Back to your working branch
git checkout main
```

Sanity check before the push: `git cat-file -p HEAD:app/frontend/assets/BotIcon.png`
should start with `version https://git-lfs…` (i.e. it's an LFS pointer, not a PNG).
After the push, HF rebuilds the Space automatically; watch the build + runtime
logs (`[gateway_supervisor]` → `[bioseq.bootstrap]` → `Loading existing … index`
→ `Uvicorn running on :8002`).

### Rollback

The previous live snapshot is the `deploy/hf-spaces` branch (old v3.0 in-process
build). Restore it with:

```powershell
git push space deploy/hf-spaces:main --force
```

## Local smoke test

```powershell
$env:BIOSEQ_SPAWN_GATEWAY = "true"
$env:BIOSEQ_FRONTEND_BACKEND = "real"
$env:BIOSEQ_BACKEND = "runtime"
$env:BIOSEQ_DATA_SOURCE = "hf:radda-i/bioseq-data"
streamlit run app/frontend/app.py
# Watch the terminal for [gateway_supervisor] / [bioseq.bootstrap] lines and
# the gateway's "Uvicorn running on http://0.0.0.0:8002".
```

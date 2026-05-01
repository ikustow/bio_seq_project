# Deploying BioSeq Investigator to a Hugging Face Space

One-time instructions for publishing this branch (`deploy/hf-spaces`)
as a Streamlit Space on huggingface.co. Follow top-to-bottom — most
steps are once-per-account or once-per-Space.

> **Status:** branch ready, not yet pushed. The actions below are the
> ones a human must take (account creation, Space creation, secret
> entry, large-file uploads). Anything visible to others or
> account-scoped is intentionally **not** automated.

---

## 0. Prerequisites

- A Hugging Face account at https://huggingface.co.
- A **write-scoped** access token at https://huggingface.co/settings/tokens.
  Save it locally — you need it for `git push` and dataset uploads.
- `git` and `git-lfs` available locally (`git lfs install` once per machine).
- A valid **Mistral API key** (the same one used in dev) to paste into
  Space Secrets.

---

## 1. Decide the Space identity

Pick:

- **Owner**: your HF username, or an org you own (e.g. `cleve` or `bioseq-team`).
- **Space name**: e.g. `bioseq-investigator`.
- **Visibility**: free Spaces are **public**. For a course capstone this is
  fine; if you need private, you'll need HF Pro.
- **Hardware**: start with the **CPU basic / free** tier (2 vCPU, 16 GB RAM).
  The Space fits — see cold-start budget in `README.md`.

Keep the full ID handy: `<owner>/<space-name>`. Examples below use
`radda-i/BioSeq_investigator` as a placeholder — substitute throughout.

---

## 2. Stage the embeddings on a HF Dataset

Three files are too big to commit to the Space repo: `per-protein.h5`
(1.38 GB), the pre-built FAISS HNSW index `per-protein.index` (2.51 GB),
and the accession cache `per-protein.accessions.pkl` (5.2 MB). All
three already exist on the strong laptop in
`D:\Alina_data_Sanity\bio_seq_project\bioseq_retriever\data\` after the
2026-05-01 smoke test.

We host them in a **HF Dataset** and have the Space pull all three on
first boot. Including the pre-built index in the dataset cuts ~5–15 min
off every cold start (no FAISS rebuild needed).

### 2a. Create the dataset repo

On https://huggingface.co/new-dataset:

- Owner: same as Space owner
- Name: e.g. `bioseq-data`
- Visibility: **public** is fine (the file already is — it comes from UniProt)

Full ID: `radda-i/bioseq-data`.

### 2b. Upload the files

On the **strong laptop** (where the data lives):

```powershell
cd D:\Alina_data_Sanity\bio_seq_project

# huggingface_hub is already in the venv. Set the write token in the
# session env first (so it is not in shell history):
$env:HF_TOKEN = "<paste your HF write token>"

.\.venv\python.exe -c @"
import os
from huggingface_hub import HfApi
api = HfApi(token=os.environ['HF_TOKEN'])
for src in [
    'bioseq_retriever/data/per-protein.h5',
    'bioseq_retriever/data/per-protein.index',
    'bioseq_retriever/data/per-protein.accessions.pkl',
]:
    print(f'uploading {src}...')
    api.upload_file(
        path_or_fileobj=src,
        path_in_repo=os.path.basename(src),
        repo_id='radda-i/bioseq-data',
        repo_type='dataset',
    )
print('done')
"@
```

Total upload is ~3.9 GB. Over a typical home upload link this takes
20–60 minutes; do it once and forget. Subsequent re-uploads are deduped
via HF's xet backend.

---

## 3. Create the Space

On https://huggingface.co/new-space:

- Owner: `radda-i`
- Space name: `BioSeq_investigator`
- License: `mit` (or whatever matches your README)
- SDK: **Streamlit**
- Hardware: **CPU basic** (free)
- Visibility: as decided in §1
- "Create Space"

HF will give you the git remote URL:
`https://huggingface.co/spaces/radda-i/BioSeq_investigator`.

---

## 4. Set Space secrets and variables

In the Space's **Settings → Variables and secrets**:

**Secrets** (encrypted, not visible after save):

- `MISTRAL_API_KEY` = your Mistral key

**Variables** (visible in Space settings):

- `BIOSEQ_BACKEND` = `real`
- `BIOSEQ_DATA_SOURCE` = `hf:radda-i/bioseq-data` *(if you did §2)*
  or omit / set to `uniprot` to fall back to UniProt FTP.

---

## 5. Push the deploy branch to the Space

From this repo (any machine — primary laptop is fine):

```bash
# One-time: add the Space as a git remote.
git remote add space https://huggingface.co/spaces/radda-i/BioSeq_investigator

# Push the deploy branch as the Space's main branch.
git checkout deploy/hf-spaces
git push space deploy/hf-spaces:main
```

When prompted for credentials:

- Username: your HF username
- Password: your HF **write token** (not your account password)

You can store it via `git credential-cache` or a Windows Credential
Manager entry to avoid re-typing.

The push triggers HF's build of the Space (logs visible in the **App**
tab, then the **Logs** sub-tab on the Space page).

---

## 6. First-boot watch

Open the Space, then click **App → Logs** to watch the build and runtime
logs. Expect this sequence:

1. `Building image…` (pip installs, ~3–6 min)
2. `Streamlit started` and "App is ready"
3. Click into the App tab. The chat column appears immediately. Paste a
   FASTA in chat and submit.
4. Logs show:
   - `[bioseq.bootstrap] HF Hub: radda-i/bioseq-data::per-protein.h5` (or `GET https://ftp.uniprot.org/...`)
   - `[bioseq.bootstrap] saved bioseq_retriever/data/per-protein.h5`
   - `Loading model Rostlab/prot_t5_xl_uniref50...` (only first time per
     container — afterwards the HF cache covers it)
   - `Searching index for top 50 matches...`
   - `Using Mistral AI Embeddings for local reranking...`
5. Right protein card populates.

---

## 7. Updating after a code change

Fast path (any change in this repo on `deploy/hf-spaces`):

```bash
git checkout deploy/hf-spaces
git push space deploy/hf-spaces:main
```

HF rebuilds the image automatically.

If you only changed the data source / secrets, no rebuild is needed —
hit **Settings → Restart this Space** instead.

---

## 8. Common gotchas

- **`MISTRAL_API_KEY environment variable is not set`** in logs after a
  push: the secret is missing or you typo'd the name. Fix in Settings →
  Variables and secrets, then Restart.
- **Cold start times out at first query**: the free tier kills idle
  Spaces. The first query after a wake-up triggers all of bootstrap +
  ProtT5 weights download + FAISS build, which together can exceed
  Streamlit's default request timeout. The right protein card may appear
  empty on the very first try — the next query (with everything cached
  on disk) is fast. Hit it once before any demo to warm caches.
- **OOM during FAISS index build**: the rank step holds 574k × 1024 ×
  float32 ≈ 2.4 GB plus the HNSW graph. On 16 GB free tier this fits,
  but if you also have ProtT5 model loaded simultaneously, memory gets
  tight. If you see OOM, run a single warm-up query to build the index
  via `python -c "from bioseq_retriever.src.bootstrap import ensure_data; ensure_data()"`,
  then upload the resulting `.index` and `.accessions.pkl` to the
  dataset (§2b, second half) so subsequent cold starts skip the build.

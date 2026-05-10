---
title: BioSeq Investigator
emoji: 🧬
colorFrom: indigo
colorTo: green
sdk: streamlit
sdk_version: 1.57.0
app_file: app/frontend/app.py
pinned: false
license: mit
short_description: Paste a FASTA, get an evidence-grounded UniProt match.
---

# BioSeq Investigator

Paste a DNA or protein FASTA sequence, ask a question, and get an
evidence-grounded answer backed by ProtT5 sequence search over Swiss-Prot
plus Mistral-driven contextual reranking. Follow-up questions are
answered by a Gemini chat module via a Cloudflare proxy; per-session
chat history is persisted to a Supabase Postgres table.

## Configuration (HF Space Secrets / Variables)

| Name                       | Where        | Required | Notes |
|----------------------------|--------------|----------|-------|
| `MISTRAL_API_KEY`          | **Secret**   | yes      | Mistral API key — drives extract/classify and reranking. |
| `SUPABASE_DB_URL`          | **Secret**   | yes      | Postgres connection string for `public.chat_sessions`. Without it, the sidebar shows "Session history is not persisted" and only the current tab keeps state. |
| `BIOSEQ_LLM_PROXY_URL`     | **Secret**   | yes      | Cloudflare Worker URL fronting Gemini. Required for follow-up chat turns. |
| `BIOSEQ_LLM_PROXY_TOKEN`   | **Secret**   | yes      | Bearer token expected by the Cloudflare proxy. |
| `BIOSEQ_BACKEND`           | **Variable** | yes      | Set to `real` for live pipeline. `mock` keeps the demo UI without backend. |
| `BIOSEQ_DATA_SOURCE`       | **Variable** | recommended | Set to `hf:radda-i/bioseq-data` — pulls `per-protein.h5` (~1.3 GB), the pre-built FAISS index (~2.5 GB) and the accession cache from a HF dataset repo. Cold start ~1–2 min. Defaults to `uniprot` (downloads from UniProt FTP, no index → +5–15 min FAISS rebuild on every cold start). |
| `BIOSEQ_DATA_DIR`          | **Variable** | optional | Override the default `bioseq_retriever/data` location for the embeddings + index. |

## Cold-start expectations (free-tier 16 GB CPU Space)

1. `per-protein.h5` (~1.3 GB) downloads to `bioseq_retriever/data/`
   (~5–10 min from UniProt FTP, ~1–2 min from the HF dataset).
2. `Rostlab/prot_t5_xl_uniref50` weights (~3 GB) download to the
   HF cache from the public Hub on first ProtT5 use.
3. FAISS HNSW index loads from the dataset (or builds from the .h5,
   5–15 min single-threaded, if the index was not pre-uploaded).
4. Each subsequent query is ~30–90 s.

For demo days, hitting the Space at least once before the audience does
warms all of the above.

## Local development

```bash
pip install -r requirements.txt

# .env in the repo root:
#   MISTRAL_API_KEY=...
#   SUPABASE_DB_URL=postgresql://...
#   BIOSEQ_LLM_PROXY_URL=https://...
#   BIOSEQ_LLM_PROXY_TOKEN=...

# Mock UI (no backend, no key needed):
streamlit run app/frontend/app.py

# Real pipeline:
BIOSEQ_BACKEND=real streamlit run app/frontend/app.py
```

## Project layout

- [`app/frontend/`](app/frontend/) — Streamlit UI: chat, protein card, alignment viewer, session sidebar, identity bootstrap.
- [`bioseq_retriever/`](bioseq_retriever/) — LangGraph pipeline: extract → classify → translate → rank (FAISS over ProtT5) → rerank (Mistral embeddings).
- [`app/backend/`](app/backend/) — backend services and contracts (Supabase persistence, app contracts, mappers).

---

## Repository workflow rules

1. Clone the repository

- Copy the repository locally:
  - `git clone <url>`
- Enter the project folder:
  - `cd bio_seq_project`

2. Update your local copy

- Always sync with the remote `main` branch before starting work:
  - `git checkout main`
  - `git pull origin main`

3. Create a new branch

- The starting point is always `main`.
- Create your branch from the up-to-date `main` branch:
  - `git checkout main`
  - `git pull origin main`
  - `git checkout -b feature/your-task-name`

4. Branch naming rules

- Use clear and concise names.
- Branch name format:
  - `feature/<description>` — new feature
  - `fix/<description>` — bug fix
  - `docs/<description>` — documentation
  - `chore/<description>` — maintenance tasks
- Examples:
  - `feature/add-sequence-parser`
  - `fix/readme-typo`

5. Working in your branch

- Make small, logical commits.
- Write meaningful commit messages:
  - `git commit -m "Add sequence parser"`
- Before pushing, make sure your branch is clean:
  - `git status`

6. Publishing your branch

- Push your branch to the remote repository:
  - `git push -u origin <branch-name>`

7. Creating a pull request / merge request

- Create PR/MR into `main`.
- In the description include:
  - what was done;
  - why it was done;
  - if needed — a short test plan.

8. Review and merge

- After positive review, merge changes into `main`.
- Before merging, update your branch from `main` if needed:
  - `git checkout main`
  - `git pull origin main`
  - `git checkout <branch-name>`
  - `git merge main`

9. Deleting a branch

- After merge, delete the local and remote branch:
  - `git branch -d <branch-name>`
  - `git push origin --delete <branch-name>`

10. General recommendations

- Work from an up-to-date `main`.
- Avoid working directly on `main`.
- Write understandable commit messages.
- Commit frequently.

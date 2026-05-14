# BioSeq Investigator — app

🇷🇺 Russian version: [README_app.md](README_app.md).

Streamlit app on top of a ProtT5/FAISS embedding retriever and a Gemini chat for follow-up turns. Architecture overview — [ARCHITECTURE_en.md](ARCHITECTURE_en.md). Full interim project report — [../report/REPORT_EN.MD](../report/REPORT_EN.MD).

## What it does

- The user pastes a DNA/protein sequence + a natural-language question.
- **The first turn** in a session goes to the embedding retriever (`bioseq_retriever/src/pipeline.py` via [frontend/embeddings_pipeline.py](frontend/embeddings_pipeline.py)): extract → translate-if-DNA → ProtT5 query embedding → FAISS top-50 over precomputed UniProt embeddings → UniProt REST fetch → LLM rerank top-5. Chat on the left, top-5 candidates on the right.
- **Follow-up turns** go to Gemini via a Cloudflare proxy ([frontend/chat_llm_pipeline.py](frontend/chat_llm_pipeline.py)). The protein card is not rebuilt — the user keeps discussing the selected candidate.
- Session state (chat transcript + top-5 candidates + revealed card sections) is written to `public.chat_sessions` via [frontend/session_db_adapter.py](frontend/session_db_adapter.py); the sidebar shows the list of previous sessions.

## Local run (only if you need to run locally instead of using the live HF deploy — https://huggingface.co/spaces/radda-i/BioSeq_investigator)

```bash
streamlit run app/frontend/app.py
```

Heavy ML deps (`torch`, `transformers`, `faiss`, `h5py`, `sentencepiece`, …) are imported lazily — without them the first Streamlit run does not crash, but the retriever returns a friendly error in chat on the first submit. Install extras: see `requirements.txt` in the repo root.

`per-protein.h5` is pulled automatically from a Hugging Face Dataset on first run if `BIOSEQ_DATA_SOURCE=hf:OWNER/REPO` is set (see [bioseq_retriever/src/bootstrap.py](../bioseq_retriever/src/bootstrap.py)). Otherwise it is expected to live at `BIOSEQ_H5_PATH` (default `bioseq_retriever/data/per-protein.h5`).

## Environment variables (`.env` in the repo root)

**Required for the retriever turn:**
- `MISTRAL_API_KEY` or `OPENAI_API_KEY` — LLM used by the contextual reranker top-50 → top-5. Without it the preflight returns "No LLM credentials available" in chat.
- `BIOSEQ_H5_PATH` (or `BIOSEQ_DATA_SOURCE=hf:OWNER/REPO`) — where to fetch the precomputed protein embeddings.

**Required for the Gemini follow-up chat:**
- `BIOSEQ_LLM_PROXY_URL`, `BIOSEQ_LLM_PROXY_TOKEN` — endpoint and token of the Cloudflare proxy that fronts Gemini.

**Optional:**
- `SUPABASE_DB_URL` — Postgres URL. Without it the repository is `NullSessionRepository`, history is not persisted, and the sidebar shows the "Session history is not persisted" warning. The chat keeps working in the current tab but everything dies on restart/close.
- `BIOSEQ_INDEX_PATH`, `BIOSEQ_ACCESSIONS_CACHE_PATH` — override paths for the FAISS index and accession cache (default: next to `per-protein.h5`).
- `APP_PASSWORD` — single-password gate (used on the HF Spaces deploy; usually unset locally).
- `BIOSEQ_USE_SERVICES` — `embeddings_pipeline` forces it to `false` so the retriever does not go through HTTP microservices; do not change unless you know why.

**Legacy / demo:**
- `BIOSEQ_FRONTEND_BACKEND=mock` — scripted demo conversation from [frontend/mock/conversation.py](frontend/mock/conversation.py). Active only when `config.USE_VECTOR_DB_MODE = False`. By default the flag is on (`True`) and the whole UI goes through `chat_pipeline.run_turn`.
- `BIOSEQ_FRONTEND_BACKEND=real` — old single-shot path through [frontend/backend_adapter.py](frontend/backend_adapter.py). Not used in live mode.

## Layout

| Path | What's inside |
|---|---|
| [frontend/app.py](frontend/app.py) | Streamlit entry point: layout, password gate, identity bootstrap, submit dispatcher |
| [frontend/chat_pipeline.py](frontend/chat_pipeline.py) | Turn router: first turn → embeddings, follow-up → chat-LLM (decision based on `working_memory.turn_count`) |
| [frontend/embeddings_pipeline.py](frontend/embeddings_pipeline.py) | Streamlit adapter around `bioseq_retriever`: preflight, cached ProtT5+FAISS resources, normalization of UniProt JSON into the UI shape, persistence |
| [frontend/chat_llm_pipeline.py](frontend/chat_llm_pipeline.py) | POSTs to the Cloudflare proxy for Gemini, with the current protein context + chat history |
| [frontend/session_db_adapter.py](frontend/session_db_adapter.py) | Cached `PostgresSessionRepository`, the sole writer of `public.chat_sessions` |
| [frontend/session_identity.py](frontend/session_identity.py) | Cookie-based `user_id` (1y) + `session_id` (7d) with two-phase "pending → ready" cookie-controller handling |
| [frontend/backend_choice.py](frontend/backend_choice.py) | Single-backend stub — after the sidebar radio was removed only `embeddings` remained; the file is kept for call-site compatibility |
| [frontend/config.py](frontend/config.py) | One runtime switch — `USE_VECTOR_DB_MODE` (default `True`) |
| [frontend/components/](frontend/components/) | UI: `chat`, `protein_card`, `session_sidebar`, `domain_diagram`, `alignment_viewer` |
| [frontend/mock/](frontend/mock/) | Scripted demo + TypedDict shape (`Candidate`, `ProteinView`, …) — also used as the UI shape for the real backend |
| [backend/](backend/) | **Dormant at runtime**: the old Neo4j graph agent (`agents_core/retriever_agent`, `app_services/graph_retrieval.py`), offline graph build (`graph_core/scripts/`). The frontend never imports these modules. Kept for history. |
| [backend/agents_core/shared/](backend/agents_core/shared/) | `AppContext`, `PostgresSessionRepository`, env loader — used by live code through `session_db_adapter` |

## Known mismatches with the docs

- **[ARCHITECTURE.md](ARCHITECTURE.md) was outdated** until 2026-05-14 (described a Streamlit-over-Neo4j-graph setup, a `graph | embeddings` backend radio, the two-writer persistence scheme). It has been rewritten to match reality — see the EN version [ARCHITECTURE_en.md](ARCHITECTURE_en.md).
- **[frontend/TO-DO.md](frontend/TO-DO.md)** — old frontend↔backend session-model TODO; most P0 items are already done.

## TODO

### Retriever
- [ ] Does the preflight catch a missing `per-protein.h5` correctly in every scenario (no HF source + no local file)? It does not currently check the file — we rely on `_build_pipeline_resources` to fail with a clear message. Worth running an explicit "cold start without data" check.
- [ ] The LLM rerank currently uses Mistral/OpenAI text embeddings → cosine. REPORT mentions that `rerank-by-context` is only meaningfully used for top-50 → top-5; make sure the reranker actually receives a meaningful context, not an empty string (see `state.context` in `bioseq_retriever/src/pipeline.py`).

### Persistence
- [ ] Confirm `SUPABASE_DB_URL` on the HF Spaces deploy (see `~/.claude/.../deploy_hf.md`). Without it sidebar history silently turns off.
- [ ] Add an `is_persistent()` + `current_mode` indicator to the Debug expander so you don't have to dig into SQL to confirm a write.

### Chat-LLM
- [ ] The Cloudflare proxy timeout is 45 s. With large proteins and a detailed context Gemini sometimes responds slower; bump the timeout or surface progress.
- [ ] `_get_current_protein_context` hardcodes a field set. When we add new card sections (variants, pathways, …), remember to extend the Gemini context too.

### Adjacent (P2)
- [ ] Decide the fate of dormant code: `app/backend/agents_core/retriever_agent/`, `app/backend/graph_core/`, `frontend/backend_adapter.py`, `frontend/vector_db_adapter.py`. If the graph direction is definitely closed — delete or mark deprecated so new contributors are not confused.
- [ ] `BioSeqChatService` in `backend/app_services/bioseq_chat.py` is built around `ChatTurnRequest/Result` but not wired up. Either wire it in or delete it — don't leave a half-contract.

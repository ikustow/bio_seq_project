# BioSeq Investigator — app architecture

🇷🇺 Russian version: [ARCHITECTURE.md](ARCHITECTURE.md).

Branch: `ui_streamlit_v3.0`
Date: 2026-05-14
Related docs: full overview and diagrams — [../report/REPORT_EN.MD](../report/REPORT_EN.MD); user-facing app README — [README_app_en.md](README_app_en.md); root README — [../README.md](../README.md) / [../README_RU.md](../README_RU.md).

This document describes the current state of the app: which modules are live, how they talk to each other, how each user turn is routed, and which parts of the code are already dormant. Overview-level, for the team; per-module details live in the docstrings.


---

## 1. App contour

Three-layer setup; all three layers are designed to run in one Streamlit process, but the retrieval microservice can be split out into a separate uvicorn.

```
┌───────────────────────────────────────────────────────────────────────────┐
│                            Browser (Streamlit)                            │
│  cookies: bioseq_user_id (1y), bioseq_session_id (7d)                     │
└───────────────┬───────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  app/frontend  —  Streamlit UI                                            │
│                                                                           │
│  app.py  ─►  components/{chat, protein_card, session_sidebar, ...}        │
│       │                                                                   │
│       ├── session_identity.py    (cookie ↔ user_id / session_id)          │
│       ├── chat_pipeline.py       (turn router by working_memory.turn_count)│
│       ├── embeddings_pipeline.py (1st turn → bioseq_retriever, lazy)      │
│       ├── chat_llm_pipeline.py   (follow-up → Gemini via Cloudflare)      │
│       ├── backend_choice.py      (single-backend stub, "embeddings")      │
│       └── session_db_adapter.py  (bridge to public.chat_sessions)         │
└───────────────┬───────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  bioseq_retriever  —  retrieval pipeline                                  │
│                                                                           │
│  src/pipeline.py — LangGraph DAG:                                         │
│    extract → resolve/raw → translate/pass → rank → rerank                 │
│                                                                           │
│  src/search.py   ──HTTP──►  services/search_service.py (FastAPI)          │
│    (BIOSEQ_USE_SERVICES=true)     │  ProtT5 + FAISS HNSW in-process       │
│                                   │  load per-protein.h5 / .index         │
│                                                                           │
│  src/reranking.py     ── Mistral/OpenAI text embeddings + in-memory FAISS │
│  src/data_fetcher.py  ── UniProt REST                                     │
│  src/utils.py         ── get_llm(), get_text_embedder(), translate, FASTA │
│  src/bootstrap.py     ── ensure_data(): HF Dataset or UniProt FTP         │
└───────────────┬───────────────────┬───────────────────────────────────────┘
                │                   │
                ▼                   ▼
        ┌──────────────┐    ┌────────────────────┐
        │ UniProt REST │    │ Supabase / Postgres│
        │              │    │ public.chat_sessions
        └──────────────┘    └────────────────────┘
                                    ▲
                                    │   (live writer)
                                    │
┌───────────────────────────────────────────────────────────────────────────┐
│  Hugging Face Hub                                                         │
│   • Rostlab/prot_t5_xl_uniref50 (model weights, ~3 GB)                    │
│   • OWNER/bioseq-data (per-protein.h5, .index, .accessions.json)          │
└───────────────────────────────────────────────────────────────────────────┘
                                    ▲
                                    │
┌───────────────────────────────────────────────────────────────────────────┐
│  Cloudflare Worker  ──►  Google Gemini API                                │
│  (follow-up chat for non-first turns)                                     │
└───────────────────────────────────────────────────────────────────────────┘
```

The full architectural diagram with all artifacts is in [../report/REPORT_EN.MD §3](../report/REPORT_EN.MD) and in [../report/diagrams/embedding-retrieval-architecture.svg](../report/diagrams/embedding-retrieval-architecture.svg).

---

## 2. Modules: what's inside and why

### 2.1 `frontend/` — Streamlit UI

Entry point — [app/frontend/app.py](frontend/app.py). It renders the two-column layout (chat on the left, protein card on the right), bootstraps identity, and decides how to handle a user submit.

| File | Responsibility |
|---|---|
| [app.py](frontend/app.py) | Layout, password gate (`APP_PASSWORD`), identity bootstrap, dispatcher: submit → `chat_pipeline.run_turn` (when `USE_VECTOR_DB_MODE=True`, default) |
| [config.py](frontend/config.py) | One runtime switch — `USE_VECTOR_DB_MODE` (default `True`); flip to `False` to drop into legacy / mock mode |
| [session_identity.py](frontend/session_identity.py) | `user_id` (1y cookie) + `session_id` (7d cookie) with a two-phase "pending → ready" reconciliation for `streamlit-cookies-controller` (needed because `getAll()` returns `{}` on render 0) |
| [chat_pipeline.py](frontend/chat_pipeline.py) | Turn router: first turn (`working_memory.turn_count == 0`) → `embeddings_pipeline.run_turn_embeddings`; follow-up → `chat_llm_pipeline.run_turn_chat_llm`. Also: session restore from DB (`restore_session_state`, `auto_restore_if_fresh_load`) and shape normalization via `_candidate_from_backend` |
| [embeddings_pipeline.py](frontend/embeddings_pipeline.py) | Streamlit adapter around `bioseq_retriever`: preflight (deps, LLM key), `@st.cache_resource` for ProtT5+FAISS+reranker, normalizes UniProt JSON into the UI shape via `mock.protein_loader.from_dict`, persists the turn through `session_db_adapter.save_turn(..., current_mode='embeddings_retriever')` |
| [chat_llm_pipeline.py](frontend/chat_llm_pipeline.py) | POST to `BIOSEQ_LLM_PROXY_URL` with `X-BioSeq-Token`. Context: last 20 messages + an expanded card of the currently selected protein (`_get_current_protein_context`). Persists via `session_db_adapter.save_turn(..., update_candidates=False)` so the right-hand card is not touched |
| [backend_choice.py](frontend/backend_choice.py) | Single-backend stub: only `BACKEND_EMBEDDINGS = "embeddings"`. The file is kept for call-site compatibility — previously it held a radio for `graph` / `embeddings` |
| [session_db_adapter.py](frontend/session_db_adapter.py) | Cached `PostgresSessionRepository` (or `NullSessionRepository` when `SUPABASE_DB_URL` is missing); read-merge-write upsert of UI-side fields; reconstruction of candidates and messages from `working_memory` |
| [backend_adapter.py](frontend/backend_adapter.py), [vector_db_adapter.py](frontend/vector_db_adapter.py) | Legacy adapters (`BIOSEQ_FRONTEND_BACKEND=real` single-shot path). Not called on the live path |

#### Components

| File | What it renders |
|---|---|
| [components/chat.py](frontend/components/chat.py) | Left column: message history, streamed assistant reply, Reset, suggestion chips, `chat_input` |
| [components/protein_card.py](frontend/components/protein_card.py) | Right column: 13 protein-card sections (`header`, `alignment`, `keyfacts`, `function`, `expression`, `interactions`, `domains`, `regulation`, `variants`, `structure`, `pathways`, `disease`, `references`) with progressive lock/unlock — `card_sections_revealed` decides which ones are visible |
| [components/session_sidebar.py](frontend/components/session_sidebar.py) | Sidebar: New chat, list of the user's previous sessions, debug ids, persistence warning |
| [components/domain_diagram.py](frontend/components/domain_diagram.py) | Plotly diagram of protein domains |
| [components/alignment_viewer.py](frontend/components/alignment_viewer.py) | Pairwise alignment query↔top-1 |

#### Mock

[mock/conversation.py](frontend/mock/conversation.py) and [mock/protein_loader.py](frontend/mock/protein_loader.py) — the scripted demo mode (requires `USE_VECTOR_DB_MODE=False`, `BIOSEQ_FRONTEND_BACKEND=mock`) and the TypedDict shape for the UI (`Candidate`, `ProteinView`, `DomainFeature`, `DiseaseInfo`). These TypedDicts are also used as the UI shape for the real-backend path, so `mock/` is also the source of truth for the render schema.

### 2.2 `bioseq_retriever/` — retrieval pipeline

The LangGraph pipeline lives in a separate package (no circular dep against `app/`). Detailed pipeline walkthrough — [REPORT_EN.MD §3.1–3.2](../report/REPORT_EN.MD), [retriever-workflow.svg](../report/diagrams/retriever-workflow.svg), [runtime-flow.svg](../report/diagrams/runtime-flow.svg).

| File | Role |
|---|---|
| [src/pipeline.py](../bioseq_retriever/src/pipeline.py) | LangGraph DAG: `extract → (resolve_file \| use_raw) → (translate \| pass_protein) → rank → rerank → END`. Nodes are pure functions of state. Entry points: `create_pipeline()` / `run_bioseq_pipeline(prompt)` |
| [src/search.py](../bioseq_retriever/src/search.py) | HTTP client `search_top_k(sequence, k)` → POST `{SEARCH_SERVICE_URL}/search`. Used by `rank_node` |
| [src/reranking.py](../bioseq_retriever/src/reranking.py) | `LocalReranker.rerank_by_context`: formats UniProt records into text passages, embeds them via `get_text_embedder()` (Mistral / OpenAI), runs cosine via an in-memory `faiss.IndexFlatIP`, returns top_n=5 |
| [src/data_fetcher.py](../bioseq_retriever/src/data_fetcher.py) | `get_uniprot_records(accessions)` → REST `https://rest.uniprot.org/uniprotkb/search` |
| [src/utils.py](../bioseq_retriever/src/utils.py) | `get_llm()` (ChatMistralAI / ChatOpenAI), `get_text_embedder()`, standard codon table + `translate_dna_to_protein`, `clean_sequence`, `is_secure_path`, `get_first_fasta_entry` |
| [src/config.py](../bioseq_retriever/src/config.py) | Env vars: data paths, `EMBEDDING_SERVICE_URL` / `SEARCH_SERVICE_URL`, `USE_SERVICES` (default `true`) |
| [src/bootstrap.py](../bioseq_retriever/src/bootstrap.py) | `ensure_data()`: first-time download of `per-protein.h5` (+ optionally `.index`, `.accessions.json`) per `BIOSEQ_DATA_SOURCE` — `hf:OWNER/REPO` (via `huggingface_hub.hf_hub_download`) or `uniprot` (UniProt FTP). Idempotent |
| [src/api_client.py](../bioseq_retriever/src/api_client.py) | Centralized HTTP client with connection pooling and exponential retry |
| [services/search_service.py](../bioseq_retriever/services/search_service.py) | FastAPI on `BIOSEQ_SEARCH_SERVICE_URL` (default `:8002`). On startup: loads `Rostlab/prot_t5_xl_uniref50`, reads `per-protein.h5` in batches, L2-normalizes, builds/loads a FAISS HNSW index. `POST /search` endpoint: embeds the input sequence with ProtT5 (mean residue embedding), normalizes, searches top-k in HNSW |
| [services/config.py](../bioseq_retriever/services/config.py) | HNSW parameters (`M`, `efConstruction`, `efSearch`), port, model name |
| [pipeline_interface.py](../bioseq_retriever/pipeline_interface.py) | CLI wrapper for running the pipeline outside of Streamlit |

### 2.3 `backend/` — persistence + dormant graph agent

Used on the live path only partially. The active piece:

| File | Role |
|---|---|
| [backend/agents_core/shared/config.py](backend/agents_core/shared/config.py) | `.env` loader, `DEFAULT_ENV_PATH` |
| [backend/agents_core/shared/models.py](backend/agents_core/shared/models.py) | `AppContext` — carries `user_id`, `session_id`, `workspace_id`, `user_role` |
| [backend/agents_core/shared/services/persistence.py](backend/agents_core/shared/services/persistence.py) | `PostgresSessionRepository` (CRUD on `public.chat_sessions`), `NullSessionRepository` fallback. Used through `session_db_adapter` |

**Dormant at runtime** (the frontend doesn't import these; kept for history and a potential graph-backend revival):

- [`backend/agents_core/retriever_agent/`](backend/agents_core/retriever_agent/) — `BioSeqRetrieverGraphAgent` (LangGraph + Neo4j).
- [`backend/app_services/`](backend/app_services/) — `service_factory`, `graph_retrieval`, `protein_view_mapper`, `bioseq_chat`. `BioSeqChatService` is built around `ChatTurnRequest/Result`, but the frontend goes around it and uses `chat_pipeline` directly.
- [`backend/app_contracts/`](backend/app_contracts/) — pydantic contracts `ProteinView`, `DomainFeature`, `DiseaseInfo`, `CandidateView`, `ChatTurnRequest`, `ChatTurnResult`. The UI shape matches these types after the unification, but in practice the UI works through the `mock/protein_loader.TypedDict` shapes.
- [`backend/graph_core/`](backend/graph_core/) — offline scripts for building the Neo4j graph (UniProt → CSV → Neo4j); not called from runtime.

---

## 3. Data flow of one turn

Shorthand: "cards" = `list[Candidate]` UI shape; "raw" = UniProt JSON records.

### 3.1 Page bootstrap (every Streamlit rerun)

```
[Browser]                            [app.py]                         [session_identity]
    │                                    │                                    │
    │ GET /  (cookies: user/session)     │                                    │
    │───────────────────────────────────►│ bootstrap_identity()               │
    │                                    │───────────────────────────────────►│
    │                                    │  read cookies (pending/ready)      │
    │                                    │  reconcile with st.session_state   │
    │                                    │◄───────────────────────────────────│
    │                                    │  user_id, session_id               │
    │                                    │                                    │
    │                                    │  if session_id and USE_VECTOR_DB_MODE
    │                                    │  → chat_pipeline.auto_restore_     │
    │                                    │     if_fresh_load(session_id)      │
    │                                    │     (one-shot, only if history is fresh)
```

Fires once per session_id (guarded by `_auto_restore_attempted`) and only when `messages` ≤ 1 welcome + no `candidates` — to avoid clobbering an in-progress chat when the user clicks around the sidebar.

### 3.2 Submit → first turn (retriever)

```
[chat_input]
    │ "MAVL... what is this?"
    ▼
components/chat._handle_submission()
    │ st.session_state.messages.append({user, ...})
    │
    ▼ on_submit = app._handle_vector_db_submission
chat_pipeline.run_turn(prompt)
    │
    ├── _is_first_turn_in_session()   ← reads working_memory.turn_count
    │   from public.chat_sessions
    │   ├─ True  → embeddings_pipeline.run_turn_embeddings(prompt)
    │   └─ False → chat_llm_pipeline.run_turn_chat_llm(prompt)     (3.3)
    │
    ▼ embeddings_pipeline.run_turn_embeddings(prompt)
    │
    │ context = session_db_adapter.make_context(user_id, session_id, ...)
    │
    │ _preflight_check():
    │   missing deps (torch / transformers / faiss / h5py)? → friendly error
    │   MISTRAL_API_KEY / OPENAI_API_KEY missing?           → friendly error
    │
    │ resources = _build_pipeline_resources()   # @st.cache_resource
    │   ├── bootstrap.ensure_data()             # download per-protein.h5 if missing
    │   ├── BIOSEQ_USE_SERVICES = "false"       # force in-process path
    │   └── build embedder + index + reranker  ← see ⚠ below
    │
    │ result = _run_legacy_pipeline(prompt, resources):
    │   ├─ extract_and_classify_node      ← LLM structured output (Mistral/OpenAI)
    │   ├─ use_raw_sequence_node / resolve_filepath_node
    │   ├─ translate_dna_node / pass_protein_node     ← standard codon table
    │   ├─ rank_node:
    │   │     search_top_k(protein_seq, k=50)         ← HTTP to /search in services/
    │   │     get_uniprot_records(top-50 accessions)  ← UniProt REST
    │   │     attach _bioseq_embedding_score per record
    │   └─ rerank_node:
    │         LocalReranker.rerank_by_context(records, context, top_n=5)
    │           text embeddings (Mistral/OpenAI) + in-memory FAISS cosine
    │
    │ ui_candidates = [_candidate_from_legacy(rec) for rec in final_results]
    │ reply         = _assistant_message(result)          # markdown summary
    │ reveals       = _revealed_sections(ui_candidates, query_protein_sequence)
    │
    ▼ _safe_save_turn(context, prompt, reply, raw_candidates, reveals, ...)
    │
session_db_adapter.save_turn(... current_mode='embeddings_retriever')
    │ read-merge-write:
    │   proteins        = merge(saved, new top-5 compact)     # by accession
    │   working_memory  = {
    │       ...saved_wm,
    │       messages: [...prior, user, assistant],            # last 200
    │       last_candidates: candidates[:20],                  # full cards for restore
    │       last_query_protein_sequence,
    │       last_revealed_sections,
    │       turn_count: saved_wm.turn_count + 1,
    │       ui_writer: 'streamlit_frontend',
    │   }
    │   active_accession = top-1 OR saved
    │   current_mode     = 'embeddings_retriever'
    │ repo.upsert_session(context, state)
    │
chat_pipeline returns dict {reply, candidates, candidates_raw, reveals,
                            warnings, result, persisted, backend, update_card=True}
    │
    ▼
app._handle_vector_db_submission
    │ st.session_state.candidates = outcome.candidates
    │ st.session_state.selected_candidate_idx = 0
    │ st.session_state.card_sections_revealed = set(outcome.reveals)
    │ st.session_state.query_protein_sequence = outcome.query_protein_sequence
    │ st.session_state.pending_assistant      = outcome.reply
    │
    ▼ st.rerun()
       components/chat streams pending_assistant into chat_message
       components/protein_card.render() draws the new sections
```

> ⚠ The exact chain of imports inside `_build_pipeline_resources` is currently out of sync with the retriever, which moved to services. The live HF Space depends on a patched copy in `deploy/hf-spaces`. Details and migration plan — in [TODO.MD](TODO.MD) (section "Architecture / drift").

### 3.3 Submit → follow-up (Gemini via Cloudflare)

Identical to 3.2 up to `_is_first_turn_in_session() == False`. Then:

```
chat_llm_pipeline.run_turn_chat_llm(prompt)
    │ _call_gemini_proxy(prompt):
    │   payload = {
    │     contents: _build_gemini_contents(prompt),       # last 20 messages
    │     systemInstruction: <"expert assistant for protein sequence analysis">,
    │     generationConfig: { temperature: 0.2, maxOutputTokens: 4096 },
    │   }
    │   POST BIOSEQ_LLM_PROXY_URL  X-BioSeq-Token: BIOSEQ_LLM_PROXY_TOKEN
    │   timeout=45s
    │   → reply = _extract_gemini_text(response.json())
    │
    │ Protein context (injected as a first user-message before history):
    │   _get_current_protein_context():
    │     accession, name, gene, organism, match_score, length, mol_weight,
    │     function_text, tissue_specificity, subunit_text, subcellular_locations,
    │     domains[:5], interactions[:3], disease, keywords[:8], pathways[:3]
    │
    │ session_db_adapter.save_turn(
    │       candidates=[],
    │       update_candidates=False,         # proteins/last_candidates/active_accession
    │                                         # — NOT touched
    │       current_mode='chat_llm' | 'chat_llm_error',
    │ )
    │
    └── return {update_card: False, candidates: current, reveals: current, ...}
```

`update_card=False` matters: inside `app._handle_vector_db_submission` it means "don't rebuild `st.session_state.candidates` and don't reset `selected_candidate_idx`". The user keeps seeing the result of the previous retriever turn while Gemini answers questions about that same protein.

### 3.4 Session restore (reload / sidebar switch)

```
session_sidebar._switch_to_session(sid)        ──┐
session_identity.switch_session(sid)             ├─► chat_pipeline.restore_session_state(sid)
                                               ──┘             │
app._bootstrap_session() on reload:                            │
   chat_pipeline.auto_restore_if_fresh_load(session_id) ───────┘
                                                               │
                                                               ▼
                                          session_db_adapter.load_session(session_id)
                                                               │
                                                               ▼
                                            extract_candidates(row.working_memory.last_candidates)
                                            extract_messages(row.working_memory.messages)
                                                               │
                                                               ▼
                                            st.session_state.candidates / messages /
                                            card_sections_revealed / query_protein_sequence
```

See also [session-restore.svg](../report/diagrams/session-restore.svg) in REPORT_EN.MD.

---

## 4. What lives in `public.chat_sessions`

One row per `session_id`. After the Neo4j agent was retired, this row has a single writer — [session_db_adapter.save_turn](frontend/session_db_adapter.py).

| Field | Written by | Holds |
|---|---|---|
| `session_id`, `thread_id`, `user_id`, `workspace_id`, `user_role` | UI | identity (`thread_id == session_id`) |
| `session_summary` | UI | a short summary of the latest turn (fallback if nothing better) |
| `proteins` | UI | compact `ProteinRecord` per accession (top-1+top-5, deduped by accession) |
| `sequences` | UI | persisted as `saved.get("sequences")` (legacy from the graph agent); usually empty now |
| `working_memory.messages` | UI | chat transcript, last 200 messages |
| `working_memory.last_candidates` | UI | up to 20 full cards — the right column is rebuilt from this |
| `working_memory.last_query_protein_sequence` | UI | for the alignment section |
| `working_memory.last_revealed_sections` | UI | which card sections were unlocked |
| `working_memory.turn_count` | UI | user-turn counter (used for the first-vs-followup decision) |
| `working_memory.ui_writer` | UI | constant `'streamlit_frontend'` |
| `active_accession`, `active_sequence_id`, `working_set_ids` | UI, merge | top-1 accession + accumulated history (last 40) |
| `current_mode` | UI (override > saved) | `embeddings_retriever` / `chat_llm` / `chat_llm_error` |
| `last_tool_results_summary`, `last_analysis_summary` | UI | bookkeeping summary fields |

If `SUPABASE_DB_URL` is not set — repository becomes `NullSessionRepository`, `is_persistent() == False`, the sidebar shows the "Session history is not persisted" warning, and **follow-up routing degrades to "always first turn"** (`_is_first_turn_in_session` returns `True` without DB, so every submit hits the retriever — `chat_llm_pipeline` never fires without persistence).

---

## 5. Decision logic

Every user-visible "if/else", in one place. Each node is a real branch in code.

### 5.1 Page level

```
Page open
├── APP_PASSWORD present?  (env or st.secrets)
│   ├─ yes → password gate (app._require_password)
│   └─ no  → skip
│
├── bootstrap_identity()
│   ├── controller pending (render 0) → mint temp id, DO NOT write cookie
│   ├── controller ready, cookie present → adopt
│   └── controller ready, cookie empty  → mint and write cookie
│
└── USE_VECTOR_DB_MODE and session_id present?
    ├─ yes → chat_pipeline.auto_restore_if_fresh_load() (one-shot)
    └─ no  → demo/legacy branch (BIOSEQ_FRONTEND_BACKEND=mock|real)
```

### 5.2 Submit level

```
user submitted a message
│
├── on_submit = _handle_vector_db_submission (USE_VECTOR_DB_MODE=True, default)
│   └── chat_pipeline.run_turn(prompt)
│
└── on_submit = None (USE_VECTOR_DB_MODE=False)
    ├─ BIOSEQ_FRONTEND_BACKEND=mock → conversation.route() (scripted demo)
    └─ BIOSEQ_FRONTEND_BACKEND=real → backend_adapter.run_search (legacy single-shot)

chat_pipeline.run_turn:
│
├── _is_first_turn_in_session()  ← turn_count from public.chat_sessions
│   ├─ True  → embeddings_pipeline.run_turn_embeddings
│   │           ├── _missing_packages() ≠ [] → friendly error
│   │           ├── no MISTRAL/OPENAI key   → friendly error
│   │           └── all good → ProtT5 + FAISS + LocalReranker
│   │           update_card=True
│   │
│   └─ False → chat_llm_pipeline.run_turn_chat_llm
│               ├── BIOSEQ_LLM_PROXY_URL/TOKEN missing → friendly error
│               └── POST → Gemini → reply
│               update_card=False
│
└── outcome dict of the same shape for both backends
```

### 5.3 Inside the LangGraph retriever

```
extract_and_classify_node
└── LLM with structured_output (InputExtraction)
    ├── input_type: SEQUENCE | FILEPATH
    └── sequence_type: DNA | PROTEIN

input_type == FILEPATH? ─ yes ─► resolve_file (is_secure_path → ALLOWED_DATA_DIR)
                       └── no ──► use_raw_sequence (clean_sequence: strip header, A-Z only)

sequence_type == DNA?  ─ yes ─► translate_dna  (standard codon table; len % 3 == 0)
                       └── no ──► pass_protein

rank_node:
└── search_top_k(protein, k=50)  ──HTTP──► services/search_service.py
        ProtT5 mean-residue embedding → FAISS HNSW IP cosine → top-50
    get_uniprot_records(accessions)  ── UniProt REST search
    attach _bioseq_embedding_score per record

rerank_node:
└── LocalReranker.rerank_by_context(records, context, top_n=5)
        _format_record_for_reranking: "Gene: X; Organism: Y; Protein: Z; Description: ..."
        get_text_embedder()  ── Mistral mistral-embed or OpenAI text-embedding-3-small
        in-memory faiss.IndexFlatIP with normalize_L2 → top-5

Any node returning error → graph short-circuits to END.
```

### 5.4 Persistence level

```
session_db_adapter.save_turn(...)
├── repository — Null? → no-op, return None
├── update_candidates=True (retriever turn):
│   ├── proteins      = merge(saved.proteins, new top-5 compact) by accession
│   ├── last_candidates = candidates[:20]
│   ├── revealed_list = sort(unique(reveals))
│   ├── working_set_ids = (saved + new accessions)[-40:]
│   └── active_accession = new top-1 OR saved
│
└── update_candidates=False (chat-LLM follow-up):
    ├── proteins/last_candidates/working_set_ids/active_accession = saved (as-is)
    ├── revealed_list = saved.last_revealed_sections (unless override)
    └── messages, turn_count, last_user/assistant_message — updated as usual
```

### 5.5 Identity level (cookie controller)

```
Cookie controller lifecycle
├── render 0 (state="pending"):
│   ├── st.session_state[key] exists → reuse, set pending_promotion flag
│   └── doesn't exist                → mint temp, set pending_promotion flag
│       (no cookie write in this render — JS hasn't returned real values yet)
│
└── render 1+ (state="ready"):
    ├── cookie populated → adopt cookie (overrides temp), clear flag
    └── cookie empty:
        ├── temp exists → promote temp to cookie
        └── no temp     → mint and write cookie

start_new_session (Reset / New chat):
├── controller ready → write a fresh session_id to cookie immediately
└── controller pending → set pending_promotion, bootstrap will promote on next rerun

switch_session (sidebar): same, but the user picks the session_id
```

The "why" is in the docstring of [session_identity.py](frontend/session_identity.py): `streamlit-cookies-controller.getAll()` returns `{}` on render 0 even for returning users, and writing a cookie at that moment silently overwrites the existing one.

---

## 6. Data artifacts: lifecycle

| Artifact | Storage / source of truth | Runtime cache |
|---|---|---|
| `per-protein.h5` (~1.3 GB, 574,615 × 1024 ProtT5 mean-residue embeddings) | Hugging Face Dataset (`BIOSEQ_DATA_SOURCE=hf:OWNER/REPO`) or UniProt FTP | `bioseq_retriever/data/per-protein.h5` |
| `per-protein.index` (FAISS HNSW, ~2.5 GB) | HF Dataset (optional); otherwise built from `.h5` on first launch of `services/search_service.py` | `bioseq_retriever/data/per-protein.index` |
| `per-protein.accessions.json` | HF Dataset (next to `.index`) or built at index-build time | `bioseq_retriever/data/per-protein.accessions.json` (`bootstrap.py` historically uses `.pkl` — a mismatch with `search_service.py`, which writes `.json`; see TODO in `README_app_en.md`) |
| `Rostlab/prot_t5_xl_uniref50` (model weights, ~3 GB) | Hugging Face Model Hub | HF cache (default `~/.cache/huggingface`) |
| UniProt JSON records | UniProt REST `https://rest.uniprot.org/uniprotkb/search` | not cached (fetched per turn) |
| `public.chat_sessions` | Supabase / Postgres | — |

Bootstrap (`src/bootstrap.py::ensure_data`) is idempotent: calling it n times downloads missing artifacts exactly once. This lets the live HF Space boot without a separate pre-warm step — the first ProtT5 query triggers model download, data download, and index build all at once.

Full artifact activity diagram: [REPORT_EN.MD §2.4](../report/REPORT_EN.MD), [data-artifact-lifecycle.svg](../report/diagrams/data-artifact-lifecycle.svg).

---

## 7. External dependencies (what must be available)

| Dependency | Why | What to set |
|---|---|---|
| Hugging Face Model Hub | ProtT5 weights | network access; if the model is private — `HF_TOKEN` |
| Hugging Face Dataset (optional) | `per-protein.h5` + index | `BIOSEQ_DATA_SOURCE=hf:OWNER/REPO` |
| UniProt REST | metadata for top-50 candidates | network access; retry is built into `api_client.py` |
| Mistral API | `get_llm()` extract/classify + `get_text_embedder()` rerank | `MISTRAL_API_KEY` |
| OpenAI API (fallback) | same, when Mistral is unavailable | `OPENAI_API_KEY` |
| Supabase Postgres | `public.chat_sessions` | `SUPABASE_DB_URL` |
| Cloudflare Worker → Gemini | follow-up chat | `BIOSEQ_LLM_PROXY_URL`, `BIOSEQ_LLM_PROXY_TOKEN` |

Full env-vars table with optionality — in [../README.md](../README.md) and [README_app_en.md](README_app_en.md).

---

## 8. Open work

Known drift, dormant code, and other deferred decisions are tracked in [TODO.MD](TODO.MD) (section "Architecture / drift"). Same place for how-to notes on extension (adding a new retriever backend, evolving the chat-LLM module).

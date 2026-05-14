# BioSeq Investigator — архитектура app

🇬🇧 English version: [ARCHITECTURE_en.md](ARCHITECTURE_en.md).

Ветка: `ui_streamlit_v3.0`
Дата: 2026-05-14
Связанные документы: общий обзор и диаграммы — [../report/REPORT.MD](../report/REPORT.MD); пользовательский README app-а — [README_app.md](README_app.md); корневой README — [../README.md](../README.md) / [../README_RU.md](../README_RU.md).

Документ описывает текущее состояние app: какие модули запускаются live, как они общаются, по какой логике обрабатывается каждый ход пользователя и какие части кода уже dormant. Уровень — обзорный, для команды; детали внутри модулей живут в docstring-ах файлов.


---

## 1. Контур приложения

Three-layer setup, все три слоя нацелены на work in one Streamlit process, но retrieval-микросервис может быть вынесен в отдельный uvicorn.

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
│       └── session_db_adapter.py  (мост к public.chat_sessions)            │
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
│  src/bootstrap.py     ── ensure_data(): HF Dataset или UniProt FTP        │
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
│  (follow-up чат для не-первого turn-а)                                    │
└───────────────────────────────────────────────────────────────────────────┘
```

Полная архитектурная диаграмма с указанием всех артефактов лежит в [../report/REPORT.MD §3](../report/REPORT.MD) и в [../report/diagrams/embedding-retrieval-architecture.svg](../report/diagrams/embedding-retrieval-architecture.svg).

---

## 2. Модули: что внутри и зачем

### 2.1 `frontend/` — Streamlit UI

Точка входа — [app/frontend/app.py](frontend/app.py). Файл рисует двухколоночный layout (chat слева, protein card справа), bootstrap-ит идентичность и решает, как обрабатывать submit пользователя.

| Файл | Ответственность |
|---|---|
| [app.py](frontend/app.py) | Layout, password gate (`APP_PASSWORD`), identity bootstrap, диспетчер: submit → `chat_pipeline.run_turn` (при `USE_VECTOR_DB_MODE=True`, default) |
| [config.py](frontend/config.py) | Один runtime-switch `USE_VECTOR_DB_MODE` (default `True`) — флипнуть на `False`, чтобы UI ушёл в legacy/mock-режим |
| [session_identity.py](frontend/session_identity.py) | `user_id` (1y cookie) + `session_id` (7d cookie) с двухфазным «pending → ready» реконсилем для `streamlit-cookies-controller` (нужен из-за того, что `getAll()` возвращает `{}` на render 0) |
| [chat_pipeline.py](frontend/chat_pipeline.py) | Turn-router: первый turn (`working_memory.turn_count == 0`) → `embeddings_pipeline.run_turn_embeddings`; follow-up → `chat_llm_pipeline.run_turn_chat_llm`. Также: восстановление сессии из БД (`restore_session_state`, `auto_restore_if_fresh_load`) и нормализация формы `_candidate_from_backend` |
| [embeddings_pipeline.py](frontend/embeddings_pipeline.py) | Streamlit-адаптер вокруг `bioseq_retriever`: preflight (deps, LLM key), `@st.cache_resource` для ProtT5+FAISS+reranker, нормализация UniProt JSON в UI shape через `mock.protein_loader.from_dict`, запись turn-а через `session_db_adapter.save_turn(..., current_mode='embeddings_retriever')` |
| [chat_llm_pipeline.py](frontend/chat_llm_pipeline.py) | POST в `BIOSEQ_LLM_PROXY_URL` с `X-BioSeq-Token`. Контекст: последние 20 сообщений + развёрнутая карточка текущего выбранного белка (`_get_current_protein_context`). Persist через `session_db_adapter.save_turn(..., update_candidates=False)` — не трогает карточку справа |
| [backend_choice.py](frontend/backend_choice.py) | Single-backend stub: только `BACKEND_EMBEDDINGS = "embeddings"`. Файл оставлен для call-site совместимости — раньше тут жил radio с выбором `graph` / `embeddings` |
| [session_db_adapter.py](frontend/session_db_adapter.py) | Cached `PostgresSessionRepository` (или `NullSessionRepository` при отсутствии `SUPABASE_DB_URL`); read-merge-write upsert UI-полей; восстановление кандидатов и сообщений из `working_memory` |
| [backend_adapter.py](frontend/backend_adapter.py), [vector_db_adapter.py](frontend/vector_db_adapter.py) | Legacy-адаптеры (`BIOSEQ_FRONTEND_BACKEND=real` single-shot путь). На live-пути не вызываются |

#### Components

| Файл | Что рендерит |
|---|---|
| [components/chat.py](frontend/components/chat.py) | Левая колонка: история сообщений, streamed assistant reply, Reset, suggestion-chip, `chat_input` |
| [components/protein_card.py](frontend/components/protein_card.py) | Правая колонка: 13 секций карточки белка (`header`, `alignment`, `keyfacts`, `function`, `expression`, `interactions`, `domains`, `regulation`, `variants`, `structure`, `pathways`, `disease`, `references`) с прогрессивным lock/unlock — `card_sections_revealed` решает, какие видны |
| [components/session_sidebar.py](frontend/components/session_sidebar.py) | Sidebar: New chat, список прошлых сессий пользователя, debug-ids, warning о persistence |
| [components/domain_diagram.py](frontend/components/domain_diagram.py) | Plotly-диаграмма доменов |
| [components/alignment_viewer.py](frontend/components/alignment_viewer.py) | Pairwise alignment query↔top-1 |

#### Mock

[mock/conversation.py](frontend/mock/conversation.py) и [mock/protein_loader.py](frontend/mock/protein_loader.py) — скриптованный демо-режим (требует `USE_VECTOR_DB_MODE=False`, `BIOSEQ_FRONTEND_BACKEND=mock`) и TypedDict-shape для UI (`Candidate`, `ProteinView`, `DomainFeature`, `DiseaseInfo`). Эти TypedDict-ы используются и как UI-shape для real-backend пути, поэтому `mock/` — это ещё и source of truth для render-схемы.

### 2.2 `bioseq_retriever/` — retrieval pipeline

LangGraph-пайплайн вынесен в отдельный пакет (поверх `app/` нет circular dep). Подробный обзор пайплайна — [REPORT.MD §3.1–3.2](../report/REPORT.MD), [retriever-workflow.svg](../report/diagrams/retriever-workflow.svg), [runtime-flow.svg](../report/diagrams/runtime-flow.svg).

| Файл | Роль |
|---|---|
| [src/pipeline.py](../bioseq_retriever/src/pipeline.py) | LangGraph DAG: `extract → (resolve_file \| use_raw) → (translate \| pass_protein) → rank → rerank → END`. Узлы — pure-функции состояния. Точка входа `create_pipeline()` / `run_bioseq_pipeline(prompt)` |
| [src/search.py](../bioseq_retriever/src/search.py) | HTTP-клиент `search_top_k(sequence, k)` → POST `{SEARCH_SERVICE_URL}/search`. Используется `rank_node` |
| [src/reranking.py](../bioseq_retriever/src/reranking.py) | `LocalReranker.rerank_by_context`: форматирует UniProt records в текстовые passages, эмбеддит через `get_text_embedder()` (Mistral / OpenAI), считает cosine через in-memory `faiss.IndexFlatIP`, возвращает top_n=5 |
| [src/data_fetcher.py](../bioseq_retriever/src/data_fetcher.py) | `get_uniprot_records(accessions)` → REST `https://rest.uniprot.org/uniprotkb/search` |
| [src/utils.py](../bioseq_retriever/src/utils.py) | `get_llm()` (ChatMistralAI / ChatOpenAI), `get_text_embedder()`, standard codon table + `translate_dna_to_protein`, `clean_sequence`, `is_secure_path`, `get_first_fasta_entry` |
| [src/config.py](../bioseq_retriever/src/config.py) | env vars: пути к данным, `EMBEDDING_SERVICE_URL` / `SEARCH_SERVICE_URL`, `USE_SERVICES` (default `true`) |
| [src/bootstrap.py](../bioseq_retriever/src/bootstrap.py) | `ensure_data()`: первая загрузка `per-protein.h5` (+ опционально `.index`, `.accessions.json`) по `BIOSEQ_DATA_SOURCE` — `hf:OWNER/REPO` (через `huggingface_hub.hf_hub_download`) или `uniprot` (UniProt FTP). Идемпотентно |
| [src/api_client.py](../bioseq_retriever/src/api_client.py) | Centralized HTTP client с пулом коннекций и экспоненциальным retry |
| [services/search_service.py](../bioseq_retriever/services/search_service.py) | FastAPI на `BIOSEQ_SEARCH_SERVICE_URL` (default `:8002`). При старте: загружает `Rostlab/prot_t5_xl_uniref50`, читает `per-protein.h5` пачками, нормализует L2, строит/грузит FAISS HNSW индекс. Эндпоинт `POST /search`: эмбеддит входную sequence через ProtT5 (`mean residue embedding`), нормализует, ищет top-k в HNSW |
| [services/config.py](../bioseq_retriever/services/config.py) | HNSW-параметры (`M`, `efConstruction`, `efSearch`), порт, имя модели |
| [pipeline_interface.py](../bioseq_retriever/pipeline_interface.py) | CLI-обёртка для запуска пайплайна вне Streamlit |

### 2.3 `backend/` — persistence + dormant graph-агент

Используется на live-пути только частично. Активный кусок:

| Файл | Роль |
|---|---|
| [backend/agents_core/shared/config.py](backend/agents_core/shared/config.py) | `.env` loader, `DEFAULT_ENV_PATH` |
| [backend/agents_core/shared/models.py](backend/agents_core/shared/models.py) | `AppContext` — носит `user_id`, `session_id`, `workspace_id`, `user_role` |
| [backend/agents_core/shared/services/persistence.py](backend/agents_core/shared/services/persistence.py) | `PostgresSessionRepository` (CRUD `public.chat_sessions`), `NullSessionRepository` fallback. Используется через `session_db_adapter` |

**Dormant в runtime** (frontend в эти модули не ходит, оставлены для истории и возможного возврата к graph-варианту):

- [`backend/agents_core/retriever_agent/`](backend/agents_core/retriever_agent/) — `BioSeqRetrieverGraphAgent` (LangGraph + Neo4j).
- [`backend/app_services/`](backend/app_services/) — `service_factory`, `graph_retrieval`, `protein_view_mapper`, `bioseq_chat`. `BioSeqChatService` собран под `ChatTurnRequest/Result`, но frontend ходит мимо него через `chat_pipeline` напрямую.
- [`backend/app_contracts/`](backend/app_contracts/) — pydantic-контракты `ProteinView`, `DomainFeature`, `DiseaseInfo`, `CandidateView`, `ChatTurnRequest`, `ChatTurnResult`. UI-shape совпадает с этими типами после унификации, но реально UI работает через `mock/protein_loader.TypedDict`-формы.
- [`backend/graph_core/`](backend/graph_core/) — оффлайн-скрипты сборки Neo4j-графа (UniProt → CSV → Neo4j); не вызываются из runtime.

---

## 3. Поток данных одного хода

Сокращения: «cards» = `list[Candidate]` UI-shape; «raw» = UniProt JSON-record-ы.

### 3.1 Bootstrap страницы (каждый rerun Streamlit)

```
[Browser]                            [app.py]                         [session_identity]
    │                                    │                                    │
    │ GET /  (cookies: user/session)     │                                    │
    │───────────────────────────────────►│ bootstrap_identity()               │
    │                                    │───────────────────────────────────►│
    │                                    │  read cookies (pending/ready)      │
    │                                    │  reconcile с st.session_state      │
    │                                    │◄───────────────────────────────────│
    │                                    │  user_id, session_id               │
    │                                    │                                    │
    │                                    │  if session_id и USE_VECTOR_DB_MODE│
    │                                    │  → chat_pipeline.auto_restore_     │
    │                                    │     if_fresh_load(session_id)      │
    │                                    │     (одноразово, если history свежая)
```

Срабатывает один раз на session_id (флаг `_auto_restore_attempted`) и только если `messages` ≤ 1 welcome + нет `candidates` — чтобы не затирать живой чат при кликах по сайдбару.

### 3.2 Submit → первый turn (retriever)

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
    ├── _is_first_turn_in_session()   ← читает working_memory.turn_count
    │   из public.chat_sessions
    │   ├─ True  → embeddings_pipeline.run_turn_embeddings(prompt)
    │   └─ False → chat_llm_pipeline.run_turn_chat_llm(prompt)     (3.3)
    │
    ▼ embeddings_pipeline.run_turn_embeddings(prompt)
    │
    │ context = session_db_adapter.make_context(user_id, session_id, ...)
    │
    │ _preflight_check():
    │   missing deps (torch / transformers / faiss / h5py)? → friendly error
    │   MISTRAL_API_KEY / OPENAI_API_KEY отсутствует?        → friendly error
    │
    │ resources = _build_pipeline_resources()   # @st.cache_resource
    │   ├── bootstrap.ensure_data()             # download per-protein.h5 если нет
    │   ├── BIOSEQ_USE_SERVICES = "false"       # форсим in-process путь
    │   └── build embedder + index + reranker  ← см. ⚠ ниже
    │
    │ result = _run_legacy_pipeline(prompt, resources):
    │   ├─ extract_and_classify_node      ← LLM structured output (Mistral/OpenAI)
    │   ├─ use_raw_sequence_node / resolve_filepath_node
    │   ├─ translate_dna_node / pass_protein_node     ← standard codon table
    │   ├─ rank_node:
    │   │     search_top_k(protein_seq, k=50)         ← HTTP к /search в services/
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
    │       messages: [...прошлые, user, assistant],          # последние 200
    │       last_candidates: candidates[:20],                  # full cards для restore
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
       components/chat стримит pending_assistant в chat_message
       components/protein_card.render() рисует новые секции
```

> ⚠ Конкретная цепочка импортов в `_build_pipeline_resources` сейчас рассинхронизирована с retriever-ом, который ушёл на services. Live HF Space держится на правленой копии в `deploy/hf-spaces`. Детали и план миграции — в [TODO.MD](TODO.MD) (раздел «Архитектура / drift»).

### 3.3 Submit → follow-up (Gemini через Cloudflare)

Идентично 3.2 до момента `_is_first_turn_in_session() == False`. Дальше:

```
chat_llm_pipeline.run_turn_chat_llm(prompt)
    │ _call_gemini_proxy(prompt):
    │   payload = {
    │     contents: _build_gemini_contents(prompt),       # последние 20 сообщений
    │     systemInstruction: <"expert assistant for protein sequence analysis">,
    │     generationConfig: { temperature: 0.2, maxOutputTokens: 4096 },
    │   }
    │   POST BIOSEQ_LLM_PROXY_URL  X-BioSeq-Token: BIOSEQ_LLM_PROXY_TOKEN
    │   timeout=45s
    │   → reply = _extract_gemini_text(response.json())
    │
    │ Контекст белка (вставляется первым user-message-ем перед историей):
    │   _get_current_protein_context():
    │     accession, name, gene, organism, match_score, length, mol_weight,
    │     function_text, tissue_specificity, subunit_text, subcellular_locations,
    │     domains[:5], interactions[:3], disease, keywords[:8], pathways[:3]
    │
    │ session_db_adapter.save_turn(
    │       candidates=[],
    │       update_candidates=False,         # proteins/last_candidates/active_accession
    │                                         # — НЕ трогаем
    │       current_mode='chat_llm' | 'chat_llm_error',
    │ )
    │
    └── return {update_card: False, candidates: текущие, reveals: текущие, ...}
```

`update_card=False` важен: в `app._handle_vector_db_submission` это означает «не пересобирать `st.session_state.candidates` и не сбрасывать `selected_candidate_idx`». Пользователь продолжает видеть результат прошлого retriever-turn-а, а Gemini отвечает на вопросы по этому же белку.

### 3.4 Восстановление сессии (reload / sidebar switch)

```
session_sidebar._switch_to_session(sid)        ──┐
session_identity.switch_session(sid)             ├─► chat_pipeline.restore_session_state(sid)
                                               ──┘             │
app._bootstrap_session() при reload:                           │
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

См. также [session-restore.svg](../report/diagrams/session-restore.svg) в REPORT.MD.

---

## 4. Что лежит в `public.chat_sessions`

Одна строка на `session_id`. После удаления Neo4j-агента у этой строки один writer — [session_db_adapter.save_turn](frontend/session_db_adapter.py).

| Поле | Кто пишет | Что хранит |
|---|---|---|
| `session_id`, `thread_id`, `user_id`, `workspace_id`, `user_role` | UI | identity (`thread_id == session_id`) |
| `session_summary` | UI | короткий summary последнего turn-а (фолбэк, если ничего интереснее нет) |
| `proteins` | UI | compact `ProteinRecord` per accession (top-1+top-5, дедуп по accession) |
| `sequences` | UI | сохраняется как `saved.get("sequences")` (наследие от graph-агента); сейчас обычно пусто |
| `working_memory.messages` | UI | chat transcript, последние 200 сообщений |
| `working_memory.last_candidates` | UI | до 20 полных карточек — отсюда восстанавливается правый столбец |
| `working_memory.last_query_protein_sequence` | UI | для alignment-секции |
| `working_memory.last_revealed_sections` | UI | какие секции карточки были unlock-нуты |
| `working_memory.turn_count` | UI | счётчик user-turns (используется для first-vs-followup решения) |
| `working_memory.ui_writer` | UI | константа `'streamlit_frontend'` |
| `active_accession`, `active_sequence_id`, `working_set_ids` | UI, merge | top-1 accession + накопленная history (последние 40) |
| `current_mode` | UI (override > saved) | `embeddings_retriever` / `chat_llm` / `chat_llm_error` |
| `last_tool_results_summary`, `last_analysis_summary` | UI | служебные summary-поля |

Если `SUPABASE_DB_URL` не задан — repository — `NullSessionRepository`, `is_persistent() == False`, sidebar показывает warning «Session history is not persisted», и **follow-up routing деградирует к «всегда первый ход»** (`_is_first_turn_in_session` возвращает `True` без БД, поэтому каждый submit идёт в retriever — `chat_llm_pipeline` без persistence не вызовется).

---

## 5. Логика принятия решений

Все «if/else» точки, которые видит пользователь, в одном месте. Каждый узел — реальная ветка в коде.

### 5.1 На уровне страницы

```
Открытие страницы
├── есть APP_PASSWORD?  (env или st.secrets)
│   ├─ да  → password gate (app._require_password)
│   └─ нет → пропускаем
│
├── bootstrap_identity()
│   ├── controller pending (render 0) → mint temp id, НЕ пишем cookie
│   ├── controller ready, cookie есть → adopt
│   └── controller ready, cookie пусто → mint и пишем cookie
│
└── USE_VECTOR_DB_MODE и есть session_id?
    ├─ да  → chat_pipeline.auto_restore_if_fresh_load() (одноразово)
    └─ нет → демо/legacy ветка (BIOSEQ_FRONTEND_BACKEND=mock|real)
```

### 5.2 На уровне submit

```
user отправил message
│
├── on_submit = _handle_vector_db_submission (USE_VECTOR_DB_MODE=True, default)
│   └── chat_pipeline.run_turn(prompt)
│
└── on_submit = None (USE_VECTOR_DB_MODE=False)
    ├─ BIOSEQ_FRONTEND_BACKEND=mock → conversation.route() (scripted demo)
    └─ BIOSEQ_FRONTEND_BACKEND=real → backend_adapter.run_search (legacy single-shot)

chat_pipeline.run_turn:
│
├── _is_first_turn_in_session()  ← turn_count из public.chat_sessions
│   ├─ True  → embeddings_pipeline.run_turn_embeddings
│   │           ├── _missing_packages() ≠ [] → friendly error
│   │           ├── нет MISTRAL/OPENAI key → friendly error
│   │           └── всё ок → ProtT5 + FAISS + LocalReranker
│   │           update_card=True
│   │
│   └─ False → chat_llm_pipeline.run_turn_chat_llm
│               ├── BIOSEQ_LLM_PROXY_URL/TOKEN не заданы → friendly error
│               └── POST → Gemini → reply
│               update_card=False
│
└── outcome dict одинаковой формы для обоих backend-ов
```

### 5.3 Внутри LangGraph retriever-а

```
extract_and_classify_node
└── LLM с structured_output (InputExtraction)
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
        get_text_embedder()  ── Mistral mistral-embed или OpenAI text-embedding-3-small
        in-memory faiss.IndexFlatIP с normalize_L2 → top-5

Любой узел вернул error → graph short-circuit-ит до END.
```

### 5.4 На уровне persistence

```
session_db_adapter.save_turn(...)
├── repository — Null? → no-op, return None
├── update_candidates=True (retriever-turn):
│   ├── proteins      = merge(saved.proteins, new top-5 compact) by accession
│   ├── last_candidates = candidates[:20]
│   ├── revealed_list = sort(unique(reveals))
│   ├── working_set_ids = (saved + new accessions)[-40:]
│   └── active_accession = new top-1 OR saved
│
└── update_candidates=False (chat-LLM follow-up):
    ├── proteins/last_candidates/working_set_ids/active_accession = saved (как есть)
    ├── revealed_list = saved.last_revealed_sections (если не override)
    └── messages, turn_count, last_user/assistant_message — обновляем как обычно
```

### 5.5 На уровне идентичности (cookie controller)

```
Cookie controller жизненный цикл
├── render 0 (state="pending"):
│   ├── есть st.session_state[key] → reuse, флаг pending_promotion
│   └── нет                          → mint temp, флаг pending_promotion
│       (в этот render cookie НЕ пишем — JS ещё не отдал реальные значения)
│
└── render 1+ (state="ready"):
    ├── cookie заполнена → adopt cookie (overrides temp), сбросить флаг
    └── cookie пусто:
        ├── есть temp → promote temp в cookie
        └── нет temp  → mint и записать cookie

start_new_session (Reset / New chat):
├── controller ready → сразу пишем новый session_id в cookie
└── controller pending → ставим pending_promotion, bootstrap на следующем rerun промоутит

switch_session (sidebar): то же самое, но session_id выбирает пользователь
```

Подробности «почему так» — в docstring [session_identity.py](frontend/session_identity.py): `streamlit-cookies-controller.getAll()` возвращает `{}` на render 0 даже для returning users, и запись cookie в этот момент молча перезатирает существующую.

---

## 6. Data artifacts: жизненный цикл

| Artifact | Storage / source of truth | Runtime cache |
|---|---|---|
| `per-protein.h5` (~1.3 GB, 574,615 × 1024 ProtT5 mean-residue embeddings) | Hugging Face Dataset (`BIOSEQ_DATA_SOURCE=hf:OWNER/REPO`) или UniProt FTP | `bioseq_retriever/data/per-protein.h5` |
| `per-protein.index` (FAISS HNSW, ~2.5 GB) | HF Dataset (опционально); иначе строится из `.h5` при первом запуске `services/search_service.py` | `bioseq_retriever/data/per-protein.index` |
| `per-protein.accessions.json` | HF Dataset (рядом с `.index`) или строится при build | `bioseq_retriever/data/per-protein.accessions.json` (в `bootstrap.py` исторически `.pkl` — расхождение с `search_service.py`, который пишет `.json`; см. TODO в `README_app.md`) |
| `Rostlab/prot_t5_xl_uniref50` (model weights, ~3 GB) | Hugging Face Model Hub | HF cache (default `~/.cache/huggingface`) |
| UniProt JSON records | UniProt REST `https://rest.uniprot.org/uniprotkb/search` | не кешируется (fetch per turn) |
| `public.chat_sessions` | Supabase / Postgres | — |

Bootstrap (`src/bootstrap.py::ensure_data`) идемпотентен: вызвав его n раз, мы скачаем недостающие артефакты ровно один раз. Это позволяет live-deploy на HF Space подниматься без отдельного pre-warm-шага — первый ProtT5-запрос триггерит и model download, и data download, и index build (тогда же).

Полная активити-диаграмма артефактов: [REPORT.MD §2.4](../report/REPORT.MD), [data-artifact-lifecycle.svg](../report/diagrams/data-artifact-lifecycle.svg).

---

## 7. Внешние зависимости (что должно быть доступно)

| Зависимость | Зачем | Что задать |
|---|---|---|
| Hugging Face Model Hub | ProtT5 weights | network access; при private modeled — `HF_TOKEN` |
| Hugging Face Dataset (опц.) | `per-protein.h5` + индекс | `BIOSEQ_DATA_SOURCE=hf:OWNER/REPO` |
| UniProt REST | metadata top-50 кандидатов | network access; есть retry в `api_client.py` |
| Mistral API | `get_llm()` extract/classify + `get_text_embedder()` rerank | `MISTRAL_API_KEY` |
| OpenAI API (fallback) | то же самое, если Mistral недоступен | `OPENAI_API_KEY` |
| Supabase Postgres | `public.chat_sessions` | `SUPABASE_DB_URL` |
| Cloudflare Worker → Gemini | follow-up chat | `BIOSEQ_LLM_PROXY_URL`, `BIOSEQ_LLM_PROXY_TOKEN` |

Полная таблица env vars и где они опциональны — в [../README.md](../README.md) и [README_app.md](README_app.md).

---

## 8. Open work

Список известного drift-а, dormant-кода и других «отложенных решений» вынесен в [TODO.MD](TODO.MD) (раздел «Архитектура / drift»). Туда же — how-to-инструкции по расширению (добавление нового retriever-backend, эволюция chat-LLM-модуля).

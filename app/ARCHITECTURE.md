# BioSeq Investigator — архитектура app

Ветка: `ui_streamlit_v3.0`
Дата: 2026-05-08

Документ описывает текущее состояние app: какие модули уже собраны, как они общаются и по какой логике обрабатывается каждый ход пользователя. Уровень — обзорный, для команды; детали внутри модулей живут в docstring-ах файлов.

---

## 1. Контур приложения

Three-layer setup, всё работает in-process под одним Streamlit-процессом.

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
│  app.py  ─►  components/{chat, protein_card, session_sidebar}             │
│       │                                                                   │
│       ├── session_identity.py   (cookie ↔ user_id / session_id)           │
│       ├── chat_pipeline.py      (turn router + graph backend)             │
│       ├── chat_llm_pipeline.py  (follow-up; пока stub)                    │
│       ├── embeddings_pipeline.py (legacy ProtT5+FAISS, lazy)              │
│       ├── backend_choice.py     (graph | embeddings)                      │
│       └── session_db_adapter.py (мост к public.chat_sessions)             │
└───────────────┬───────────────────────────────────────────────────────────┘
                │  AppContext(user_id, session_id, ...)
                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  app/backend                                                              │
│                                                                           │
│  app_services/service_factory.create_bioseq_retriever_graph_agent()       │
│         │                                                                 │
│         ▼                                                                 │
│  agents_core/retriever_agent.BioSeqRetrieverGraphAgent                    │
│         (LangGraph: extract → resolve/raw → translate/pass → rank →       │
│          rerank, checkpoint per session_id)                               │
│         │                                                                 │
│         ├── app_services/graph_retrieval.GraphRetrievalService            │
│         │       └── shared/services/graph.Neo4jGraphClient                │
│         │                                                                 │
│         └── shared/services/persistence                                   │
│                  ├── PostgresSaver (LangGraph checkpoints)                │
│                  └── PostgresSessionRepository → public.chat_sessions     │
└───────────────┬─────────────────────────┬─────────────────────────────────┘
                │                         │
                ▼                         ▼
        ┌──────────────┐         ┌────────────────────┐
        │  Neo4j graph │         │ Supabase / Postgres│
        │  (Protein,   │         │ public.chat_sessions
        │   Disease,   │         │ langgraph_*        │
        │   Domain ...)│         └────────────────────┘
        └──────────────┘
```

---

## 2. Модули: что внутри и зачем

### 2.1 `frontend/` — Streamlit UI

Точка входа — [app/frontend/app.py](frontend/app.py). Файл рисует двухколоночный layout (chat слева, protein card справа), bootstrap-ит идентичность и решает, в какой backend пойдёт текущий ход.

| Файл | Ответственность |
|---|---|
| [app.py](frontend/app.py) | Layout, password gate, bootstrap, диспетчер: ход → `chat_pipeline.run_turn` |
| [session_identity.py](frontend/session_identity.py) | `user_id` (1y cookie) + `session_id` (7d cookie) с двухфазным "pending → ready" реконсилем для `streamlit-cookies-controller` |
| [chat_pipeline.py](frontend/chat_pipeline.py) | Turn-router (1-й vs follow-up), graph-backend turn, восстановление сессии из БД, маппинг backend→UI shape |
| [chat_llm_pipeline.py](frontend/chat_llm_pipeline.py) | Stub follow-up handler (отдаёт «module baking», но ход в БД пишет, чтобы был в истории сайдбара) |
| [embeddings_pipeline.py](frontend/embeddings_pipeline.py) | Legacy ProtT5+FAISS+UniProt путь как альтернативный retriever; тяжёлые deps (~2 GB) импортируются лениво |
| [backend_choice.py](frontend/backend_choice.py) | Per-tab выбор retriever-backend: `graph` (Neo4j) или `embeddings` (ProtT5/FAISS) |
| [session_db_adapter.py](frontend/session_db_adapter.py) | Cached `PostgresSessionRepository`, read-merge-write апсёрт UI-полей поверх того, что записал агент; восстановление кандидатов и сообщений |
| [config.py](frontend/config.py) | Один глобальный switch — `USE_VECTOR_DB_MODE` |
| [backend_adapter.py](frontend/backend_adapter.py), [vector_db_adapter.py](frontend/vector_db_adapter.py) | Legacy-адаптеры, оставлены для `BIOSEQ_FRONTEND_BACKEND=real` режима без vector DB |

#### Components

| Файл | Что рендерит |
|---|---|
| [components/chat.py](frontend/components/chat.py) | Левая колонка: история сообщений, streamed assistant reply, Reset, suggestion-chip, `chat_input` |
| [components/protein_card.py](frontend/components/protein_card.py) | Правая колонка: 8 секций карточки белка с прогрессивным "lock/unlock" |
| [components/session_sidebar.py](frontend/components/session_sidebar.py) | Sidebar: New chat, выбор retriever-backend, список прошлых сессий пользователя, debug ids |
| [components/domain_diagram.py](frontend/components/domain_diagram.py) | Plotly-диаграмма доменов |

#### Mock

[mock/conversation.py](frontend/mock/conversation.py) и [mock/protein_loader.py](frontend/mock/protein_loader.py) — скриптованный демо-режим (`BIOSEQ_FRONTEND_BACKEND=mock`) и TypedDict-shape для UI (`Candidate`, `ProteinView`, `DomainFeature`, `DiseaseInfo`). Эти TypedDict-ы используются и как UI-shape для real-backend пути, поэтому модуль mock сейчас не только для демки, но и source of truth для render-схемы.

### 2.2 `backend/app_contracts/` — внешние pydantic-контракты

[protein_view.py](backend/app_contracts/protein_view.py), [chat.py](backend/app_contracts/chat.py), [pipeline.py](backend/app_contracts/pipeline.py), [session.py](backend/app_contracts/session.py).

Ключевые типы:
- `ProteinView`, `DomainFeature`, `DiseaseInfo`, `CandidateView` — всё, что UI ждёт от backend. После последней унификации (`1142da2 contracts: unify backend ProteinView/DiseaseInfo/DomainFeature with UI shape`) форма совпадает с frontend mock TypedDict-ами.
- `ChatTurnRequest` / `ChatTurnResult` — целевой контракт chat-сервиса. Сейчас в проде не используется в hot path, но `BioSeqChatService` уже под него заточен.

### 2.3 `backend/app_services/` — сервисный слой

| Файл | Роль |
|---|---|
| [service_factory.py](backend/app_services/service_factory.py) | `create_bioseq_retriever_graph_agent(use_llm_extractor=…)` — единственный production-factory, который сейчас вызывает frontend |
| [graph_retrieval.py](backend/app_services/graph_retrieval.py) | `GraphRetrievalService` — обёртка над Neo4j: lookup по hash, по acc/gene, retrieve_candidates(neighbors) |
| [retriever_pipeline.py](backend/app_services/retriever_pipeline.py) | Deterministic FASTA/path extractor, classifier DNA/PROTEIN, codon table, system prompt для LLM-экстрактора |
| [protein_view_mapper.py](backend/app_services/protein_view_mapper.py) | Neo4j record → `CandidateView`/`ProteinView` |
| [bioseq_chat.py](backend/app_services/bioseq_chat.py) | `BioSeqChatService` под `ChatTurnRequest/Result` контракт. Сейчас не подключён к UI hot path (frontend ходит напрямую через `chat_pipeline`) |

### 2.4 `backend/agents_core/retriever_agent/` — LangGraph-агент

[agent.py](backend/agents_core/retriever_agent/agent.py) — `BioSeqRetrieverGraphAgent`. LangGraph DAG:

```
extract  ─▶  (input_type=FILEPATH? → resolve_file ; else use_raw)
                          ▼
              (sequence_type=DNA? → translate ; else pass_protein)
                          ▼
                        rank   ▶   rerank   ▶   END
```

Узлы:
- `extract_and_classify_node` — детерминистический парсер или LLM-экстрактор (выбор на конструкторе через `use_llm_extractor`). Возвращает `sequence_or_path`, `input_type`, `context`, `sequence_type`, `is_confident`.
- `resolve_filepath_node` — отключён в DB-only режиме (возвращает error: «runtime file path resolution is disabled»).
- `use_raw_sequence_node` — нормализует raw FASTA.
- `translate_dna_node` — DNA→protein через standard codon table.
- `pass_protein_node` — нормализует protein-последовательность.
- `rank_node` — `find_by_sequence_hash` (или `find_encoded_protein_by_sequence_hash` для DNA), затем `retrieve_candidates(limit=50, neighbor_pool=50)`.
- `rerank_node` — повторный `retrieve_candidates(limit=5, …, context=…)` от accession топ-1; контекст здесь — это пользовательский вопрос.

Persistence слой: LangGraph checkpoint (`PostgresSaver`/`InMemorySaver`) keyed by `thread_id == session_id`, плюс свой compact patch в `public.chat_sessions` (см. §4).

### 2.5 `backend/agents_core/shared/`

- [models.py](backend/agents_core/shared/models.py) — `AppContext`, `SessionPatch`, `SessionRow`, `PersistenceResources`.
- [services/graph.py](backend/agents_core/shared/services/graph.py) — `Neo4jGraphClient`, resolve URI/insecure flag.
- [services/persistence.py](backend/agents_core/shared/services/persistence.py) — `PostgresSessionRepository` (CRUD `public.chat_sessions`), `NullSessionRepository` fallback, `create_persistence_resources(SUPABASE_DB_URL, ExitStack)`.
- [services/session_state.py](backend/agents_core/shared/services/session_state.py) — сериализация LangGraph messages.
- [config.py](backend/agents_core/shared/config.py) — `.env` loader, `resolve_neo4j_settings()`, дефолты.

### 2.6 `backend/graph_core/` — построение графа

[scripts/](backend/graph_core/scripts/) — оффлайн-инструменты для загрузки UniProt → Neo4j. Не вызываются из runtime app, документация в [how_to_use.md](backend/graph_core/how_to_use.md).

---

## 3. Поток данных одного хода

Для краткости: «cards» = `list[Candidate]` UI-shape, «raw» = `list[CandidateView.model_dump()]` backend-shape.

### 3.1 Bootstrap страницы (каждый rerun Streamlit)

```
[Browser]                             [app.py]                          [session_identity]
    │                                    │                                     │
    │  GET /  (cookies: user/session)    │                                     │
    │───────────────────────────────────▶│  bootstrap_identity()               │
    │                                    │────────────────────────────────────▶│
    │                                    │   read cookies (pending/ready)      │
    │                                    │   reconcile с st.session_state      │
    │                                    │◀────────────────────────────────────│
    │                                    │   user_id, session_id               │
    │                                    │                                     │
    │                                    │  if session_id и USE_VECTOR_DB_MODE │
    │                                    │  → chat_pipeline.auto_restore...   │
    │                                    │     (только если history «свежая»)  │
```

### 3.2 Submit пользователя → graph-backend

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
    ├── _is_first_turn_in_session() — читает working_memory.turn_count
    │   из public.chat_sessions через session_db_adapter
    │   ├─ True  → дальше graph/embeddings retriever
    │   └─ False → chat_llm_pipeline.run_turn_chat_llm() (stub)
    │
    ├── backend_choice.get_backend()
    │   ├─ "embeddings" → embeddings_pipeline.run_turn_embeddings()
    │   └─ "graph" (default) → _run_turn_graph()
    │
    ▼ _run_turn_graph(prompt)
    │
    │ context = AppContext(user_id, session_id, workspace_id, user_role)
    │ agent   = _get_agent()  # @st.cache_resource — один на процесс
    │
    ▼
BioSeqRetrieverGraphAgent.invoke(prompt, context)
    │ thread_id = session_id  (LangGraph)
    │ saved = persistence.session_repository.get_session(session_id)
    │
    │ result = self._graph.invoke(initial_state, config={thread_id})
    │   ├─ extract_and_classify_node
    │   ├─ use_raw_sequence_node | resolve_filepath_node
    │   ├─ translate_dna_node    | pass_protein_node
    │   ├─ rank_node   ← GraphRetrievalService.find_by_sequence_hash
    │   └─ rerank_node ← GraphRetrievalService.retrieve_candidates(context=...)
    │
    │ self._graph.update_state(config, {messages:[AIMessage(summary)]})
    │ patch = _derive_session_patch(current_state)
    │ persistence.session_repository.upsert_session(context, patch)
    │           └─► public.chat_sessions  (agent-side fields:
    │                   session_summary, proteins[top-1], sequences[input],
    │                   working_memory.last_retriever_state, active_accession,
    │                   current_mode='bioseq_retriever_langgraph')
    │
    └── return (result, current_state)
        ▲
        │ raw_candidates = result.final_results
        ▲
chat_pipeline._run_turn_graph
    │ ui_candidates = [_candidate_from_backend(r) for r in raw_candidates]
    │   (нормализация формы через _PROTEIN_DEFAULTS / _DISEASE_DEFAULTS)
    │ reply         = _assistant_message(result)   # markdown summary
    │ reveals       = _revealed_sections(ui_candidates)
    │
    ▼ session_db_adapter.save_turn(context, prompt, reply, raw_candidates, reveals)
    │
session_db_adapter.save_turn  (read-merge-write)
    │ saved = repo.get_session(session_id)   # уже с agent-полями
    │ proteins      = merge(saved.proteins, candidates[*].protein)   # top-1+top-5
    │ working_memory= {
    │     ...saved_wm,                # сохраняем agent-side keys
    │     messages: [...прошлые, user, assistant],   # transcript
    │     last_candidates: candidates[:20],          # full cards для restore
    │     last_revealed_sections, last_turn_at,
    │     turn_count: saved_wm.turn_count + 1,
    │     ui_writer: 'streamlit_frontend',
    │ }
    │ active_accession = candidates[0].protein.accession or saved.active_accession
    │ current_mode     = override or saved.current_mode or 'streamlit_ui'
    │ repo.upsert_session(context, state)
    │           └─► public.chat_sessions  (UI-side fields поверх agent-side)
    ▼
chat_pipeline returns dict {reply, candidates, reveals, warnings, result, persisted, backend, update_card=True}
    │
    ▼
app._handle_vector_db_submission
    │ st.session_state.candidates = outcome.candidates
    │ st.session_state.selected_candidate_idx = 0
    │ st.session_state.card_sections_revealed = outcome.reveals
    │ st.session_state.pending_assistant      = outcome.reply
    │
    ▼ st.rerun()  (chat.py стримит pending_assistant в chat_message,
                   protein_card.render() рисует новые секции)
```

### 3.3 Submit пользователя → follow-up (chat-LLM stub)

Идентично 3.2 до `_is_first_turn_in_session() == False`. Дальше:

```
chat_llm_pipeline.run_turn_chat_llm(prompt)
    │ STUB_MESSAGE = "⏳ Chat agent module is still baking…"
    │ session_db_adapter.save_turn(
    │       …,
    │       candidates=[],
    │       update_candidates=False,        # карточку и кандидаты НЕ трогаем
    │       current_mode='chat_llm_stub',
    │ )
    └── return {update_card: False, candidates: текущие, ...}
```

`update_card=False` важен: в `app._handle_vector_db_submission` это означает «не пересобирать `st.session_state.candidates` и не сбрасывать `selected_candidate_idx`», то есть пользователь продолжает видеть результат прошлого retriever-turn.

### 3.4 Восстановление сессии (browser reload / sidebar switch)

```
session_sidebar._switch_to_session(sid)        ──┐
session_identity.switch_session(sid)             ├─►  chat_pipeline.restore_session_state(sid)
                                               ──┘            │
app._bootstrap_session() при reload:                          │
   chat_pipeline.auto_restore_if_fresh_load(session_id) ──────┘
                                                              │
                                                              ▼
                                          session_db_adapter.load_session(session_id)
                                                              │
                                                              ▼
                                             extract_candidates(row.working_memory.last_candidates)
                                             extract_messages(row.working_memory.messages)
                                                              │
                                                              ▼
                                          st.session_state.candidates / messages / reveals
                                          backend_choice.set_backend(row.current_mode)
```

`auto_restore_if_fresh_load` отрабатывает один раз на session_id (флаг `_auto_restore_attempted`) и только если history выглядит «свежей» (≤1 welcome-сообщение, нет кандидатов) — чтобы не затирать живой чат, когда пользователь кликает по сайдбару.

---

## 4. Что лежит в `public.chat_sessions`

Одна строка на `session_id`. Ответственность за поля поделена между retriever-агентом и UI:

| Поле | Кто пишет | Что хранит |
|---|---|---|
| `session_id`, `thread_id`, `user_id`, `workspace_id`, `user_role` | оба | `thread_id == session_id`, identity |
| `session_summary` | agent (UI fallback) | короткий summary последнего ретрив-хода |
| `proteins` | agent (top-1) + UI (extends до top-5) | compact `ProteinRecord` per accession |
| `sequences` | agent | input-последовательности с hash-ID |
| `working_memory.last_retriever_state` | agent | `_compact_state` из LangGraph: prompt, type, counts, top_accession |
| `working_memory.messages` | UI | chat transcript (последние 200 сообщений) |
| `working_memory.last_candidates` | UI | до 20 полных карточек — отсюда восстанавливается правый столбец |
| `working_memory.last_revealed_sections` | UI | какие секции карточки были unlock-нуты |
| `working_memory.turn_count` | UI | счётчик user-turns (используется для first-vs-followup решения) |
| `active_accession`, `active_sequence_id`, `working_set_ids` | оба, merge | top-1 accession + history |
| `current_mode` | оба (override > agent > UI) | `bioseq_retriever_langgraph` / `embeddings_retriever` / `chat_llm_stub` / `streamlit_ui` |

LangGraph checkpoints живут в отдельных таблицах `langgraph_*`, их создаёт `PostgresSaver.setup()` при первом подключении.

Если `SUPABASE_DB_URL` не задан — repository — `NullSessionRepository`, `is_persistent()==False`, history не сохраняется, sidebar показывает warning, follow-up routing деградирует к «всегда первый ход».

---

## 5. Логика принятия решений

Все «if/else» точки, которые видит пользователь, в одном месте. Дерево читается сверху вниз: каждый узел — реальная ветка в коде.

### 5.1 На уровне страницы

```
Открытие страницы
├── есть password в st.secrets?
│   ├─ да  → password gate (app._require_password)
│   └─ нет → пропускаем
│
├── bootstrap_identity()
│   ├── controller pending → mint temp id, не пишем cookie
│   ├── controller ready, cookie есть → adopt
│   └── controller ready, cookie пусто → mint и пишем cookie
│
└── USE_VECTOR_DB_MODE и есть session_id?
    ├─ да  → chat_pipeline.auto_restore_if_fresh_load() (одноразово)
    └─ нет → демо/legacy ветка (BACKEND_MODE=mock|real)
```

### 5.2 На уровне submit

```
user отправил message
│
├── BACKEND_MODE/USE_VECTOR_DB_MODE
│   ├─ USE_VECTOR_DB_MODE=True (default) → chat_pipeline.run_turn
│   ├─ BACKEND_MODE=real                 → backend_adapter.run_search (legacy single-shot)
│   └─ BACKEND_MODE=mock                 → conversation.route() (scripted demo)
│
└── chat_pipeline.run_turn  (только vector-DB режим):
    │
    ├── _is_first_turn_in_session()  — берём turn_count из public.chat_sessions
    │   ├─ True  → retriever (graph | embeddings)
    │   └─ False → chat_llm_pipeline (stub, не трогает карточку)
    │
    └── retriever (только если first):
        │
        ├── backend_choice.get_backend()
        │   ├─ "graph"      → BioSeqRetrieverGraphAgent (Neo4j)
        │   └─ "embeddings" → embeddings_pipeline
        │       │
        │       ├── _missing_packages() ≠ [] → friendly error в чат
        │       ├── нет MISTRAL/OPENAI key → friendly error в чат
        │       └─ всё ок → ProtT5 + FAISS + LocalReranker
        │
        └── на любом backend результат идёт в одну форму outcome dict
```

### 5.3 Внутри LangGraph агента

```
extract_and_classify
├── use_llm_extractor=True и llm_factory задан → LLM с structured_output
└── deterministic парсер (regex-based)
    │
    ├── input_type detection: FILEPATH (regex .fasta/.faa/...) | SEQUENCE
    └── sequence_type detection: DNA | PROTEIN
        ├── есть «protein-only» буква (L,F,W,…) → PROTEIN
        └── алфавит ⊆ {A,C,G,T,U,N,…}        → DNA

if extract упал, но в state есть прошлая extraction → используем её
                                                      (continuation case)

input_type == FILEPATH? ─ yes ─► resolve_file (DB-only mode → error)
                       └── no ──► use_raw_sequence

sequence_type == DNA?  ─ yes ─► translate_dna
                       └── no ──► pass_protein

rank_node:
├── DNA  → find_encoded_protein_by_sequence_hash(dna, protein)
├── ___ → find_by_sequence_hash(protein)
└── hit is None → error «outside prepared graph dataset»
└── hit ok       → retrieve_candidates(limit=50, neighbor_pool=50)

rerank_node:
├── ranked_results пусто       → final_results=[]
├── у топ-1 нет accession      → final_results = ranked[:5] (passthrough)
└── retrieve_candidates(limit=5, …, context=user_question)
```

### 5.4 На уровне persistence

```
session_db_adapter.save_turn(...)
├── repository — Null? → no-op, return None
├── update_candidates=True (retriever-turn):
│   ├── proteins      = merge(saved.proteins, new top-5) by accession
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

### 5.5 На уровне идентичности

```
Cookie controller жизненный цикл
├── render 0 (state="pending"):
│   ├── есть st.session_state[key] → reuse, флаг pending_promotion
│   └── нет                          → mint temp, флаг pending_promotion
│       (кук в этом render не пишем — JS ещё не отдал реальные значения)
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

---

## 6. Как добавлять новый retriever-backend

1. Реализовать модуль `app/frontend/<name>_pipeline.py` с `run_turn_<name>(prompt) -> outcome dict` той же формы, что и `chat_pipeline._run_turn_graph` (`reply`, `candidates`, `candidates_raw`, `reveals`, `warnings`, `result`, `persisted`).
2. Добавить константу в [backend_choice.py](frontend/backend_choice.py): `BACKEND_<NAME>`, расширить `ALL_BACKENDS`, `LABELS`, `DESCRIPTIONS`.
3. В [chat_pipeline.run_turn](frontend/chat_pipeline.py) добавить ветку в селектор бэкендов (рядом с `BACKEND_EMBEDDINGS`).
4. В [chat_pipeline.restore_session_state](frontend/chat_pipeline.py) добавить mapping `current_mode` → `set_backend(...)`, чтобы reload восстанавливал выбор.
5. Внутри turn-handler писать persistence через `session_db_adapter.save_turn(..., current_mode='<your_mode>')`, чтобы сайдбар корректно отображал тип сессии.

## 7. Как реальный chat-LLM встанет на место stub-а

Текущий [chat_llm_pipeline.py](frontend/chat_llm_pipeline.py) уже соответствует контракту. Чтобы заменить на боевой агент:

1. Заменить тело `run_turn_chat_llm` на вызов нового агента (например, через `service_factory.create_chat_llm_service()` под `@st.cache_resource`).
2. Сохранить return-shape (`reply`, `candidates`, `reveals`, `warnings`, `result`, `persisted`, `backend`, `update_card=False`).
3. В `session_db_adapter.save_turn` поменять `current_mode='chat_llm_stub'` на `current_mode='chat_llm'`.
4. Если боевой LLM получит право обновлять карточку (например, «уточнить» функцию белка), переключать `update_card=True` и заполнять `candidates`/`reveals` соответствующим образом.

Никаких изменений в `app.py`, `components/chat.py` или `protein_card.py` это не потребует — UI слушает только outcome dict.

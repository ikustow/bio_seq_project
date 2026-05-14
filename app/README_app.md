# BioSeq Investigator — app

🇬🇧 English version: [README_app_en.md](README_app_en.md).

Streamlit-приложение поверх ProtT5/FAISS embedding-retriever и Gemini-чата для follow-up. Архитектурный обзор — [ARCHITECTURE.md](ARCHITECTURE.md). Промежуточный отчёт по проекту целиком — [../report/REPORT.MD](../report/REPORT.MD).

## Что делает

- Пользователь вставляет DNA/protein sequence + вопрос на естественном языке.
- **Первый turn** в сессии идёт в embedding-retriever (`bioseq_retriever/src/pipeline.py` через [frontend/embeddings_pipeline.py](frontend/embeddings_pipeline.py)): extract → translate-если-DNA → ProtT5 query embedding → FAISS top-50 по precomputed UniProt embeddings → UniProt REST fetch → LLM-rerank top-5. Карточка белка слева, top-5 кандидатов справа.
- **Follow-up турны** идут в Gemini через Cloudflare-прокси ([frontend/chat_llm_pipeline.py](frontend/chat_llm_pipeline.py)). Карточка белка при этом не пересобирается — пользователь продолжает обсуждать выбранного кандидата.
- История сессии (chat transcript + top-5 кандидаты + revealed sections карточки) пишется в `public.chat_sessions` через [frontend/session_db_adapter.py](frontend/session_db_adapter.py); сайдбар показывает список прошлых сессий.

## Локальный запуск (если необходимо запустить локально, а не использовать готовый рабочий поднятый интерфейс на HF - https://huggingface.co/spaces/radda-i/BioSeq_investigator)

```bash
streamlit run app/frontend/app.py
```

Тяжёлые ML deps (`torch`, `transformers`, `faiss`, `h5py`, `sentencepiece`, …) импортируются лениво — без них первый запуск Streamlit не падает, но retriever вернёт friendly-error в чат при первом submit. Установка extras: см. `requirements.txt` в корне.

`per-protein.h5` на первом запуске автоматически подтягивается из Hugging Face Dataset, если задан `BIOSEQ_DATA_SOURCE=hf:OWNER/REPO` (см. [bioseq_retriever/src/bootstrap.py](../bioseq_retriever/src/bootstrap.py)). Иначе ожидается файл по пути из `BIOSEQ_H5_PATH` (по умолчанию `bioseq_retriever/data/per-protein.h5`).

## Переменные окружения (`.env` в корне репозитория)

**Обязательно для retriever-турна:**
- `MISTRAL_API_KEY` или `OPENAI_API_KEY` — LLM для контекстного reranker-а top-50 → top-5. Без него preflight возвращает «No LLM credentials available» в чат.
- `BIOSEQ_H5_PATH` (или `BIOSEQ_DATA_SOURCE=hf:OWNER/REPO`) — где взять precomputed protein embeddings.

**Обязательно для follow-up Gemini-чата:**
- `BIOSEQ_LLM_PROXY_URL`, `BIOSEQ_LLM_PROXY_TOKEN` — endpoint и токен Cloudflare-прокси к Gemini.

**Опционально:**
- `SUPABASE_DB_URL` — Postgres URL. Без него repository → `NullSessionRepository`, история не сохраняется, sidebar показывает warning «Session history is not persisted». Чат продолжает работать в рамках текущей вкладки, но всё умирает после рестарта/закрытия.
- `BIOSEQ_INDEX_PATH`, `BIOSEQ_ACCESSIONS_CACHE_PATH` — переопределение путей FAISS-индекса и accession-кеша (по умолчанию рядом с `per-protein.h5`).
- `APP_PASSWORD` — single-password gate (используется на HF Spaces deploy; локально обычно не задан).
- `BIOSEQ_USE_SERVICES` — embeddings_pipeline принудительно ставит `false`, чтобы retriever не уходил в HTTP-микросервисы; не трогать без причины.

**Legacy / демо:**
- `BIOSEQ_FRONTEND_BACKEND=mock` — скриптованная демо-беседа из [frontend/mock/conversation.py](frontend/mock/conversation.py). Активен только когда `config.USE_VECTOR_DB_MODE = False`. По дефолту флаг включён (`True`), и весь UI идёт через `chat_pipeline.run_turn`.
- `BIOSEQ_FRONTEND_BACKEND=real` — старый single-shot путь через [frontend/backend_adapter.py](frontend/backend_adapter.py). Не используется в live-режиме.

## Структура

| Путь | Что внутри |
|---|---|
| [frontend/app.py](frontend/app.py) | Streamlit entry point: layout, password gate, identity bootstrap, диспетчер submit-а |
| [frontend/chat_pipeline.py](frontend/chat_pipeline.py) | Turn-router: первый turn → embeddings, follow-up → chat-LLM (решение по `working_memory.turn_count`) |
| [frontend/embeddings_pipeline.py](frontend/embeddings_pipeline.py) | Streamlit-адаптер вокруг `bioseq_retriever`: preflight, cached ProtT5+FAISS resources, нормализация UniProt JSON в UI shape, persistence |
| [frontend/chat_llm_pipeline.py](frontend/chat_llm_pipeline.py) | POST в Cloudflare-прокси Gemini с контекстом текущего белка + chat history |
| [frontend/session_db_adapter.py](frontend/session_db_adapter.py) | Cached `PostgresSessionRepository`, единственный writer в `public.chat_sessions` |
| [frontend/session_identity.py](frontend/session_identity.py) | Cookie-based `user_id` (1y) + `session_id` (7d) с двухфазным «pending → ready» хэндлингом cookie-controller-а |
| [frontend/backend_choice.py](frontend/backend_choice.py) | Single-backend stub — после удаления radio в sidebar остался только `embeddings`, файл сохранён для call-site compat |
| [frontend/config.py](frontend/config.py) | Один runtime-switch `USE_VECTOR_DB_MODE` (default `True`) |
| [frontend/components/](frontend/components/) | UI: `chat`, `protein_card`, `session_sidebar`, `domain_diagram`, `alignment_viewer` |
| [frontend/mock/](frontend/mock/) | Scripted demo + TypedDict shape (`Candidate`, `ProteinView`, …) — используется и как UI-shape для real-backend |
| [backend/](backend/) | **Dormant в runtime**: старый Neo4j graph-агент (`agents_core/retriever_agent`, `app_services/graph_retrieval.py`), оффлайн graph-build (`graph_core/scripts/`). Frontend в эти модули не ходит. Оставлены для истории. |
| [backend/agents_core/shared/](backend/agents_core/shared/) | `AppContext`, `PostgresSessionRepository`, env loader — используется live-кодом через `session_db_adapter` |

## Известные расхождения с документацией

- **[ARCHITECTURE.md](ARCHITECTURE.md) устарел.** Он описывает Streamlit поверх Neo4j-графа (`BioSeqRetrieverGraphAgent`, `GraphRetrievalService`), backend-radio `graph | embeddings`, two-writer-схему persistence. Это всё больше не запускается на runtime-пути; в проде остался один backend — embeddings, и один writer — `session_db_adapter`. Документ нужно либо переписать, либо пометить как «historical / pre-2026-05-11».
- **[frontend/TO-DO.md](frontend/TO-DO.md)** — старый TODO по frontend↔backend session-model, бóльшая часть P0 уже сделана.

## TODO

### Retriever
- [ ] Засёк ли preflight отсутствие `per-protein.h5` корректно во всех сценариях (нет HF source + нет файла локально)? Сейчас preflight файл не проверяет — ждём, что `_build_pipeline_resources` упадёт с понятным сообщением. Стоит явно прогнать «cold start без данных».
- [ ] LLM-rerank сейчас использует `Mistral`/`OpenAI` text-embeddings → cosine. В REPORT.MD упоминается, что rerank-by-context фактически нужен только для top-50 → top-5; убедиться, что reranker реально получает осмысленный context, а не пустую строку (см. поле `state.context` в `bioseq_retriever/src/pipeline.py`).

### Persistence
- [ ] Подтвердить `SUPABASE_DB_URL` на HF Spaces deploy (см. `~/.claude/.../deploy_hf.md`). Без него sidebar history молча выключается.
- [ ] Добавить в Debug-expander индикатор `is_persistent()` и `current_mode` последнего turn-а, чтобы не лезть в SQL для проверки записи.

### Chat-LLM
- [ ] Cloudflare proxy таймаут — 45 s. На больших протеинах с подробным контекстом Gemini иногда отвечает дольше; стоит поднять или показать progress.
- [ ] `_get_current_protein_context` хардкодит набор полей. Когда добавим новые секции карточки (variants, pathways, …), не забыть расширить контекст для Gemini.

### Сопутствующее (P2)
- [ ] Решить судьбу dormant-кода: `app/backend/agents_core/retriever_agent/`, `app/backend/graph_core/`, `frontend/backend_adapter.py`, `frontend/vector_db_adapter.py`. Если graph-направление точно закрыто — удалить или явно пометить deprecated, чтобы новые контрибьюторы не путались.
- [ ] `BioSeqChatService` в `backend/app_services/bioseq_chat.py` собран под `ChatTurnRequest/Result`, но не подключён. Либо подключить, либо удалить — не оставлять «полу-контракт».

# BioSeq Investigator — app

Streamlit-приложение поверх Neo4j-графа белков. Полный обзор архитектуры — [ARCHITECTURE.md](ARCHITECTURE.md).

## Запуск

```bash
streamlit run app/frontend/app.py
```

Переменные окружения (в `.env`):
- `SUPABASE_DB_URL` — без неё session history не персистится (см. TODO ниже).
- `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` — обязательны для graph-backend.
- `MISTRAL_API_KEY` или `OPENAI_API_KEY` — обязательны только для embeddings-backend.
- `BIOSEQ_FRONTEND_BACKEND` — `mock` (по умолчанию демо) / `real` (legacy ProtT5+FAISS) / vector-DB режим включается отдельно через `config.USE_VECTOR_DB_MODE=True`.

## TODO по итогам аудита 2026-05-08

### Persistence (запись белков в БД)

- [ ] **Проверить `SUPABASE_DB_URL` на каждом окружении.** Если переменная не задана, repository — `NullSessionRepository`, `upsert_session` это no-op, всё живёт только в `st.session_state` + LangGraph `InMemorySaver` и пропадает после рестарта. Сайдбар в этом случае показывает warning «Session history is not persisted» — это сигнал, а не косметика.
- [ ] **Добавить в sidebar/Debug expander индикатор `is_persistent()` и `current_mode` последнего turn-а.** Сейчас, чтобы понять «реально ли пишется», нужно лезть в SQL.
- [ ] **Зафиксировать разделение ответственности «кто что пишет».** Сейчас:
  - retriever-агент пишет **top-1** (`proteins[0]`, `active_accession`, agent-side `working_memory`);
  - UI делает read-merge-write поверх и добавляет **top-5** (`proteins[*]`) + полные карточки в `working_memory.last_candidates` + transcript + `turn_count`.
  - Это два UPDATE-а на каждый user turn по одной строке через два разных коннекшена. Для single-process Streamlit ок, но при многопроцессном/multi-tab нагрузке нужен либо advisory lock, либо один writer.
- [ ] **Добавить smoke-test:** один turn → проверить `jsonb_array_length(proteins) == 5` и `jsonb_array_length(working_memory->'last_candidates') >= 1`. Любое расхождение значит, что один из двух writer-ов упал тихо.

### Retriever (контекст и качество результатов)

- [ ] **Починить `neighbor_pool=50` в `GraphRetrievalService.retrieve_candidates`.** Сейчас в [graph_retrieval.py:175-176](backend/app_services/graph_retrieval.py#L175-L176) цикл обрывается на `limit=5` ДО rerank-а. Cypher достаёт 50 соседей, но reranker видит только 4 (плюс target). Пул в 50 — мёртвый код. Исправление: убрать `break`, отдавать `_rerank_candidates_by_context` весь пул, а уже rerank возвращает `limit`.
- [ ] **Решить, должен ли target быть жёстко зафиксирован на 1-й позиции.** В `_rerank_candidates_by_context` ([graph_retrieval.py:223-233](backend/app_services/graph_retrieval.py#L223-L233)) `[target, *neighbors][:limit]` — top-1 всегда sequence-hash hit, контекст переупорядочивает только 2-5. Если sequence попадает в неправильный organism, это нельзя исправить контекстом.
- [ ] **Заменить `_lexical_context_score` на семантический скор.** Сейчас это token-overlap по словам ≥3 символов. Запрос «human» не матчится с «Homo sapiens», запрос про функцию матчится только если те же слова буквально есть в `function_text`. В проекте уже есть Mistral/OpenAI embedder — переиспользовать.
- [ ] **Передавать ретриверу больше, чем `prompt: str`.** Сейчас в `agent.invoke(prompt, context)` уходит только сырая строка + identity. Не передаётся: история сообщений, `selected_accession`, прошлый `active_accession`, ui_context. Контракт `app_contracts.ChatTurnRequest` уже это умеет — нужно подключить через `BioSeqChatService` (см. TODO в архитектуре).
- [ ] **Унифицировать поведение LLM- vs deterministic-экстрактора.** В проде стоит `use_llm_extractor=False` ([chat_pipeline.py:71](frontend/chat_pipeline.py#L71)). Системный промпт LLM-экстрактора учит классифицировать DNA/protein, но не извлекает структурированный «контекст-для-rerank». То есть даже если включить LLM, rerank всё равно идёт через лексический скор. Нужно либо вынести семантический rerank в отдельный LLM-вызов, либо явно задокументировать, что контекст влияет только лексически.
- [ ] **Залогировать `state.context` и `context_score` per candidate.** Быстрый способ убедиться, что контекст вообще доезжает до rerank — напечатать `result["context"]` и `[c.context_score for c in final_results]`. Если у всех `None` или одинаковые мизерные значения — подтверждение, что rerank-by-context фактически не работает.

### Сопутствующее (P2)

- [ ] Решить судьбу legacy-адаптеров: `backend_adapter.py` (vector_db_adapter тоже ещё лежит как историческая ветка) дублирует логику `chat_pipeline`. Выпилить или явно пометить deprecated.
- [ ] `BioSeqChatService` собран под `ChatTurnRequest/ChatTurnResult`, но frontend ходит мимо него напрямую через `chat_pipeline`. Когда дойдут руки — перевести UI на сервисный контракт, тогда `selected_accession` и история сообщений будут передаваться без новых хаков.
- [ ] Stub `chat_llm_pipeline.py` — заменить на боевой follow-up агент (см. §7 в [ARCHITECTURE.md](ARCHITECTURE.md)).

## Файлы

- [ARCHITECTURE.md](ARCHITECTURE.md) — модули, потоки данных, decision points.
- [frontend/](frontend/) — Streamlit UI.
- [backend/](backend/) — agents_core, app_services, app_contracts, graph_core.
- [backend/graph_core/how_to_use.md](backend/graph_core/how_to_use.md) — как наполнять Neo4j.
- [frontend/TO-DO.md](frontend/TO-DO.md) — старый TO-DO по интеграции frontend ↔ backend session model (большая часть P0 уже сделана; оставшиеся пункты — в этом README).

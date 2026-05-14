# BioSeq Investigator app

🇬🇧 English version: [README_en.md](README_en.md).

Актуальное состояние на 2026-05-13: `app/` - основной runtime контур Streamlit-приложения. Поиск и карточки белков идут через `app/backend/bioseq_retriever`, `app/backend/app_services` и локальные FAISS/HDF5 artifacts. Neo4j/graph database контур из runtime убран.

## Что входит в app

| Путь | Назначение |
| --- | --- |
| `frontend/` | Streamlit UI: чат, protein card, sidebar с историями, cookie identity. |
| `backend/app_contracts/` | Pydantic-контракты между UI, service layer и session agent. |
| `backend/app_services/` | `BioSeqChatService`, wrapper над retriever pipeline, mapping UniProt records в UI-карточки, factory. |
| `backend/agents_core/` | LangGraph session agent и persistence glue для Supabase/Postgres. |
| `backend/bioseq_retriever/` | Рабочая копия bioseq retriever внутри backend: pipeline, UniProt fetch, search-service client, rerank. |

Старый root-level retriever вынесен в `depricated/bioseq_retriever/` как rollback/reference snapshot. Новую runtime-интеграцию нужно делать через `app/backend/bioseq_retriever`.

## Быстрый запуск

```bash
pip install -r app/frontend/requirements.txt
```

Запуск search/rerank gateway:

```bash
python app/backend/bioseq_retriever/services/search_service.py
```

Запуск Streamlit:

```bash
BIOSEQ_BACKEND=runtime streamlit run app/frontend/app.py
```

Локально приложение доступно на стандартном Streamlit URL, обычно `http://localhost:8501`.

## Минимальная конфигурация

Пример переменных лежит в `example.env.txt`. Основные переменные:

| Переменная | Назначение |
| --- | --- |
| `BIOSEQ_BACKEND=runtime` | Включает основной backend runtime. Допустим также `mock` для scripted UI demo. |
| `BIOSEQ_SEARCH_SERVICE_URL=http://localhost:8002` | URL unified BioSeq search/rerank gateway. |
| `BIOSEQ_ENABLE_RUNTIME_RETRIEVER=true` | Разрешает вызов `app/backend/bioseq_retriever` из service layer. |
| `MISTRAL_API_KEY` или `OPENAI_API_KEY` | Нужен текущему `backend/bioseq_retriever/src/pipeline.py` для LLM extraction. |
| `SUPABASE_DB_URL` | Optional, но нужен для persistent history, sidebar restore и корректного follow-up routing. |
| `APP_WORKSPACE_ID`, `APP_USER_ROLE` | Optional metadata для session context. |
| `APP_PASSWORD` | Optional простой password gate для Streamlit. |

Локальные data artifacts:

| Artifact | Назначение |
| --- | --- |
| `data/per-protein.h5` | Protein embeddings. |
| `data/per-protein.index` | FAISS protein index. |
| `data/per-protein.accessions.json` | FAISS row -> UniProt accession cache. |
| `data/per-gene.*` | Optional DNA artifacts. |

Эти файлы тяжелые и должны оставаться локальными или жить в dataset/object storage. Они не должны попадать в git.

## Chat LLM follow-up

Первый пользовательский turn с последовательностью идет в runtime retriever. Follow-up вопросы после сохраненного первого turn идут в `frontend/chat_llm_pipeline.py` и не перерисовывают protein card.

Provider выбирается через:

| Переменная | Поведение |
| --- | --- |
| `BIOSEQ_CHAT_LLM_PROVIDER=auto` | По умолчанию: Gemini proxy, если задан proxy URL/token; иначе OpenAI при наличии `OPENAI_API_KEY`. |
| `BIOSEQ_CHAT_LLM_PROVIDER=gemini_proxy` | Явно использовать proxy. |
| `BIOSEQ_CHAT_LLM_PROVIDER=openai` | Явно использовать OpenAI. |
| `BIOSEQ_LLM_PROXY_URL`, `BIOSEQ_LLM_PROXY_TOKEN` | Gemini proxy endpoint/token. |
| `OPENAI_API_KEY`, `BIOSEQ_OPENAI_CHAT_MODEL` | OpenAI key/model для follow-up chat. |

`OPENAI_API_KEY` может одновременно использоваться и текущим retriever pipeline, и follow-up chat LLM. Для retriever-provider выбора есть `BIOSEQ_LLM_PROVIDER=mistral|openai`.

Важно: текущий routing первого/follow-up turn опирается на `working_memory.turn_count` в `public.chat_sessions`. Без `SUPABASE_DB_URL` persistence выключается, и follow-up routing деградирует в повторный retriever turn.

## Runtime data flow

```text
Streamlit submit
  -> frontend/chat_pipeline.py
  -> backend/app_services/BioSeqChatService
  -> backend/app_services/BioSeqRetrieverPipeline
  -> backend/bioseq_retriever/src/pipeline.py
  -> backend/bioseq_retriever/services/search_service.py
  -> UniProt metadata + CandidateView/ProteinView
  -> agents_core/retriever_agent/runtime_agent.py
  -> session_db_adapter -> public.chat_sessions
  -> Streamlit protein card
```

Search service - отдельный тяжелый process, который грузит embedding models и FAISS indices. Это не FastAPI API "внутри агента"; агент и app services используют его как runtime dependency через `BIOSEQ_SEARCH_SERVICE_URL`.

## Persistence

Если задан `SUPABASE_DB_URL`, backend включает:

- LangGraph checkpoints;
- LangGraph store;
- compact session rows в `public.chat_sessions`;
- sidebar history и restore;
- turn counter для routing retriever vs follow-up LLM.

Если `SUPABASE_DB_URL` не задан или init падает, используется memory fallback. Приложение стартует, но история и follow-up routing не будут надежно сохраняться между turns/reruns.

## Тесты

Все тесты проекта собраны в верхнем `tests/`:

| Путь | Назначение |
| --- | --- |
| `tests/backend/bioseq_retriever/` | Тесты рабочей backend-копии retriever. |
| `tests/depricated/bioseq_retriever/` | Тесты deprecated snapshot. |
| `tests/scripts/` | Тесты старых utility/scripts checks. |
| `tests/eval/` | Evaluation suite и validation datasets. |

## Вне app

`data_prep/` остается отдельным project-level контуром подготовки локальных artifacts. Он не является runtime частью `app/`, но нужен для генерации/обновления данных.

## Legacy и cleanup

- `frontend/embeddings_pipeline.py` и `frontend/vector_db_adapter.py` оставлены как legacy paths до отдельной frontend cleanup.
- `depricated/bioseq_retriever/` оставлен как rollback/reference snapshot.
- Neo4j, `graph_core` и graph retriever agent не являются частью актуального runtime.

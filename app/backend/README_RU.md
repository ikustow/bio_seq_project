# Backend Layer

English version: [README.md](README.md).

`app/backend` - application backend BioSeq Investigator. Этот слой принимает
структурированные запросы от Streamlit UI, решает, нужен ли новый retrieval
turn или follow-up ответ, нормализует результаты UniProt в UI-ready контракты
и сохраняет состояние сессии.

## Зачем нужен backend

Backend отделяет продуктовую логику от Streamlit. Благодаря этому frontend
работает с простыми DTO (`ChatTurnRequest`, `ChatTurnResult`,
`CandidateView`, `ProteinView`) и не знает, как именно запускаются LangGraph,
FAISS, ProtT5, UniProt lookup, Chat LLM или Supabase persistence.

Практический эффект:

- проще менять retriever без переписывания UI;
- проще тестировать routing и session state отдельно от Streamlit;
- один контракт подходит для Streamlit, будущего API и eval harness;
- тяжелые модели и gateway можно держать отдельно от пользовательского
  интерфейса;
- session restore остается воспроизводимым, потому что backend пишет compact
  state в Postgres.

## Основной flow

```text
ChatTurnRequest
  -> app_services.BioSeqChatService.submit_turn()
  -> route:
       direct UniProt lookup
       object follow-up
       sequence retrieval
       plain Chat LLM follow-up
  -> BioSeqRetrieverPipeline / ChatLLMService / SuggestedQuestionsService
  -> agents_core runtime session state
  -> ChatTurnResult
```

Для sequence retrieval flow длиннее:

```text
BioSeqRetrieverPipeline
  -> app/backend/bioseq_retriever/src/pipeline.py
  -> search_service.py FastAPI gateway
  -> ProtT5/FAISS or BLAST/DNA path
  -> UniProt metadata
  -> rerank
  -> protein_view_mapper.uniprot_record_to_candidate()
```

## Папки и ответственность

| Путь | Роль |
| --- | --- |
| `app_contracts/` | Pydantic DTO для границы backend/frontend: requests, responses, session snapshots, protein/candidate views. |
| `app_services/` | Application orchestration: turn routing, retriever adapter, Chat LLM, suggested questions, UniProt direct lookup. |
| `agents_core/` | LangGraph session-agent, shared `AppContext`, persistence resources, compact session state. |
| `agents_core/docs/` | Технические заметки по agent layer, contracts и Supabase/Postgres integration. |
| `bioseq_retriever/` | Runtime retriever library: LangGraph pipeline, UniProt fetch, search clients, rerank, FastAPI gateway. |
| `graph_core/` | Reserved/legacy graph data area; не основной runtime path. |

## Ключевые entrypoints

- `app_services/service_factory.py` - создает runtime или mock chat service по
  `BIOSEQ_BACKEND`.
- `app_services/bioseq_chat.py` - главный application service и turn router.
- `app_services/retriever_pipeline.py` - service-level wrapper над
  `bioseq_retriever`, deterministic DNA/protein extraction и safety checks.
- `app_services/chat_llm.py` - follow-up LLM provider abstraction.
- `app_services/protein_view_mapper.py` - UniProt JSON -> `CandidateView`.
- `app_services/uniprot_lookup.py` - прямой lookup accession/mnemonic ID.
- `agents_core/retriever_agent/runtime_agent.py` - session agent state.
- `bioseq_retriever/services/search_service.py` - FastAPI gateway с тяжелыми
  моделями и FAISS индексами.

## Контракты

Главный публичный контракт backend:

```python
from backend.app_contracts import ChatTurnRequest
from backend.app_services.service_factory import create_bioseq_chat_service

service = create_bioseq_chat_service()
result = service.submit_turn(
    ChatTurnRequest(
        message=">seq\nMENS...",
        session_id="session_001",
        user_id="local-user",
    )
)
```

Важные DTO:

- `ChatTurnRequest` - вход одного пользовательского turn-а.
- `ChatTurnResult` - ответ backend для UI.
- `ObjectsPatch` - patch для frontend object registry.
- `BioSeqPipelineSnapshot` - состояние extraction/retrieval pipeline.
- `SessionSnapshot` - компактный snapshot сессии.
- `ProteinView` и `CandidateView` - UI-ready protein card model.

Источник правды для этих контрактов лежит в `app_contracts/`; поведение
сервисов реализовано в `app_services/`.

## Runtime режимы

`BIOSEQ_BACKEND`:

- `runtime`, `bioseq`, `bioseq_retriever` - live backend;
- `mock` - scripted service для UI/dev demo без тяжелых моделей.

Live backend создает:

1. `PersistenceResources` из `SUPABASE_DB_URL` или null fallback.
2. `BioSeqRuntimeSessionAgent`.
3. `BioSeqRetrieverPipeline`.
4. `ChatLLMService`.
5. `SuggestedQuestionsService`.
6. `BioSeqChatService`.

## Search gateway

`bioseq_retriever/services/search_service.py` поднимает FastAPI приложение и
держит тяжелые модели в отдельном процессе.

| Endpoint | Назначение |
| --- | --- |
| `POST /search/protein` | ProtT5 embedding + FAISS protein index. |
| `POST /search/dna` | DNA embedding/index path, если artifacts доступны. |
| `POST /rerank` | Contextual rerank по UniProt records и вопросу пользователя. |

Локально gateway обычно запускается отдельно:

```bash
python app/backend/bioseq_retriever/services/search_service.py
```

Streamlit Space может стартовать gateway через frontend supervisor, если
включен `BIOSEQ_SPAWN_GATEWAY`.

## Persistence

Если задан `SUPABASE_DB_URL`, backend использует Postgres-compatible storage:

- LangGraph checkpointer/store для agent state;
- `public.chat_sessions` repository для sidebar/session restore;
- compact `working_memory` с последними candidates, объектами, сообщениями и
  выбранным accession/sequence.

Если `SUPABASE_DB_URL` не задан или соединение не удалось, backend продолжает
работать с null repository, но история сессий не сохраняется.

Реализация persistence лежит в `agents_core/shared/services/persistence.py`.

## Конфигурация

Минимум для live backend:

```dotenv
BIOSEQ_BACKEND=runtime
BIOSEQ_ENABLE_RUNTIME_RETRIEVER=true
BIOSEQ_SEARCH_SERVICE_URL=http://localhost:8002
MISTRAL_API_KEY=...
# или OPENAI_API_KEY=...
```

Рекомендуемое:

```dotenv
SUPABASE_DB_URL=postgresql://user:password@host:5432/postgres
BIOSEQ_DATA_SOURCE=hf:radda-i/bioseq-data
BIOSEQ_CHAT_LLM_PROVIDER=auto
```

Полный шаблон: [../../example.env.txt](../../example.env.txt).

## Тесты

Полезные проверки backend слоя:

```bash
pytest tests/unit/backend/app_services
pytest tests/unit/backend/agents_core
pytest tests/backend/bioseq_retriever
python scripts/smoke_chat_pipeline_routing.py
python scripts/smoke_first_turn_router.py
```

Для качества retrieval:

```bash
python tests/eval/run_all.py
```

Сценарии eval описаны в [../../tests/eval/README.md](../../tests/eval/README.md).

## Технические ссылки

Внутренние:

- [BioSeq Retriever](bioseq_retriever/README.md)

Внешние:

- [FastAPI docs](https://fastapi.tiangolo.com/)
- [LangGraph docs](https://docs.langchain.com/oss/python/langgraph)
- [FAISS docs](https://faiss.ai/index.html)
- [ProtT5 model card](https://huggingface.co/Rostlab/prot_t5_xl_uniref50)
- [UniProt API help](https://www.uniprot.org/help/api)
- [Supabase Postgres docs](https://supabase.com/docs/guides/database/overview)

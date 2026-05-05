# Agent Core Documentation

Этот каталог описывает текущую агентскую систему в `app/backend/agents_core`.

## Что сейчас есть

`agents_core` - это слой с LangGraph-агентами и общей инфраструктурой для контекста, памяти и доступа к графовой БД.

Текущая структура:

| Путь | Назначение |
| --- | --- |
| `retriever_agent/` | DB-only LangGraph pipeline для поиска похожих белков по последовательности или accession-контексту. |
| `shared/config.py` | Env-настройки, Neo4j profiles, лимиты session state. |
| `shared/models.py` | Общие модели контекста, сессии и persistence resources. |
| `shared/services/graph.py` | Тонкий Neo4j client и read-only Cypher guard helper. |
| `shared/services/persistence.py` | LangGraph checkpointer/store и `chat_sessions` repository. |
| `shared/services/session_state.py` | Извлечение session patch из сообщений обычных chat/tool агентов. |
| `docs/` | Эта документация. |

В `app/backend/app_services/service_factory.py` также есть ссылка на `backend.agents_core.session_agent.agent.SessionGraphAgent`, но в текущем дереве `agents_core` такого пакета нет. Рабочий агент в текущей папке - `retriever_agent`.

## Общая архитектура

Поток данных для агента выглядит так:

1. Внешний слой создает `AppContext`.
2. Factory создает Neo4j client, graph retrieval service и persistence resources.
3. Агент компилирует LangGraph pipeline с `persistence.checkpointer`.
4. Каждый вызов агента использует `context.session_id` как LangGraph `thread_id`.
5. LangGraph сохраняет полное состояние по thread через checkpointer.
6. Агент отдельно собирает компактный session patch и пишет его в `public.chat_sessions`.

`AppContext`:

```python
class AppContext(BaseModel):
    user_id: str
    session_id: str
    workspace_id: str | None = None
    user_role: str | None = None
```

`session_id` одновременно является:

- внешним id сессии;
- LangGraph `thread_id`;
- `thread_id` в строке `public.chat_sessions`.

## Persistence modes

Persistence создается через:

```python
create_persistence_resources(os.getenv("SUPABASE_DB_URL"), exit_stack)
```

Если `SUPABASE_DB_URL` задан и зависимости доступны, включается `mode="postgres"`:

- `PostgresSaver` - short-term state/checkpoints LangGraph;
- `PostgresStore` - long-term store LangGraph;
- `PostgresSessionRepository` - компактная строка сессии в `public.chat_sessions`.

Если `SUPABASE_DB_URL` не задан или Postgres/Supabase init падает, включается `mode="memory"`:

- `InMemorySaver`;
- `InMemoryStore`;
- `NullSessionRepository`;
- warning в `PersistenceResources.warnings`.

Важно: memory mode теряет состояние после рестарта процесса.

## Основные классы и модели

| Класс/модель | Файл | Что делает |
| --- | --- | --- |
| `AppContext` | `shared/models.py` | Контекст пользователя и сессии. Передается в каждый агентский вызов. |
| `PersistenceResources` | `shared/models.py` | Объединяет `checkpointer`, `store`, `session_repository`, `mode`, `warnings`. |
| `SessionPatch` | `shared/models.py` | Канонический компактный state, который можно сохранять в `chat_sessions`. |
| `SessionRow` | `shared/models.py` | Полная строка `public.chat_sessions`. |
| `SessionStateView` | `shared/models.py` | Упрощенный view для derive logic из message history. |
| `Neo4jGraphClient` | `shared/services/graph.py` | Выполняет Cypher через neo4j driver. |
| `PostgresSessionRepository` | `shared/services/persistence.py` | Читает/пишет `public.chat_sessions`. |
| `BioSeqRetrieverGraphAgent` | `retriever_agent/agent.py` | Публичный wrapper над retriever LangGraph pipeline. |
| `GraphRetrievalService` | `app_services/graph_retrieval.py` | Доменный слой поиска protein/candidates в Neo4j. |

## Тулы и сервисные операции

В текущем `retriever_agent` нет LangChain tool-calling loop и нет функций, оформленных как `@tool`. Вместо этого есть фиксированный LangGraph pipeline, где роль "тулов" выполняют node-функции и доменные service methods.

Graph nodes:

| Node | Тип операции |
| --- | --- |
| `extract` | LLM/deterministic structured extraction. |
| `resolve_file` | Controlled miss для runtime filepath. |
| `use_raw` | Sequence normalization. |
| `translate` | DNA -> protein translation. |
| `pass_protein` | Protein normalization. |
| `rank` | Exact graph/hash retrieval. |
| `rerank` | Context-aware candidate rerank. |

Доступные доменные операции `GraphRetrievalService`:

| Метод | Что делает |
| --- | --- |
| `resolve_input` | Ищет protein по accession/gene/entry/name. |
| `find_by_sequence_hash` | Ищет protein по hash белковой последовательности. |
| `find_encoded_protein_by_sequence_hash` | Ищет protein, кодируемый DNA sequence, с fallback на translated protein hash. |
| `retrieve_candidates` | Возвращает target protein и похожих соседей из Neo4j. |
| `get_protein_view` | Возвращает один `ProteinView`. |
| `get_candidate_context` | Возвращает краткий контекст соседей. |

## Env options

Общие переменные:

| Env | Значение |
| --- | --- |
| `SUPABASE_DB_URL` | Postgres connection string Supabase. Нужен для production persistence. |
| `APP_USER_ID` | User id для `AppContext`. |
| `APP_SESSION_ID` | Session/thread id. |
| `APP_WORKSPACE_ID` | Workspace id, optional. |
| `APP_USER_ROLE` | User role, optional. |
| `NEO4J_PROFILE` | `local` или `cloud`. |
| `NEO4J_*` / `NEO4J_LOCAL_*` / `NEO4J_CLOUD_*` | URI, database, username, password, insecure flag. |
| `BIOSEQ_LLM_PROVIDER` | `openai` или `mistral` для LLM extraction. |
| `OPENAI_API_KEY` / `MISTRAL_API_KEY` | API keys для extractor. |
| `OPENAI_MODEL` / `MISTRAL_MODEL` | Модель extractor. |
| `BIOSEQ_INPUT_EXTRACTOR` | В `pipeline_interface.py`: `llm` включает LLM extractor, иначе deterministic. |

## Документы

- [retriever_agent.md](retriever_agent.md) - подробно про `retriever_agent`.
- [app_services_contracts.md](app_services_contracts.md) - как агенты связаны с `app_services` и `app_contracts`.
- [adding_agents_supabase.md](adding_agents_supabase.md) - как подключать контекст, LangGraph memory и Supabase session storage к новым агентам.

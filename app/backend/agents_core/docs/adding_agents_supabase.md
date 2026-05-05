# Adding New Agents With Context And Supabase Persistence

Этот документ описывает текущий локальный паттерн подключения новых агентов к `AppContext`, LangGraph memory и Supabase/Postgres session storage.

## Минимальный контракт нового агента

Новый агент должен принимать `AppContext` в публичных методах и всегда использовать `context.session_id` как LangGraph `thread_id`.

Рекомендуемый public API:

```python
class MyAgent:
    @property
    def warnings(self) -> list[str]:
        return self._persistence.warnings

    @property
    def persistence_mode(self) -> str:
        return self._persistence.mode

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]:
        config = {"configurable": {"thread_id": context.session_id}}
        result = self._graph.invoke(initial_state(message), config=config)
        current_state = dict(self._graph.get_state(config).values)
        self._sync_session(context, current_state)
        return result, current_state

    def get_current_state(self, context: AppContext) -> dict[str, Any]:
        config = {"configurable": {"thread_id": context.session_id}}
        return dict(self._graph.get_state(config).values)

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]:
        config = {"configurable": {"thread_id": context.session_id}}
        self._graph.update_state(config, patch)
        current_state = dict(self._graph.get_state(config).values)
        self._sync_session(context, current_state)
        return current_state
```

## Создание persistence resources

Factory должен держать `ExitStack` живым столько же, сколько жив агент/сервис.

```python
from contextlib import ExitStack
from backend.agents_core.shared.services.persistence import create_persistence_resources

exit_stack = ExitStack()
persistence = create_persistence_resources(os.getenv("SUPABASE_DB_URL"), exit_stack)
exit_stack.callback(persistence.session_repository.close)

agent = MyAgent(..., persistence=persistence)
agent._exit_stack = exit_stack
```

Почему `ExitStack` важен:

- `PostgresSaver.from_conn_string(...)` и `PostgresStore.from_conn_string(...)` открывают context-managed ресурсы;
- если stack закрыть сразу после factory, checkpointer/store перестанут работать;
- поэтому `service_factory.py` кладет `_exit_stack` на agent/service.

## Компиляция LangGraph

Для thread-scoped short-term memory:

```python
graph = builder.compile(checkpointer=persistence.checkpointer)
```

Если агенту нужна long-term memory между разными сессиями, store тоже надо передать при compile:

```python
graph = builder.compile(
    checkpointer=persistence.checkpointer,
    store=persistence.store,
)
```

Текущий `retriever_agent` передает только checkpointer. `persistence.store` создается, но напрямую не используется.

## Использование thread_id

Всегда передавайте config в `invoke`, `get_state`, `update_state`:

```python
config = {"configurable": {"thread_id": context.session_id}}
```

Без `thread_id` LangGraph state не будет корректно привязан к сессии. В текущем проекте `session_id` является единственным стабильным ключом между API, LangGraph checkpoints и `chat_sessions`.

## Session storage в `public.chat_sessions`

LangGraph checkpointer хранит полный технический state. `public.chat_sessions` хранит компактную application snapshot для UI/API.

Писать туда нужно через:

```python
saved_session = self._persistence.session_repository.get_session(context.session_id)
patch = derive_or_build_session_patch(current_state)
merged = merge_with_saved(saved_session, patch)
self._persistence.session_repository.upsert_session(context, merged)
```

Для простого chat/tool агента можно переиспользовать общий helper:

```python
from backend.agents_core.shared.services.session_state import derive_session_patch

patch = derive_session_patch(current_state)
self._persistence.session_repository.upsert_session(context, patch)
```

`derive_session_patch` умеет:

- брать `messages`;
- вытаскивать protein records из JSON tool output;
- вытаскивать protein-like sequences из текста;
- обновлять `working_memory.message_count`;
- проставлять `active_accession`, `active_sequence_id`, `working_set_ids`, summaries.

Если у агента доменная state shape отличается, лучше сделать свой `_derive_session_patch`, как в `retriever_agent/agent.py`, но сохранить те же поля `SessionPatch`.

## Поля SessionPatch

Сохраняемый patch должен соответствовать `SessionPatch`:

| Поле | Как использовать |
| --- | --- |
| `session_summary` | Краткий человекочитаемый итог текущей сессии/последнего шага. |
| `proteins` | Список `ProteinRecord`. |
| `sequences` | Список `SequenceRecord`. |
| `working_memory` | Компактная машинная память для UI/следующих шагов. |
| `active_sequence_id` | Текущая выбранная sequence. |
| `active_accession` | Текущий выбранный protein accession. |
| `last_analysis_summary` | Краткий итог последнего анализа. |
| `working_set_ids` | Стабильный набор accession/sequence ids для текущей работы. |
| `current_mode` | Название режима/агента, например `bioseq_retriever_langgraph`. |
| `last_tool_results_summary` | Краткий итог последнего tool/db output. |

## Требования к Supabase/Postgres

`create_persistence_resources` автоматически вызывает:

```python
checkpointer.setup()
store.setup()
```

Это поднимает внутренние таблицы LangGraph checkpoint/store.

Но application table `public.chat_sessions` должна существовать отдельно. `PostgresSessionRepository` ожидает такие колонки:

```sql
create table if not exists public.chat_sessions (
    session_id text primary key,
    thread_id text not null,
    user_id text not null,
    workspace_id text,
    user_role text,
    session_summary text,
    proteins jsonb not null default '[]'::jsonb,
    sequences jsonb not null default '[]'::jsonb,
    working_memory jsonb not null default '{}'::jsonb,
    active_sequence_id text,
    active_accession text,
    last_analysis_summary text,
    working_set_ids jsonb not null default '[]'::jsonb,
    current_mode text,
    last_tool_results_summary text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
```

Если таблица доступна через Supabase Data API, включайте RLS и политики под реальную модель доступа. Backend connection string для агента должен быть серверным секретом и не должен попадать во frontend.

## Factory checklist для нового агента

1. Загрузить `.env` через `load_env_file(DEFAULT_ENV_PATH)`.
2. Собрать внешние клиенты: Neo4j, LLM, tools, domain services.
3. Создать `ExitStack`.
4. Вызвать `create_persistence_resources(os.getenv("SUPABASE_DB_URL"), exit_stack)`.
5. Зарегистрировать `exit_stack.callback(persistence.session_repository.close)`.
6. Передать `persistence` в агент.
7. Сохранить `exit_stack` на долгоживущем agent/service объекте.
8. В каждом public вызове строить `AppContext` и config с `thread_id=context.session_id`.
9. После `invoke`/`update_state` синхронизировать compact session patch в `chat_sessions`.
10. Возвращать `warnings`, чтобы UI/API мог показать fallback на memory mode или ошибки persistence.

## Minimal skeleton

```python
from typing import Any, Annotated, TypedDict
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, AIMessage

from backend.agents_core.shared.models import AppContext, PersistenceResources
from backend.agents_core.shared.services.session_state import derive_session_patch


class MyState(TypedDict):
    messages: Annotated[list[Any], add_messages]
    current_mode: str | None
    working_memory: dict[str, Any]


class MyAgent:
    def __init__(self, persistence: PersistenceResources) -> None:
        self._persistence = persistence
        builder = StateGraph(MyState)
        builder.add_node("respond", self._respond)
        builder.set_entry_point("respond")
        builder.add_edge("respond", END)
        self._graph = builder.compile(checkpointer=persistence.checkpointer)

    @property
    def warnings(self) -> list[str]:
        return self._persistence.warnings

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]:
        config = {"configurable": {"thread_id": context.session_id}}
        result = self._graph.invoke(
            {
                "messages": [HumanMessage(content=message)],
                "current_mode": "my_agent",
                "working_memory": {},
            },
            config=config,
        )
        self._graph.update_state(config, {"messages": [AIMessage(content="Done.")]})
        current_state = dict(self._graph.get_state(config).values)
        self._persistence.session_repository.upsert_session(
            context,
            derive_session_patch(current_state),
        )
        return result, current_state

    def _respond(self, state: MyState) -> dict[str, Any]:
        return {
            "current_mode": "my_agent",
            "working_memory": {
                **(state.get("working_memory") or {}),
                "last_sync_source": "my_agent",
            },
        }
```

## Практические правила

- Не пишите напрямую в `chat_sessions` из node-функций. Лучше синхронизировать session patch на границе public method после завершения graph run.
- Для списков в LangGraph state используйте reducer, например `add_messages`, иначе новые значения перетрут старые.
- Не храните большие raw payloads в `working_memory`; туда лучше класть compact summaries, ids и counts.
- Для production используйте `SUPABASE_DB_URL`; memory mode годится только для локальной разработки.
- Для reset/delete session нужен отдельный дизайн: LangGraph checkpoints/store и `chat_sessions` - разные уровни хранения.

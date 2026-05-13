# Как добавить нового агента и подключить Supabase

Этот документ объясняет максимально простым языком, как подключать новых агентов к:

- `AppContext`;
- LangGraph memory;
- Supabase/Postgres persistence;
- `public.chat_sessions`.

## Что мы хотим получить

Новый агент должен уметь:

1. принимать сообщение пользователя;
2. знать, в какой сессии он работает;
3. сохранять свое состояние;
4. восстанавливать состояние по `session_id`;
5. записывать короткий snapshot для UI;
6. работать локально даже без Supabase, но предупреждать об этом.

## Минимальная идея

У каждого вызова агента есть context:

```python
AppContext(
    user_id="...",
    session_id="...",
    workspace_id="...",
    user_role="...",
)
```

Самое важное поле:

```python
session_id
```

Потому что оно используется как:

```text
session_id
  = внешний id сессии
  = LangGraph thread_id
  = thread_id в public.chat_sessions
```

## Минимальный контракт агента

Новый агент должен иметь примерно такие методы:

```python
class MyAgent:
    @property
    def warnings(self) -> list[str]:
        return self._persistence.warnings

    @property
    def persistence_mode(self) -> str:
        return self._persistence.mode

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]:
        ...

    def get_current_state(self, context: AppContext) -> dict[str, Any]:
        ...

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]:
        ...
```

Простыми словами:

| Метод | Зачем нужен |
| --- | --- |
| `warnings` | Сообщить, если persistence работает не так, как хотелось. |
| `persistence_mode` | Показать, используется `postgres` или `memory`. |
| `invoke` | Запустить агента. |
| `get_current_state` | Получить состояние текущей сессии. |
| `update_current_state` | Вручную поменять состояние. |

## Как создать persistence

В проекте уже есть helper:

```python
create_persistence_resources(...)
```

Он лежит в:

```text
app/backend/agents_core/shared/services/persistence.py
```

Использование:

```python
from contextlib import ExitStack
from backend.agents_core.shared.services.persistence import create_persistence_resources

exit_stack = ExitStack()
persistence = create_persistence_resources(os.getenv("SUPABASE_DB_URL"), exit_stack)
exit_stack.callback(persistence.session_repository.close)

agent = MyAgent(..., persistence=persistence)
agent._exit_stack = exit_stack
```

## Зачем нужен ExitStack

`PostgresSaver` и `PostgresStore` открывают соединения/контексты.

Если закрыть их сразу после создания агента, агент потом не сможет сохранять state.

Поэтому `ExitStack` должен жить столько же, сколько живет agent или service.

В проекте используется простой прием:

```python
agent._exit_stack = exit_stack
```

или:

```python
service._exit_stack = exit_stack
```

Это нужно, чтобы Python не закрыл ресурсы слишком рано.

## Что возвращает create_persistence_resources

Он возвращает:

```python
PersistenceResources(
    checkpointer=...,
    store=...,
    session_repository=...,
    mode="postgres" или "memory",
    warnings=[...],
)
```

Что это значит:

| Поле | Простое объяснение |
| --- | --- |
| `checkpointer` | Сохраняет полный LangGraph state. |
| `store` | Долгосрочная память LangGraph. |
| `session_repository` | Читает/пишет `public.chat_sessions`. |
| `mode` | Где сейчас хранится память: Postgres или memory. |
| `warnings` | Что пошло не так или какой fallback включился. |

## Два режима persistence

### Postgres mode

Включается, если:

```text
SUPABASE_DB_URL задан
```

и нужные зависимости установлены:

```text
langgraph-checkpoint-postgres
psycopg[binary]
```

Тогда используются:

- `PostgresSaver`;
- `PostgresStore`;
- `PostgresSessionRepository`.

### Memory mode

Включается, если:

- `SUPABASE_DB_URL` не задан;
- или зависимости не установлены;
- или подключиться к Supabase/Postgres не получилось.

Тогда используются:

- `InMemorySaver`;
- `InMemoryStore`;
- `NullSessionRepository`.

Важно:

```text
memory mode теряет данные после рестарта процесса
```

## Как подключить LangGraph checkpointer

Когда вы собираете граф:

```python
builder = StateGraph(MyState)
```

в конце надо сделать:

```python
self._graph = builder.compile(checkpointer=persistence.checkpointer)
```

Это говорит LangGraph:

```text
Сохраняй state после шагов графа.
```

Если агенту нужна долгосрочная память через store:

```python
self._graph = builder.compile(
    checkpointer=persistence.checkpointer,
    store=persistence.store,
)
```

Текущий retriever agent использует только `checkpointer`.

## Как использовать thread_id

В каждом public method агента надо делать:

```python
config = {"configurable": {"thread_id": context.session_id}}
```

И потом использовать этот config:

```python
result = self._graph.invoke(initial_state, config=config)
current_state = dict(self._graph.get_state(config).values)
self._graph.update_state(config, patch)
```

Если забыть `thread_id`, LangGraph не будет нормально понимать, к какой сессии относится state.

## Как сохранять compact session в chat_sessions

Полный LangGraph state может быть большим и техническим.

UI обычно не нужно видеть все.

Поэтому после запуска агента надо собрать короткий patch:

```python
patch = derive_session_patch(current_state)
```

и записать:

```python
self._persistence.session_repository.upsert_session(context, patch)
```

Для простых chat/tool агентов можно использовать общий helper:

```python
from backend.agents_core.shared.services.session_state import derive_session_patch
```

Если у агента специфичная структура state, лучше сделать свой `_derive_session_patch`.

Так сделано в:

```text
app/backend/agents_core/retriever_agent/agent.py
```

## Что такое SessionPatch

`SessionPatch` - это форма короткого состояния.

Поля:

| Поле | Простое объяснение |
| --- | --- |
| `session_summary` | Короткое описание текущей сессии. |
| `proteins` | Белки, которые агент нашел или использует. |
| `sequences` | Последовательности, которые агент нашел или использует. |
| `working_memory` | Небольшая машинная память для следующих шагов. |
| `active_sequence_id` | Какая sequence сейчас активна. |
| `active_accession` | Какой protein accession сейчас активен. |
| `last_analysis_summary` | Короткий итог последнего анализа. |
| `working_set_ids` | Id объектов, с которыми работает пользователь. |
| `current_mode` | Какой режим/агент сейчас активен. |
| `last_tool_results_summary` | Краткий итог последнего tool/db результата. |

## Таблица public.chat_sessions

LangGraph сам создает свои технические таблицы через:

```python
checkpointer.setup()
store.setup()
```

Но таблицу приложения:

```text
public.chat_sessions
```

надо создать отдельно.

Минимальная схема:

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

Если таблица доступна через Supabase Data API, надо подумать про RLS.

Простое правило:

```text
server-side connection string не должен попасть во frontend
```

## Минимальный пример агента

Это простой skeleton.

Он ничего умного не делает, но показывает правильную форму:

```python
from typing import Any, Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

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

    @property
    def persistence_mode(self) -> str:
        return self._persistence.mode

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

    def get_current_state(self, context: AppContext) -> dict[str, Any]:
        config = {"configurable": {"thread_id": context.session_id}}
        return dict(self._graph.get_state(config).values)

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]:
        config = {"configurable": {"thread_id": context.session_id}}
        self._graph.update_state(config, patch)
        current_state = dict(self._graph.get_state(config).values)
        self._persistence.session_repository.upsert_session(
            context,
            derive_session_patch(current_state),
        )
        return current_state

    def _respond(self, state: MyState) -> dict[str, Any]:
        return {
            "current_mode": "my_agent",
            "working_memory": {
                **(state.get("working_memory") or {}),
                "last_sync_source": "my_agent",
            },
        }
```

## Checklist для добавления нового агента

1. Создать пакет агента в `app/backend/agents_core`.
2. Описать `State` агента.
3. Создать `StateGraph`.
4. Добавить nodes.
5. Добавить edges.
6. Скомпилировать graph с `persistence.checkpointer`.
7. В public methods использовать `thread_id=context.session_id`.
8. После запуска синхронизировать `chat_sessions`.
9. Вернуть `warnings`.
10. Создать factory в `app_services`, который соберет agent dependencies.

## Частые ошибки

### Ошибка: забыли thread_id

Плохо:

```python
self._graph.invoke(state)
```

Хорошо:

```python
self._graph.invoke(state, config={"configurable": {"thread_id": context.session_id}})
```

### Ошибка: закрыли ExitStack слишком рано

Плохо:

```python
with ExitStack() as stack:
    persistence = create_persistence_resources(url, stack)
    agent = MyAgent(persistence)
return agent
```

После выхода из `with` ресурсы закрыты.

Лучше:

```python
exit_stack = ExitStack()
persistence = create_persistence_resources(url, exit_stack)
agent = MyAgent(persistence)
agent._exit_stack = exit_stack
return agent
```

### Ошибка: писать огромные данные в working_memory

`working_memory` должна быть компактной.

Лучше хранить:

- ids;
- counts;
- короткие summaries;
- last mode;
- last source.

Не надо хранить там огромные сырые результаты.

### Ошибка: думать, что Supabase таблица chat_sessions создается сама

LangGraph tables создаются через `setup()`.

Но:

```text
public.chat_sessions
```

это application table. Ее надо создать отдельно.

## Самая короткая версия

Чтобы новый агент нормально работал с памятью:

```text
1. Передай ему PersistenceResources
2. Скомпилируй graph с checkpointer
3. Всегда используй thread_id=context.session_id
4. После invoke/update_state пиши compact patch в chat_sessions
5. Держи ExitStack живым
```


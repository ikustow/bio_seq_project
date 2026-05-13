# Как агенты связаны с app_services и app_contracts

Этот документ объясняет, зачем нужны `app_services` и `app_contracts`, если у нас уже есть `agents_core`.

Главная идея:

```text
agents_core не должен напрямую быть UI/API.
agents_core делает агентскую работу.
app_services связывает эту работу с приложением.
app_contracts описывает входы и выходы приложения.
```

## Три слоя простыми словами

### `app_contracts`

Это папка с Pydantic-моделями.

Можно думать так:

```text
app_contracts = договор о форме данных
```

Например:

- что приходит от UI;
- что возвращается в UI;
- как выглядит карточка protein;
- как выглядит candidate;
- как выглядит snapshot сессии.

### `app_services`

Это слой логики приложения.

Он не является самим агентом, но он решает:

- какой агент создать;
- какие клиенты ему передать;
- как превратить request в `AppContext`;
- когда вызвать agent;
- когда не вызывать agent;
- как собрать ответ для UI.

### `agents_core`

Это слой агентов.

Здесь:

- LangGraph pipeline;
- agent state;
- persistence;
- context models;
- session memory.

## Общая схема

```text
UI/API
  -> ChatTurnRequest из app_contracts
  -> BioSeqChatService из app_services
  -> AppContext
  -> Agent из agents_core
  -> GraphRetrievalService
  -> Neo4j
  -> Agent state
  -> SessionSnapshot
  -> ChatTurnResult из app_contracts
```

## Почему агент не принимает ChatTurnRequest напрямую

Потому что `ChatTurnRequest` - это внешний формат приложения.

Внутри агенту нужен более простой контекст:

```python
class AppContext(BaseModel):
    user_id: str
    session_id: str
    workspace_id: str | None = None
    user_role: str | None = None
```

То есть агенту важно знать:

- кто пользователь;
- какая сессия;
- какой workspace;
- какая роль.

А вот такие поля как:

- `selected_candidate_index`;
- `ui_context`;
- формат ответа для UI;

это уже забота `app_services`, а не агента.

## Что такое ChatTurnRequest

`ChatTurnRequest` лежит в:

```text
app/backend/app_contracts/chat.py
```

Он описывает входной запрос.

Поля:

| Поле | Простое объяснение |
| --- | --- |
| `message` | Текст пользователя. |
| `session_id` | Id сессии. Очень важное поле. |
| `user_id` | Id пользователя. |
| `workspace_id` | Id workspace, если есть. |
| `user_role` | Роль пользователя, если есть. |
| `selected_accession` | Если пользователь выбрал конкретный protein accession. |
| `selected_candidate_index` | Какой candidate выбран в UI. |
| `ui_context` | Дополнительный UI-контекст. Сейчас почти не используется. |

## Как ChatTurnRequest превращается в AppContext

В `BioSeqChatService` есть helper:

```python
def _context_from_request(request: ChatTurnRequest) -> AppContext:
    return AppContext(
        user_id=request.user_id,
        session_id=request.session_id,
        workspace_id=request.workspace_id,
        user_role=request.user_role,
    )
```

То есть сервис берет внешнюю модель и делает из нее внутренний agent context.

## Какой контракт должен поддерживать агент

`BioSeqChatService` не обязан знать конкретный класс агента.

Он ожидает, что агент умеет:

```python
class SessionGraphAgent(Protocol):
    @property
    def warnings(self) -> list[str]: ...

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def get_current_state(self, context: AppContext) -> dict[str, Any]: ...

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]: ...
```

Простыми словами:

| Метод | Что должен делать |
| --- | --- |
| `warnings` | Вернуть предупреждения, например что persistence работает в memory mode. |
| `invoke` | Запустить агента на сообщении пользователя. |
| `get_current_state` | Вернуть текущее состояние сессии. |
| `update_current_state` | Изменить состояние сессии вручную. |

Если новый агент поддерживает эти методы, его легче подключить к `BioSeqChatService`.

## Что делает BioSeqChatService

Файл:

```text
app/backend/app_services/bioseq_chat.py
```

Главный метод:

```python
submit_turn(request: ChatTurnRequest) -> ChatTurnResult
```

Он выбирает один из сценариев.

## Сценарий 1: пользователь выбрал accession

Если в request есть:

```python
selected_accession
```

сервис делает:

```python
agent.update_current_state(context, {"active_accession": request.selected_accession})
```

То есть он говорит агенту:

```text
Теперь активный белок вот этот.
```

Потом сервис сам достает candidates:

```python
GraphRetrievalService.retrieve_candidates(...)
```

И возвращает ответ.

В этом сценарии полноценный `agent.invoke` не нужен.

## Сценарий 2: пользователь отправил sequence или filepath

Сервис сначала запускает compatibility pipeline:

```python
BioSeqRetrieverPipeline
```

Это не тот же самый `retriever_agent`, а service-level pipeline, который помогает быстро обработать sequence-like input.

Если pipeline нашел candidates, сервис может вернуть результат без вызова основного agent `invoke`.

Если pipeline получил controlled miss, сервис возвращает аккуратное сообщение.

При этом сервис патчит state агента:

```python
agent.update_current_state(context, state_patch)
```

Так агентская сессия все равно помнит, что произошло.

## Сценарий 3: обычное сообщение

Если это не выбор accession и не sequence-like input, сервис вызывает:

```python
result, state = agent.invoke(request.message, context)
```

Потом:

1. достает assistant message из agent result;
2. смотрит `active_accession`;
3. если `active_accession` нет, пробует найти accession/gene/name через `GraphRetrievalService.resolve_input`;
4. если accession найден, обновляет state;
5. достает candidates;
6. собирает `ChatTurnResult`.

## Что такое ChatTurnResult

`ChatTurnResult` - это ответ наружу.

Он содержит:

| Поле | Что значит |
| --- | --- |
| `session_id` | Id сессии. |
| `assistant_message` | Текст ответа ассистента. |
| `candidates` | Список protein candidates. |
| `selected_candidate_index` | Какой candidate выбран. |
| `revealed_sections` | Какие секции карточки можно показать. |
| `session` | Snapshot сессии. |
| `pipeline` | Snapshot pipeline, если он запускался. |
| `warnings` | Предупреждения. |

## Что такое SessionSnapshot

`SessionSnapshot` - это упрощенный state для UI.

Он содержит:

- `session_id`;
- `user_id`;
- `workspace_id`;
- `user_role`;
- `active_accession`;
- `active_sequence_id`;
- `current_mode`;
- `proteins`;
- `sequences`;
- `working_memory`;
- `message_history`.

Это не полный LangGraph state.
Это удобная витрина для приложения.

## Что такое ProteinView и CandidateView

`ProteinView` - это готовое описание белка для UI.

Там есть:

- accession;
- name;
- gene;
- organism;
- function text;
- disease;
- domains;
- keywords;
- GO terms;
- PubMed ids;
- sequence.

`CandidateView` - это protein плюс score:

- `protein`;
- `match_score`;
- `rank`;
- `similarity_score`;
- `context_score`;
- `evidence`.

## Где появляется GraphRetrievalService

`GraphRetrievalService` лежит в:

```text
app/backend/app_services/graph_retrieval.py
```

Он является мостом между агентом/сервисами и Neo4j.

Он умеет:

| Метод | Что делает |
| --- | --- |
| `resolve_input` | Ищет protein по accession/gene/name. |
| `find_by_sequence_hash` | Ищет protein по hash protein sequence. |
| `find_encoded_protein_by_sequence_hash` | Для DNA ищет protein, который sequence кодирует. |
| `retrieve_candidates` | Возвращает target protein и соседей. |
| `get_protein_view` | Возвращает карточку одного protein. |
| `get_candidate_context` | Возвращает краткий контекст соседей. |

## Почему GraphRetrievalService лежит в app_services, но используется агентом

Идеально `agents_core` мог бы быть совсем независимым.

Но сейчас `retriever_agent` переиспользует уже существующую доменную логику из `app_services`.

Это значит:

```text
retriever_agent
  -> GraphRetrievalService
  -> Neo4jGraphClient
  -> Neo4j
```

Так меньше дублирования Cypher и mapping-логики.

## Где Neo4j record превращается в UI-модель

Это делает:

```text
app/backend/app_services/protein_view_mapper.py
```

Схема:

```text
Neo4j record
  -> protein_record_to_view(...)
  -> ProteinView
  -> neighbor_record_to_candidate(...)
  -> CandidateView
```

## Что делает service_factory

Файл:

```text
app/backend/app_services/service_factory.py
```

Он собирает все зависимости.

Примерно так:

```text
прочитать .env
  -> понять настройки Neo4j
  -> создать Neo4jGraphClient
  -> создать GraphRetrievalService
  -> создать PersistenceResources
  -> создать Agent
  -> создать BioSeqChatService
```

Для retriever agent есть функция:

```python
create_bioseq_retriever_graph_agent(...)
```

Для chat service есть функция:

```python
create_bioseq_chat_service()
```

## Важное про graph-backed chat service

В `service_factory.py` есть импорт:

```python
from backend.agents_core.session_agent.agent import SessionGraphAgent
```

Но папки `session_agent` сейчас нет.

Значит, если включить `BIOSEQ_BACKEND=graph`, код может ожидать агент, которого в текущем дереве нет.

Рабочий и документированный агент сейчас:

```text
app/backend/agents_core/retriever_agent
```

## Правило для новых агентов

Если вы добавляете нового агента:

1. Не заставляйте агента принимать `ChatTurnRequest`.
2. Пусть агент принимает `AppContext`.
3. Пусть `app_services` решает, как из request сделать context.
4. Не отдавайте внутренний LangGraph state прямо в UI.
5. Возвращайте наружу `ChatTurnResult`, `SessionSnapshot`, `CandidateView`, `ProteinView`.
6. Если нужен Neo4j, передайте агенту service, а не создавайте driver внутри node.

## Простая ментальная модель

```text
app_contracts = форма входа и выхода
app_services = диспетчер и сборщик ответа
agents_core = рабочий механизм агента
GraphRetrievalService = мост к Neo4j
```


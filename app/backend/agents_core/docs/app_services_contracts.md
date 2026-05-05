# Agents, App Services, And App Contracts

Этот документ описывает, как `agents_core` связан с `app/backend/app_services` и `app/backend/app_contracts`.

## Слои

```text
UI / API
  -> app_contracts
  -> app_services
  -> agents_core
  -> Neo4j / Supabase / LLM providers
```

Роли слоев:

| Слой | Роль |
| --- | --- |
| `app_contracts` | Pydantic DTO для внешнего API/UI: request, response, session snapshot, protein/candidate views. |
| `app_services` | Application orchestration: создание агентов, wiring клиентов, адаптация request -> context -> response. |
| `agents_core` | LangGraph agents, shared context models, persistence, graph state и agent runtime. |

На практике `agents_core/retriever_agent` сейчас зависит от части `app_services`: он переиспользует `GraphRetrievalService` и helper-функции из `retriever_pipeline.py`.

## Как request доходит до агента

Основной chat flow живет в `app_services/bioseq_chat.py`.

```text
ChatTurnRequest
  -> _context_from_request(...)
  -> AppContext
  -> agent.invoke(...) / agent.update_current_state(...)
  -> internal LangGraph state
  -> SessionSnapshot
  -> ChatTurnResult
```

`ChatTurnRequest` из `app_contracts/chat.py` содержит:

| Поле | Как используется |
| --- | --- |
| `message` | User prompt для pipeline/agent. |
| `session_id` | Главный session/thread id. Попадает в `AppContext.session_id`. |
| `user_id` | Попадает в `AppContext.user_id` и session snapshot. |
| `workspace_id` | Optional context/snapshot поле. |
| `user_role` | Optional context/snapshot поле. |
| `selected_accession` | Если задан, сервис не спрашивает агента, а патчит `active_accession`. |
| `selected_candidate_index` | UI selection index в `ChatTurnResult`. |
| `ui_context` | Сейчас в agent flow почти не используется, зарезервирован для UI-side контекста. |

Преобразование в agent context:

```python
def _context_from_request(request: ChatTurnRequest) -> AppContext:
    return AppContext(
        user_id=request.user_id,
        session_id=request.session_id,
        workspace_id=request.workspace_id,
        user_role=request.user_role,
    )
```

## Protocol для агента в app_services

`BioSeqChatService` не привязан к конкретному классу агента. Он ожидает объект по protocol:

```python
class SessionGraphAgent(Protocol):
    @property
    def warnings(self) -> list[str]: ...

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def get_current_state(self, context: AppContext) -> dict[str, Any]: ...

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]: ...
```

Новый агент, который должен работать через `BioSeqChatService`, должен поддерживать этот минимальный контракт.

## Что делает BioSeqChatService

`BioSeqChatService.submit_turn(request)` выбирает ветку:

1. Если есть `selected_accession`:
   - вызывает `agent.update_current_state(context, {"active_accession": ...})`;
   - достает candidates через `GraphRetrievalService.retrieve_candidates`;
   - возвращает `ChatTurnResult`.

2. Если prompt похож на sequence/filepath:
   - запускает compatibility `BioSeqRetrieverPipeline`;
   - сохраняет pipeline snapshot в agent state через `update_current_state`;
   - если есть candidates, возвращает их без вызова основного agent `invoke`;
   - если controlled miss/error, возвращает controlled response.

3. Иначе:
   - вызывает `agent.invoke(request.message, context)`;
   - берет assistant message из последнего agent message;
   - если `active_accession` не найден, пробует `GraphRetrievalService.resolve_input`;
   - возвращает candidates и `SessionSnapshot`.

## Как app_contracts используются агентами

Агенты напрямую почти не должны зависеть от внешних DTO. Но текущий retriever agent зависит косвенно:

- `GraphRetrievalService.retrieve_candidates(...)` возвращает `list[CandidateView]`;
- retriever agent кладет candidates в `GraphState` как `candidate.model_dump()`;
- `BioSeqChatService` возвращает наружу `CandidateView` в `ChatTurnResult`.

Ключевые контракты:

| Контракт | Где используется |
| --- | --- |
| `ChatTurnRequest` | Вход в `BioSeqChatService.submit_turn`. |
| `ChatTurnResult` | Выход из `BioSeqChatService.submit_turn`. |
| `SessionSnapshot` | UI/API snapshot agent/session state. |
| `BioSeqPipelineSnapshot` | Snapshot compatibility retriever pipeline. |
| `BioSeqInputExtraction` | Structured extraction model для service-level pipeline. |
| `ProteinView` | UI-ready view одной protein record. |
| `CandidateView` | UI-ready candidate: `ProteinView`, scores, rank, evidence. |
| `EvidenceItem`, `DiseaseInfo`, `DomainFeature` | Вложенные UI-ready части protein/candidate view. |

## GraphRetrievalService как мост

`GraphRetrievalService` находится в `app_services`, но используется и сервисами, и `retriever_agent`.

Он делает domain-level запросы к Neo4j через `Neo4jGraphClient`:

| Метод | Кто использует |
| --- | --- |
| `resolve_input` | `BioSeqChatService` fallback для текстового accession/gene/name input. |
| `find_by_sequence_hash` | `retriever_agent.rank_node`, `BioSeqRetrieverPipeline`. |
| `find_encoded_protein_by_sequence_hash` | `retriever_agent.rank_node`, `BioSeqRetrieverPipeline`. |
| `retrieve_candidates` | `retriever_agent.rank/rerank`, `BioSeqChatService`, `BioSeqRetrieverPipeline`. |
| `get_protein_view` | Внутри `retrieve_candidates`. |
| `get_candidate_context` | Доступный helper для краткого контекста соседей. |

Маппинг Neo4j records в API-ready модели происходит в `app_services/protein_view_mapper.py`:

```text
Neo4j record
  -> protein_record_to_view(...)
  -> ProteinView
  -> neighbor_record_to_candidate(...)
  -> CandidateView
```

## service_factory

`app_services/service_factory.py` собирает зависимости:

```text
.env
  -> Neo4j settings
  -> Neo4jGraphClient
  -> GraphRetrievalService
  -> PersistenceResources
  -> Agent
  -> BioSeqChatService
```

Для retriever graph agent:

```python
create_bioseq_retriever_graph_agent(use_llm_extractor=True)
```

создает:

- `Neo4jGraphClient`;
- `GraphRetrievalService`;
- `PersistenceResources`;
- optional LLM extractor factory;
- `BioSeqRetrieverGraphAgent`.

Для chat service:

```python
create_bioseq_chat_service()
```

выбирает:

- `BIOSEQ_BACKEND=mock` -> `MockBioSeqChatService`;
- `BIOSEQ_BACKEND=graph` -> graph-backed service.

В текущем коде graph-backed chat service импортирует `backend.agents_core.session_agent.agent.SessionGraphAgent`, но такого пакета нет в текущем дереве `agents_core`. Поэтому реально документированный рабочий агент сейчас - `retriever_agent`.

## Правила для новых агентов

- Внешние request/response модели держать в `app_contracts`, не в `agents_core`.
- Agent public methods должны принимать `AppContext`, а не `ChatTurnRequest`.
- `app_services` должен адаптировать `ChatTurnRequest` в `AppContext` и agent state в `ChatTurnResult`.
- Внутренний LangGraph state не стоит отдавать напрямую во frontend.
- Если агенту нужен Neo4j domain access, передавать ему service вроде `GraphRetrievalService`, а не строить driver внутри node.
- Если агент возвращает protein/candidate данные для UI, использовать `ProteinView`/`CandidateView` через service layer.
- Для совместимости с `BioSeqChatService` новый агент должен реализовать `warnings`, `invoke`, `get_current_state`, `update_current_state`.

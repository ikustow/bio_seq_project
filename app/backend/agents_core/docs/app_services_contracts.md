# App Services And Contracts

Этот документ фиксирует актуальную связь между `app_contracts`, `app_services` и `agents_core`.

## Слои

```text
UI / API
  -> app_contracts
  -> app_services
  -> agents_core
  -> app/backend/bioseq_retriever + Supabase/Postgres persistence
```

| Слой | Роль |
| --- | --- |
| `app_contracts` | Pydantic DTO для внешнего API/UI: request, response, session snapshot, protein/candidate views. |
| `app_services` | Application orchestration: request -> context -> retriever pipeline -> agent session state -> response. |
| `agents_core` | LangGraph session agents, shared context models and persistence. |
| `bioseq_retriever` | Runtime data retrieval pipeline over local/search-service bioseq artifacts. |

## Основной Flow

`BioSeqChatService.submit_turn(request)` делает:

1. превращает `ChatTurnRequest` в `AppContext`;
2. запускает `BioSeqRetrieverPipeline` для sequence/filepath-like prompt;
3. получает `CandidateView` через mapper `protein_view_mapper.uniprot_record_to_candidate`;
4. сохраняет compact state через `BioSeqRuntimeSessionAgent.update_current_state`;
5. возвращает `ChatTurnResult` с candidates, revealed sections, pipeline snapshot и session snapshot.

Для обычного текстового follow-up без новой sequence сервис вызывает `agent.invoke(...)`, чтобы обновить session message history.

## Контракт Агента

`BioSeqChatService` ожидает agent object с минимальным public API:

```python
class SessionAgent(Protocol):
    @property
    def warnings(self) -> list[str]: ...

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def get_current_state(self, context: AppContext) -> dict[str, Any]: ...

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]: ...
```

Текущая реализация: `app/backend/agents_core/retriever_agent/runtime_agent.py`.

## Ключевые Контракты

| Контракт | Где используется |
| --- | --- |
| `ChatTurnRequest` | Вход в `BioSeqChatService.submit_turn`. |
| `ChatTurnResult` | Выход из `BioSeqChatService.submit_turn`. |
| `SessionSnapshot` | UI/API snapshot состояния сессии. |
| `BioSeqPipelineSnapshot` | Snapshot runtime retriever pipeline. |
| `BioSeqInputExtraction` | Structured extraction model для service-level pipeline. |
| `ProteinView` | UI-ready view одной protein record. |
| `CandidateView` | UI-ready candidate: `ProteinView`, scores, rank, evidence. |

## Factory

`app_services/service_factory.py` поддерживает:

- `BIOSEQ_BACKEND=runtime`, `bioseq` или `bioseq_retriever` - основной режим;
- `BIOSEQ_BACKEND=mock` - scripted UI/dev mode.

Основной режим создает:

- `PersistenceResources`;
- `BioSeqRuntimeSessionAgent`;
- `BioSeqRetrieverPipeline`;
- `BioSeqChatService`.

`ExitStack` хранится на service instance, чтобы Postgres/Supabase checkpointer/store жили столько же, сколько cached service.

# Agent Core Documentation

Этот каталог описывает актуальный backend agent layer после удаления graph database контура.

## Что Сейчас Есть

| Путь | Назначение |
| --- | --- |
| `retriever_agent/runtime_agent.py` | LangGraph session-agent для runtime `bioseq_retriever`: хранит состояние сессии, active accession/sequence и синхронизирует compact snapshot. |
| `shared/config.py` | `.env` loader и лимиты compact session state. |
| `shared/models.py` | `AppContext`, session records, `SessionPatch`, `PersistenceResources`. |
| `shared/services/persistence.py` | LangGraph checkpointer/store и `chat_sessions` repository. |
| `shared/services/session_state.py` | Общие helpers для сообщений и compact session patches. |

Поиск похожих белков теперь выполняется через `app/backend/bioseq_retriever` и сервисный слой `app/backend/app_services/retriever_pipeline.py`.

## Runtime Flow

```text
ChatTurnRequest
  -> BioSeqChatService
  -> BioSeqRetrieverPipeline
  -> app/backend/bioseq_retriever/src/pipeline.py
  -> CandidateView / ProteinView
  -> BioSeqRuntimeSessionAgent.update_current_state(...)
  -> SessionSnapshot / ChatTurnResult
```

`context.session_id` остается единым ключом для:

- внешней chat session;
- LangGraph `thread_id`;
- строки `public.chat_sessions`.

## Документы

- [app_services_contracts.md](app_services_contracts.md) - как `app_services`, `app_contracts` и runtime agent связаны между собой.
- [adding_agents_supabase.md](adding_agents_supabase.md) - как подключать `AppContext`, LangGraph memory и Supabase/Postgres session storage к новым агентам.

# Frontend TODO

Актуальный backend runtime идет через `app/backend/app_services` и `app/backend/bioseq_retriever`.

## Ближайшее

- Оставить `embeddings_pipeline.py` только как rollback path или удалить после полного frontend regression smoke.
- Синхронизировать candidate selection так, чтобы `selected_accession` не требовал повторной загрузки candidates.
- Заменить stub `chat_llm_pipeline.py` на полноценный follow-up chat agent.
- Добавить frontend smoke на один sequence turn: candidates отображаются, protein card sections раскрываются, session id сохраняется.

## Chat LLM migration gaps

- Перенести ownership persistence из frontend в backend: сейчас `chat_pipeline.py` вызывает `session_db_adapter.save_turn(...)`, а целевое состояние — сохранение follow-up turn внутри backend service/helper без double-write.
- Сделать backend-first routing по session row: сейчас backend получает `ui_context.turn_count` от frontend, а должен сам читать `public.chat_sessions.working_memory.turn_count` и решать retriever vs Chat LLM.
- Убрать legacy `frontend/chat_llm_pipeline.py` после regression smoke: активный `app.py` уже ходит через `chat_pipeline.run_turn()`, но wrapper всё ещё лежит как совместимый хвост миграции.
- Закрыть debug secret leak: `debug_request.headers` сейчас может содержать `X-BioSeq-Token`, который попадает в Streamlit debug panel/curl reproducer. Нужно маскировать/не передавать secret headers в UI state.
- Обновить `app/README.md`, `app/README_en.md`, `app/ARCHITECTURE.md`, `app/ARCHITECTURE_en.md`: сейчас часть docs всё ещё описывает follow-up flow через `frontend/chat_llm_pipeline.py`.

# Frontend TODO

Актуальный backend runtime идет через `app/backend/app_services` и `app/backend/bioseq_retriever`.

## Ближайшее

- Оставить `embeddings_pipeline.py` только как rollback path или удалить после полного frontend regression smoke.
- Синхронизировать candidate selection так, чтобы `selected_accession` не требовал повторной загрузки candidates.
- Заменить stub `chat_llm_pipeline.py` на полноценный follow-up chat agent.
- Добавить frontend smoke на один sequence turn: candidates отображаются, protein card sections раскрываются, session id сохраняется.

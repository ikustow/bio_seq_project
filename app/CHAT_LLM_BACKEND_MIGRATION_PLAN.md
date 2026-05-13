# План миграции Chat LLM из frontend в backend

Дата: 2026-05-13.

## Цель

Перенести follow-up Chat LLM контур из `app/frontend/chat_llm_pipeline.py` в backend-слой `app/backend`, не меняя пользовательское поведение приложения:

- первый turn с последовательностью по-прежнему запускает `bioseq_retriever`;
- последующие вопросы по уже найденному белку отвечают через Chat LLM;
- protein card справа не перерисовывается и не очищается на follow-up turn;
- Supabase/Postgres session history, sidebar restore и `turn_count` продолжают работать;
- Gemini proxy и OpenAI остаются optional providers через текущие env-переменные.

Это не миграция на отдельный HTTP backend. В текущей monorepo-архитектуре backend service layer может оставаться in-process внутри Streamlit runtime.

## Текущее состояние

Сейчас routing разделен так:

```text
app/frontend/chat_pipeline.py
  -> первый turn:
       backend/app_services/BioSeqChatService
       -> bioseq_retriever
       -> session_db_adapter.save_turn(update_candidates=True)

  -> follow-up turn:
       app/frontend/chat_llm_pipeline.py
       -> Gemini proxy или OpenAI
       -> session_db_adapter.save_turn(update_candidates=False)
```

Критичные frontend-зависимости внутри `chat_llm_pipeline.py`:

- читает `st.session_state.messages`;
- читает `st.session_state.candidates`;
- читает `st.session_state.selected_candidate_idx`;
- сам строит protein context;
- сам выбирает provider;
- сам сохраняет turn в `public.chat_sessions` через `session_db_adapter`;
- возвращает `update_card=False`, чтобы UI не заменял карточку.

## Целевая схема

Минимально безопасная целевая схема:

```text
app/frontend/chat_pipeline.py
  -> для любого turn вызывает backend/app_services/BioSeqChatService

backend/app_services/BioSeqChatService
  -> если это первый bioseq turn:
       BioSeqRetrieverPipeline
       -> CandidateView / ProteinView
       -> session state update
       -> ChatTurnResult(update_card=True)

  -> если это follow-up turn:
       backend/app_services/ChatLLMService
       -> provider adapter: Gemini proxy или OpenAI
       -> session state update without candidate overwrite
       -> ChatTurnResult(update_card=False)
```

На первом этапе можно оставить frontend `chat_llm_pipeline.py` как thin compatibility wrapper, но он не должен содержать provider calls, prompt building и LLM keys. Его задача только передать запрос в backend и сохранить старый dict-shape для UI, если это нужно для постепенной миграции.

## Что нужно перенести

1. Provider selection:
   - `BIOSEQ_CHAT_LLM_PROVIDER=auto|gemini_proxy|openai`;
   - `BIOSEQ_LLM_PROXY_URL`;
   - `BIOSEQ_LLM_PROXY_TOKEN`;
   - `OPENAI_API_KEY`;
   - `BIOSEQ_OPENAI_CHAT_MODEL`;
   - fallback на `OPENAI_MODEL`.

2. Provider adapters:
   - Gemini proxy HTTP call;
   - OpenAI call;
   - единый timeout;
   - нормализация ошибок в warnings/result metadata.

3. Prompt/context builder:
   - system prompt;
   - recent message history;
   - current selected protein context;
   - защита от повторного добавления текущего prompt.

4. Follow-up turn handler:
   - получить текущую сессию;
   - определить selected candidate;
   - построить LLM context;
   - вызвать provider;
   - вернуть `assistant_message`;
   - сохранить messages/turn_count;
   - не перезаписывать candidates/protein card state.

## Что нужно добавить в backend

Предлагаемая структура:

```text
app/backend/app_services/chat_llm.py
app/backend/app_services/chat_context.py
app/backend/app_services/chat_providers.py
```

Или компактнее на первом шаге:

```text
app/backend/app_services/chat_llm.py
```

Рекомендуемые сущности:

- `ChatLLMService`: orchestration follow-up turn.
- `ChatLLMProvider` protocol: единый интерфейс provider adapter.
- `GeminiProxyChatProvider`.
- `OpenAIChatProvider`.
- `build_chat_context(...)`: собирает selected protein + history.

`chat_llm.py` не должен импортировать Streamlit и не должен читать `st.session_state`.

## Контракт с frontend

Сейчас frontend ожидает dict:

```python
{
    "reply": str,
    "candidates": list,
    "candidates_raw": list,
    "reveals": set,
    "warnings": list[str],
    "result": dict,
    "persisted": bool,
    "backend": str,
    "update_card": bool,
}
```

Backend-контракт `ChatTurnResult` сейчас не содержит `update_card`. Его лучше расширить backward-compatible полями с default:

```python
current_mode: str | None = None
update_card: bool = True
provider: str | None = None
provider_model: str | None = None
metadata: dict[str, Any] = Field(default_factory=dict)
```

Важно: старые вызовы retriever не должны менять поведение, потому что default `update_card=True`.

Frontend должен передавать в `ChatTurnRequest`:

- `selected_candidate_index`;
- `selected_accession`, если выбранный accession известен;
- минимальный `ui_context` только как fallback, если persistence недоступен.

## Session и persistence

Самое хрупкое место - сохранение `public.chat_sessions`.

Сейчас для follow-up используется:

```python
session_db_adapter.save_turn(..., update_candidates=False)
```

При переносе в backend нужно сохранить ту же семантику:

- `working_memory.messages` дополняется новым user/assistant turn;
- `working_memory.turn_count` увеличивается;
- `working_memory.last_candidates` сохраняется без изменений;
- `working_memory.last_revealed_sections` сохраняется без изменений;
- `active_accession` сохраняется без изменений;
- `working_set_ids` сохраняется без изменений;
- `proteins` и `sequences` не затираются пустыми списками;
- `current_mode` становится `chat_llm` / `chat_llm_error`, но не ломает restore.

Лучше вынести merge/update логику из `frontend/session_db_adapter.py` в backend service/helper, а frontend оставить только reader/adapter для UI restore. Но это второй шаг, не обязательный для первого безопасного переноса LLM calls.

## Routing первого и follow-up turn

Нельзя оставить routing только во frontend навсегда, иначе backend не будет владеть chat logic.

Целевой routing:

- backend читает session row;
- если `working_memory.turn_count` отсутствует или равен `0`, идет retriever;
- если `turn_count > 0`, идет Chat LLM;
- если пользователь прислал новую явную sequence/filepath, backend может принудительно отправить turn в retriever даже внутри существующей сессии;
- если persistence выключен, backend должен безопасно деградировать:
  - либо использовать `ui_context` из request;
  - либо вести себя как сейчас и считать turn первым.

Важно не сломать текущий сценарий без `SUPABASE_DB_URL`: приложение должно стартовать и отвечать понятной ошибкой/предупреждением, а не падать.

## Что нельзя сломать

- Первый sequence turn должен продолжать возвращать candidates и protein card.
- Follow-up вопрос не должен очищать правую карточку.
- Follow-up вопрос не должен запускать новый bioseq search без явной новой последовательности.
- `selected_candidate_idx` должен сохраняться между turns.
- Sidebar restore должен восстанавливать messages и last candidates.
- `working_memory.turn_count` должен расти ровно на один за пользовательский turn.
- `last_candidates` не должен заменяться на `[]` при Chat LLM ответе.
- `active_accession` не должен сбрасываться.
- Mock mode `BIOSEQ_BACKEND=mock` должен остаться рабочим.
- Search service и `bioseq_retriever` не должны зависеть от Chat LLM provider.
- Neo4j/graph контур не должен возвращаться в runtime.
- LLM keys не должны протекать в UI state, persisted session row или rendered debug output.

## План работ

### 1. Зафиксировать текущий контракт

- Описать текущий dict-shape из `frontend/chat_pipeline.run_turn()`.
- Добавить/обновить contract tests на:
  - retriever turn: `update_card=True`;
  - follow-up turn: `update_card=False`;
  - preservation of candidates/reveals.

### 2. Расширить backend contracts

- Добавить optional поля в `ChatTurnResult`.
- При необходимости расширить `ChatTurnRequest.ui_context` соглашением:
  - `messages`;
  - `candidates`;
  - `selected_candidate_index`;
  - `revealed_sections`.

### 3. Вынести provider adapters в backend

- Создать backend Chat LLM service.
- Перенести Gemini proxy вызов.
- Перенести OpenAI вызов.
- Оставить current env names без переименования.
- Убрать Streamlit-зависимости из LLM provider code.

### 4. Вынести context builder

- Перенести `_system_prompt()`.
- Перенести protein context builder.
- Источник context сделать backend-first:
  - session row `working_memory.last_candidates`;
  - `selected_candidate_index` из request;
  - `ui_context` только fallback.

### 5. Подключить follow-up routing в backend

- В `BioSeqChatService.submit_turn()` добавить ветку follow-up.
- Не запускать retriever на обычный вопрос после первого turn.
- Вернуть `ChatTurnResult(update_card=False, current_mode="chat_llm")`.

### 6. Упростить frontend

- `frontend/chat_pipeline.py` должен вызывать backend service для всех turns.
- `frontend/chat_llm_pipeline.py` временно оставить wrapper-ом или удалить после проверки.
- Frontend должен только:
  - передавать selected candidate metadata;
  - адаптировать `ChatTurnResult` в старый UI dict;
  - не знать provider keys.

### 7. Перенести save follow-up turn в backend

Безопасный вариант: отдельным шагом после переноса provider calls.

- Вынести merge-save семантику `update_candidates=False` в backend.
- Убедиться, что frontend не делает double-write.
- Оставить frontend restore/read path, пока UI зависит от Streamlit state.

### 8. Обновить docs

- `app/README.md`: заменить описание `frontend/chat_llm_pipeline.py` на backend Chat LLM service.
- `app/ARCHITECTURE.md`: обновить follow-up flow.
- Env table оставить с теми же именами.

### 9. Проверка

Минимальный smoke:

1. Запустить search service.
2. Запустить Streamlit.
3. Первый turn с protein sequence:
   - появляется protein card;
   - `turn_count=1`;
   - есть `last_candidates`.
4. Follow-up вопрос:
   - отвечает Chat LLM;
   - card не очищается;
   - `turn_count=2`;
   - `last_candidates` прежний;
   - `active_accession` прежний.
5. Reload page:
   - sidebar/session restore возвращает chat history и card.
6. New chat/reset:
   - создается новая session;
   - первый turn снова идет в retriever.

Отдельный real-provider smoke:

- `BIOSEQ_CHAT_LLM_PROVIDER=gemini_proxy`;
- `BIOSEQ_CHAT_LLM_PROVIDER=openai`.

Unit-level проверки можно делать без внешнего LLM на fake provider adapter, но финальный e2e smoke должен проходить с реальным provider key/proxy.

## Рекомендуемый порядок миграции

1. Backend Chat LLM service без изменения frontend behavior.
2. Thin wrapper во frontend, который вызывает backend service вместо прямого provider call.
3. Backend routing для first/follow-up turns.
4. Backend ownership of follow-up persistence.
5. Удаление legacy `frontend/chat_llm_pipeline.py`.
6. Обновление README/ARCHITECTURE.

Такой порядок снижает риск: сначала переносим LLM keys/provider calls, потом routing, потом persistence ownership.

## Критерий готовности

Миграция считается завершенной, когда:

- в `app/frontend` нет прямых Gemini/OpenAI вызовов;
- frontend не читает LLM provider env variables;
- все turns проходят через backend service contract;
- follow-up Chat LLM сохраняет session history без перезаписи candidates;
- Streamlit UI визуально ведет себя так же, как до миграции;
- локальный run с реальным provider проходит первый turn + follow-up + reload restore.

# Session Agent

`session_agent` это stateful-агент для работы с графом белков в Neo4j. Он построен на `LangChain create_agent`, использует `LangGraph` для состояния диалога и умеет сохранять как краткоживущую историю сессии, так и долговременные пользовательские данные.

Схема взаимодействия: [схема.puml](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/docs/схема.puml)

## Что делает агент

- Принимает сообщение пользователя и `AppContext` с `user_id` и `session_id`.
- Поднимает состояние сессии по `thread_id = session_id`.
- При необходимости догружает сохранённый session snapshot из `chat_sessions`.
- Даёт модели доступ к domain tools для Neo4j, user memory и просмотру текущего session context.
- После ответа модели пересчитывает session patch: summary, найденные белки, последовательности, working memory и active ids.
- Сохраняет обновлённое состояние обратно в persistence слой.

## Поток выполнения

1. [main.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/main.py) читает аргументы CLI, `.env` и создаёт `AppContext`.
2. Там же создаются `Neo4jGraphClient` и `PersistenceResources`.
3. [agent.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/agent.py) собирает `SessionGraphAgent` через `create_agent(...)`.
4. `SessionGraphAgent.invoke()` формирует входной payload и вызывает LangChain-агента.
5. После ответа вызывается `derive_session_patch(...)`, который извлекает структурированные данные из истории сообщений.
6. Итоговый state сохраняется через `session_repository`, а checkpoint/state store обслуживаются `LangGraph`.

## Структура файлов

- [agent.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/agent.py) — основной runtime-класс `SessionGraphAgent`.
- [main.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/main.py) — CLI entrypoint.
- [config.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/config.py) — константы, prompt, загрузка `.env`.
- [models.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/models.py) — `pydantic`-модели контекста, session state и persistence payload.
- [services/graph.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/services/graph.py) — Neo4j клиент и защита для read-only Cypher.
- [services/persistence.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/services/persistence.py) — настройка persistence и работа с `chat_sessions`.
- [services/session_state.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/services/session_state.py) — извлечение белков, последовательностей и сборка session patch.
- [tools/](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/tools) — все tools, которые видит LLM.
- [docs/схема.puml](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/docs/схема.puml) — UML последовательности вызова.

## Ключевые классы и функции

### `agent.py`

- `SessionGraphAgent.__init__(...)`
  Создаёт `ChatOpenAI`, регистрирует tools, `state_schema`, `context_schema`, `checkpointer` и `store`.

- `SessionGraphAgent.invoke(message, context)`
  Главная точка входа. Вызывает агента, затем нормализует и сохраняет session state.

- `SessionGraphAgent.get_current_state(context)`
  Возвращает текущее состояние LangGraph по `thread_id`.

- `SessionGraphAgent.get_message_history(context)`
  Возвращает сериализованную историю сообщений в простом формате `{role, content}`.

- `SessionGraphAgent._build_input_payload(...)`
  Если активный thread пустой, пытается восстановить session-поля из `session_repository`.

### `config.py`

- `load_env_file(env_path)`
  Подгружает переменные окружения из `.env`, не перезаписывая уже выставленные значения.

- `SESSION_STATE_KEYS`
  Канонический список session-полей, которые мы показываем и восстанавливаем.

- `SYSTEM_PROMPT`
  Правила работы агента: сначала domain tools, осторожность с биологическими выводами, поддержка user memory.

### `models.py`

- `AppContext`
  Контекст вызова: `user_id`, `session_id`, `workspace_id`, `user_role`.

- `ProteinRecord` и `SequenceRecord`
  Нормализованные сущности, которые извлекаются из tool output и текстов сообщений.

- `SessionPatch`
  Модель частичного обновления session state после ответа агента.

- `SessionRow`
  Модель строки для сохранения в `chat_sessions`.

- `SessionStateView`
  Упрощённый view над state, который используется при постобработке сообщений.

- `PersistenceResources`
  Контейнер для `checkpointer`, `store`, `session_repository`, режима persistence и предупреждений.

### `services/graph.py`

- `resolve_driver_uri(uri, insecure)`
  Переводит secure URI в `+ssc` вариант для окружений с self-signed TLS.

- `ensure_read_only_cypher(query)`
  Валидирует, что произвольный Cypher остаётся read-only.

- `Neo4jGraphClient.execute(...)`
  Выполняет запрос к Neo4j и при `ServiceUnavailable` пробует fallback URI.

### `services/persistence.py`

- `NullSessionRepository`
  Заглушка, если Postgres persistence недоступен.

- `PostgresSessionRepository.get_session(session_id)`
  Читает session snapshot из `public.chat_sessions`.

- `PostgresSessionRepository.upsert_session(context, state)`
  Сохраняет session snapshot в `public.chat_sessions`.

- `build_session_row(context, state)`
  Превращает текущий `state` в `SessionRow`.

- `create_persistence_resources(db_url, exit_stack)`
  Выбирает между Postgres-backed persistence и in-memory fallback.

### `services/session_state.py`

- `get_message_text(...)`
  Приводит сообщения LangChain к строке.

- `serialize_message(...)`
  Делает компактное представление истории для отладки и CLI.

- `extract_proteins(messages, existing)`
  Ищет в сообщениях белковые записи и соседей, возвращённых tool-ами.

- `extract_sequences(messages, existing)`
  Извлекает аминокислотные последовательности по regex.

- `derive_session_patch(state)`
  Считает итоговый patch: summary, proteins, sequences, working memory, active ids и `last_tool_results_summary`.

### `tools`

- [tools/graph.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/tools/graph.py)
  Domain tools для графа:
  `graph_schema_guide`, `find_proteins`, `get_protein_neighbors`, `get_neighbor_diseases`, `summarize_neighbor_disease_context`, `run_read_cypher`.

- [tools/memory.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/tools/memory.py)
  Durable memory tools:
  `save_user_profile`, `get_user_profile`, `save_user_preference`, `save_user_fact`, `save_investigation_default`, `get_investigation_defaults`.

- [tools/session.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/tools/session.py)
  Tool `get_session_context`, который даёт модели текущий контекст пользователя и краткий session state.

- [tools/base.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/tools/base.py)
  Маленькие helper-функции для JSON-ответов и записи в `store`.

- [tools/__init__.py](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/tools/__init__.py)
  `build_tools(client)` собирает финальный список tools для агента.

## Как хранится состояние

Есть два уровня памяти:

- `checkpointer` и `store` из LangGraph
  Нужны самому агенту во время выполнения. Могут быть PostgreSQL-backed или in-memory.

- `session_repository`
  Явно сохраняет компактный session snapshot в таблицу `public.chat_sessions`, чтобы можно было восстановить важные поля даже отдельно от внутреннего checkpoint-механизма.

Если `SUPABASE_DB_URL` не настроен или Postgres persistence не поднялся, агент продолжит работать в `memory` режиме.

## Что именно попадает в session state

Важные поля перечислены в `SESSION_STATE_KEYS`:

- `session_summary`
- `proteins`
- `sequences`
- `working_memory`
- `active_sequence_id`
- `active_accession`
- `last_analysis_summary`
- `working_set_ids`
- `current_mode`
- `last_tool_results_summary`

## Как запускать

Пример CLI-вызова:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "Find neighbors for TP53" \
  --show-session-state
```

Для работы нужны:

- `OPENAI_API_KEY`
- `NEO4J_URI`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`
- `SUPABASE_DB_URL`

## Ограничения и замечания

- `run_read_cypher` разрешает только read-only запросы.
- Извлечение последовательностей основано на regex и подходит только для достаточно длинных amino-acid строк.
- Session patch строится постфактум по истории сообщений, поэтому качество структуры зависит от того, насколько стабильно tools возвращают JSON.
- UML-схема в [схема.puml](/Users/ilia_kustov/Documents/dev/bio_seq_project/backend/agents_core/session_agent/docs/схема.puml) вручную синхронизирована с текущим кодом. Автоматически через `plantuml` в этом окружении она не проверялась, потому что бинарь не установлен.

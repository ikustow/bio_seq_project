# Session Agent Scripts

Этот файл нужен как шпаргалка для человека или другой LLM: что именно запускать для `session_agent`, в каких случаях, и какие команды можно просто вставить в терминал без дополнительной подготовки.

Все примеры ниже запускаются из корня проекта:

```bash
cd /Users/ilia_kustov/Documents/dev/bio_seq_project
```

Основная команда:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main
```

## Быстрые правила

- Если нужен один вопрос агенту, используй `--message`.
- Если нужно посмотреть session state после ответа, добавь `--show-session-state`.
- Если нужно поднять уже существующую сессию и посмотреть её историю, используй `--dump-history`.
- Если нужна новая чистая сессия, всегда передавай новый `--session-id`.
- Если ничего не передавать в `--message`, агент запустится в интерактивном режиме.

## Готовые команды

### 1. Проверить, что агент вообще запускается

Что делает:
Запускает новую сессию и просит агента коротко описать доступные возможности.

Команда:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "What tools do you have available for this protein graph? Please answer in one short paragraph." \
  --session-id smoke_test_$(date +%s) \
  --show-session-state
```

Когда использовать:
- smoke test после изменений
- проверка ключей, сети и persistence

Что сказать LLM:
`Запусти smoke test session_agent на новой сессии и покажи ответ вместе с session state.`

### 2. Запустить агент на новой чистой сессии

Что делает:
Создаёт новый `session_id`, отправляет один вопрос и сохраняет результат как новую сессию.

Команда:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "Find proteins related to TP53 and summarize what you found." \
  --session-id session_$(date +%s) \
  --show-session-state
```

Когда использовать:
- если не хочется смешивать новый запрос со старой историей
- если нужен воспроизводимый тест отдельного сценария

Что сказать LLM:
`Запусти session_agent в новой сессии с этим сообщением и покажи итоговый session state.`

### 3. Проверить сценарий по конкретному accession

Что делает:
Проверяет биологический сценарий с disease summary по соседям белка.

Команда:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "For accession A2ACJ2, summarize common diseases across its neighbors and give a short interpretation. Ответь на русском" \
  --session-id a2acj2_case_$(date +%s) \
  --show-session-state
```

Когда использовать:
- для ручной проверки качества ответа агента
- для регрессионной проверки после правок prompt или tools

Что сказать LLM:
`Прогони сценарий для accession A2ACJ2 на новой сессии и оцени, насколько ответ соответствует данным tool output.`

### 4. Открыть интерактивный режим

Что делает:
Запускает агент как REPL. Можно последовательно задавать вопросы в рамках одной сессии.

Команда:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --session-id interactive_$(date +%s) \
  --show-session-state
```

Когда использовать:
- для ручного исследования графа
- для проверки, как агент накапливает контекст в одной сессии

Что сказать LLM:
`Запусти session_agent в интерактивном режиме на новой сессии.`

### 5. Посмотреть историю уже существующей сессии

Что делает:
Выводит историю сообщений для конкретного `session_id`.

Команда:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --dump-history \
  --session-id test_session_a2acj2_1777312279
```

Когда использовать:
- чтобы понять, какие tools реально вызвались
- чтобы проверить, как выглядит восстановленная история
- чтобы дебажить плохой ответ агента

Что сказать LLM:
`Подними историю этой сессии и проанализируй, какими tool-ами агент пользовался.`

### 6. Проверить, что session state восстанавливается между запусками

Что делает:
Сначала создаёт сессию, затем вторым запуском продолжает её с тем же `session_id`.

Шаг 1:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "Find protein A2ACJ2 and remember it as the active target." \
  --session-id restore_test_manual \
  --show-session-state
```

Шаг 2:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "What protein are we currently focused on?" \
  --session-id restore_test_manual \
  --show-session-state
```

Когда использовать:
- для проверки persistence
- после изменений в `session_repository` или `derive_session_patch`

Что сказать LLM:
`Проверь, восстанавливается ли session state между двумя отдельными запусками с одинаковым session_id.`

### 7. Проверить только history без нового сообщения

Что делает:
Не вызывает модель, а просто читает уже накопленную историю.

Команда:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --dump-history \
  --session-id restore_test_manual
```

Когда использовать:
- если нужен только audit trail
- если не хочется тратить вызов модели

Что сказать LLM:
`Прочитай историю этой сессии без нового вызова модели.`

### 8. Запуск с явным указанием пользователя и workspace

Что делает:
Проверяет, как агент работает с заданным `user_id`, `workspace_id` и `user_role`.

Команда:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "Save that I prefer concise answers in Russian." \
  --session-id user_context_test_$(date +%s) \
  --user-id demo-user \
  --workspace-id demo-workspace \
  --user-role ADMIN \
  --show-session-state
```

Когда использовать:
- для проверки user memory
- для сценариев, где важен контекст пользователя

Что сказать LLM:
`Запусти agent с явным user_id и workspace_id и проверь, что контекст пользователя доходит до tools.`

### 9. Запуск с альтернативной моделью

Что делает:
Позволяет проверить поведение агента на другой модели без изменения `.env`.

Команда:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --model gpt-4.1-mini \
  --message "Find neighbors for BRCA1 and summarize the result briefly." \
  --session-id alt_model_$(date +%s)
```

Когда использовать:
- для сравнения качества и стоимости
- для smoke test на другой модели

Что сказать LLM:
`Прогони тот же сценарий на другой модели и сравни ответ с базовой.`

### 10. Запуск в режиме без Postgres persistence

Что делает:
Форсирует in-memory режим, если временно отключить `SUPABASE_DB_URL`.

Команда:

```bash
SUPABASE_DB_URL= ./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "What persistence mode are you using?" \
  --session-id memory_mode_$(date +%s) \
  --show-session-state
```

Когда использовать:
- для проверки fallback логики
- если нужно исключить влияние Supabase

Что сказать LLM:
`Запусти session_agent без SUPABASE_DB_URL и проверь, что он переключился в memory mode.`

### 11. Проверить read-only исследование графа

Что делает:
Даёт агенту задачу, где он с высокой вероятностью использует graph tools и не должен выходить за read-only рамки.

Команда:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "Find proteins matching Hps5 and list their closest neighbors." \
  --session-id graph_read_test_$(date +%s) \
  --show-session-state
```

Когда использовать:
- для проверки `find_proteins` и `get_protein_neighbors`
- для проверки, что агент не пытается писать в граф

Что сказать LLM:
`Прогони безопасный read-only сценарий по protein graph и покажи, какие данные агент вернул.`

### 12. Проверить сохранённые предпочтения пользователя

Что делает:
Создаёт сессию, в которой пользователь сообщает предпочтение, потом на новой сессии того же пользователя проверяет, помнит ли агент это.

Шаг 1:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "Please remember that I prefer answers in Russian and in a concise style." \
  --session-id pref_save_1 \
  --user-id pref-user
```

Шаг 2:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "What do you remember about my answer preferences?" \
  --session-id pref_save_2 \
  --user-id pref-user \
  --show-session-state
```

Когда использовать:
- для проверки durable user memory
- после изменений в `tools/memory.py`

Что сказать LLM:
`Проверь, переносится ли user memory между разными сессиями одного и того же user_id.`

## Полезные шаблоны

### Новый уникальный session id

```bash
--session-id test_$(date +%s)
```

### Запуск с показом state

```bash
--show-session-state
```

### Чтение history вместо нового запроса

```bash
--dump-history --session-id <existing_session_id>
```

### Переопределение подключения к Neo4j

```bash
--uri <neo4j_uri> --database <db_name> --user <neo4j_user> --password <neo4j_password>
```

## Как формулировать задачу для LLM

Хорошие формулировки:

- `Запусти session_agent на новой сессии и покажи ответ вместе с session state.`
- `Подними историю этой сессии и проанализируй tool calls.`
- `Проверь сценарий для accession A2ACJ2 и оцени корректность ответа по данным tool output.`
- `Сравни поведение агента в postgres mode и memory mode.`
- `Проверь, восстанавливается ли состояние между двумя отдельными запусками с одинаковым session_id.`

Плохие формулировки:

- `проверь агента`
- `запусти что-нибудь`
- `посмотри историю`

Плохие формулировки не говорят:
- нужна ли новая или старая сессия
- нужен ли `session state`
- нужно ли просто выполнить сценарий или ещё и оценить качество ответа

## Минимальный набор для копипаста

Новая сессия:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --message "<your_message>" \
  --session-id test_$(date +%s) \
  --show-session-state
```

История существующей сессии:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --dump-history \
  --session-id <existing_session_id>
```

Интерактивный режим:

```bash
./.venv/bin/python -m backend.agents_core.session_agent.main \
  --session-id interactive_$(date +%s) \
  --show-session-state
```

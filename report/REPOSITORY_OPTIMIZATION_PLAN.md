# Отчет по оптимизации структуры репозитория

Дата: 2026-05-13

## Цель

Привести репозиторий к более чистой backend-ориентированной структуре, максимально сохранив текущее поведение приложения без Neo4j. Это не радикальная смена архитектуры, а аккуратная уборка проекта: перенос модулей, исправление импортов, централизация тестов и удаление graph database контура.

Текущее поведение, которое нужно сохранить:

- первый turn работает через embeddings / `bioseq_retriever`;
- follow-up вопросы работают через текущий chat LLM / Gemini proxy контур;
- session history продолжает использовать `SUPABASE_DB_URL`, а без него приложение работает в non-persistent режиме;
- локальные bioseq-артефакты HDF5/FAISS/index/cache остаются основой retrieval без Neo4j;
- Streamlit UI и формат карточек кандидатов не меняются.

Структурные цели:

- сделать `bioseq_retriever` ключевым агентом для работы с биологическими данными;
- перенести `bioseq_retriever` в `app/backend`;
- логично разместить chat-agent внутри `app/backend`;
- убрать Neo4j-контур из проекта;
- перенести тестирование в единую папку `tests`;
- определить назначение `scripts` и оставить там только настоящие служебные скрипты.

## Текущее состояние

Сейчас в проекте одновременно живут несколько контуров:

- корневой `bioseq_retriever/` - legacy/основной retriever pipeline для последовательностей, FAISS/HDF5, UniProt и rerank;
- `app/backend/agents_core/retriever_agent/` - graph-first агент, завязанный на Neo4j;
- `app/backend/graph_core/` - offline-пайплайн подготовки и импорта данных в Neo4j;
- `app/frontend/chat_llm_pipeline.py` - follow-up chat logic, которая сейчас живет во frontend;
- `scripts/` - фактически набор unit/smoke checks, а не production/dev scripts;
- `tests/eval/` - evaluation harness, уже лежит в правильном верхнеуровневом тестовом контуре.

Главная проблема: архитектура сейчас смешивает старый embeddings retriever, Neo4j graph runtime и frontend-level chat orchestration. Для дальнейшей поддержки лучше оставить один понятный backend-контур, но переносить его нужно консервативно: сначала сохранить текущую рабочую схему, затем убрать лишнее.

## Целевая структура

Рекомендуемая структура:

```text
app/
  backend/
    bioseq_retriever/      # главный агент/сервис работы с bioseq-данными
    chat_agent/            # follow-up/chat агент
    app_contracts/         # pydantic-контракты
    app_services/          # фабрики и orchestration между агентами
    shared/ или persistence/
  frontend/
tests/
  unit/
  integration/
  smoke/
  eval/
scripts/                  # только служебные dev/ops-команды, не тесты
  data_prep/              # offline-подготовка bioseq-артефактов
```

## Выводы по компонентам

### `bioseq_retriever`

`bioseq_retriever` должен стать основным backend data-agent. Его нужно перенести из корня репозитория в:

```text
app/backend/bioseq_retriever/
```

После переноса нужно убрать `sys.path`-хаки и заменить импорты вида `from src...` на нормальные пакетные импорты. Также нужно обновить пути к данным, README, evaluation harness и frontend adapter.

### Neo4j / graph agent

Neo4j-контур нужно считать deprecated:

- `app/backend/graph_core/`;
- `app/backend/app_services/graph_retrieval.py`;
- `app/backend/agents_core/shared/services/graph.py`;
- `app/backend/agents_core/retriever_agent/`;
- Neo4j env-переменные и документация.

На первом этапе лучше пометить этот контур как deprecated и отключить от runtime. После переноса `bioseq_retriever` в backend можно удалить Neo4j-код, инструкции импорта и связанные переменные окружения.

### Chat-agent

Follow-up chat сейчас находится во frontend в `app/frontend/chat_llm_pipeline.py`. Логичнее вынести его в:

```text
app/backend/chat_agent/
```

Frontend должен постепенно стать тонким UI-слоем. На переходном этапе допустимы compatibility wrappers, если это помогает сохранить текущее поведение без большого переписывания.

`BioSeqChatService` уже близок к нужной роли, но текущий graph-mode ссылается на отсутствующий `SessionGraphAgent`. Его нужно перепривязать к новому backend chat-agent и/или к `bioseq_retriever` как data-agent, не меняя смысл first-turn/follow-up routing.

### Tests

Тесты нужно централизовать в `tests/`:

- `bioseq_retriever/tests/` -> `tests/unit/bioseq_retriever/`;
- `scripts/test_*` -> `tests/unit/...`;
- `scripts/smoke_*` -> `tests/smoke/...`;
- `tests/eval/` оставить как evaluation контур.

Это упростит запуск тестов и уберет двусмысленность, где искать проверки.

### Scripts

Текущая корневая папка `scripts/` содержит не служебные скрипты, а тестовые сценарии. Их стоит перенести в `tests`.

В `scripts/` после чистки должны остаться только команды для обслуживания проекта: подготовка артефактов, миграции, локальные dev-утилиты, one-shot maintenance tasks. Скрипты, связанные с Neo4j import/export, после удаления Neo4j-контура не нужны.

### `data_prep`

`data_prep/` похож на offline-подготовку DNA/HyenaDNA артефактов. Так как это не runtime-код агента, а служебный процесс генерации данных, его логично перенести в:

```text
scripts/data_prep/
```

Если часть этой логики станет переиспользуемой библиотечной функциональностью для `bioseq_retriever`, ее стоит вынести в `app/backend/bioseq_retriever/`, а в `scripts/data_prep/` оставить только CLI/запускающие обертки.

## Примерный план действий

1. Зафиксировать целевую архитектуру в документации: `bioseq_retriever` - главный data-agent, `chat_agent` - отдельный follow-up агент, Neo4j - deprecated.

2. Перенести `bioseq_retriever/` в `app/backend/bioseq_retriever/`.

3. Починить импорты, пути к данным, CLI, README и evaluation scripts после переноса.

4. Переключить frontend на backend service/factory вместо прямого вызова frontend-level `embeddings_pipeline`.

5. Вынести chat logic из `app/frontend/chat_llm_pipeline.py` в `app/backend/chat_agent/`.

6. Пересобрать `BioSeqChatService` вокруг нового backend-контракта: first turn идет в `bioseq_retriever`, follow-up turns идут в `chat_agent`.

7. Пометить Neo4j-контур deprecated, убрать его из runtime factory и env-документации.

8. После стабилизации нового data-agent удалить Neo4j-код, `graph_core`, Neo4j scripts и связанные документы.

9. Перенести все тесты в `tests/`, разделив их на `unit`, `integration`, `smoke`, `eval`.

10. Перенести `data_prep/` в `scripts/data_prep/` как offline tooling для подготовки bioseq-артефактов.

11. Почистить `scripts/`, оставив только настоящие служебные команды.

12. Обновить root README, `app/README.md`, `.env.example` / `example.env.txt` и deployment notes под новую структуру.

## Риски и замечания

- В текущем frontend есть прямые зависимости от корневого `bioseq_retriever`, поэтому перенос нужно делать с одновременным обновлением import paths.
- Neo4j-код все еще используется в backend factories и документации, поэтому удалять его лучше после переключения runtime на новый `bioseq_retriever`.
- Нужно отдельно проверить состояние embeddings-модулей: frontend ссылается на `bioseq_retriever.src.embeddings`, но в текущем дереве этот файл отсутствует.
- `example.env.txt` содержит Neo4j/OpenAI/Supabase примеры и должен быть очищен до безопасных placeholders.

## Итог

Рекомендуемый вектор: один основной backend data-agent в `app/backend/bioseq_retriever`, отдельный backend chat-agent в `app/backend/chat_agent`, frontend без тяжелой backend-логики, тесты в `tests`, Neo4j-контур полностью deprecated и затем удален.

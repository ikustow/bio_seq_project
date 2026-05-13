# Подробный task plan миграции репозитория

Дата: 2026-05-13

Связанный обзорный документ: [`REPOSITORY_OPTIMIZATION_PLAN.md`](REPOSITORY_OPTIMIZATION_PLAN.md).

## Цель миграции

Собрать репозиторий вокруг понятной backend-архитектуры, максимально сохранив текущую рабочую логику приложения. Это не переписывание продукта и не смена runtime-подхода, а уборка структуры, импортов, тестов и deprecated Neo4j-контура.

Текущее поведение, которое нужно сохранить:

- первый пользовательский turn идет в embeddings/bioseq retriever;
- retriever работает через локальные bioseq-артефакты: HDF5, FAISS/index/cache и UniProt/LLM rerank там, где это уже используется;
- follow-up вопросы идут в chat LLM через существующий Gemini proxy контур;
- session history продолжает работать через `SUPABASE_DB_URL`;
- при отсутствии `SUPABASE_DB_URL` приложение продолжает деградировать в non-persistent режим;
- Streamlit UI, карточки кандидатов, sidebar restore и first-turn/follow-up routing должны вести себя как сейчас;
- Neo4j/graph database из runtime убирается.

Структурная цель:

- `app/backend/bioseq_retriever/` - основной data-agent для работы с биологическими последовательностями и данными;
- `app/backend/chat_agent/` - отдельный chat/follow-up агент;
- `app/frontend/` - тонкий Streamlit UI без тяжелой backend-логики;
- `tests/` - единый контур тестирования;
- `scripts/` - только служебные offline/dev/ops скрипты;
- Neo4j и graph database контур - deprecated, затем полностью удален.

## Не цели этой миграции

- Не менять UX приложения.
- Не менять смысл first-turn/follow-up routing.
- Не менять LLM provider/proxy без отдельного решения.
- Не вводить новую graph/vector database вместо текущих локальных FAISS/HDF5 артефактов.
- Не переписывать retriever algorithm, rerank quality logic или evaluation criteria.
- Не менять persistence model радикально: Supabase/Postgres остается текущим контуром истории; локальный Postgres может использовать тот же `SUPABASE_DB_URL`-compatible путь.
- Не удалять старый код до появления совместимого нового пути.
- Не делать крупный LangGraph/agent rewrite в рамках механической миграции.

## Принципы выполнения

- Делать миграцию небольшими PR-ами, чтобы каждый шаг можно было проверить отдельно.
- Сначала добавить совместимые адаптеры и новые пути, потом удалить старые.
- Не смешивать перенос файлов с изменением логики, если это можно разделить.
- Любой перенос должен сопровождаться обновлением импортов, документации и тестов.
- Neo4j удалять после того, как runtime стабильно работает через `bioseq_retriever`.
- `frontend` можно временно оставить с wrapper-ами, но целевое направление - перенести backend orchestration в `app/backend` без изменения поведения.
- `scripts` не должен содержать тесты.
- В `example.env.txt` / `.env.example` не должно быть реальных ключей, паролей, URI приватных баз.

## Целевая структура после миграции

```text
app/
  backend/
    bioseq_retriever/
      __init__.py
      agent.py или pipeline.py
      pipeline_interface.py
      services/
      src/ или core/
      offline/              # только если появится reusable offline library logic
      README.md
    chat_agent/
      __init__.py
      service.py
      proxy_client.py
      prompt.py
      README.md
    app_contracts/
    app_services/
      service_factory.py
      bioseq_chat.py
    shared/ или persistence/
  frontend/
    app.py
    chat_pipeline.py        # thin adapter; routing behavior как сейчас
    components/
tests/
  unit/
    bioseq_retriever/
    frontend/
    backend/
  integration/
  smoke/
  eval/
scripts/
  data_prep/
    README.md
    backtranslate_and_embed.py
    config.py
```

## Фаза 0. Подготовка и инвентаризация

Цель: перед переносом понять, что реально используется, где есть сломанные импорты, какие тесты должны стать baseline.

### 0.1. Зафиксировать текущее состояние ветки

- [ ] Проверить `git status --short --branch`.
- [ ] Убедиться, что изменения в `app/backend/agents_core/docs...` принадлежат текущей задаче или отдельной пользовательской правке.
- [ ] Не смешивать эти изменения с миграцией, если они не относятся к ней.
- [ ] Зафиксировать текущий commit hash ветки как baseline.

Готово, когда:

- есть понимание, какие изменения уже были в working tree до миграции;
- миграционные изменения можно отделить в PR.

### 0.2. Составить карту импортов

- [ ] Найти все импорты `bioseq_retriever`.
- [ ] Найти все импорты `from src...` внутри `bioseq_retriever`.
- [ ] Найти все `sys.path.insert` / `sys.path.append`, связанные с backend/frontend/retriever.
- [ ] Найти все упоминания Neo4j:
  - `neo4j`;
  - `Neo4j`;
  - `GraphDatabase`;
  - `graph_core`;
  - `GraphRetrievalService`;
  - `Neo4jGraphClient`;
  - `NEO4J_*`.
- [ ] Найти все runtime-вызовы `chat_llm_pipeline`.
- [ ] Найти все тестовые скрипты в `scripts`.

Рекомендуемые команды:

```bash
rg -n "bioseq_retriever|from src\\.|sys\\.path|neo4j|Neo4j|GraphDatabase|graph_core|GraphRetrievalService|Neo4jGraphClient|NEO4J_|chat_llm_pipeline" .
find scripts -maxdepth 2 -type f | sort
find tests -maxdepth 4 -type f | sort
```

Готово, когда:

- есть список файлов, которые нужно трогать;
- понятны runtime-зависимости frontend -> backend -> retriever/chat.

### 0.3. Проверить текущую работоспособность тестов

- [ ] Запустить быстрые unit tests, если окружение готово.
- [ ] Запустить evaluation help/validate commands без тяжелых моделей.
- [ ] Отдельно отметить тесты, которые зависят от Supabase/LLM/FAISS/HDF5.
- [ ] Отдельно отметить тесты, которые уже устарели и должны быть переписаны.

Минимальный baseline:

```bash
python -m pytest -q
python -m tests.eval.validate_data
```

Если pytest-конфигурации еще нет:

- [ ] добавить `pytest.ini` или `pyproject.toml` на отдельном шаге;
- [ ] не блокировать архитектурный перенос отсутствием идеальной test infra.

Готово, когда:

- понятно, какие проверки являются обязательными для каждого PR;
- тяжелые integration/eval проверки отделены от быстрых unit/smoke.

## Фаза 1. Подготовка Python package layout

Цель: сделать `app` и `app/backend` нормальными импортируемыми пакетами, чтобы перенос `bioseq_retriever` не держался на `sys.path`-хаках.

### 1.1. Добавить package markers

- [ ] Добавить `app/__init__.py`, если принято делать `app` пакетом.
- [ ] Добавить `app/backend/__init__.py`.
- [ ] Проверить, нужен ли `app/frontend/__init__.py`. Если frontend запускается напрямую через Streamlit, можно не превращать его в public package без необходимости.
- [ ] Убедиться, что `backend.app_contracts`, `backend.app_services` продолжают импортироваться в текущем режиме запуска.

Готово, когда:

- backend можно импортировать через `app.backend...` или выбранный стабильный путь;
- не появилось конфликтов с текущими Streamlit imports.

### 1.2. Выбрать единый стиль импортов

Рекомендуемый вариант:

- внутри backend использовать абсолютные импорты от backend package:
  - `from app.backend.app_contracts import ...`;
  - `from app.backend.bioseq_retriever... import ...`.

Альтернативный вариант:

- сохранить запуск с `PYTHONPATH=app` и импорты `from backend...`.

Нужно выбрать один стиль до массового переноса.

- [ ] Зафиксировать выбранный стиль в этом документе или `app/ARCHITECTURE.md`.
- [ ] Обновить README с командой запуска, которая соответствует выбранному стилю.
- [ ] Проверить запуск Streamlit.

Готово, когда:

- нет двух конкурирующих import-root стратегий;
- новые модули не добавляют новые `sys.path` вставки.

## Фаза 2. Перенос `bioseq_retriever` в `app/backend`

Цель: сделать `bioseq_retriever` основным backend data-agent и убрать его из корня репозитория.

### 2.1. Создать новый пакет

- [ ] Создать директорию `app/backend/bioseq_retriever/`.
- [ ] Перенести туда:
  - `bioseq_retriever/src/`;
  - `bioseq_retriever/services/`;
  - `bioseq_retriever/pipeline_interface.py`;
  - `bioseq_retriever/README.md`.
- [ ] Добавить `app/backend/bioseq_retriever/__init__.py`.
- [ ] Решить, сохраняем ли подпапку `src/` или переименовываем в `core/`.

Рекомендация:

- для первого PR оставить `src/`, чтобы минимизировать риск;
- переименование `src -> core` сделать отдельным рефакторингом, если будет нужно.

Готово, когда:

- физически пакет лежит в `app/backend/bioseq_retriever/`;
- старый корневой `bioseq_retriever/` больше не содержит runtime-код.

### 2.2. Исправить импорты внутри retriever

Текущая проблема: внутри `bioseq_retriever` есть импорты вида:

```python
from src.pipeline import ...
from src.utils import ...
from services.config import ...
```

Нужно:

- [ ] заменить импорты `from src...` на пакетные;
- [ ] заменить импорты `from services...` на пакетные;
- [ ] проверить `pipeline_interface.py`;
- [ ] проверить `services/search_service.py`;
- [ ] проверить `tests/eval/retriever_eval.py`;
- [ ] проверить README commands.

Пример целевого вида:

```python
from app.backend.bioseq_retriever.src.pipeline import run_bioseq_pipeline
```

или, если выбран `PYTHONPATH=app`:

```python
from backend.bioseq_retriever.src.pipeline import run_bioseq_pipeline
```

Готово, когда:

- `rg -n "from src\\.|import src|from services\\.|import services" app/backend/bioseq_retriever` ничего не находит;
- `python -m app.backend.bioseq_retriever.pipeline_interface --help` или выбранный аналог запускается.

### 2.3. Исправить пути к данным

Текущие пути часто завязаны на `bioseq_retriever/data`.

Нужно:

- [ ] определить новый default data dir;
- [ ] рекомендуемый default: `app/backend/bioseq_retriever/data`;
- [ ] проверить `BIOSEQ_DATA_DIR`;
- [ ] проверить `BIOSEQ_H5_PATH`;
- [ ] проверить `BIOSEQ_INDEX_PATH`;
- [ ] проверить `BIOSEQ_ACCESSIONS_CACHE_PATH`;
- [ ] обновить `bootstrap.py`;
- [ ] обновить README;
- [ ] убедиться, что большие data files остаются в `.gitignore`.

Готово, когда:

- HDF5/index/cache пути работают из нового расположения;
- старый `bioseq_retriever/data` больше не фигурирует как runtime default, кроме временной backward-compat заметки.

### 2.4. Проверить missing/устаревшие модули

Во время аудита найден риск: frontend ссылается на `bioseq_retriever.src.embeddings`, но в текущем дереве файла `bioseq_retriever/src/embeddings.py` нет.

Нужно:

- [ ] подтвердить, был ли файл удален случайно или заменен `services/search_service.py`;
- [ ] если нужен local FAISS mode, восстановить/перенести модуль embeddings;
- [ ] если local mode больше не нужен, удалить frontend code path, который его импортирует;
- [ ] обновить tests/eval docs, где упоминается local/service mode;
- [ ] привести `bioseq_retriever/src/search.py` и `services/search_service.py` к одному runtime-режиму.

Готово, когда:

- нет импортов несуществующих модулей;
- выбран один основной способ запуска retriever в приложении.

### 2.5. Добавить compatibility shim, если нужен плавный переход

Если перенос большой и нельзя сразу обновить все импорты:

- [ ] временно оставить корневой `bioseq_retriever/__init__.py`;
- [ ] сделать shim, который импортирует из `app.backend.bioseq_retriever`;
- [ ] пометить shim deprecated;
- [ ] добавить TODO на удаление shim в финальной фазе.

Рекомендация:

- лучше избегать shim, если можно обновить все импорты одним PR;
- shim допустим, если evaluation harness или deploy еще ожидают старый путь.

Готово, когда:

- старые импорты либо не используются, либо явно deprecated.

## Фаза 3. Тонкий backend layer вокруг текущего `bioseq_retriever`

Цель: не менять работу retriever, а вынести его запуск из frontend-слоя в backend-слой. На этом шаге нельзя менять алгоритм поиска, формат карточек, поведение ошибок, persistence semantics или first-turn routing.

### 3.1. Зафиксировать текущий contract embeddings path

Перед проектированием нового API нужно описать текущий contract, который уже ожидает frontend:

- [ ] вход: `prompt: str`;
- [ ] выходной dict из текущего `run_turn_embeddings`;
- [ ] обязательные поля:
  - `reply`;
  - `candidates`;
  - `candidates_raw`;
  - `reveals`;
  - `warnings`;
  - `result`;
  - `persisted`;
  - `query_protein_sequence`;
- [ ] поведение preflight errors;
- [ ] поведение сохранения turn-а в session DB;
- [ ] поведение при отсутствующих heavy deps/data artifacts/LLM credentials.

Готово, когда:

- понятно, что именно нужно сохранить при переносе из `app/frontend/embeddings_pipeline.py` в backend.

### 3.2. Создать совместимый backend wrapper

Минимальный совместимый контракт может быть таким:

```python
class BioSeqRetrieverService:
    def run_first_turn(prompt: str, context: AppContext) -> dict:
        ...
```

На первом этапе можно намеренно вернуть тот же dict shape, который сейчас возвращает frontend adapter. `ChatTurnRequest` / `ChatTurnResult` можно подключать позже, отдельным аккуратным PR, если это не ломает UI.

Задачи:

- [ ] создать backend wrapper вокруг текущей логики embeddings retriever;
- [ ] перенести тяжелую инициализацию из frontend в backend без изменения порядка вызовов;
- [ ] сохранить preflight behavior;
- [ ] сохранить session save behavior или явно оставить его во frontend wrapper на переходный период;
- [ ] не менять формат candidates.

Готово, когда:

- backend wrapper возвращает тот же результат, что текущий `run_turn_embeddings`;
- frontend можно переключить на wrapper без видимого изменения поведения.

### 3.3. Обновить `app/backend/app_services/service_factory.py`

Нужно:

- [ ] добавить factory для нового `bioseq_retriever` service;
- [ ] убрать обязательную Neo4j-инициализацию из runtime path;
- [ ] оставить mock mode, если он нужен для UI demo;
- [ ] разделить modes:
  - `mock`;
  - `bioseq_retriever`;
  - `chat_agent` для follow-up;
  - возможно `deprecated_graph`, только временно.
- [ ] убрать импорт отсутствующего `SessionGraphAgent` из основного пути.

Готово, когда:

- `create_bioseq_chat_service()` не падает из-за отсутствующего graph-agent;
- factory может собрать backend без Neo4j credentials;
- основной путь повторяет текущую embeddings + chat proxy схему.

### 3.4. Аккуратно подключить `BioSeqChatService`, если это не ломает текущий flow

Текущий `BioSeqChatService` почти подходит как orchestration layer, но завязан на `GraphRetrievalService`. Не нужно насильно переводить весь frontend на него в первом PR. Лучше сначала сделать совместимый backend wrapper, а затем постепенно привести сервисный контракт к `ChatTurnRequest` / `ChatTurnResult`.

Нужно:

- [ ] заменить graph-specific зависимости на интерфейсы;
- [ ] добавить data-agent dependency;
- [ ] добавить chat-agent dependency;
- [ ] реализовать routing:
  - первый turn -> `bioseq_retriever`;
  - follow-up turn -> `chat_agent`;
  - selection change -> backend session update без нового retrieval;
- [ ] принимать `selected_accession`, `selected_candidate_index`, `ui_context`;
- [ ] возвращать `ChatTurnResult`;
- [ ] синхронизировать revealed sections с frontend keys.

Готово, когда:

- `BioSeqChatService.submit_turn()` становится единственным backend entrypoint для UI turn-а;
- сервис не импортирует Neo4j;
- поведение UI совпадает с текущим.

### 3.5. Уточнить persistence responsibility

Сейчас запись делает frontend adapter через `session_db_adapter`. Для консервативной миграции допустимо временно сохранить этот путь, чтобы не менять persistence behavior вместе с переносом файлов.

Целевой вариант после стабилизации:

- `BioSeqChatService` единственный writer в `public.chat_sessions`.

Задачи:

- [ ] сначала зафиксировать текущее поведение `save_turn`;
- [ ] не менять writer в том же PR, где переносится `bioseq_retriever`;
- [ ] после стабилизации вынести save_turn из frontend adapter;
- [ ] сделать backend service ответственным за transcript;
- [ ] сделать backend service ответственным за `turn_count`;
- [ ] сделать backend service ответственным за candidates/card state;
- [ ] оставить frontend только consumer-ом session state.

Готово, когда:

- один user turn = одна логическая backend operation;
- нет двух независимых UPDATE одной session row на один turn.

## Фаза 4. Перенос chat-agent в backend

Цель: убрать follow-up LLM logic из frontend, но сохранить текущий Gemini proxy flow, prompt behavior, ошибки и то, что follow-up turn не сбрасывает карточку белка.

### 4.1. Создать пакет `app/backend/chat_agent`

- [ ] Создать `app/backend/chat_agent/__init__.py`.
- [ ] Создать `service.py`.
- [ ] Создать `proxy_client.py`.
- [ ] Создать `prompt.py`.
- [ ] Создать `README.md`.
- [ ] Перенести constants:
  - `BIOSEQ_LLM_PROXY_URL`;
  - `BIOSEQ_LLM_PROXY_TOKEN`;
  - request timeout;
  - generation config.

Готово, когда:

- chat-agent можно импортировать и вызывать из backend без Streamlit.

### 4.2. Перенести `_call_gemini_proxy`

Из `app/frontend/chat_llm_pipeline.py` перенести:

- [ ] HTTP-вызов Gemini proxy;
- [ ] разбор ответа;
- [ ] обработку ошибок;
- [ ] timeout;
- [ ] token/header logic.

Важно:

- backend function не должна читать `st.session_state`;
- весь контекст должен приходить явно через request/session snapshot.
- текст system instruction и формат payload сначала переносим без смыслового изменения.

Готово, когда:

- chat proxy можно unit-тестировать без Streamlit.

### 4.3. Перенести построение protein context

Сейчас `_get_current_protein_context()` читает candidates из `st.session_state`.

Нужно:

- [ ] сделать функцию `build_protein_context(candidates, selected_index)`;
- [ ] передавать candidates/session state из backend persistence или request;
- [ ] не читать frontend globals;
- [ ] покрыть unit tests:
  - нет candidates;
  - invalid selected index;
  - protein with function/domains/disease;
  - missing optional fields.

Готово, когда:

- follow-up answer grounded на текущую карточку без зависимости от Streamlit.

### 4.4. Оставить frontend compatibility wrapper

На переходный период:

- [ ] оставить `app/frontend/chat_llm_pipeline.py` как тонкую обертку;
- [ ] внутри вызывать backend `ChatAgentService`;
- [ ] пометить модуль как transitional;
- [ ] удалить после полного перехода frontend на `BioSeqChatService`.

Готово, когда:

- frontend file не содержит LLM prompt/proxy business logic.

## Фаза 5. Переключение frontend на backend services

Цель: `app/frontend` постепенно перестает быть местом backend orchestration. На первом этапе допустимы compatibility wrappers, если это помогает сохранить текущее поведение без большого переписывания.

### 5.1. Обновить `app/frontend/chat_pipeline.py`

Нужно:

- [ ] убрать прямой импорт `embeddings_pipeline` как основного runtime path;
- [ ] создать thin call в backend service;
- [ ] сохранить текущий return dict shape для UI;
- [ ] формировать `ChatTurnRequest` только если это не ломает текущий flow:
  - `message`;
  - `session_id`;
  - `user_id`;
  - `workspace_id`;
  - `user_role`;
  - `selected_accession`;
  - `selected_candidate_index`;
  - `ui_context`;
- [ ] маппить `ChatTurnResult` в текущий UI dict shape;
- [ ] оставить `_is_first_turn_in_session()` только если routing остается во frontend временно;
- [ ] не менять first/follow-up routing в том же PR, где переносится retriever.

Готово, когда:

- frontend не импортирует `bioseq_retriever` напрямую;
- frontend не знает про FAISS/HDF5/LLM provider details;
- визуальное поведение совпадает с текущим.

### 5.2. Обновить `app/frontend/embeddings_pipeline.py`

Варианты:

1. Удалить после переключения на backend service.
2. Оставить как transitional wrapper.

Если wrapper:

- [ ] сначала перенаправить вызов в backend service;
- [ ] сохранить старую функцию `run_turn_embeddings(prompt)` как совместимый wrapper;
- [ ] удалить тяжелую resource initialization из frontend только после проверки backend wrapper;
- [ ] удалить direct imports `bioseq_retriever.src...`;
- [ ] добавить deprecation note;
- [ ] не менять тексты ошибок/preflight в этом же PR.

Готово, когда:

- нет frontend-level загрузки ProtT5/FAISS;
- тяжелая логика живет только в backend;
- старый UI call path продолжает работать через wrapper.

### 5.3. Обновить `backend_choice.py`

Сейчас backend choice фактически один: embeddings.

Нужно:

- [ ] решить, остается ли выбор backend в UI;
- [ ] если выбора больше нет, упростить модуль;
- [ ] если остается, добавить явные modes:
  - `bioseq_retriever`;
  - `mock`;
  - `deprecated_graph` только временно;
- [ ] убрать устаревшие названия `graph`/`embeddings`, если они больше путают.

Готово, когда:

- UI labels соответствуют реальной архитектуре.

### 5.4. Проверить Streamlit app flow

- [ ] Первый turn с sequence показывает candidates.
- [ ] Follow-up turn не сбрасывает выбранную карточку.
- [ ] Sidebar session restore работает.
- [ ] New chat создает новый session id.
- [ ] Reload страницы восстанавливает историю, если persistence включен.
- [ ] Без `SUPABASE_DB_URL` приложение деградирует предсказуемо.
- [ ] Без LLM proxy follow-up дает понятную ошибку.
- [ ] Без HDF5/index retriever дает понятную ошибку.

Готово, когда:

- существующий UX сохранен;
- backend orchestration переехал из frontend.

## Фаза 6. Деприкация Neo4j-контура

Цель: сначала безопасно отключить Neo4j из runtime, затем удалить.

### 6.1. Пометить Neo4j modules deprecated

Файлы-кандидаты:

- `app/backend/graph_core/`;
- `app/backend/app_services/graph_retrieval.py`;
- `app/backend/agents_core/shared/services/graph.py`;
- `app/backend/agents_core/retriever_agent/`;
- Neo4j docs в `app/backend/agents_core/docs*`.

Задачи:

- [ ] добавить deprecation note в README/docs;
- [ ] добавить warnings в factory, если кто-то выбирает graph mode;
- [ ] переименовать mode в `deprecated_graph`, если временно сохраняется;
- [ ] убрать graph mode из default runtime;
- [ ] убедиться, что приложение стартует без Neo4j env.

Готово, когда:

- Neo4j больше не нужен для обычного запуска приложения.

### 6.2. Убрать Neo4j из `service_factory`

- [ ] Удалить `resolve_neo4j_settings()` из основного factory path.
- [ ] Удалить создание `Neo4jGraphClient` из основного runtime path.
- [ ] Удалить `GraphRetrievalService` из `BioSeqChatService` runtime path.
- [ ] Если временный graph mode остается, вынести в отдельную deprecated factory-функцию.

Готово, когда:

- backend service для приложения создается без Neo4j driver.

### 6.3. Убрать Neo4j env/config

- [ ] Удалить defaults `DEFAULT_URI`, `DEFAULT_DATABASE`, если они больше не нужны.
- [ ] Удалить `Neo4jConnectionSettings`.
- [ ] Удалить `resolve_neo4j_settings`.
- [ ] Удалить `NEO4J_*` из example env.
- [ ] Удалить Neo4j credentials/placeholders из docs.
- [ ] Проверить, что `.env` локального пользователя не трогается автоматически.

Готово, когда:

- `rg -n "NEO4J|Neo4j|neo4j" app README.md README_RU.md example.env.txt report` показывает только исторические/deprecated notes или ничего.

### 6.4. Удалить Neo4j data pipeline

После стабилизации нового runtime:

- [ ] удалить `app/backend/graph_core/scripts/import_to_neo4j.py`;
- [ ] удалить `app/backend/graph_core/scripts/export_for_neo4j.py`;
- [ ] удалить docs `how_to_use.md` / `how_to_use_en.md`, если они только про Neo4j;
- [ ] удалить graph visualization assets, если они только для Neo4j;
- [ ] удалить `.local/start-neo4j-local.sh`, если tracked или documented;
- [ ] удалить Neo4j references из PRD/report docs или пометить историческими.

Готово, когда:

- в runtime tree нет graph database контура;
- Neo4j не упоминается как актуальная зависимость.

## Фаза 7. Перенос тестов в `tests`

Цель: все проверки живут в одном test tree, `scripts` очищен.

### 7.1. Перенести `bioseq_retriever/tests`

Целевое место:

```text
tests/unit/bioseq_retriever/
```

Задачи:

- [ ] перенести `test_pipeline.py`;
- [ ] перенести `test_search_client.py`;
- [ ] перенести `test_unified_search_service.py`;
- [ ] перенести `test_utils.py`;
- [ ] обновить imports;
- [ ] удалить старую папку `app/backend/bioseq_retriever/tests` или `bioseq_retriever/tests`, если она больше не нужна;
- [ ] проверить pytest discovery.

Готово, когда:

- все unit tests запускаются из `tests/unit/bioseq_retriever`.

### 7.2. Перенести `scripts/test_*`

Текущие файлы:

- `scripts/test_schema_unification.py`;
- `scripts/test_session_identity_resolver.py`.

Целевое место:

```text
tests/unit/frontend/
tests/unit/backend/
```

Задачи:

- [ ] перенести schema unification test в backend/frontend boundary tests;
- [ ] перенести session identity resolver test в frontend unit tests;
- [ ] заменить ручной `main()` runner на pytest-compatible tests;
- [ ] оставить возможность запуска через `pytest`.

Готово, когда:

- `scripts/test_*` больше нет;
- тесты запускаются через pytest.

### 7.3. Перенести `scripts/smoke_*`

Текущие файлы:

- `scripts/smoke_embeddings_dispatch.py`;
- `scripts/smoke_first_turn_router.py`;
- `scripts/smoke_session_db.py`.

Целевое место:

```text
tests/smoke/
```

Задачи:

- [ ] перенести smoke tests;
- [ ] добавить pytest markers:
  - `smoke`;
  - `requires_supabase`;
  - `requires_llm`;
  - `requires_data`;
- [ ] сделать graceful skip, если env не задан;
- [ ] убрать устаревшее ожидание missing heavy deps, если runtime больше backend-only.

Готово, когда:

- smoke tests не лежат в `scripts`;
- их можно запускать выборочно:

```bash
python -m pytest tests/smoke -m smoke
```

### 7.4. Обновить `tests/eval`

Задачи:

- [ ] заменить старый путь к `bioseq_retriever`;
- [ ] убрать `sys.path` вставку на корневой `bioseq_retriever`;
- [ ] импортировать новый backend package;
- [ ] обновить README;
- [ ] проверить `validate_data`;
- [ ] проверить `retriever_eval`;
- [ ] проверить `e2e_eval`;
- [ ] проверить `llm_eval`;
- [ ] убедиться, что eval outputs остаются в `tests/eval/runs`.

Готово, когда:

- evaluation harness работает с новым `app/backend/bioseq_retriever`.

### 7.5. Добавить test config

- [ ] Создать `pytest.ini` или `pyproject.toml`.
- [ ] Настроить testpaths:
  - `tests/unit`;
  - `tests/integration`;
  - `tests/smoke`;
- [ ] Добавить markers.
- [ ] Добавить pythonpath, если выбран такой подход.
- [ ] Документировать test commands в README.

Пример:

```ini
[pytest]
testpaths = tests
markers =
    unit: fast unit tests
    integration: integration tests
    smoke: smoke tests
    requires_supabase: needs SUPABASE_DB_URL
    requires_llm: needs LLM credentials or proxy
    requires_data: needs local HDF5/index artifacts
```

Готово, когда:

- новый разработчик понимает, как запустить быстрые и тяжелые проверки.

## Фаза 8. Перенос `data_prep` в `scripts/data_prep`

Цель: убрать offline data preparation из корня и явно поместить его в служебные скрипты.

### 8.1. Перенести файлы

Из:

```text
data_prep/
```

В:

```text
scripts/data_prep/
```

Файлы:

- [ ] `README.md`;
- [ ] `backtranslate_and_embed.py`;
- [ ] `config.py`.

Готово, когда:

- корневая папка `data_prep/` удалена;
- offline tooling лежит в `scripts/data_prep/`.

### 8.2. Исправить пути и запуск

- [ ] Проверить `SWISSPROT_TSV`.
- [ ] Проверить `EMBEDDING_OUTPUT_FILE`.
- [ ] Сделать пути относительно `scripts/data_prep/` или repo root явно.
- [ ] Добавить CLI args для input/output, если их нет.
- [ ] Убрать hardcoded assumptions про текущую директорию запуска.

Рекомендуемый запуск:

```bash
python scripts/data_prep/backtranslate_and_embed.py \
  --input data/swissprot.tsv \
  --output artifacts/per-gene.h5
```

Готово, когда:

- скрипт можно запускать из repo root;
- README не требует `cd data_prep`.

### 8.3. Разделить CLI и библиотечную логику

Если код нужен runtime/reusable:

- [ ] вынести codon table / helper functions в `app/backend/bioseq_retriever`;
- [ ] оставить в `scripts/data_prep` только CLI wrapper;
- [ ] покрыть reusable logic unit tests.

Если код одноразовый:

- [ ] явно назвать его offline experimental/prep script;
- [ ] не импортировать его из backend.

Готово, когда:

- `scripts/data_prep` не становится скрытой runtime dependency.

## Фаза 9. Чистка `scripts`

Цель: `scripts` = служебные команды, не тесты и не runtime.

### 9.1. Определить допустимые категории scripts

Оставляем только:

- [ ] data preparation;
- [ ] artifact bootstrap/upload/download;
- [ ] local maintenance;
- [ ] one-shot migrations;
- [ ] report/evaluation helper commands, если они не являются tests.

Не оставляем:

- [ ] unit tests;
- [ ] smoke tests;
- [ ] frontend runtime adapters;
- [ ] backend service code.

Готово, когда:

- каждый файл в `scripts` можно объяснить как служебный command-line инструмент.

### 9.2. Добавить `scripts/README.md`

Содержимое:

- [ ] что лежит в `scripts`;
- [ ] что запрещено класть в `scripts`;
- [ ] как запускать `scripts/data_prep`;
- [ ] какие env/data artifacts нужны;
- [ ] где лежат тесты.

Готово, когда:

- у команды нет повода снова класть smoke tests в `scripts`.

## Фаза 10. Документация

Цель: docs должны описывать новую архитектуру, а не смесь старых контуров.

### 10.1. Обновить root README

Задачи:

- [ ] заменить путь `bioseq_retriever/` на `app/backend/bioseq_retriever/`;
- [ ] описать новый backend service flow;
- [ ] убрать Neo4j как обязательную зависимость;
- [ ] обновить env table;
- [ ] обновить local development commands;
- [ ] обновить project layout;
- [ ] уточнить cold-start/data artifact expectations.

Готово, когда:

- README описывает актуальный запуск приложения без Neo4j.

### 10.2. Обновить `app/README.md`

Задачи:

- [ ] убрать описание Streamlit поверх Neo4j-графа;
- [ ] описать `bioseq_retriever` как backend data-agent;
- [ ] описать `chat_agent`;
- [ ] обновить TODO;
- [ ] убрать старые graph-specific задачи или перенести их в deprecated notes.

Готово, когда:

- `app/README.md` не конфликтует с root README.

### 10.3. Обновить `app/ARCHITECTURE.md`

Задачи:

- [ ] заменить diagram с Neo4j на новый flow:
  - Browser/Streamlit;
  - frontend;
  - `BioSeqChatService`;
  - `bioseq_retriever`;
  - `chat_agent`;
  - Supabase/Postgres persistence;
  - HDF5/FAISS/UniProt/LLM providers.
- [ ] удалить graph runtime flow;
- [ ] добавить deprecation note для Neo4j, если исторически нужно.

Готово, когда:

- архитектурный документ совпадает с кодом.

### 10.4. Обновить retriever README

Задачи:

- [ ] перенести README вместе с пакетом;
- [ ] обновить setup commands;
- [ ] обновить import examples;
- [ ] обновить service/local runtime mode;
- [ ] обновить data paths;
- [ ] убрать устаревшие references на старый root package.

Готово, когда:

- разработчик может запустить retriever из нового места по README.

### 10.5. Обновить env examples

Задачи:

- [ ] очистить `example.env.txt` от реальных-looking secrets;
- [ ] убрать `NEO4J_*`;
- [ ] добавить placeholders:
  - `MISTRAL_API_KEY=...`;
  - `OPENAI_API_KEY=...`, если нужен;
  - `SUPABASE_DB_URL=...`;
  - `BIOSEQ_LLM_PROXY_URL=...`;
  - `BIOSEQ_LLM_PROXY_TOKEN=...`;
  - `BIOSEQ_DATA_SOURCE=...`;
  - `BIOSEQ_DATA_DIR=...`;
  - `BIOSEQ_H5_PATH=...`;
- [ ] создать `.env.example`, если `example.env.txt` остается неканоничным;
- [ ] убедиться, что `.env` не трогается.

Готово, когда:

- пример env безопасен для коммита;
- Neo4j env больше не нужен.

## Фаза 11. Dependency cleanup

Цель: зависимости отражают реальный runtime.

### 11.1. Разделить core/frontend/heavy deps

Задачи:

- [ ] понять, нужен ли один `requirements.txt` или несколько;
- [ ] выделить frontend deps;
- [ ] выделить backend core deps;
- [ ] выделить heavy retriever deps:
  - `torch`;
  - `transformers`;
  - `faiss-cpu`;
  - `h5py`;
  - `sentencepiece`;
  - `protobuf`;
  - `huggingface_hub`;
- [ ] выделить dev/test deps:
  - `pytest`;
  - возможно `pytest-mock`;
  - возможно `ruff`;
- [ ] убрать `neo4j`, если он был добавлен как dependency.

Готово, когда:

- deploy/install instructions не заставляют ставить graph database deps.

### 11.2. Проверить imports vs requirements

- [ ] `rg -n "import fastapi|from fastapi|uvicorn|faiss|h5py|torch|transformers|neo4j|polars|Bio|pyfaidx" .`
- [ ] сверить с requirements;
- [ ] убрать неиспользуемые;
- [ ] добавить отсутствующие.

Готово, когда:

- clean environment может установить зависимости и пройти smoke import.

## Фаза 12. Удаление legacy/deprecated кода

Цель: финальная чистка после того, как новый контур стабилен.

### 12.1. Удалить старый root `bioseq_retriever`

- [ ] убедиться, что imports больше не используют старый путь;
- [ ] удалить compatibility shim, если он был;
- [ ] удалить старые docs;
- [ ] проверить `rg -n "bioseq_retriever/" .`.

Готово, когда:

- `bioseq_retriever` существует только как `app/backend/bioseq_retriever`.

### 12.2. Удалить deprecated Neo4j

- [ ] удалить graph runtime code;
- [ ] удалить graph scripts;
- [ ] удалить graph docs;
- [ ] удалить env/config;
- [ ] удалить tests, которые проверяли graph-only behavior;
- [ ] удалить PRD/docs references или пометить историческими, если они нужны для архива.

Готово, когда:

- `rg -n "Neo4j|neo4j|GraphDatabase|graph_core|GraphRetrievalService|Neo4jGraphClient" app tests scripts README.md README_RU.md example.env.txt` ничего актуального не находит.

### 12.3. Удалить frontend legacy adapters

Кандидаты:

- `app/frontend/backend_adapter.py`;
- `app/frontend/vector_db_adapter.py`;
- `app/frontend/embeddings_pipeline.py`, если полностью заменен backend service;
- устаревшие docs `DEPLOY_old.md`, `TECH_SPEC_old.md`, `README_old.md`, если не нужны.

Задачи:

- [ ] проверить imports;
- [ ] удалить dead files;
- [ ] обновить README;
- [ ] обновить tests.

Готово, когда:

- frontend содержит только UI и thin backend adapter.

## Фаза 13. Verification checklist

Цель: убедиться, что миграция закончена, а не просто файлы переложены.

### 13.1. Static checks

- [ ] `rg -n "from src\\.|import src" app tests scripts` не находит старых импортов.
- [ ] `rg -n "sys\\.path" app tests scripts` показывает только оправданные test/bootstrap места.
- [ ] `rg -n "Neo4j|neo4j|GraphDatabase|NEO4J_" app tests scripts README.md example.env.txt` не показывает актуальных зависимостей.
- [ ] `rg -n "bioseq_retriever/" README.md app report tests scripts` не показывает старого root path как актуального.
- [ ] `find . -name "__pycache__" -o -name "*.pyc"` ничего tracked не показывает.

### 13.2. Import checks

```bash
python -c "import app.backend.bioseq_retriever"
python -c "import app.backend.chat_agent"
python -c "from app.backend.app_services.service_factory import create_bioseq_chat_service"
```

Если выбран `PYTHONPATH=app` style, команды заменить на:

```bash
PYTHONPATH=app python -c "import backend.bioseq_retriever"
PYTHONPATH=app python -c "import backend.chat_agent"
```

### 13.3. Unit tests

```bash
python -m pytest tests/unit -q
```

Критерий:

- быстрые unit tests не требуют Supabase, LLM proxy, HDF5, FAISS index, Neo4j.

### 13.4. Smoke tests

```bash
python -m pytest tests/smoke -q
```

Критерий:

- tests с внешними зависимостями skip-аются при отсутствии env;
- при наличии env проходят.

### 13.5. Evaluation checks

```bash
python -m tests.eval.validate_data
python -m tests.eval.retriever_eval --help
```

Тяжелый прогон:

```bash
python -m tests.eval.run_all
```

Критерий:

- eval harness импортирует новый backend retriever;
- outputs пишутся в `tests/eval/runs`.

### 13.6. App manual QA

- [ ] Запустить Streamlit.
- [ ] Создать новый чат.
- [ ] Вставить protein sequence.
- [ ] Проверить top candidates/card.
- [ ] Задать follow-up вопрос.
- [ ] Проверить, что карточка не сбрасывается.
- [ ] Перезагрузить страницу.
- [ ] Проверить восстановление session history.
- [ ] Проверить поведение без Supabase.
- [ ] Проверить понятную ошибку без LLM proxy.
- [ ] Проверить понятную ошибку без data artifacts.

## Рекомендуемый порядок PR-ов

### PR 1. Test/documentation baseline

- Добавить этот task plan.
- Добавить/обновить pytest config, если нужно.
- Перенести очевидные test scripts из `scripts/test_*` в `tests/unit`.
- Без runtime-миграции.

Критерий:

- есть baseline быстрых тестов.

### PR 2. Package layout prep

- Добавить package markers.
- Зафиксировать import style.
- Убрать самые простые `sys.path` хаки.

Критерий:

- backend импортируется стабильно.

### PR 3. Move `bioseq_retriever`

- Перенести пакет в `app/backend/bioseq_retriever`.
- Обновить imports.
- Обновить tests/eval imports.
- Обновить README paths.

Критерий:

- retriever unit/eval help проходят из нового места.

### PR 4. Backend data-agent service

- Добавить совместимый backend wrapper/factory вокруг текущего retriever.
- Сохранить текущий output dict shape для frontend.
- Убрать прямой frontend call в retriever internals только через compatibility wrapper.
- Не менять алгоритм поиска, preflight errors и persistence behavior.

Критерий:

- первый turn идет через backend service и выглядит для UI так же, как до миграции.

### PR 5. Move chat-agent

- Создать `app/backend/chat_agent`.
- Перенести Gemini proxy logic без изменения payload/prompt semantics.
- Оставить frontend wrapper.

Критерий:

- follow-up turn идет через backend chat-agent и не сбрасывает текущую карточку, как сейчас.

### PR 6. Frontend simplification

- Упростить `chat_pipeline.py`.
- Задепрекейтить `embeddings_pipeline.py` как runtime component, но удалить только после проверки wrapper-а.
- Обновить sidebar/backend choice.

Критерий:

- frontend является тонким UI adapter, поведение first-turn/follow-up сохранено.

### PR 7. Neo4j deprecation

- Отключить Neo4j из default runtime.
- Убрать Neo4j env из docs.
- Пометить graph modules deprecated.

Критерий:

- app стартует без Neo4j env.

### PR 8. Move `data_prep`

- Перенести `data_prep` в `scripts/data_prep`.
- Обновить README и paths.
- Добавить `scripts/README.md`.

Критерий:

- корневой `data_prep` удален.

### PR 9. Test tree cleanup

- Перенести `scripts/smoke_*` в `tests/smoke`.
- Перенести `bioseq_retriever/tests` в `tests/unit/bioseq_retriever`.
- Обновить markers/skips.

Критерий:

- `scripts` больше не содержит тесты.

### PR 10. Remove Neo4j and legacy dead code

- Удалить `graph_core`.
- Удалить graph services/agents.
- Удалить legacy frontend adapters.
- Финально обновить docs.

Критерий:

- grep по Neo4j/root retriever старым путям чистый.

## Definition of Done всей миграции

- [ ] `bioseq_retriever` находится в `app/backend/bioseq_retriever`.
- [ ] Chat-agent находится в `app/backend/chat_agent`.
- [ ] First-turn retriever behavior совпадает с текущим embeddings/bioseq flow.
- [ ] Follow-up chat behavior совпадает с текущим Gemini proxy flow.
- [ ] Frontend не содержит heavy retriever/chat proxy business logic, кроме временных совместимых wrapper-ов во время перехода.
- [ ] Backend wrapper/service является главным entrypoint для UI turns.
- [ ] Neo4j не является runtime dependency.
- [ ] Graph database code удален или оставлен только в явно deprecated archive, если команда решит сохранить историю.
- [ ] Все тесты лежат в `tests`.
- [ ] `scripts` содержит только служебные команды.
- [ ] `data_prep` лежит в `scripts/data_prep`.
- [ ] README и architecture docs совпадают с кодом.
- [ ] Example env безопасен и не содержит реальных credentials.
- [ ] Быстрые unit tests проходят.
- [ ] Smoke tests корректно проходят или skip-аются по отсутствующим env.
- [ ] Evaluation harness импортирует новый backend retriever.
- [ ] Streamlit app проходит ручной first-turn/follow-up/session-restore сценарий.

## Открытые решения перед началом реализации

- [ ] Какой import root выбираем: `app.backend...` или `backend...` через `PYTHONPATH=app`.
- [ ] Оставляем ли `src/` внутри `bioseq_retriever` или переименовываем в `core/`.
- [ ] Нужен ли временный compatibility shim для старого `bioseq_retriever`.
- [ ] Где окончательно живет persistence orchestration: в `BioSeqChatService` или в отдельных agent services.
- [ ] Оставляем ли mock mode как backend service или только frontend demo mode.
- [ ] Какой runtime mode у retriever основной: local in-process или service process.
- [ ] Нужно ли сохранять исторические Neo4j docs в архиве или удалить полностью.

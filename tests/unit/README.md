# Unit-тесты приложения

Эта папка содержит быстрые unit-тесты для основного runtime-кода из `app/`.

Идея простая: эти тесты не вызывают реальные LLM, не ходят в Supabase, не
поднимают Streamlit в браузере, не грузят FAISS/ProtT5 и не требуют API-ключей.
Они проверяют каркас приложения вокруг LLM и retrieval flow: какие данные
передаются, как выбирается provider, как сохраняется состояние, не затирается
ли protein card на follow-up вопросах.

Для оценки качества реальных LLM-ответов есть отдельный слой `tests/eval/`.
Unit-тесты здесь отвечают на вопрос: "мы правильно собрали и провели flow?",
а eval-тесты отвечают на вопрос: "получился ли хороший ответ модели?".

## Как запускать

Из корня проекта:

```bash
python3 -m pytest tests/unit
```

Подробный вывод:

```bash
python3 -m pytest tests/unit -v
```

Очень подробный вывод с `print`/logs:

```bash
python3 -m pytest tests/unit -vv -s
```

Остановиться на первом падении:

```bash
python3 -m pytest tests/unit -x
```

Показать список тестов без запуска:

```bash
python3 -m pytest tests/unit --collect-only -q
```

Запустить один файл:

```bash
python3 -m pytest tests/unit/backend/app_services/test_chat_llm.py
```

Запустить один конкретный тест:

```bash
python3 -m pytest tests/unit/backend/app_services/test_chat_llm.py::test_auto_provider_prefers_proxy
```

Если вы хотите запускать тесты из `.venv`, сначала установите `pytest` внутрь
виртуального окружения:

```bash
source .venv/bin/activate
python -m pip install pytest
python -m pytest tests/unit
```

## Что проверяет каждый файл

### `conftest.py`

Общие настройки для unit-тестов.

Что делает:

- добавляет `app/`, `app/frontend/` и корень проекта в `sys.path`;
- очищает переменные окружения с ключами и runtime-настройками перед каждым
  тестом;
- даёт фикстуры `candidate_view` и `candidate_dict` с примером protein card.

Зачем это нужно:

- тесты не должны случайно использовать реальные ключи из вашей среды;
- все тесты получают одинаковые стабильные данные о белке.

### `backend/app_services/test_chat_llm.py`

Проверяет чистую логику `ChatLLMService` и helper-функции из
`backend/app_services/chat_llm.py`.

Что проверяется:

- provider `auto` выбирает Gemini proxy, если есть proxy URL/token;
- provider `auto` выбирает OpenAI, если есть только OpenAI key;
- `provider_override` сильнее env-переменных;
- без credentials сервис падает понятной ошибкой;
- protein context содержит accession, match confidence, function, domains,
  GO terms;
- Gemini payload не дублирует текущий prompt;
- извлечение текста из Gemini/OpenAI ответов работает;
- пустые ответы от provider превращаются в понятные ошибки.

Зачем это нужно:

- чтобы follow-up LLM всегда получал правильный контекст;
- чтобы смена provider не ломала flow;
- чтобы ошибки модели были понятны пользователю и разработчику.

### `backend/app_services/test_chat_llm_providers.py`

Проверяет адаптеры реальных provider-ов, но без реальной сети и без ключей.

Что проверяется:

- Gemini proxy adapter собирает правильный HTTP payload;
- Gemini proxy adapter требует URL и token;
- OpenAI adapter создаёт `ChatOpenAI` с нужной model/temperature/timeout;
- OpenAI adapter передаёт в модель system prompt и protein context.

Зачем это нужно:

- мы не проверяем качество ответа модели;
- мы проверяем, что в provider уйдёт правильный запрос.

### `backend/app_services/test_bioseq_chat_service.py`

Проверяет основной backend service `BioSeqChatService`.

Что проверяется:

- первый sequence turn обновляет agent state и возвращает candidates;
- protein card sections раскрываются правильными frontend-ключами, включая
  `pathways`;
- follow-up turn с `turn_count > 0` идёт в Chat LLM, а не в retriever;
- на follow-up возвращается `update_card=False`;
- выбранный candidate передаётся в LLM context;
- ошибка Chat LLM не затирает protein card.

Зачем это нужно:

- это главный контракт LLM flow: follow-up вопрос должен отвечаться по текущей
  карточке и не должен перерисовывать правый блок.

### `backend/app_services/test_retriever_pipeline.py`

Проверяет deterministic часть retriever pipeline.

Что проверяется:

- raw protein sequence классифицируется как protein;
- DNA sequence классифицируется как DNA;
- DNA translation останавливается на stop codon;
- плохая длина DNA даёт controlled miss;
- выключенный runtime retriever не пытается грузить тяжёлый backend;
- fake runtime result мапится в `CandidateView`.

Зачем это нужно:

- быстрые unit-тесты должны ловить ошибки до того, как мы дойдём до тяжёлого
  FAISS/ProtT5/search-service слоя.

### `backend/agents_core/test_runtime_agent.py`

Проверяет LangGraph session agent на in-memory persistence.

Что проверяется:

- разные `session_id` хранят отдельное состояние;
- active accession синхронизируется в session repository;
- agent не смешивает две пользовательские сессии.

Зачем это нужно:

- история и выбранный белок должны быть привязаны к конкретной chat session.

### `frontend/test_chat_pipeline.py`

Проверяет frontend orchestration в `chat_pipeline.py` без настоящего Streamlit.

Что проверяется:

- `_build_ui_context()` берёт именно выбранный candidate;
- follow-up turn сохраняет существующие candidates из `st.session_state`;
- follow-up turn сохраняет `card_sections_revealed`;
- `save_turn()` вызывается с `update_candidates=False`;
- backend получает selected candidate в `ui_context`.

Зачем это нужно:

- если пользователь выбрал второй/третий match, LLM должен получить именно его,
  а не всегда первый candidate.

### `frontend/test_session_db_adapter.py`

Проверяет сохранение turn-ов в session storage через fake in-memory repo.

Что проверяется:

- retriever turn сохраняет candidates, revealed sections, active accession,
  query protein sequence;
- follow-up turn не перетирает `last_candidates`;
- follow-up turn не перетирает `active_accession`;
- follow-up turn добавляет user/assistant messages и увеличивает `turn_count`.

Зачем это нужно:

- это защита от самого неприятного UX-багa: пользователь задал follow-up
  вопрос, а карточка справа очистилась или сменилась.

## Как читать результат

Успешный запуск выглядит примерно так:

```text
23 passed
```

Warning от `langchain_core` про Python 3.14 и Pydantic v1 сейчас не является
ошибкой тестов. Он приходит из зависимости.

Если тест падает, используйте:

```bash
python3 -m pytest tests/unit -vv -s --tb=long
```

Так будет видно больше контекста: какой тест упал, на каком assert, и какие
данные были внутри.

# BioSeq Investigator — как это работает

Документ-проводник по проекту. Цель — чтобы человек, не писавший на Python, мог прочитать это сверху вниз и точно понимать, что происходит, когда пользователь вставляет последовательность в браузере, до какого метода она долетает, что возвращается обратно и почему сейчас нужны два терминала.

Все названия методов и файлов — кликабельные. Жми, попадёшь в нужное место в коде.

---

## 1. Что вообще делает проект

Пользователь приходит на веб-страничку. В большом чат-инпуте вставляет последовательность аминокислот (или ДНК, но с примечанием, что её сначала транслируют). Может дописать что-то вроде «найди что-то про глюкозу». Жмёт Enter.

Через ~10–30 секунд справа от чата появляется **карточка белка** — UniProt-выписка по самому похожему белку из базы. Слева в чате — ассистент пишет, что нашёл, со списком топ-5 кандидатов.

Дальше пользователь может задавать **уточняющие вопросы** в том же чате: «что это за домены?», «с чем взаимодействует?», «при каких болезнях встречается?». Эти вопросы уже не запускают повторный поиск — отвечает LLM, опираясь на уже найденную карточку.

История чатов копится в сайдбаре слева; по клику на старую сессию она восстанавливается полностью.

---

## 2. Из чего проект состоит физически

Три участника процесса. Внутри одного компьютера это три отдельных процесса, у каждого своя ответственность.

| # | Имя           | Что это                            | Где живёт                                | Порт          |
|---|---------------|------------------------------------|------------------------------------------|---------------|
| 1 | Streamlit UI  | Веб-страница, чат, карточка        | `streamlit run app/frontend/app.py`      | 8501 (обычно) |
| 2 | Search-сервис | ProtT5 + FAISS, поиск по эмбеддингам| `python -m services.search_service`     | 8002          |
| 3 | LLM прокси    | Прокси к Gemini для follow-up чата | Cloudflare (внешний)                      | —             |

Дополнительно есть внешние HTTP-зависимости, которые не запускаются нами вообще:

- **UniProt REST API** (`https://rest.uniprot.org`) — берём оттуда полные описания белков по их accession-кодам. Публичный, ходим напрямую.
- **Supabase** (PostgreSQL) — туда сохраняется история сессий. Адрес и креды через переменную `SUPABASE_DB_URL` в `.env`. Если её нет, история не сохраняется, но всё остальное работает.
- **HuggingFace Hub** — оттуда search-сервис первый раз скачивает веса ProtT5 (`Rostlab/prot_t5_xl_uniref50`, ~2 GB). После первого раза лежит в кэше `~/.cache/huggingface/`.

---

## 3. Что должно крутиться, чтобы это вообще работало

### Локально, на твоей машине, прямо сейчас

**Два терминала, оба с активным `.venv` проекта:**

```powershell
# Терминал A — фронт
streamlit run app/frontend/app.py

# Терминал B — поисковый движок
cd bioseq_retriever
python -m services.search_service
```

Закрыл терминал B — Streamlit будет жить, но любой первый запрос упадёт с `ConnectionError: localhost:8002`. Закрыл терминал A — фронт пропал, движок крутится впустую.

### Что должно быть в файлах данных

- `bioseq_retriever/data/per-protein.h5` — 1.4 GB, набор эмбеддингов ProtT5 для ~196 тысяч белков. Скачивается заранее (см. [data_prep/](data_prep) или просто кладётся вручную). **Без него сервис не стартанёт.**
- `bioseq_retriever/data/per-protein.index` — бинарный FAISS-индекс. **Не существует при первом запуске** — сервис построит его сам из `.h5` за пару минут и сохранит на диск.
- `bioseq_retriever/data/per-protein.accessions.json` — JSON-список UniProt-аксессов в порядке, в котором они лежат в индексе. Тоже автогенерируется при первом запуске.

### Что должно быть в `.env`

В корне проекта файл `.env` (пример — [example.env.txt](example.env.txt)):

```
MISTRAL_API_KEY=...           # для LLM-шагов внутри ретривера
SUPABASE_DB_URL=postgres://...# опционально, для истории сессий
BIOSEQ_LLM_PROXY_URL=...      # для follow-up чата (Gemini через Cloudflare)
BIOSEQ_LLM_PROXY_TOKEN=...
APP_PASSWORD=...              # опционально, парольный гейт перед страницей
```

Если `MISTRAL_API_KEY` нет, ретривер откажется работать и пользователь увидит в чате честную плашку. Если нет `BIOSEQ_LLM_PROXY_URL` — follow-up вопросы будут падать.

### В будущем

Search-сервис разработчик, по идее, выложит на удалённый сервер (HF Spaces или подобное). Когда это случится, локально его поднимать не нужно — достаточно в `.env` написать:

```
BIOSEQ_SEARCH_SERVICE_URL=https://search.bioseq.example.com
```

И всё. Останется один терминал — со Streamlit.

---

## 4. Карта репозитория

```
bio_seq_project/
├── app/
│   ├── frontend/                     ← Streamlit-фронт (терминал A)
│   │   ├── app.py                    ← точка входа, рисует страницу
│   │   ├── chat_pipeline.py          ← диспетчер: первый ход vs follow-up
│   │   ├── embeddings_pipeline.py    ← адаптер к ретриверу для первого хода
│   │   ├── chat_llm_pipeline.py      ← адаптер к Gemini для follow-up
│   │   ├── session_db_adapter.py     ← запись/чтение истории в Supabase
│   │   ├── session_identity.py       ← cookie user_id + per-tab session_id
│   │   ├── backend_adapter.py        ← (легаси, прямой вызов ретривера)
│   │   ├── components/               ← UI-блоки: чат, карточка, сайдбар
│   │   └── mock/                     ← демо-режим без бэкенда
│   └── backend/                       ← общие модели + Supabase-репозиторий
│
├── bioseq_retriever/                 ← Логика ретривера + search-сервис (терминал B)
│   ├── pipeline_interface.py         ← публичная точка входа
│   ├── src/                           ← LangGraph-пайплайн
│   │   ├── pipeline.py               ← граф: extract → rank → rerank
│   │   ├── search.py                 ← HTTP-клиент к search-сервису
│   │   ├── reranking.py              ← семантический реранк (LLM-эмбеддинги)
│   │   ├── data_fetcher.py           ← клиент UniProt REST
│   │   ├── api_client.py             ← HTTP с ретраями
│   │   ├── utils.py                  ← LLM, перевод ДНК→белок, FASTA-парсинг
│   │   └── config.py                 ← env-флаги, URL'ы сервисов
│   ├── services/                      ← Сам микросервис (тяжёлый)
│   │   ├── search_service.py         ← FastAPI: /search → ProtT5 + FAISS
│   │   └── config.py                 ← порт, путь к данным, гиперпараметры
│   └── data/                          ← .h5, .index, .accessions.json
│
├── data_prep/                         ← Скрипты подготовки .h5 (offline)
├── tests/                             ← Тесты ретривера
├── scripts/                           ← Eval-харнессы (L1/L2/L3)
├── requirements.txt                   ← Все зависимости одним списком
└── .venv/                             ← Локальная Python-среда
```

Из чего состоит **app/frontend**: тонкий слой Streamlit (рисует UI), плюс группа адаптеров — каждый из них переводит свой вызов в нужный язык (HTTP, БД, и т.д.).

Из чего состоит **bioseq_retriever**: два независимых блока. Внутри `src/` — оркестратор (LangGraph: разобрать ввод, классифицировать, искать, реранкнуть). Внутри `services/` — отдельный микросервис, который держит в памяти ProtT5 и FAISS-индекс. Ретривер ходит к сервису по HTTP, как обычный клиент.

---

## 5. Полный путь первого сообщения

Пользователь вставил в чат:

```
>sp|Q9NZT1|CALL5_HUMAN ...
MAGELTPEEEAQYKKAFSAVDTDGNGTINAQELGAALKATGKNLSEAQLRKLISEV...
Найди похожие белки.
```

Жмёт Enter. Что происходит:

### Шаг 1. Streamlit принимает ввод

Браузер шлёт POST на свой собственный Streamlit-процесс. Внутри [app.py](app/frontend/app.py) уже отрисованы две колонки. Левая (чат) рендерится через [chat.render()](app/frontend/components/chat.py). У неё есть `on_submit` callback — это функция [_handle_vector_db_submission()](app/frontend/app.py#L162) из app.py.

Внутри `_handle_vector_db_submission` происходит главное:

```python
import chat_pipeline
outcome = chat_pipeline.run_turn(text)
```

Всё остальное — обвязка: обновить state, нарисовать спиннер.

### Шаг 2. Диспетчер решает, какой это ход

[chat_pipeline.run_turn()](app/frontend/chat_pipeline.py#L39) — короткая функция-роутер. Она задаёт один вопрос: **это первое сообщение в этой сессии или уже не первое?**

Решение принимается через [_is_first_turn_in_session()](app/frontend/chat_pipeline.py#L72). Логика:

1. Берёт `session_id` из `st.session_state`.
2. Идёт в Supabase, читает строку `public.chat_sessions` по этому session_id.
3. Смотрит поле `working_memory.turn_count`. Если `0` или строки вообще нет — это первый ход.
4. Если БД недоступна или session_id отсутствует — тоже считаем первым ходом (graceful degradation).

Поскольку это первый ход — управление уходит в [embeddings_pipeline.run_turn_embeddings()](app/frontend/embeddings_pipeline.py#L154).

### Шаг 3. Адаптер фронта вызывает ретривер

Внутри [run_turn_embeddings()](app/frontend/embeddings_pipeline.py#L154):

1. **Собирает контекст** (`user_id`, `session_id`, `workspace_id`) для записи в БД — это просто dataclass с идентификаторами.

2. **Preflight** через [_preflight_check()](app/frontend/embeddings_pipeline.py). Проверяет, что установлены `torch`, `transformers`, `faiss`, `h5py` и что есть `MISTRAL_API_KEY` или `OPENAI_API_KEY` в окружении. Если что-то отсутствует — возвращает дружелюбную плашку прямо в чат и сохраняет это как ход с ошибкой в БД.

3. **Зовёт ретривер** через его публичный API:
   ```python
   from bioseq_retriever.pipeline_interface import run_pipeline_interface
   result = run_pipeline_interface(prompt)
   ```

### Шаг 4. Ретривер запускает LangGraph-пайплайн

[run_pipeline_interface()](bioseq_retriever/pipeline_interface.py#L9) делает буквально две вещи:

1. [setup_environment()](bioseq_retriever/src/utils.py#L60) — проверяет, что LLM-ключ в env вообще есть, иначе сразу `ValueError`.
2. `asyncio.run(run_bioseq_pipeline(prompt))` — запускает асинхронный пайплайн.

[run_bioseq_pipeline()](bioseq_retriever/src/pipeline.py#L177) собирает пустой стейт и запускает граф из [create_pipeline()](bioseq_retriever/src/pipeline.py#L153).

Граф — это направленный граф из узлов (нод), каждый — обычная Python-функция, принимающая state и возвращающая патч к нему. Узлы:

#### 4a. `extract` → [extract_and_classify_node()](bioseq_retriever/src/pipeline.py#L39)

Зовёт LLM (Mistral по умолчанию) с системным промптом «ты биоинформатик, разбери ввод». LLM возвращает структурированный JSON: что в инпуте — сама последовательность или путь к файлу, какой это тип (DNA / PROTEIN), уверена ли модель в классификации, и что было контекстом (типа «найди что-то про глюкозу»).

#### 4b. Условный переход

[should_resolve_filepath()](bioseq_retriever/src/pipeline.py#L143) смотрит на `input_type`:
- если `FILEPATH` → [resolve_filepath_node()](bioseq_retriever/src/pipeline.py#L79) (читает FASTA с диска, проверяет, что путь в разрешённой папке)
- если `SEQUENCE` → [use_raw_sequence_node()](bioseq_retriever/src/pipeline.py#L97) (просто чистит строку через [clean_sequence()](bioseq_retriever/src/utils.py#L11))

#### 4c. Условный переход

[should_translate()](bioseq_retriever/src/pipeline.py#L147) смотрит на `sequence_type`:
- если `DNA` → [translate_dna_node()](bioseq_retriever/src/pipeline.py#L104) (кодоны → аминокислоты через [translate_dna_to_protein()](bioseq_retriever/src/utils.py#L101))
- если `PROTEIN` → [pass_protein_node()](bioseq_retriever/src/pipeline.py#L113) (no-op)

После этого узла в стейте лежит чистая аминокислотная последовательность в поле `protein_sequence`.

#### 4d. `rank` → [rank_node()](bioseq_retriever/src/pipeline.py#L118)

Внутри одна строка:

```python
matches = search_top_k(state['protein_sequence'], k=50)
```

[search_top_k()](bioseq_retriever/src/search.py#L6) — **тонкий HTTP-клиент**. Делает POST на `http://localhost:8002/search` (URL из [SEARCH_SERVICE_URL](bioseq_retriever/src/config.py#L22)), тело — `{"sequence": "MAGELT...", "k": 50}`.

**Здесь мы выходим из ретривера и попадаем в search-сервис.** Об этом — Шаг 5.

Дальше `rank_node` получает массив пар `(accession, score)` от сервиса, и идёт в [get_uniprot_records()](bioseq_retriever/src/data_fetcher.py#L4) — это клиент UniProt REST API. Грубо: формирует запрос вида `accession:P12345 OR accession:Q67890 OR ...` и тянет полные JSON-описания этих 50 белков. Кладёт в `state["ranked_results"]`.

#### 4e. `rerank` → [rerank_node()](bioseq_retriever/src/pipeline.py#L128)

Создаёт [LocalReranker](bioseq_retriever/src/reranking.py#L28) и зовёт его метод [rerank_by_context()](bioseq_retriever/src/reranking.py#L48).

Это **второй слой ранжирования**, теперь уже семантический. Логика:

1. Каждая из 50 UniProt-записей описывается коротким текстом через [_format_record_for_reranking()](bioseq_retriever/src/reranking.py#L6) — собирает ген, организм, имя белка, FUNCTION-комментарии. Получается 50 строк.
2. Контекст пользователя («найди что-то про глюкозу») и эти 50 строк прогоняются через текстовый эмбеддер (Mistral Embeddings или OpenAI Embeddings — какой ключ есть).
3. Получаем 50 векторов и 1 вектор запроса. Считаем косинусную близость через FAISS `IndexFlatIP` (это уже мини-FAISS на 50 элементов, **в памяти, на лету** — не путать с большим индексом в search-сервисе).
4. Сортируем по близости, возвращаем top-5.

Этот шаг — про **смысловое соответствие**: первый этап (FAISS-поиск в сервисе) ищет белки **похожие структурно** (по последовательности), а реранк двигает наверх те, что **подходят по контексту запроса**.

После `rerank` граф заканчивается. В стейте — `final_results: list[dict]`, пять UniProt-записей.

`asyncio.run` возвращает этот стейт в `run_pipeline_interface`, тот — в `run_turn_embeddings`.

### Шаг 5. Что происходит внутри search-сервиса

[search_service.py](bioseq_retriever/services/search_service.py) — это FastAPI-приложение на `:8002`. На старте (один раз) он:

1. Загружает [ProtT5](bioseq_retriever/services/search_service.py#L33) с HuggingFace (или из локального кэша).
2. Зовёт [get_or_create_index()](bioseq_retriever/services/search_service.py#L75):
   - Если `data/per-protein.index` и `.accessions.json` уже на диске — просто читает их в память.
   - Если нет — строит индекс через [build_index()](bioseq_retriever/services/search_service.py#L56). Идёт по `.h5` пачками по 1000 эмбеддингов, нормализует L2 (для cosine similarity), добавляет в HNSW-индекс с параметрами `M=128, efConstruction=512`. Сохраняет результат на диск.

После старта в памяти живут: модель ProtT5, FAISS-индекс (`index`), массив `accessions` (порядок UniProt-кодов в индексе).

Когда прилетает POST `/search`:

```
{"sequence": "MAGELT...", "k": 50}
```

эндпоинт [search()](bioseq_retriever/services/search_service.py#L143) делает:

1. **Эмбеддинг запроса** — [_embed()](bioseq_retriever/services/search_service.py#L105). Токенизирует последовательность, прогоняет через ProtT5, делает `mean pooling` по позициям (один вектор на белок), нормализует. Получаем 1024-мерный вектор.
2. **Поиск** — [_perform_search()](bioseq_retriever/services/search_service.py#L121). Зовёт `index.search(query_vec, k=50)`. FAISS возвращает индексы и cosine-скоры.
3. Маппит индексы на UniProt-коды через массив `accessions`, формирует ответ:

```json
{"results": [{"accession": "Q9NZT1", "score": 0.987}, ...]}
```

Обе тяжёлые операции выполняются в `ThreadPoolExecutor`, чтобы не блокировать event loop FastAPI.

### Шаг 6. Адаптер форматирует результат для UI

Управление вернулось в [run_turn_embeddings()](app/frontend/embeddings_pipeline.py#L154). У него на руках:

- `result["final_results"]` — пять UniProt JSON-записей
- `result["protein_sequence"]` — нормализованная аминокислотная последовательность из запроса
- `result["sequence_type"]`, `result["is_confident"]` — мета от LLM-классификации

Дальше:

1. **UniProt JSON → UI Candidate.** Каждая запись прогоняется через [from_dict()](app/frontend/mock/protein_loader.py) (плоский маппинг полей UniProt в плоскую TypedDict, которую умеет рендерить компонент карточки). Получается `list[Candidate]`.

2. **Сообщение в чат** — [_assistant_message()](app/frontend/embeddings_pipeline.py) собирает markdown с «вот тип последовательности, вот сколько матчей, вот топ-5 со ссылками».

3. **Какие секции карточки раскрывать** — [_revealed_sections()](app/frontend/embeddings_pipeline.py) смотрит, какие поля заполнены в первом кандидате, и формирует множество имён секций («function», «expression», «domains», ...). Это идёт в UI, чтобы скрытые секции не рендерились пустыми.

4. **Сохранение хода в БД** — [_safe_save_turn()](app/frontend/embeddings_pipeline.py) вызывает [session_db_adapter.save_turn()](app/frontend/session_db_adapter.py). Туда пишется: текст юзера, текст ассистента, кандидаты, `turn_count += 1`, `current_mode = "embeddings_retriever"`. Если БД не подключена — тихо логируем варнинг.

5. **Возвращает наверх** dict с `reply`, `candidates`, `reveals`, `warnings`, и `update_card: True`.

### Шаг 7. Streamlit рисует результат

`_handle_vector_db_submission` обновляет `st.session_state`:

```python
st.session_state.candidates = outcome["candidates"]      # карточка справа
st.session_state.card_sections_revealed = outcome["reveals"]
st.session_state.query_protein_sequence = outcome.get("query_protein_sequence")
```

Возвращает `reply` в чат, который дописывает его в `messages`. Streamlit делает rerun страницы. Карточка справа теперь содержит белок Q9NZT1, секции раскрыты, в чате — список матчей.

Готово.

---

## 6. Полный путь второго и далее сообщений

Пользователь пишет: «А что у этого белка с кальмодулиновыми доменами?»

### Шаг 1. То же самое — до диспетчера

`app.py` → `chat.render()` → `_handle_vector_db_submission()` → `chat_pipeline.run_turn()`.

### Шаг 2. Диспетчер решает иначе

[_is_first_turn_in_session()](app/frontend/chat_pipeline.py#L72) идёт в Supabase, видит `turn_count = 1`, возвращает `False`.

Управление уходит в [chat_llm_pipeline.run_turn_chat_llm()](app/frontend/chat_llm_pipeline.py#L34). **Никакого поиска по эмбеддингам тут не происходит** — карточка справа остаётся той же.

### Шаг 3. Адаптер собирает запрос к Gemini

Внутри [run_turn_chat_llm()](app/frontend/chat_llm_pipeline.py#L34):

1. Берёт `BIOSEQ_LLM_PROXY_URL` и `BIOSEQ_LLM_PROXY_TOKEN` из env. Это адрес Cloudflare Worker, через который ходим в Gemini (чтобы не светить API-ключ в коде).

2. Собирает payload через [_build_gemini_contents()](app/frontend/chat_llm_pipeline.py#L136):
   - **Контекст белка** — [_get_current_protein_context()](app/frontend/chat_llm_pipeline.py#L167). Берёт выбранную карточку из `st.session_state.candidates`, выкачивает оттуда accession, имя, ген, функцию, ткани, домены, болезни, ... — формирует большой текстовый блок. Подсовывает его как первое user-сообщение в истории и сразу за ним model-сообщение «Ок, контекст принял».
   - **Историю чата** — последние 20 сообщений из `st.session_state.messages`, переведённые в формат Gemini API (`role: user|model, parts: [{text}]`).

3. Делает POST на прокси с заголовком `X-BioSeq-Token`.

4. Парсит ответ через [_extract_gemini_text()](app/frontend/chat_llm_pipeline.py) (там Gemini-specific JSON-структура).

### Шаг 4. Сохраняем и возвращаем

[session_db_adapter.save_turn()](app/frontend/session_db_adapter.py) пишет ход, но **с `update_candidates=False`**. То есть карточка в БД не перезаписывается — пользователь продолжает смотреть на того же белка.

Возвращаем dict с `update_card: False`.

### Шаг 5. UI

`_handle_vector_db_submission` видит `update_card=False` и **не трогает** `st.session_state.candidates`. Только дорисовывает ответ ассистента в чат.

Карточка справа осталась прежней, в чате — текст ответа Gemini, в БД — ещё один ход с `current_mode = "chat_llm"`.

---

## 7. Где данные физически живут

### На диске

| Где                                           | Что                          | Кто пишет             |
|-----------------------------------------------|------------------------------|------------------------|
| `bioseq_retriever/data/per-protein.h5`        | Эмбеддинги ProtT5, 1.4 GB    | Подготовлено заранее   |
| `bioseq_retriever/data/per-protein.index`     | FAISS HNSW-индекс            | search-сервис, 1 раз   |
| `bioseq_retriever/data/per-protein.accessions.json` | Порядок аксессов       | search-сервис, 1 раз   |
| `~/.cache/huggingface/`                       | Веса ProtT5, ~2 GB           | transformers lib       |

### В оперативке search-сервиса (всё время, пока крутится)

- ProtT5-модель (~1–2 GB в зависимости от dtype)
- FAISS-индекс (~1 GB)
- Массив `accessions: list[str]` на ~196k элементов

Итого порядка 3–5 GB RAM, поэтому держать сервис «вторым процессом на ноутбуке» — нормально, но не на слабой машине.

### В Supabase (если подключён)

Одна таблица `public.chat_sessions`, ключ — `session_id`. В каждой строке:
- `messages` — JSON-массив всей переписки
- `proteins` — топ-кандидаты, найденные ретривером
- `working_memory` — служебный JSON: `turn_count`, `current_mode`, `last_candidates`, `last_query_protein_sequence`
- метаданные (`user_id`, `created_at`, `updated_at`)

### Идентичность пользователя в браузере

[session_identity.bootstrap_identity()](app/frontend/session_identity.py) при первом заходе ставит две cookie:
- `bioseq_user_id` — на 1 год, чтобы узнать пользователя при возвращении
- `bioseq_session_id` — на 7 дней, чтобы рестор сессии при ребуте Streamlit

Никакой регистрации/логина — это просто стабильные ID для группировки истории.

---

## 8. Что сейчас локально, а что должно уехать на сервер

Текущий setup — это **«всё на твоём ноутбуке»**, кроме UniProt, Supabase и Gemini-прокси.

| Что               | Сейчас        | В продакшене                                  |
|-------------------|---------------|-----------------------------------------------|
| Streamlit         | localhost     | HF Spaces (см. [PRD/](PRD/Merge%20plan/Research.md))|
| Search-сервис     | localhost:8002| Удалённый хост, `BIOSEQ_SEARCH_SERVICE_URL`   |
| FAISS-индекс      | 1.4 GB на диске | Грузится туда же, где сервис                |
| ProtT5            | в RAM локально | в RAM сервиса                                |
| LLM (Mistral)     | через API     | через API                                     |
| LLM (Gemini)      | через прокси  | через прокси                                  |
| Supabase          | облако        | облако                                        |
| UniProt           | публичный API | публичный API                                 |

То есть **архитектурно** проект уже готов к удалённому деплою (благодаря разделению на сервисы). Просто пока ProtT5+FAISS никто не поднял на отдельной машине.

Это объясняет, почему сейчас два терминала: ты вручную имитируешь то, что в проде будет «отдельный сервис на отдельной машине, который кто-то уже запустил».

---

## 9. Какие ошибки бывают и где их искать

### `Could not initialize embeddings backend: No module named 'bioseq_retriever.src.embeddings'`

Появлялась до фикса. Источник — старый код [embeddings_pipeline.py](app/frontend/embeddings_pipeline.py) пытался вручную импортировать удалённый модуль (рефакторинг ретривера, коммит `65f5049`). Лечится тем, что фронт зовёт публичную точку входа [run_pipeline_interface()](bioseq_retriever/pipeline_interface.py#L9), а не лезет внутрь.

### `Embeddings pipeline error: Ranking failed: Failed to execute POST request to http://localhost:8002/search after 5 attempts.`

Значит, search-сервис не запущен или умер. Поднять [services/search_service.py](bioseq_retriever/services/search_service.py) в отдельном терминале. Ретрай-логика — в [api_client.py](bioseq_retriever/src/api_client.py#L16): 5 попыток с экспоненциальной задержкой; если все упали — сдаётся.

### `No LLM credentials available for the contextual reranker.`

`MISTRAL_API_KEY` (или `OPENAI_API_KEY`) отсутствует в `.env`. Проверяется в [_preflight_check()](app/frontend/embeddings_pipeline.py) **до** запуска ретривера, поэтому появляется сразу после Enter.

### `Embeddings H5 file not found at ...`

Запустился search-сервис, но `bioseq_retriever/data/per-protein.h5` отсутствует. Файл надо скачать (см. `data_prep/`) и положить по адресу.

### `Chat LLM error: BIOSEQ_LLM_PROXY_URL is not set.`

Follow-up чат не настроен — нужны `BIOSEQ_LLM_PROXY_URL` и `BIOSEQ_LLM_PROXY_TOKEN` в `.env`. Первый ход при этом работает (он не использует Gemini), а вот второй и далее — нет.

### Connection error при сохранении в БД

`SUPABASE_DB_URL` не задан или Supabase недоступен. Это **не блокирует** работу — поиск всё равно отработает, просто история не сохранится. Варнинг попадёт в `st.session_state.backend_warnings` (видно справа от текста ассистента).

---

## 10. Минимальный чек-лист «запустить с нуля»

1. **Зависимости:** `pip install -r requirements.txt` в `.venv`. Плюс `pip install fastapi` если ещё не стоит (он не в requirements).
2. **Данные:** убедиться, что `bioseq_retriever/data/per-protein.h5` лежит на месте (1.4 GB).
3. **`.env`:** скопировать [example.env.txt](example.env.txt) в `.env`, заполнить `MISTRAL_API_KEY`. Опционально — Supabase и LLM-прокси.
4. **Терминал A:** `streamlit run app/frontend/app.py` → откроется на `http://localhost:8501`.
5. **Терминал B:** `cd bioseq_retriever && python -m services.search_service`. Первый запуск — несколько минут (качается ProtT5, строится индекс). Финальное «`Uvicorn running on http://0.0.0.0:8002`» — сигнал, что готово.
6. В браузере вставить FASTA или просто аминокислотную строку, Enter.
7. Через 10–30 секунд справа появится карточка, в чате — топ-5 матчей.
8. Дальше можно задавать вопросы по карточке — пойдут через Gemini-прокси.

Всё.

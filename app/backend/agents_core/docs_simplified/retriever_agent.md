# Retriever Agent простыми словами

Этот документ объясняет `app/backend/agents_core/retriever_agent`.

Главная задача этого агента:

```text
Получить от пользователя биологическую последовательность,
понять что это,
найти ее в подготовленном Neo4j graph,
вернуть похожие белки,
сохранить состояние сессии.
```

## Очень коротко

Пользователь может написать что-то вроде:

```text
MALWMRLLPLLALLALWGPDPAAA...
I am looking for sequences involved in glucose metabolism.
```

Агент должен:

1. отделить sequence от обычного текста;
2. понять, это DNA или protein;
3. если это DNA, перевести ее в protein;
4. найти такой protein в Neo4j по hash;
5. найти похожих соседей;
6. учесть контекст `"glucose metabolism"`;
7. вернуть top candidates;
8. записать в память, что было найдено.

## Где лежит код

| Файл | Простое объяснение |
| --- | --- |
| `agent.py` | Сам агент: state, шаги LangGraph, сохранение результатов. |
| `llm.py` | Как выбрать OpenAI/Mistral и модель для извлечения sequence из текста. |
| `main.py` | CLI-запуск агента из терминала. |
| `pipeline_interface.py` | Удобная функция для запуска pipeline из Python. |
| `__init__.py` | Экспорт основных классов наружу. |

## Агент - это wrapper вокруг LangGraph

Главный класс:

```python
BioSeqRetrieverGraphAgent
```

Он не делает всю работу сам прямо в одном методе.

Он:

1. получает зависимости;
2. создает LangGraph pipeline;
3. запускает pipeline;
4. после запуска сохраняет состояние.

Создание выглядит так:

```python
agent = BioSeqRetrieverGraphAgent(
    graph_retrieval=GraphRetrievalService(client),
    persistence=persistence,
    llm_factory=llm_factory,
    use_llm_extractor=True,
)
```

Что это значит:

| Аргумент | Зачем нужен |
| --- | --- |
| `graph_retrieval` | Умеет искать белки и кандидатов в Neo4j. |
| `persistence` | Умеет сохранять state и session snapshot. |
| `llm_factory` | Создает LLM для извлечения sequence из текста. |
| `use_llm_extractor` | Включает или выключает LLM extraction. |

## Public methods

У агента есть несколько важных методов.

### `invoke(prompt, context)`

Это главный запуск.

```python
result, current_state = agent.invoke(prompt, context)
```

Он делает:

1. берет `context.session_id`;
2. делает из него LangGraph `thread_id`;
3. запускает pipeline;
4. добавляет assistant message с коротким результатом;
5. получает полный current state;
6. строит compact session patch;
7. записывает patch в `public.chat_sessions`;
8. возвращает результат.

### `get_current_state(context)`

Получает сохраненный LangGraph state для этой сессии.

Просто говоря:

```text
Покажи, что агент помнит про session_id.
```

### `update_current_state(context, patch)`

Ручное обновление state.

Например, пользователь выбрал конкретный accession в UI.
Тогда service layer может сделать:

```python
agent.update_current_state(context, {"active_accession": "P12345"})
```

После этого агент тоже синхронизирует `chat_sessions`.

### `get_message_history(context)`

Возвращает историю сообщений в простом формате:

```json
[
  {"role": "human", "content": "..."},
  {"role": "ai", "content": "..."}
]
```

## Что такое GraphState

`GraphState` - это внутренняя "рабочая папка" агента.

Каждый шаг pipeline берет эту папку, читает из нее данные и докладывает туда новые данные.

Поля:

| Поле | Что значит простыми словами |
| --- | --- |
| `messages` | История сообщений. |
| `prompt` | Исходный текст пользователя. |
| `sequence_or_path` | То, что агент извлек: sequence или filepath. |
| `input_type` | Что именно нашли: `SEQUENCE` или `FILEPATH`. |
| `context` | Обычный текст пользователя без sequence. |
| `sequence` | Очищенная sequence. |
| `sequence_type` | `DNA` или `PROTEIN`. |
| `protein_sequence` | Белковая sequence. Если вход был DNA, сюда кладется результат перевода. |
| `is_confident` | Насколько extractor уверен в классификации. |
| `ranked_results` | Первые результаты из графа. |
| `final_results` | Финальный top после rerank. |
| `error` | Ошибка, если что-то пошло не так. |

Важная деталь:

```python
messages: Annotated[list[Any], add_messages]
```

Это значит, что сообщения не перетираются, а добавляются в список.

## Как идет pipeline

Pipeline выглядит так:

```text
extract
  -> resolve_file  -> translate/pass_protein -> rank -> rerank -> END
  -> use_raw       -> translate/pass_protein -> rank -> rerank -> END
```

Теперь разберем каждый шаг.

## Шаг 1: `extract`

Задача:

```text
Понять, что пользователь написал.
```

Например, пользователь пишет:

```text
MALWMRLLPLLALLALWGPDPAAA...
Find insulin-like proteins.
```

`extract` должен выделить:

| Что | Пример |
| --- | --- |
| sequence | `MALWMRLLPLLALLALWGPDPAAA...` |
| context | `Find insulin-like proteins.` |
| input type | `SEQUENCE` |
| sequence type | `PROTEIN` |
| confidence | `true` или `false` |

Есть два способа extraction.

### LLM extraction

Если включен LLM extractor, агент использует OpenAI или Mistral.

LLM получает system prompt и должен вернуть structured output:

```python
InputExtraction
```

Это удобно, потому что LLM может лучше понять сложный текст.

### Deterministic extraction

Если LLM выключен, используется обычная логика на regex/правилах:

- найти FASTA;
- найти filepath;
- найти длинный sequence-like token;
- посмотреть alphabet;
- понять DNA это или protein;
- учесть подсказки вроде `protein`, `DNA`, `gene`, `peptide`.

Эта логика живет в:

```text
app/backend/app_services/retriever_pipeline.py
```

## Шаг 2: выбрать ветку

После `extract` агент смотрит:

```python
input_type == "FILEPATH" ?
```

Если да:

```text
extract -> resolve_file
```

Если нет:

```text
extract -> use_raw
```

## Шаг 3A: `resolve_file`

Сейчас runtime file resolution отключен.

Это значит:

```text
Пользователь не может просто дать путь к файлу,
чтобы агент во время запроса прочитал файл и обработал sequence.
```

Почему:

- текущий режим DB-only;
- данные должны быть заранее загружены в Neo4j;
- runtime file search не реализован.

Поэтому node возвращает controlled error:

```text
File resolution failed: Runtime file path resolution is disabled in DB-only graph mode.
```

## Шаг 3B: `use_raw`

Если вход - обычная sequence, ее надо очистить.

Например:

- убрать FASTA header;
- убрать переносы строк;
- привести к upper case;
- убрать пробелы;
- убрать `-` и `*`.

После этого в state появляется:

```python
sequence
```

## Шаг 4: DNA или protein

После `resolve_file` или `use_raw` агент смотрит:

```python
sequence_type == "DNA" ?
```

Если DNA:

```text
translate
```

Если protein:

```text
pass_protein
```

## Шаг 5A: `translate`

Если вход был DNA, агент переводит DNA в protein sequence.

Пример:

```text
ATGGCC...
```

становится чем-то вроде:

```text
MA...
```

Если длина DNA не делится на 3, будет ошибка:

```text
Translation failed: The coding sequence (CDS) length must be divisible by 3
```

## Шаг 5B: `pass_protein`

Если вход уже protein, ничего переводить не надо.

Агент просто нормализует protein sequence и кладет ее в:

```python
protein_sequence
```

## Шаг 6: `rank`

Это главный поиск в Neo4j.

Агент не ищет "похожую sequence" на лету.
Он ищет exact match по hash.

Примерно так:

```text
protein_sequence
  -> normalize
  -> sha256 hash
  -> поиск Protein с таким sequence_hash в Neo4j
```

Для DNA путь чуть сложнее:

```text
raw DNA sequence hash
  -> найти Sequence node
  -> через ENCODES или TRANSLATES_TO найти Protein
```

Если protein найден:

```text
найти похожих соседей по SIMILAR_TO
```

Если protein не найден:

```text
Ranking failed: Sequence is outside the prepared graph dataset; runtime ProtT5/FAISS search is disabled.
```

Это не "краш", а ожидаемый controlled miss.

## Шаг 7: `rerank`

На этом шаге агент учитывает текстовый контекст пользователя.

Например:

```text
Find proteins related to glucose metabolism.
```

`GraphRetrievalService` берет кандидатов и считает простой lexical context score:

- какие слова есть в query;
- какие слова есть в описании белка;
- сколько пересечений.

Это не LLM rerank.
Это простая лексическая сортировка.

Target protein остается первым.
Соседи сортируются с учетом:

- context score;
- similarity score;
- rank.

## Где именно используется Neo4j

Агент сам напрямую не пишет Cypher.

Он вызывает:

```python
GraphRetrievalService
```

Этот сервис лежит в:

```text
app/backend/app_services/graph_retrieval.py
```

Важные методы:

| Метод | Что делает |
| --- | --- |
| `find_by_sequence_hash` | Ищет protein по hash protein sequence. |
| `find_encoded_protein_by_sequence_hash` | Для DNA ищет protein, который она кодирует. |
| `retrieve_candidates` | Берет target protein и похожих соседей. |
| `get_protein_view` | Достает одну карточку protein. |
| `resolve_input` | Ищет protein по accession/gene/name. В retriever node почти не используется, но используется сервисом чата. |

## Что возвращается в результате

После успешного запуска в `final_results` лежат кандидаты.

Обычно каждый кандидат похож на:

```json
{
  "protein": {
    "accession": "...",
    "name": "...",
    "gene": "...",
    "organism_scientific": "...",
    "function_text": "..."
  },
  "match_score": 1.0,
  "rank": 0,
  "similarity_score": 1.0,
  "context_score": 0.5,
  "evidence": []
}
```

Это данные, которые потом удобно показывать в UI.

## Что записывается в память

После `invoke` агент пишет два вида данных.

### Полный LangGraph state

Сохраняется автоматически через:

```python
workflow.compile(checkpointer=persistence.checkpointer)
```

и:

```python
config = {"configurable": {"thread_id": context.session_id}}
```

То есть:

```text
session_id пользователя = thread_id LangGraph
```

Если включен Supabase/Postgres, state сохраняется в Postgres-таблицах LangGraph.
Если нет, state живет только в памяти процесса.

### Compact session patch

Агент отдельно собирает короткую запись для приложения.

Она пишется в:

```text
public.chat_sessions
```

Туда попадает:

| Поле | Простое объяснение |
| --- | --- |
| `session_summary` | Короткий итог: что нашлось. |
| `proteins` | Найденный top protein. |
| `sequences` | Входная sequence. |
| `working_memory.last_retriever_state` | Короткая техническая сводка последнего запуска. |
| `active_sequence_id` | Id текущей sequence. |
| `active_accession` | Текущий accession. |
| `working_set_ids` | Id объектов, с которыми сейчас работает пользователь. |
| `current_mode` | `bioseq_retriever_langgraph`. |
| `last_tool_results_summary` | Короткий итог результата. |

## Как запустить из CLI

Пример:

```bash
python -m backend.agents_core.retriever_agent.main --message "MALWMRLLPLLALLALWGPDPAAA..."
```

Полезные flags:

| Flag | Что делает |
| --- | --- |
| `--message` | Сообщение пользователя. |
| `--deterministic-extractor` | Не использовать LLM, а использовать правила. |
| `--dump-history` | Показать историю сообщений сессии. |
| `--session-id` | Явно указать session id. |
| `--user-id` | Явно указать user id. |
| `--supabase-db-url` | Явно передать Postgres/Supabase URL. |
| `--provider` | Выбрать `openai` или `mistral`. |
| `--model` | Выбрать модель extractor-а. |

## Что делает pipeline_interface.py

`pipeline_interface.py` - это простой helper для запуска из Python.

Главная функция:

```python
run_pipeline_interface(user_prompt)
```

Она:

1. создает agent через factory;
2. создает `AppContext` из env-переменных;
3. запускает `agent.invoke`;
4. печатает краткую информацию;
5. возвращает result.

LLM extraction включается только если:

```text
BIOSEQ_INPUT_EXTRACTOR=llm
```

## Главные ограничения

Важно понимать эти ограничения, чтобы не искать баг там, где поведение ожидаемое.

| Ограничение | Что это значит |
| --- | --- |
| Runtime filepath отключен | Нельзя дать агенту путь к файлу и ожидать чтение файла во время запроса. |
| Runtime FAISS/ProtT5 отключены | Агент не ищет новую похожую sequence на лету. |
| Нужен prepared graph | Sequence должна уже быть в Neo4j. |
| Store почти не используется | `persistence.store` создается, но retriever agent напрямую его не читает и не пишет. |
| `chat_sessions` надо создать отдельно | LangGraph создает свои таблицы, но application table `public.chat_sessions` должна существовать заранее. |

## Простая ментальная модель

Можно представить agent как конвейер:

```text
сырой текст
  -> выделить sequence
  -> понять DNA/protein
  -> получить protein sequence
  -> найти exact protein в graph
  -> взять похожих соседей
  -> отсортировать по контексту
  -> сохранить state
```

Если на любом шаге понятно, что продолжать нельзя, агент кладет текст ошибки в `error` и спокойно завершает run.


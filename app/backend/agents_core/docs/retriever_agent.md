# Retriever Agent

`app/backend/agents_core/retriever_agent` - это LangGraph port старого `BioSeqRetrieverPipeline`, но в режиме graph-first / DB-only. Агент не запускает runtime embeddings, ProtT5 или FAISS. Он работает только по данным, заранее загруженным в Neo4j.

## Состав пакета

| Файл | Назначение |
| --- | --- |
| `agent.py` | LangGraph state, nodes, routing, public agent wrapper, session patch logic. |
| `llm.py` | Выбор provider/model и factory для structured extraction LLM. |
| `main.py` | CLI entrypoint для локального запуска агента. |
| `pipeline_interface.py` | Упрощенный interface/helper для запуска retriever graph pipeline из Python. |
| `__init__.py` | Экспорт `BioSeqRetrieverGraphAgent`, `GraphState`, `InputExtraction`, `create_pipeline`. |

## Public API

Основной класс:

```python
agent = BioSeqRetrieverGraphAgent(
    graph_retrieval=GraphRetrievalService(client),
    persistence=persistence,
    llm_factory=llm_factory,
    use_llm_extractor=True,
)

result, current_state = agent.invoke(prompt, context)
```

Методы:

| Метод | Что делает |
| --- | --- |
| `invoke(prompt, context)` | Запускает pipeline, добавляет assistant summary message, сохраняет compact session patch. |
| `get_current_state(context)` | Возвращает LangGraph state по `context.session_id`. |
| `update_current_state(context, patch)` | Патчит LangGraph state и синхронизирует `chat_sessions`. |
| `get_message_history(context)` | Возвращает serialized messages текущей сессии. |
| `warnings` | Warnings от persistence layer. |
| `persistence_mode` | `postgres` или `memory`. |

## GraphState

`GraphState` хранит runtime state одного thread:

| Поле | Значение |
| --- | --- |
| `messages` | История сообщений. Reducer `add_messages`, то есть сообщения добавляются, а не перетираются. |
| `prompt` | Исходный user prompt текущего запуска. |
| `sequence_or_path` | Извлеченная последовательность или filepath. |
| `input_type` | `SEQUENCE` или `FILEPATH`. |
| `context` | Оставшийся пользовательский контекст после извлечения sequence/path. |
| `sequence` | Нормализованная raw sequence. |
| `sequence_type` | `DNA` или `PROTEIN`. |
| `protein_sequence` | Белковая последовательность: translated DNA или normalized protein. |
| `is_confident` | Confidence extractor-а. |
| `ranked_results` | Кандидаты после первого graph retrieval. |
| `final_results` | Top candidates после context-aware rerank. |
| `error` | Текст контролируемой ошибки. |

## Pipeline flow

Граф создается в `create_pipeline(...)`:

```text
extract
  -> resolve_file  -> translate/pass_protein -> anchor -> rank -> rerank -> END
  -> use_raw       -> translate/pass_protein -> anchor -> rank -> rerank -> END
```

Узлы:

| Node | Логика |
| --- | --- |
| `extract` | Извлекает sequence/path, context, sequence type и confidence. |
| `resolve_file` | Сейчас всегда возвращает controlled error: runtime file resolution отключен. |
| `use_raw` | Нормализует raw sequence/FASTA через `use_raw_sequence`. |
| `translate` | Переводит DNA/CDS в protein sequence. |
| `pass_protein` | Нормализует protein sequence. |
| `anchor` | **Fallback**: использует MinHash-сервис для поиска ближайшего accession в Neo4j, если нет exact hash hit. |
| `rank` | Ищет exact graph hit по hash, или использует `anchor` для поиска соседей, затем тянет neighbor candidates. |
| `rerank` | **Contextual Reranking**: использует `LLMReranker` для оценки relevance 50 кандидатов относительно контекста пользователя, возвращая 5 лучших. |

---
## Retrieval Lifecycle (Tiered)
1. **Hash Lookup (Tier 1)**: Exact sequence hash match against Neo4j.
2. **MinHash Anchor (Tier 2 Fallback)**: If no match, MinHash-based lookup (in-memory) finds closest graph node (accession).
3. **Graph Traversal (Tier 3)**: Fetches up to 50 neighbor candidates via `SIMILAR_TO` edges from the anchor.
4. **Contextual Reranking (Tier 4)**: Uses OpenAI `LLMReranker` to re-order 50 candidates by relevance to the query context.
---

Conditional routing:

- после `extract`: `FILEPATH` -> `resolve_file`, иначе -> `use_raw`;
- после `resolve_file`/`use_raw`: `DNA` -> `translate`, иначе -> `pass_protein`;
- если `error` уже выставлен, downstream узлы возвращают `{}` или short-circuit route.

## Extraction

Есть два режима:

1. LLM extractor:
   - `llm_factory` создает `ChatOpenAI` или `ChatMistralAI`;
   - используется `with_structured_output(InputExtraction)`;
   - prompt = `EXTRACTION_SYSTEM_PROMPT` из `app_services/retriever_pipeline.py`.

2. Deterministic extractor:
   - `deterministic_extract_and_classify(prompt)`;
   - ищет FASTA, filepath или sequence token;
   - классифицирует DNA/PROTEIN по alphabet, hints и file extension.

В `agent.py` при ошибке extraction агент пытается взять предыдущие extraction-поля из текущего LangGraph state через `_previous_extraction(state)`. Это дает возможность не падать полностью, если thread уже содержит валидный sequence context.

## Graph retrieval

Retriever agent использует `GraphRetrievalService` из `app/backend/app_services/graph_retrieval.py`.

Ключевые методы:

| Метод | Назначение |
| --- | --- |
| `find_by_sequence_hash(sequence)` | Ищет `Protein` по `sequence_hash`. |
| `find_encoded_protein_by_sequence_hash(raw_sequence, translated)` | Для DNA ищет `Sequence` -> `ENCODES`/`TRANSLATES_TO` -> `Protein`, fallback на translated protein hash. |
| `retrieve_candidates(accession, limit, neighbor_pool, context)` | Возвращает target protein + похожих соседей по `SIMILAR_TO`, затем lexical rerank по context. |
| `get_protein_view(accession)` | Достает один `ProteinView`. |
| `resolve_input(text)` | Поиск accession/gene/entry/protein name. Используется chat service fallback-ом, не самим retriever graph node. |

Если exact hash hit не найден, `rank` пишет controlled error:

```text
Ranking failed: Sequence is outside the prepared graph dataset; runtime ProtT5/FAISS search is disabled.
```

## Что и куда записывается

Есть два уровня записи.

### 1. LangGraph checkpointer

Граф компилируется так:

```python
workflow.compile(checkpointer=persistence.checkpointer)
```

Каждый вызов использует:

```python
config = {"configurable": {"thread_id": context.session_id}}
```

Поэтому полный `GraphState`, включая `messages`, `prompt`, extracted fields, results и error, сохраняется в LangGraph checkpoint storage для конкретного `session_id`.

Если `SUPABASE_DB_URL` задан, checkpointer - `PostgresSaver`; иначе - `InMemorySaver`.

### 2. `public.chat_sessions`

После `invoke` агент:

1. получает предыдущую compact session row через `session_repository.get_session(context.session_id)`;
2. получает полный state через `self._graph.get_state(config).values`;
3. строит patch через `_derive_session_patch(current_state)`;
4. merge-ит с прошлой строкой через `_merge_session_patch(...)`;
5. делает `upsert_session(context, session_patch)`.

`_derive_session_patch` пишет:

| Поле | Что попадает |
| --- | --- |
| `session_summary` | Краткий итог retriever run. |
| `proteins` | Top accession как `ProteinRecord`, если есть match. |
| `sequences` | Входная sequence как `SequenceRecord`, если есть sequence. |
| `working_memory.last_sync_source` | `bioseq_retriever_langgraph`. |
| `working_memory.last_retriever_state` | Compact state: prompt, context, counts, top accession, error. |
| `active_sequence_id` | `dna_<sha>` или `protein_<sha>`. |
| `active_accession` | Top matched accession. |
| `last_analysis_summary` | Тот же summary. |
| `working_set_ids` | accession + sequence id. |
| `current_mode` | `bioseq_retriever_langgraph`. |
| `last_tool_results_summary` | Тот же summary. |

Merge logic:

- `proteins` merge по `accession`;
- `sequences` merge по `sequence_id`;
- `working_memory` shallow merge;
- `working_set_ids` сохраняет уникальный tail до 40 items;
- active ids берутся из incoming, иначе из saved.

## CLI options

CLI entrypoint:

```bash
python -m backend.agents_core.retriever_agent.main --message "..."
```

Основные flags:

| Flag | Значение |
| --- | --- |
| `--message` | Prompt для агента. Обязателен, кроме `--dump-history`. |
| `--provider` | `openai` или `mistral`. |
| `--model` | Override модели extractor-а. |
| `--neo4j-profile` | `local` или `cloud`. |
| `--uri`, `--database`, `--user`, `--password` | Neo4j connection override. |
| `--insecure` | Включает `neo4j+ssc`/`bolt+ssc` fallback behavior. |
| `--user-id`, `--session-id`, `--workspace-id`, `--user-role` | Поля `AppContext`. |
| `--supabase-db-url` | Override `SUPABASE_DB_URL`. |
| `--deterministic-extractor` | Не использовать LLM, брать deterministic extraction. |
| `--dump-history` | Напечатать message history текущего `session_id` и выйти. |

## Pipeline interface

`pipeline_interface.py` дает helper:

```python
result = run_pipeline_interface(user_prompt)
```

Он:

- грузит env;
- создает agent через `create_bioseq_retriever_graph_agent`;
- строит `AppContext` из `APP_*`;
- запускает `agent.invoke`;
- возвращает финальный `GraphState`.

LLM extractor в этом interface включается только если:

```text
BIOSEQ_INPUT_EXTRACTOR=llm
```

Иначе используется deterministic extraction.

## Ограничения текущей реализации

- Runtime filepath resolution отключен.
- Runtime embedding/vector search отключен.
- Sequence должна уже существовать в prepared graph dataset по hash.
- `persistence.store` создается, но retriever agent сейчас напрямую его не использует.
- `PostgresSessionRepository` ожидает существующую таблицу `public.chat_sessions`; `create_persistence_resources` создает LangGraph checkpoint/store tables, но не создает эту application table.

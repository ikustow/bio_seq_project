# Research: как заранее подготовить БД, чтобы заменить runtime-логику `bioseq_retriever`

Дата: 2026-05-01

## Отчет

Если мы сознательно ограничиваем исследование закрытым набором белков/последовательностей, которые заранее загружены в БД, то логику `bioseq_retriever` можно почти полностью перенести из runtime в offline data preparation.

В таком режиме агент при старте не должен загружать локальную ProtT5-модель. Он должен:

1. распознать, какой уже известный объект из БД имеет в виду пользователь;
2. взять заранее рассчитанные embedding-соседства, metadata, disease-связи и context-rerank признаки из Neo4j;
3. вернуть результат через graph tools.

Главное условие: БД должна хранить не только `Protein` и `SIMILAR_TO`, а достаточно богатый precomputed слой, который покрывает все этапы текущего retriever-пайплайна: sequence identity, protein embedding similarity, top-k neighbors, UniProt metadata, functional text, disease annotations и признаки для contextual reranking.

## Что именно надо покрыть из текущего retriever

Текущий `bioseq_retriever` делает такие шаги:

1. `extract_and_classify_node`: извлекает sequence/path/context и определяет DNA или PROTEIN.
2. `resolve_filepath_node` / `use_raw_sequence_node`: получает sequence из FASTA/raw input.
3. `translate_dna_node` / `pass_protein_node`: переводит DNA в protein или оставляет protein.
4. `rank_node`: считает ProtT5 embedding для query protein и ищет top-50 в FAISS.
5. `rerank_node`: по user context делает semantic rerank top-50 через текстовые embeddings UniProt-описаний и возвращает top-5.

При закрытом датасете runtime-агенту не нужно делать шаг 4 через локальную модель. Вместо этого шаг 4 должен быть заранее материализован в БД:

- каждый допустимый query protein уже является `Protein` или `Sequence`;
- для каждого такого объекта заранее рассчитаны top-k похожих белков;
- similarity score хранится в ребре;
- accession/sequence hash позволяет быстро сопоставить вход с узлом.

Шаг 5 тоже можно частично или полностью перенести в БД, если заранее сохранить functional text и его embedding/категории.

## Текущая готовность `backend/graph_core`

Уже есть хороший фундамент:

- `proteins_annotated.parquet`: 21 851 белок.
- `knn_edges.parquet`: 261 156 рёбер похожести.
- `embeddings.npy`: 21 851 x 1024 float32.
- `embeddings_l2.npy`: 21 851 x 1024 float32.
- `embeddings_l2_pca256.npy`: 21 851 x 256 float32.
- `protein_diseases.parquet`: 290 protein-disease связей.
- `protein_disease_summary.parquet`: 284 белка с disease-аннотациями.

Сейчас в Neo4j импортируются:

- `(:Protein)`
- `(:Disease)`
- `(:Protein)-[:SIMILAR_TO {cosine_sim}]->(:Protein)`
- `(:Protein)-[:ASSOCIATED_WITH]->(:Disease)`

Этого достаточно для базового graph-neighbor поиска по известному accession, но недостаточно для максимального покрытия текущего retriever.

## Целевая тактика

### Runtime

Runtime-агент должен работать без локальной ProtT5-модели:

1. Пользователь подаёт accession, gene name, protein name, FASTA header, sequence id или sequence, которая уже есть в разрешённом наборе.
2. Агент нормализует вход и ищет объект в БД.
3. Если объект найден, агент получает:
   - top-50/top-100 похожих белков;
   - UniProt metadata;
   - disease/context слой;
   - precomputed rerank score или graph/text фильтры.
4. Агент формирует ответ, grounded in graph data.

### Offline preparation

Вся дорогая биоинформатика переносится в offline pipeline:

1. собрать разрешённый набор protein/DNA sequences;
2. для каждой sequence сохранить canonical protein sequence и hash;
3. заранее посчитать embeddings тем же ProtT5, что использует retriever;
4. построить top-k similarity graph с k >= 50, лучше k=100;
5. импортировать metadata, functional comments, disease annotations;
6. при необходимости посчитать text embeddings для contextual reranking;
7. импортировать всё в Neo4j.

## Что изменить в БД

### 1. Расширить `Protein`

Сейчас `Protein` хранит accession, gene, name, organism и базовые UniProt поля. Для покрытия retriever нужно добавить:

```text
Protein {
  row_id,
  accession,
  dataset,
  entry_name,
  protein_name,
  gene_primary,
  organism_name,
  sequence_length,
  reviewed,
  annotation_score,
  protein_existence,
  ensembl_ids,

  protein_sequence,
  sequence_hash,
  embedding_model,
  embedding_dim,
  embedding_norm,
  embedding_release,

  function_text,
  comment_function,
  keywords,
  subcellular_location,
  pathway,

  disease_count,
  disease_names
}
```

Минимальный must-have для замены runtime model:

- `protein_sequence`
- `sequence_hash`
- `embedding_model`
- `embedding_release`
- `SIMILAR_TO` top-k edges

Без `protein_sequence`/`sequence_hash` агент не сможет надёжно понять, что raw sequence на входе уже есть в БД.

### 2. Добавить отдельный слой `Sequence`

Если один белок может иметь разные входные представления, лучше отделить sequence от protein accession:

```text
(:Sequence {
  sequence_hash,
  sequence_type,
  raw_sequence,
  normalized_sequence,
  protein_sequence,
  length,
  source,
  source_id
})

(:Sequence)-[:ENCODES]->(:Protein)
(:Sequence)-[:TRANSLATES_TO]->(:Sequence)
```

Это полезно, если разрешённый набор включает DNA/transcript sequences. Тогда DNA не нужно переводить в runtime локальным пайплайном: перевод уже лежит в графе.

Для protein-only датасета можно начать проще: хранить `protein_sequence` и `sequence_hash` прямо в `Protein`.

### 3. Перестроить `SIMILAR_TO`

Сейчас `build_knn_graph.py` использует `DEFAULT_K = 20` и `DEFAULT_MIN_SIM = 0.70`, а retriever ищет top-50. Для покрытия текущего поведения нужно:

- строить минимум `k=50`;
- лучше `k=100`, чтобы хватало запаса для фильтров и rerank;
- не отрезать слишком агрессивно по `min_sim`;
- хранить `rank`, а не только `cosine_sim`;
- хранить модель/версию embedding на ребре или на графовом release.

Целевое ребро:

```text
(:Protein)-[:SIMILAR_TO {
  cosine_sim,
  rank,
  method: "ProtT5 mean pooled cosine",
  embedding_model: "Rostlab/prot_t5_xl_uniref50",
  embedding_release
}]->(:Protein)
```

Важно: сейчас рёбра канонизируются как A/B и импортируются одним направлением. Агентский запрос `MATCH (p)-[:SIMILAR_TO]->(n)` может пропускать соседей, если белок оказался в `dst`. Нужно выбрать одно:

- импортировать оба направления;
- или всегда искать undirected:

```cypher
MATCH (p:Protein {accession: $accession})-[r:SIMILAR_TO]-(n:Protein)
RETURN n.accession, n.gene_primary, n.protein_name, r.cosine_sim, r.rank
ORDER BY r.cosine_sim DESC
LIMIT $limit
```

Для agent tools проще и безопаснее перейти на undirected query.

### 4. Добавить индексы/constraints

Нужны быстрые entry points:

```cypher
CREATE CONSTRAINT protein_row_id IF NOT EXISTS
FOR (p:Protein) REQUIRE p.row_id IS UNIQUE;

CREATE CONSTRAINT protein_accession IF NOT EXISTS
FOR (p:Protein) REQUIRE p.accession IS UNIQUE;

CREATE INDEX protein_gene IF NOT EXISTS
FOR (p:Protein) ON (p.gene_primary);

CREATE INDEX protein_entry_name IF NOT EXISTS
FOR (p:Protein) ON (p.entry_name);

CREATE INDEX protein_sequence_hash IF NOT EXISTS
FOR (p:Protein) ON (p.sequence_hash);
```

Если добавляем `Sequence`:

```cypher
CREATE CONSTRAINT sequence_hash IF NOT EXISTS
FOR (s:Sequence) REQUIRE s.sequence_hash IS UNIQUE;
```

### 5. Добавить full-text index

Чтобы заменить часть LLM/text-rerank поведения:

```cypher
CREATE FULLTEXT INDEX protein_text IF NOT EXISTS
FOR (p:Protein)
ON EACH [p.protein_name, p.gene_primary, p.organism_name, p.function_text, p.keywords, p.pathway, p.subcellular_location];
```

Это не полная замена Mistral semantic embeddings, но уже даст хороший runtime поиск по контексту без внешнего embedding API.

### 6. Добавить vector index для текстового rerank, если нужен parity ближе к текущему

Текущий `LocalReranker` форматирует UniProt record в строку:

```text
Gene: ...; Organism: ...; Protein: ...; Description: FUNCTION comments...
```

Чтобы заранее покрыть этот этап, нужно offline:

1. собрать такой же `rerank_text` для каждого белка;
2. посчитать text embedding один раз;
3. сохранить embedding в Neo4j или отдельном vector store;
4. в runtime искать/ранжировать top-50 по context embedding.

Но есть нюанс: если user context произвольный, embedding самого context всё равно надо получить в runtime. Это уже не ProtT5 и не локальная белковая модель, но может требовать Mistral/OpenAI embedding API. Если хотим вообще без runtime embeddings, тогда оставляем full-text/keyword scoring и заранее заданные категории.

Целевое поле:

```text
Protein {
  rerank_text,
  rerank_text_embedding,
  functional_categories
}
```

## Насколько точно БД сможет повторить текущий pipeline

### Можно повторить практически точно

Если вход уже соответствует `Protein`/`Sequence` в БД:

- primary ranking по ProtT5 cosine similarity;
- top-50/top-100 соседей;
- accession/gene/protein metadata;
- disease aggregation по соседям;
- stable повторяемость результата;
- отсутствие загрузки локальной ProtT5-модели в агенте.

При условии, что `SIMILAR_TO` построен с тем же embedding model, той же нормализацией и достаточным k.

### Можно повторить частично

Contextual reranking:

- full-text index даст быстрый и объяснимый keyword/context search;
- заранее рассчитанные functional categories помогут фильтровать;
- text vector index даст ближе к текущему semantic rerank, но context embedding всё равно должен считаться в runtime через API или отдельный сервис.

### Не нужно поддерживать в новой тактике

Если мы ограничиваем вход тем, что уже есть в БД, можно не поддерживать полноценный runtime path для неизвестной raw sequence. Вместо этого надо явно возвращать:

> Последовательность не найдена в разрешённом датасете. Добавьте её в offline ingestion pipeline, после чего она будет доступна для анализа.

Это честнее и архитектурно чище, чем запускать локальную модель в агенте.

## Изменения в `graph_core`

### `extract_embeddings.py`

Сейчас извлекаются accession и embedding. Нужно дополнительно получить/сохранить protein sequence, если она доступна в источнике или через UniProt:

- `protein_sequence`
- `sequence_hash = sha256(normalized protein_sequence)`
- `embedding_model`
- `embedding_release`

Если HDF5 не содержит sequence, добрать sequence через UniProt в annotation step.

### `fetch_uniprot_annotations.py`

Расширить `UNIPROT_FIELDS`:

- `sequence`
- `cc_function`
- `keyword`
- `cc_subcellular_location`
- `pathway`

И нормализовать в поля:

- `protein_sequence`
- `function_text`
- `keywords`
- `subcellular_location`
- `pathway`
- `rerank_text`

### `build_knn_graph.py`

Изменить defaults:

```text
DEFAULT_K = 100
DEFAULT_MIN_SIM = 0.0 или очень низкий threshold
```

Добавить в output:

- `rank`
- `embedding_model`
- `embedding_release`

Важно: для graph-only top-50 нельзя строить только top-20.

### `export_for_neo4j.py`

Нужно:

- экспортировать новые поля `Protein`;
- смерджить `protein_disease_summary.parquet` в `proteins_annotated.parquet`;
- экспортировать `rank` на `SIMILAR_TO`;
- опционально экспортировать `Sequence` nodes;
- опционально экспортировать `ENCODES`/`TRANSLATES_TO`.

### `import_to_neo4j.py`

Нужно:

- создать constraints/indexes для accession, sequence_hash, gene;
- импортировать новые поля;
- импортировать оба направления `SIMILAR_TO` или обновить tools на undirected search;
- не удалять всю БД при incremental update, если появится регулярное обновление датасета.

## Изменения в agent tools

### 1. Исправить neighbor query

В `backend/agents_core/session_agent/tools/graph.py` заменить directed pattern:

```cypher
MATCH (p:Protein {accession: $accession})-[r:SIMILAR_TO]->(n:Protein)
```

на:

```cypher
MATCH (p:Protein {accession: $accession})-[r:SIMILAR_TO]-(n:Protein)
```

### 2. Добавить поиск по sequence hash

Новый tool:

```text
find_protein_by_sequence(sequence: str) -> Protein
```

Логика:

1. normalize sequence;
2. sha256;
3. `MATCH (p:Protein {sequence_hash: $hash})`;
4. если не найдено, вернуть controlled miss: “outside prepared dataset”.

### 3. Добавить graph-only retriever tool

Новый tool:

```text
retrieve_precomputed_neighbors(accession_or_sequence_hash, limit=50, context=None)
```

Он должен:

1. найти target protein;
2. получить top-k `SIMILAR_TO`;
3. если есть context, применить:
   - full-text score;
   - keyword/category boost;
   - annotation_score/reviewed boost;
4. вернуть top-5/top-10 с объяснимыми scores.

### 4. Обновить system prompt

Добавить правило:

- если вход вне подготовленного датасета, не запускать локальную модель;
- попросить добавить sequence в offline ingestion pipeline;
- similarity трактовать как evidence/hypothesis, не как доказательство функции.

## Предлагаемый scoring в БД

Чтобы приблизиться к `rank + rerank`, можно хранить два слоя score:

1. `sequence_similarity_score = cosine_sim` из ProtT5.
2. `context_score` из full-text/vector/category match.

Итог:

```text
final_score =
  0.70 * sequence_similarity_score +
  0.20 * context_score +
  0.05 * reviewed_boost +
  0.05 * annotation_score_normalized
```

Веса стоит держать в конфиге. Для строгого parity с текущим retriever sequence similarity должен оставаться главным сигналом, потому что именно он формирует top-50.

## Проверка качества

Нужно сделать regression-набор:

1. взять 50-100 известных белков из подготовленной БД;
2. прогнать старый `bioseq_retriever` по их protein sequence;
3. сохранить top-50 accession из FAISS;
4. сравнить с Neo4j `SIMILAR_TO` top-50.

Метрики:

- Recall@5
- Recall@10
- Recall@50
- Kendall/Spearman correlation по порядку соседей
- доля exact top-1 match

Критерий готовности:

- для protein sequence, уже находящейся в БД, graph-only retrieval должен давать тот же top-k, что FAISS на тех же embeddings;
- расхождения допустимы только из-за k/min_sim/direction bugs или разных версий embeddings.

## Итоговая рекомендация

Да, БД можно заранее подготовить так, чтобы она выдавала результат максимально близко к текущей локальной модели, если вход ограничен объектами из этой БД.

Самые важные изменения:

1. хранить `protein_sequence` и `sequence_hash`;
2. строить `SIMILAR_TO` минимум на `k=50`, лучше `k=100`;
3. добавить `rank` и embedding metadata на рёбра;
4. исправить directed/undirected neighbor lookup;
5. добавить full-text и, при необходимости, text-vector слой для context rerank;
6. расширить UniProt-аннотации functional comments/keywords/pathways;
7. добавить graph-only tool, который ищет по accession/gene/name/hash и никогда не грузит ProtT5.

После этого runtime-агент будет ходить в Neo4j и покрывать текущий retriever для заранее подготовленного набора данных. Локальная модель останется только в offline ingestion pipeline, где она уместна: один раз подготовить граф, а не запускаться при каждом начале работы агента.

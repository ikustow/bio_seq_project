# Гайд по работе с `graph_core/scripts/pipeline.py`

## Что делает `pipeline.py`

Скрипт [`graph_core/scripts/pipeline.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/graph_core/scripts/pipeline.py) запускает полный пайплайн обработки protein embeddings, обогащения аннотациями UniProt, добавления disease-слоя и подготовки данных для Neo4j.

Перед запуском он:

1. Полностью очищает папку `graph_core/output/`.
2. Последовательно запускает восемь шагов:
   - `inspect_h5.py`
   - `extract_embeddings.py`
   - `prepare_vectors.py`
   - `build_knn_graph.py`
   - `analyze_graph.py`
   - `fetch_uniprot_annotations.py`
   - `fetch_disease_annotations.py`
   - `export_for_neo4j.py`

Итог пайплайна:

- из `per-protein.h5` извлекаются эмбеддинги белков;
- эмбеддинги нормализуются и при необходимости уменьшаются через PCA;
- по векторам строится kNN-граф похожести;
- считаются базовые метрики графа;
- по accession подтягиваются человекочитаемые аннотации UniProt;
- по accession подтягиваются disease-аннотации UniProt, если они есть;
- готовятся CSV-файлы для импорта в Neo4j;
- результаты сохраняются в `graph_core/output/`.

## Что нужно скачать заранее

Перед первым запуском нужно скачать файл `per-protein.h5` в папку `graph_core/data/`.

Источник:

- https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/embeddings/UP000005640_9606/

В этом каталоге сейчас лежат:

- `per-protein.h5`
- `RELEASE.metalink`

Для работы пайплайна обязателен именно `per-protein.h5`.

Итоговый путь должен быть таким:

```text
graph_core/data/per-protein.h5
```

Пример скачивания:

```bash
mkdir -p graph_core/data
curl -L https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/embeddings/UP000005640_9606/per-protein.h5 -o graph_core/data/per-protein.h5
```

## Установка зависимостей

Сначала установите зависимости из [`requirements.txt`](/Users/ilia_kustov/Documents/dev/bio_seq_project/requirements.txt):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

В `requirements.txt` используются, в частности:

- `h5py`
- `numpy`
- `pandas`
- `scikit-learn`
- `faiss-cpu`
- `networkx`
- `pyarrow`
- `pyvis`
- `neo4j`

## Как запустить пайплайн

Из корня проекта выполните:

```bash
python graph_core/scripts/pipeline.py
```

## Что делает каждый шаг

### 1. `inspect_h5.py`

Печатает структуру `graph_core/data/per-protein.h5`:

- top-level keys;
- типы объектов;
- формы массивов;
- `dtype`.

Это нужно для быстрой проверки, что HDF5-файл читается и его структура соответствует ожиданиям.

### 2. `extract_embeddings.py`

Извлекает из HDF5:

- accession белков;
- матрицу эмбеддингов.

После этого сохраняет:

- `graph_core/output/proteins.parquet`
- `graph_core/output/embeddings.npy`
- `graph_core/output/meta.txt`

### 3. `prepare_vectors.py`

Загружает `embeddings.npy`, затем:

- делает L2-нормализацию;
- сохраняет `embeddings_l2.npy`;
- строит PCA до 256 компонент;
- сохраняет `embeddings_l2_pca256.npy`;
- пишет информацию о доле объяснённой дисперсии в `pca_256_info.txt`.

### 4. `build_knn_graph.py`

Строит граф похожести по косинусной близости:

- использует `faiss`;
- ищет `k=20` ближайших соседей;
- отбрасывает связи слабее `cosine_sim < 0.70`;
- убирает дубли направлений `A->B` и `B->A`.

Результат:

- `graph_core/output/knn_edges.parquet`

### 5. `analyze_graph.py`

Собирает неориентированный граф в `networkx` и печатает:

- число узлов;
- число рёбер;
- среднюю степень;
- число компонент связности;
- размер крупнейшей компоненты.

### 6. `fetch_uniprot_annotations.py`

Запрашивает аннотации UniProt по accession, полученным на шаге `extract_embeddings.py`.

Сохраняет:

- `graph_core/output/protein_annotations.parquet`
- `graph_core/output/proteins_annotated.parquet`

В объединённой таблице появляются, в частности:

- `entry_name`
- `protein_name`
- `gene_primary`
- `organism_name`
- `sequence_length`
- `reviewed`
- `annotation_score`
- `protein_existence`
- `ensembl_ids`

### 7. `fetch_disease_annotations.py`

Пытается получить disease-аннотации из UniProt по accession.

Сохраняет:

- `graph_core/output/protein_diseases.parquet`
- `graph_core/output/protein_disease_summary.parquet`

Важно:

- для mouse proteome покрытие disease-комментариев в UniProt может быть очень редким;
- поэтому корректный результат работы этого шага вполне может быть пустым файлом без ошибок;
- это не означает, что скрипт сломан, а означает, что в UniProt для этих accession нет disease-комментариев в таком формате.

### 8. `export_for_neo4j.py`

Готовит CSV для Neo4j.

Если существует `proteins_annotated.parquet`, экспорт использует его. Иначе скрипт берёт обычный `proteins.parquet`.

Сохраняет:

- `graph_core/output/neo4j/proteins.csv`
- `graph_core/output/neo4j/edges.csv`

Если существует непустой `protein_diseases.parquet`, дополнительно сохраняет:

- `graph_core/output/neo4j/diseases.csv`
- `graph_core/output/neo4j/protein_disease_edges.csv`

## Какие файлы появляются в `graph_core/output`

После успешного запуска обычно появляются:

- `embeddings.npy`
- `embeddings_l2.npy`
- `embeddings_l2_pca256.npy`
- `knn_edges.parquet`
- `meta.txt`
- `pca_256_info.txt`
- `protein_annotations.parquet`
- `protein_diseases.parquet`
- `protein_disease_summary.parquet`
- `proteins.parquet`
- `proteins_annotated.parquet`

После экспорта для Neo4j дополнительно появляются:

- `neo4j/proteins.csv`
- `neo4j/edges.csv`

Если найден disease-слой, дополнительно появляются:

- `neo4j/diseases.csv`
- `neo4j/protein_disease_edges.csv`

Если отдельно запустить [`graph_core/scripts/viz.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/graph_core/scripts/viz.py), дополнительно создастся:

- `graph.html`

Запуск визуализации:

```bash
python graph_core/scripts/viz.py
```

## Как отдельно добавить аннотации UniProt

Файл `per-protein.h5` содержит accession и embedding-векторы, но не даёт удобные человекочитаемые поля вроде названия белка, гена и организма в том виде, в котором они нужны в Neo4j.

Для обогащения по accession используйте [`graph_core/scripts/fetch_uniprot_annotations.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/graph_core/scripts/fetch_uniprot_annotations.py):

```bash
python graph_core/scripts/fetch_uniprot_annotations.py
```

По умолчанию скрипт:

- читает `graph_core/output/proteins.parquet`;
- запрашивает аннотации через UniProt REST API;
- сохраняет `graph_core/output/protein_annotations.parquet`;
- сохраняет объединённую таблицу `graph_core/output/proteins_annotated.parquet`.

В `proteins_annotated.parquet` добавляются, в частности:

- `entry_name`
- `protein_name`
- `gene_primary`
- `organism_name`
- `sequence_length`
- `reviewed`
- `annotation_score`
- `protein_existence`
- `ensembl_ids`

## Как отдельно экспортировать в Neo4j

Сначала экспортируйте данные в CSV:

```bash
python graph_core/scripts/export_for_neo4j.py
```

Если существует `graph_core/output/proteins_annotated.parquet`, экспорт будет использовать именно его. Иначе скрипт автоматически возьмёт обычный `graph_core/output/proteins.parquet`.

Результат:

- `graph_core/output/neo4j/proteins.csv`
- `graph_core/output/neo4j/edges.csv`

Если disease-аннотации найдены, дополнительно появятся:

- `graph_core/output/neo4j/diseases.csv`
- `graph_core/output/neo4j/protein_disease_edges.csv`

## Как импортировать в Neo4j

Импорт в Neo4j:

```bash
python graph_core/scripts/import_to_neo4j.py
```

Скрипт:

- берёт `graph_core/output/neo4j/proteins.csv`;
- берёт `graph_core/output/neo4j/edges.csv`;
- при наличии берёт `graph_core/output/neo4j/diseases.csv`;
- при наличии берёт `graph_core/output/neo4j/protein_disease_edges.csv`;
- читает настройки Neo4j из `.env`;
- при TLS-ошибке автоматически переключается с `neo4j+s://` на `neo4j+ssc://`.

После такого импорта в узлах `Protein` будут не только `accession` и `dataset`, но и аннотации UniProt, например `protein_name`, `gene_primary`, `organism_name` и `sequence_length`.

Если disease-данные есть, в граф также загрузятся:

- узлы `Disease`;
- связи `(:Protein)-[:ASSOCIATED_WITH]->(:Disease)`.

## Как отдельно добавить disease-аннотации

Для отдельного запуска используйте [`graph_core/scripts/fetch_disease_annotations.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/graph_core/scripts/fetch_disease_annotations.py):

```bash
python graph_core/scripts/fetch_disease_annotations.py
```

Скрипт:

- читает `graph_core/output/proteins_annotated.parquet`;
- обращается к UniProt REST API;
- извлекает `DISEASE` comments, если они присутствуют;
- сохраняет long-form таблицу `protein_diseases.parquet`;
- сохраняет краткую сводку `protein_disease_summary.parquet`.

## Полный поток

Если нужен полный локальный прогон до файлов для Neo4j, достаточно выполнить:

```bash
python graph_core/scripts/pipeline.py
```

После этого останется только импортировать готовые CSV в Neo4j:

```bash
python graph_core/scripts/import_to_neo4j.py
```

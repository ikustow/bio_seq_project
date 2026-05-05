# Гайд по работе с `app/backend/graph_core/scripts/pipeline.py`

## Что делает `pipeline.py`

Скрипт [`pipeline.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/app/backend/graph_core/scripts/pipeline.py) запускает полный пайплайн обработки protein embeddings, обогащения аннотациями UniProt, добавления disease-слоя и подготовки данных для Neo4j.

Перед запуском он:

1. Полностью очищает папку `app/backend/graph_core/output/`.
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
- результаты сохраняются в `app/backend/graph_core/output/`.

## Что нужно скачать заранее

Перед первым запуском нужно скачать файл `per-protein.h5` в папку `app/backend/graph_core/data/`.

Источник:

- https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/embeddings/UP000005640_9606/

Обычно в этом каталоге доступны:

- `per-protein.h5`
- `RELEASE.metalink`

Для работы пайплайна обязателен именно `per-protein.h5`.

Итоговый путь должен быть таким:

```text
app/backend/graph_core/data/per-protein.h5
```

Пример скачивания:

```bash
mkdir -p app/backend/graph_core/data
curl -L https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/embeddings/UP000005640_9606/per-protein.h5 -o app/backend/graph_core/data/per-protein.h5
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
python app/backend/graph_core/scripts/pipeline.py
```

## Что делает каждый шаг

### 1. `inspect_h5.py`

Печатает структуру `app/backend/graph_core/data/per-protein.h5`:

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

- `app/backend/graph_core/output/proteins.parquet`
- `app/backend/graph_core/output/embeddings.npy`
- `app/backend/graph_core/output/meta.txt`

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
- по умолчанию запускается с `--k=3`, где один сосед обычно сам белок, поэтому остаётся до двух non-self соседей;
- отбрасывает связи слабее `cosine_sim < 0.70`;
- убирает точные дубли `src_row_id, dst_row_id`, но не склеивает обратные направления `A->B` и `B->A`.

Результат:

- `app/backend/graph_core/output/knn_edges.parquet`

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

- `app/backend/graph_core/output/protein_annotations.parquet`
- `app/backend/graph_core/output/proteins_annotated.parquet`

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

- `app/backend/graph_core/output/protein_diseases.parquet`
- `app/backend/graph_core/output/protein_disease_summary.parquet`

Важно:

- покрытие disease-комментариев в UniProt зависит от выбранного proteome и может быть очень редким;
- поэтому корректный результат работы этого шага вполне может быть пустым файлом без ошибок;
- это не означает, что скрипт сломан, а означает, что в UniProt для этих accession нет disease-комментариев в таком формате.

### 8. `export_for_neo4j.py`

Готовит CSV для Neo4j.

Если существует `proteins_annotated.parquet`, экспорт использует его. Иначе скрипт берёт обычный `proteins.parquet`.

Сохраняет:

- `app/backend/graph_core/output/neo4j/proteins.csv`
- `app/backend/graph_core/output/neo4j/edges.csv`
- `app/backend/graph_core/output/neo4j/sequences.csv`
- `app/backend/graph_core/output/neo4j/sequence_protein_edges.csv`

Если существует непустой `protein_diseases.parquet`, дополнительно сохраняет:

- `app/backend/graph_core/output/neo4j/diseases.csv`
- `app/backend/graph_core/output/neo4j/protein_disease_edges.csv`

## Какие файлы появляются в `app/backend/graph_core/output`

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
- `neo4j/sequences.csv`
- `neo4j/sequence_protein_edges.csv`

Если найден disease-слой, дополнительно появляются:

- `neo4j/diseases.csv`
- `neo4j/protein_disease_edges.csv`

Если отдельно запустить [`viz.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/app/backend/graph_core/scripts/viz.py), дополнительно создастся:

- `graph.html`

Запуск визуализации:

```bash
python app/backend/graph_core/scripts/viz.py
```

## Как отдельно добавить аннотации UniProt

Файл `per-protein.h5` содержит accession и embedding-векторы, но не даёт удобные человекочитаемые поля вроде названия белка, гена и организма в том виде, в котором они нужны в Neo4j.

Для обогащения по accession используйте [`fetch_uniprot_annotations.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/app/backend/graph_core/scripts/fetch_uniprot_annotations.py):

```bash
python app/backend/graph_core/scripts/fetch_uniprot_annotations.py
```

По умолчанию скрипт:

- читает `app/backend/graph_core/output/proteins.parquet`;
- запрашивает аннотации через UniProt REST API;
- сохраняет `app/backend/graph_core/output/protein_annotations.parquet`;
- сохраняет объединённую таблицу `app/backend/graph_core/output/proteins_annotated.parquet`.

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
python app/backend/graph_core/scripts/export_for_neo4j.py
```

Если существует `app/backend/graph_core/output/proteins_annotated.parquet`, экспорт будет использовать именно его. Иначе скрипт автоматически возьмёт обычный `app/backend/graph_core/output/proteins.parquet`.

Результат:

- `app/backend/graph_core/output/neo4j/proteins.csv`
- `app/backend/graph_core/output/neo4j/edges.csv`
- `app/backend/graph_core/output/neo4j/sequences.csv`
- `app/backend/graph_core/output/neo4j/sequence_protein_edges.csv`

Если disease-аннотации найдены, дополнительно появятся:

- `app/backend/graph_core/output/neo4j/diseases.csv`
- `app/backend/graph_core/output/neo4j/protein_disease_edges.csv`

## Как импортировать в Neo4j

Импорт в Neo4j:

```bash
python app/backend/graph_core/scripts/import_to_neo4j.py
```

Скрипт:

- берёт `app/backend/graph_core/output/neo4j/proteins.csv`;
- берёт `app/backend/graph_core/output/neo4j/edges.csv`;
- при наличии берёт `app/backend/graph_core/output/neo4j/sequences.csv`;
- при наличии берёт `app/backend/graph_core/output/neo4j/sequence_protein_edges.csv`;
- при наличии берёт `app/backend/graph_core/output/neo4j/diseases.csv`;
- при наличии берёт `app/backend/graph_core/output/neo4j/protein_disease_edges.csv`;
- читает настройки Neo4j из `.env`;
- при TLS-ошибке автоматически переключается с `neo4j+s://` на `neo4j+ssc://`.

После такого импорта в узлах `Protein` будут не только `accession` и `dataset`, но и аннотации UniProt, например `protein_name`, `gene_primary`, `organism_name` и `sequence_length`.

Если sequence-данные есть, в граф также загрузятся:

- узлы `Sequence`;
- связи `(:Sequence)-[:ENCODES]->(:Protein)`.

Если disease-данные есть, в граф также загрузятся:

- узлы `Disease`;
- связи `(:Protein)-[:ASSOCIATED_WITH]->(:Disease)`.

## Лимит 400 000 связей по умолчанию

Некоторые маленькие тарифы Neo4j/Aura ограничивают именно количество relationships, а не только размер файлов. В этом графе relationships считаются суммарно:

- `SIMILAR_TO` из `edges.csv`;
- `ENCODES` из `sequence_protein_edges.csv`;
- `ASSOCIATED_WITH` из `protein_disease_edges.csv`, если disease-слой найден.

Поэтому нельзя ориентироваться только на kNN-связи. Например, полный human proteome уже даёт сотни тысяч `ENCODES`-связей, а полный `edges.csv` может содержать миллионы `SIMILAR_TO`.

В коде уже стоят безопасные дефолты под лимит 400 000 relationships:

- `extract_embeddings.py`: `DEFAULT_MAX_PROTEINS = 100_000`, то есть по умолчанию извлекается до 100 000 белков;
- `build_knn_graph.py`: `DEFAULT_K = 3`, то есть строится максимум две non-self similarity-связи на белок;
- `import_to_neo4j.py`: `DEFAULT_MAX_EDGE_RANK = 2`, то есть импортируются только две лучшие `SIMILAR_TO`-связи на белок;
- `import_to_neo4j.py`: `DEFAULT_MAX_RELATIONSHIPS = 400_000`, то есть импорт упадёт до записи в Neo4j, если суммарное число relationships больше 400 000.

Обычный безопасный прогон поэтому выглядит так:

```bash
python app/backend/graph_core/scripts/pipeline.py
python app/backend/graph_core/scripts/import_to_neo4j.py --dry-run
python app/backend/graph_core/scripts/import_to_neo4j.py
```

`--dry-run` перед реальным импортом печатает фактические counts: `similarity_edges`, `sequence_edges`, `protein_disease_edges` и общий `Relationships: total=...`.

### Как менять лимит и размер графа

Если нужно изменить ограничения для всего пайплайна, поменяйте константы в скриптах:

- `app/backend/graph_core/scripts/extract_embeddings.py`: `DEFAULT_MAX_PROTEINS`;
- `app/backend/graph_core/scripts/build_knn_graph.py`: `DEFAULT_K`;
- `app/backend/graph_core/scripts/import_to_neo4j.py`: `DEFAULT_MAX_EDGE_RANK`;
- `app/backend/graph_core/scripts/import_to_neo4j.py`: `DEFAULT_MAX_RELATIONSHIPS`.

После изменения констант обычный запуск остаётся таким же:

```bash
python app/backend/graph_core/scripts/pipeline.py
python app/backend/graph_core/scripts/import_to_neo4j.py --dry-run
python app/backend/graph_core/scripts/import_to_neo4j.py
```

Для разового ручного запуска отдельных шагов можно не менять код, а передать флаги:

```bash
python app/backend/graph_core/scripts/extract_embeddings.py --max-proteins 50000
python app/backend/graph_core/scripts/build_knn_graph.py --k 2
python app/backend/graph_core/scripts/import_to_neo4j.py --max-edge-rank 1 --max-relationships 200000 --dry-run
python app/backend/graph_core/scripts/import_to_neo4j.py --max-edge-rank 1 --max-relationships 200000
```

Основные настройки:

- `DEFAULT_MAX_PROTEINS` или `extract_embeddings.py --max-proteins`: меняет число белков, узлов `Protein` и примерное число `ENCODES`-связей;
- `DEFAULT_K` или `build_knn_graph.py --k`: меняет число соседей, которые будут найдены при построении `knn_edges.parquet`;
- `DEFAULT_MAX_EDGE_RANK` или `import_to_neo4j.py --max-edge-rank`: меняет, сколько `SIMILAR_TO`-связей на белок реально попадёт в Neo4j;
- `DEFAULT_MAX_RELATIONSHIPS` или `import_to_neo4j.py --max-relationships`: меняет защитный лимит общего числа relationships перед импортом; значение `0` отключает эту проверку.

Как эти значения связаны:

- `DEFAULT_MAX_PROTEINS` управляет размером датасета. Чем больше белков, тем больше узлов `Protein`, больше `ENCODES`-связей и больше потенциальных `SIMILAR_TO`-связей.
- `DEFAULT_K` управляет построением kNN-графа. В `faiss` один из найденных соседей обычно сам белок, поэтому `DEFAULT_K = 3` даёт максимум две non-self similarity-связи на белок.
- `DEFAULT_MAX_EDGE_RANK` управляет импортом уже построенных similarity-связей. Если `DEFAULT_MAX_EDGE_RANK = 2`, в Neo4j попадут только связи с `rank <= 2`.
- `DEFAULT_MAX_EDGE_RANK` не должен быть больше полезного числа соседей из `DEFAULT_K`. Например, при `DEFAULT_K = 3` значение `DEFAULT_MAX_EDGE_RANK = 5` почти ничего не даст, потому что построено максимум две non-self связи на белок.
- Если нужно сделать граф плотнее, повышайте `DEFAULT_K` и `DEFAULT_MAX_EDGE_RANK` вместе. Например: `DEFAULT_K = 6` и `DEFAULT_MAX_EDGE_RANK = 5`.
- Если повышаете `DEFAULT_K` или `DEFAULT_MAX_EDGE_RANK`, нужно уменьшить `DEFAULT_MAX_PROTEINS` или поднять `DEFAULT_MAX_RELATIONSHIPS`, иначе импорт может остановиться на проверке лимита.
- `DEFAULT_MAX_RELATIONSHIPS` не уменьшает граф сам по себе. Это защитная проверка перед импортом: она считает, сколько relationships будет загружено, и останавливает импорт, если лимит превышен.

При таком сценарии верхняя оценка обычно такая:

```text
relationships ~= sequence_edges + similarity_edges + protein_disease_edges
relationships <= 100000 + 200000 + disease_edges
```

Примеры:

- Меньше граф для более дешёвого тарифа: `DEFAULT_MAX_PROTEINS = 50_000`, `DEFAULT_K = 3`, `DEFAULT_MAX_EDGE_RANK = 2`, `DEFAULT_MAX_RELATIONSHIPS = 200_000`.
- Более плотный граф при том же лимите 400 000: `DEFAULT_MAX_PROTEINS = 60_000`, `DEFAULT_K = 6`, `DEFAULT_MAX_EDGE_RANK = 5`, `DEFAULT_MAX_RELATIONSHIPS = 400_000`.
- Полный или почти полный локальный граф без лимита Aura: увеличьте `DEFAULT_MAX_PROTEINS`, увеличьте `DEFAULT_K`/`DEFAULT_MAX_EDGE_RANK` и поставьте `DEFAULT_MAX_RELATIONSHIPS = 0`.

Если `--dry-run` показывает сумму relationships больше лимита, уменьшите `DEFAULT_MAX_PROTEINS`, `DEFAULT_K` или `DEFAULT_MAX_EDGE_RANK` и заново прогоните pipeline. Если CSV уже были построены на полном датасете, одного `--max-edge-rank` может быть недостаточно: `sequence_protein_edges.csv` сам по себе может превысить лимит.

## Как отдельно добавить disease-аннотации

Для отдельного запуска используйте [`fetch_disease_annotations.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/app/backend/graph_core/scripts/fetch_disease_annotations.py):

```bash
python app/backend/graph_core/scripts/fetch_disease_annotations.py
```

Скрипт:

- читает `app/backend/graph_core/output/proteins_annotated.parquet`;
- обращается к UniProt REST API;
- извлекает `DISEASE` comments, если они присутствуют;
- сохраняет long-form таблицу `protein_diseases.parquet`;
- сохраняет краткую сводку `protein_disease_summary.parquet`.

## Полный поток

Если нужен полный локальный прогон до файлов для Neo4j, достаточно выполнить:

```bash
python app/backend/graph_core/scripts/pipeline.py
```

После этого останется только импортировать готовые CSV в Neo4j:

```bash
python app/backend/graph_core/scripts/import_to_neo4j.py
```

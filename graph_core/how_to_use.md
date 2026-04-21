# Гайд по работе с `graph_core/scripts/pipeline.py`

## Что делает `pipeline.py`

Скрипт [`graph_core/scripts/pipeline.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/graph_core/scripts/pipeline.py) запускает полный пайплайн обработки protein embeddings и сборки графа похожести белков.

Перед запуском он:

1. Полностью очищает папку `graph_core/output/`.
2. Последовательно запускает пять шагов:
   - `inspect_h5.py`
   - `extract_embeddings.py`
   - `prepare_vectors.py`
   - `build_knn_graph.py`
   - `analyze_graph.py`

Итог пайплайна:

- из `per-protein.h5` извлекаются эмбеддинги белков;
- эмбеддинги нормализуются и при необходимости уменьшаются через PCA;
- по векторам строится kNN-граф похожести;
- считаются базовые метрики графа;
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

## Какие файлы появляются в `graph_core/output`

После успешного запуска обычно появляются:

- `embeddings.npy`
- `embeddings_l2.npy`
- `embeddings_l2_pca256.npy`
- `knn_edges.parquet`
- `meta.txt`
- `pca_256_info.txt`
- `proteins.parquet`

Если отдельно запустить [`graph_core/scripts/viz.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/graph_core/scripts/viz.py), дополнительно создастся:

- `graph.html`

Запуск визуализации:

```bash
python graph_core/scripts/viz.py
```

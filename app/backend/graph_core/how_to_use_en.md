# Guide for `app/backend/graph_core/scripts/pipeline.py`

## What `pipeline.py` does

[`pipeline.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/app/backend/graph_core/scripts/pipeline.py) runs the full pipeline for processing protein embeddings, enriching them with UniProt annotations, adding a disease layer, and preparing Neo4j import files.

Before running, it:

1. Fully clears `app/backend/graph_core/output/`.
2. Runs eight steps in order:
   - `inspect_h5.py`
   - `extract_embeddings.py`
   - `prepare_vectors.py`
   - `build_knn_graph.py`
   - `analyze_graph.py`
   - `fetch_uniprot_annotations.py`
   - `fetch_disease_annotations.py`
   - `export_for_neo4j.py`

Pipeline output:

- extracts protein accessions and embeddings from `per-protein.h5`;
- normalizes embeddings and optionally reduces dimensionality with PCA;
- builds a kNN similarity graph from the vectors;
- computes basic graph metrics;
- fetches human-readable UniProt annotations by accession;
- fetches UniProt disease annotations by accession when available;
- prepares CSV files for Neo4j import;
- saves results under `app/backend/graph_core/output/`.

## What to download first

Before the first run, download `per-protein.h5` into `app/backend/graph_core/data/`.

Source:

- https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/embeddings/UP000005640_9606/

This directory usually contains:

- `per-protein.h5`
- `RELEASE.metalink`

The pipeline requires `per-protein.h5`.

Expected path:

```text
app/backend/graph_core/data/per-protein.h5
```

Download example:

```bash
mkdir -p app/backend/graph_core/data
curl -L https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/embeddings/UP000005640_9606/per-protein.h5 -o app/backend/graph_core/data/per-protein.h5
```

## Install dependencies

Install dependencies from [`requirements.txt`](/Users/ilia_kustov/Documents/dev/bio_seq_project/requirements.txt):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Key packages include:

- `h5py`
- `numpy`
- `pandas`
- `scikit-learn`
- `faiss-cpu`
- `networkx`
- `pyarrow`
- `pyvis`
- `neo4j`

## Run the pipeline

From the project root:

```bash
python app/backend/graph_core/scripts/pipeline.py
```

## Step details

### 1. `inspect_h5.py`

Prints the structure of `app/backend/graph_core/data/per-protein.h5`:

- top-level keys;
- object types;
- array shapes;
- `dtype`.

This is a quick check that the HDF5 file can be read and matches the expected layout.

### 2. `extract_embeddings.py`

Extracts from HDF5:

- protein accessions;
- embedding matrix.

Saves:

- `app/backend/graph_core/output/proteins.parquet`
- `app/backend/graph_core/output/embeddings.npy`
- `app/backend/graph_core/output/meta.txt`

### 3. `prepare_vectors.py`

Loads `embeddings.npy`, then:

- applies L2 normalization;
- saves `embeddings_l2.npy`;
- builds PCA to 256 components when enabled;
- saves `embeddings_l2_pca256.npy`;
- writes explained variance information to `pca_256_info.txt`.

### 4. `build_knn_graph.py`

Builds a cosine-similarity graph:

- uses `faiss`;
- defaults to `--k=3`; one neighbor is usually the protein itself, so this leaves up to two non-self neighbors;
- drops edges with `cosine_sim < 0.70`;
- removes exact duplicate `src_row_id, dst_row_id` pairs, but does not merge reverse directions `A->B` and `B->A`.

Result:

- `app/backend/graph_core/output/knn_edges.parquet`

### 5. `analyze_graph.py`

Builds an undirected graph in `networkx` and prints:

- node count;
- edge count;
- average degree;
- connected component count;
- largest component size.

### 6. `fetch_uniprot_annotations.py`

Fetches UniProt annotations for accessions produced by `extract_embeddings.py`.

Saves:

- `app/backend/graph_core/output/protein_annotations.parquet`
- `app/backend/graph_core/output/proteins_annotated.parquet`

`proteins_annotated.parquet` includes fields such as:

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

Attempts to fetch disease annotations from UniProt by accession.

Saves:

- `app/backend/graph_core/output/protein_diseases.parquet`
- `app/backend/graph_core/output/protein_disease_summary.parquet`

Important:

- disease-comment coverage in UniProt depends on the selected proteome and can be very sparse;
- an empty output file can be a correct successful result;
- it means UniProt has no disease comments for these accessions in this format, not that the script is broken.

### 8. `export_for_neo4j.py`

Prepares CSV files for Neo4j.

If `proteins_annotated.parquet` exists, export uses it. Otherwise it falls back to `proteins.parquet`.

Saves:

- `app/backend/graph_core/output/neo4j/proteins.csv`
- `app/backend/graph_core/output/neo4j/edges.csv`
- `app/backend/graph_core/output/neo4j/sequences.csv`
- `app/backend/graph_core/output/neo4j/sequence_protein_edges.csv`

If non-empty `protein_diseases.parquet` exists, it also saves:

- `app/backend/graph_core/output/neo4j/diseases.csv`
- `app/backend/graph_core/output/neo4j/protein_disease_edges.csv`

## Output files

After a successful pipeline run, the usual files are:

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

After Neo4j export, additional files appear:

- `neo4j/proteins.csv`
- `neo4j/edges.csv`
- `neo4j/sequences.csv`
- `neo4j/sequence_protein_edges.csv`

If the disease layer is found, additional files appear:

- `neo4j/diseases.csv`
- `neo4j/protein_disease_edges.csv`

Running [`viz.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/app/backend/graph_core/scripts/viz.py) separately also creates:

- `graph.html`

Visualization command:

```bash
python app/backend/graph_core/scripts/viz.py
```

## Add UniProt annotations separately

`per-protein.h5` contains accessions and embedding vectors, but not convenient human-readable fields such as protein name, gene name, and organism name in the form needed by Neo4j.

To enrich by accession, run [`fetch_uniprot_annotations.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/app/backend/graph_core/scripts/fetch_uniprot_annotations.py):

```bash
python app/backend/graph_core/scripts/fetch_uniprot_annotations.py
```

By default, the script:

- reads `app/backend/graph_core/output/proteins.parquet`;
- fetches annotations through the UniProt REST API;
- saves `app/backend/graph_core/output/protein_annotations.parquet`;
- saves merged data to `app/backend/graph_core/output/proteins_annotated.parquet`.

## Export to Neo4j separately

First export data to CSV:

```bash
python app/backend/graph_core/scripts/export_for_neo4j.py
```

Result:

- `app/backend/graph_core/output/neo4j/proteins.csv`
- `app/backend/graph_core/output/neo4j/edges.csv`
- `app/backend/graph_core/output/neo4j/sequences.csv`
- `app/backend/graph_core/output/neo4j/sequence_protein_edges.csv`

If disease annotations are found, these also appear:

- `app/backend/graph_core/output/neo4j/diseases.csv`
- `app/backend/graph_core/output/neo4j/protein_disease_edges.csv`

## Import into Neo4j

Import command:

```bash
python app/backend/graph_core/scripts/import_to_neo4j.py
```

The script:

- reads `app/backend/graph_core/output/neo4j/proteins.csv`;
- reads `app/backend/graph_core/output/neo4j/edges.csv`;
- reads `app/backend/graph_core/output/neo4j/sequences.csv` when present;
- reads `app/backend/graph_core/output/neo4j/sequence_protein_edges.csv` when present;
- reads `app/backend/graph_core/output/neo4j/diseases.csv` when present;
- reads `app/backend/graph_core/output/neo4j/protein_disease_edges.csv` when present;
- reads Neo4j settings from `.env`;
- automatically switches from `neo4j+s://` to `neo4j+ssc://` on TLS certificate errors.

After import, `Protein` nodes contain not only `accession` and `dataset`, but also UniProt annotations such as `protein_name`, `gene_primary`, `organism_name`, and `sequence_length`.

If sequence data exists, the graph also includes:

- `Sequence` nodes;
- `(:Sequence)-[:ENCODES]->(:Protein)` relationships.

If disease data exists, the graph also includes:

- `Disease` nodes;
- `(:Protein)-[:ASSOCIATED_WITH]->(:Disease)` relationships.

## Default 400,000 relationship limit

Some small Neo4j/Aura tiers limit the number of relationships, not just file size. In this graph, relationships are counted as the sum of:

- `SIMILAR_TO` from `edges.csv`;
- `ENCODES` from `sequence_protein_edges.csv`;
- `ASSOCIATED_WITH` from `protein_disease_edges.csv`, if the disease layer exists.

So do not look only at kNN relationships. For example, the full human proteome already creates hundreds of thousands of `ENCODES` relationships, and a full `edges.csv` can contain millions of `SIMILAR_TO` relationships.

The code has safe defaults for the 400,000 relationship limit:

- `extract_embeddings.py`: `DEFAULT_MAX_PROTEINS = 100_000`, so up to 100,000 proteins are extracted by default;
- `build_knn_graph.py`: `DEFAULT_K = 3`, so up to two non-self similarity relationships are built per protein;
- `import_to_neo4j.py`: `DEFAULT_MAX_EDGE_RANK = 2`, so only the two best `SIMILAR_TO` relationships per protein are imported;
- `import_to_neo4j.py`: `DEFAULT_MAX_RELATIONSHIPS = 400_000`, so import fails before writing to Neo4j if the total relationship count exceeds 400,000.

The normal safe run is:

```bash
python app/backend/graph_core/scripts/pipeline.py
python app/backend/graph_core/scripts/import_to_neo4j.py --dry-run
python app/backend/graph_core/scripts/import_to_neo4j.py
```

Run `--dry-run` before the real import. It prints the actual counts: `similarity_edges`, `sequence_edges`, `protein_disease_edges`, and `Relationships: total=...`.

### How to change graph size and limits

To change the default behavior for the whole pipeline, edit constants in the scripts:

- `app/backend/graph_core/scripts/extract_embeddings.py`: `DEFAULT_MAX_PROTEINS`;
- `app/backend/graph_core/scripts/build_knn_graph.py`: `DEFAULT_K`;
- `app/backend/graph_core/scripts/import_to_neo4j.py`: `DEFAULT_MAX_EDGE_RANK`;
- `app/backend/graph_core/scripts/import_to_neo4j.py`: `DEFAULT_MAX_RELATIONSHIPS`.

After changing constants, the normal command stays the same:

```bash
python app/backend/graph_core/scripts/pipeline.py
python app/backend/graph_core/scripts/import_to_neo4j.py --dry-run
python app/backend/graph_core/scripts/import_to_neo4j.py
```

For one-off manual runs of individual steps, use flags instead of editing code:

```bash
python app/backend/graph_core/scripts/extract_embeddings.py --max-proteins 50000
python app/backend/graph_core/scripts/build_knn_graph.py --k 2
python app/backend/graph_core/scripts/import_to_neo4j.py --max-edge-rank 1 --max-relationships 200000 --dry-run
python app/backend/graph_core/scripts/import_to_neo4j.py --max-edge-rank 1 --max-relationships 200000
```

Main settings:

- `DEFAULT_MAX_PROTEINS` or `extract_embeddings.py --max-proteins`: changes protein count, `Protein` node count, and approximate `ENCODES` relationship count;
- `DEFAULT_K` or `build_knn_graph.py --k`: changes how many neighbors are searched when building `knn_edges.parquet`;
- `DEFAULT_MAX_EDGE_RANK` or `import_to_neo4j.py --max-edge-rank`: changes how many `SIMILAR_TO` relationships per protein are actually imported into Neo4j;
- `DEFAULT_MAX_RELATIONSHIPS` or `import_to_neo4j.py --max-relationships`: changes the protective total relationship limit before import; `0` disables this check.

How these values interact:

- `DEFAULT_MAX_PROTEINS` controls dataset size. More proteins means more `Protein` nodes, more `ENCODES` relationships, and more potential `SIMILAR_TO` relationships.
- `DEFAULT_K` controls kNN graph construction. In `faiss`, one of the returned neighbors is usually the protein itself, so `DEFAULT_K = 3` gives at most two non-self similarity relationships per protein.
- `DEFAULT_MAX_EDGE_RANK` controls import of already built similarity relationships. If `DEFAULT_MAX_EDGE_RANK = 2`, only relationships with `rank <= 2` are imported into Neo4j.
- `DEFAULT_MAX_EDGE_RANK` should not be higher than the useful neighbor count from `DEFAULT_K`. For example, with `DEFAULT_K = 3`, `DEFAULT_MAX_EDGE_RANK = 5` is mostly useless because only up to two non-self relationships were built per protein.
- To make the graph denser, increase `DEFAULT_K` and `DEFAULT_MAX_EDGE_RANK` together. Example: `DEFAULT_K = 6` and `DEFAULT_MAX_EDGE_RANK = 5`.
- If you increase `DEFAULT_K` or `DEFAULT_MAX_EDGE_RANK`, reduce `DEFAULT_MAX_PROTEINS` or increase `DEFAULT_MAX_RELATIONSHIPS`; otherwise import can stop at the limit check.
- `DEFAULT_MAX_RELATIONSHIPS` does not shrink the graph by itself. It is a safety check before import: it counts how many relationships would be loaded and stops if the limit is exceeded.

Approximate upper bound for the default scenario:

```text
relationships ~= sequence_edges + similarity_edges + protein_disease_edges
relationships <= 100000 + 200000 + disease_edges
```

Examples:

- Smaller graph for a cheaper tier: `DEFAULT_MAX_PROTEINS = 50_000`, `DEFAULT_K = 3`, `DEFAULT_MAX_EDGE_RANK = 2`, `DEFAULT_MAX_RELATIONSHIPS = 200_000`.
- Denser graph under the same 400,000 limit: `DEFAULT_MAX_PROTEINS = 60_000`, `DEFAULT_K = 6`, `DEFAULT_MAX_EDGE_RANK = 5`, `DEFAULT_MAX_RELATIONSHIPS = 400_000`.
- Full or near-full local graph without an Aura limit: increase `DEFAULT_MAX_PROTEINS`, increase `DEFAULT_K`/`DEFAULT_MAX_EDGE_RANK`, and set `DEFAULT_MAX_RELATIONSHIPS = 0`.

If `--dry-run` shows total relationships above the limit, reduce `DEFAULT_MAX_PROTEINS`, `DEFAULT_K`, or `DEFAULT_MAX_EDGE_RANK` and rerun the pipeline. If CSV files were already built for the full dataset, changing only `--max-edge-rank` may not be enough: `sequence_protein_edges.csv` can exceed the limit by itself.

## Add disease annotations separately

Run [`fetch_disease_annotations.py`](/Users/ilia_kustov/Documents/dev/bio_seq_project/app/backend/graph_core/scripts/fetch_disease_annotations.py):

```bash
python app/backend/graph_core/scripts/fetch_disease_annotations.py
```

The script:

- reads `app/backend/graph_core/output/proteins_annotated.parquet`;
- calls the UniProt REST API;
- extracts `DISEASE` comments when present;
- saves the long-form table `protein_diseases.parquet`;
- saves the summary table `protein_disease_summary.parquet`.

## Full flow

For a full local run up to Neo4j CSV files:

```bash
python app/backend/graph_core/scripts/pipeline.py
```

Then import the generated CSV files into Neo4j:

```bash
python app/backend/graph_core/scripts/import_to_neo4j.py
```

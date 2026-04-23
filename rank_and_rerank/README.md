# BioSeq Investigator

A modular pipeline for DNA/protein sequence search and reranking.

## Dependencies
- h5py
- faiss-cpu
- numpy
- torch
- transformers
- requests
- sentence-transformers

## Installation
```bash
conda install -c conda-forge h5py faiss-cpu numpy pytorch transformers requests sentence-transformers
```

## Project Structure
- `bioseq_investigator/`
  - `embeddings.py`: FAISS index management (Cosine distance).
  - `search.py`: Sequence embedding and HNSW search.
  - `data_fetcher.py`: UniProt interaction.
  - `scoring.py`: Similarity scoring and ranking.
  - `reranking.py`: Context-aware reranking via local Sentence-Transformer model.
  - `utils.py`: DNA translation utilities.
- `tests/`: Automated unit and pipeline tests.

# BioSeq Investigator

A modular pipeline for DNA/protein sequence search and reranking.

## Setup Instructions

### 1. Create Conda Environment
```bash
conda create -n bioseq python=3.10 -y
conda activate bioseq
```

### 2. Install Dependencies
Install the required packages using pip:
```bash
pip install h5py faiss-cpu numpy requests pysam transformers torch sentence-transformers langchain-mistralai langgraph tiktoken sentencepiece protobuf
```

*Note: If you have a GPU, you might prefer `faiss-gpu`.*

### 3. API Keys
Ensure you have the `MISTRAL_API_KEY` environment variable set:
```bash
export MISTRAL_API_KEY='your_api_key_here'
```
On Windows:
```powershell
$env:MISTRAL_API_KEY='your_api_key_here'
```

## Running the Pipeline
You can run the full pipeline using the `run_pipeline.py` script. It now uses a LangGraph-based workflow that can handle raw sequences or file paths, and performs context-aware reranking.

```bash
python run_pipeline.py
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

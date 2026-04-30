# BioSeq Retriever

BioSeq Retriever is an advanced bioinformatics pipeline designed for context-aware biological sequence search. It leverages Large Language Models (LLMs), LangGraph, and FAISS to provide a highly flexible system that can interpret natural language queries, classify sequence types, and perform multi-stage similarity searches.

## Setup Instructions

### 1. Create Conda Environment
```bash
conda create -n bioseq python=3.12 -y
conda activate bioseq
```

### 2. Install Dependencies
Install the required packages using Conda where available, and pip for others:
```bash
conda install -c conda-forge h5py faiss-cpu numpy requests pyfaidx transformers pytorch sentence-transformers -y
pip install langchain-mistralai langgraph tiktoken sentencepiece protobuf
```

*Note: If you have a GPU, you might prefer `faiss-gpu`.*

### 3. API Keys
The pipeline requires a **Mistral AI API Key** for extraction, classification, and reranking.

#### System-Wide (Recommended)
Add the following to your shell profile (`.bashrc`, `.zshrc`, or Windows Environment Variables):
```bash
export MISTRAL_API_KEY='your-real-api-key'
```

#### Locally via `.env`
Create a `.env` file in the project root:
```text
MISTRAL_API_KEY=your-real-api-key
```

#### Programmatically
You can set the environment variable directly in your script before calling the pipeline:
```python
import os
os.environ["MISTRAL_API_KEY"] = "your-real-api-key"
```

## What This Code Does
- **Intelligent Input Parsing**: Uses LLMs to extract sequences, file paths, and semantic context from natural language prompts.
- **Automated Sequence Classification**: Employs Chain-of-Thought reasoning to determine if a sequence is DNA or Protein based on hints, headers, and character composition.
- **Conditional Workflow Execution**: Dynamically routes the execution path to resolve file paths or translate DNA to protein only when necessary.
- **High-Dimensional Similarity Search**: Performs initial ranking of protein sequences using ProtT5 embeddings and HNSW indexing for speed and accuracy.
- **Cloud-Powered Semantic Reranking**: Refines results using Mistral AI embeddings to match the semantic context provided in the user's question.
- **UniProt Data Integration**: Fetches full JSON records for top-matching proteins to provide rich biological metadata.

## Execution Flow
1. **Extraction & Classification**: The LLM extracts the search target and context, classifying the sequence type with a confidence score.
2. **Dynamic Resolution**: If a file path is provided, the system reads the FASTA content; otherwise, it uses the raw sequence.
3. **Sequence Preprocessing**: DNA sequences are automatically translated into protein sequences; protein sequences pass through unchanged.
4. **Vector Search (Ranking)**: The protein sequence is embedded and used to search a FAISS index containing pre-computed protein representations, returning the top 50 matches.
5. **Contextual Refinement (Reranking)**: The metadata of the top 50 matches is embedded and compared against the user's original context query, narrowing the results to the top 5.
6. **Result Synthesis**: The system returns the final UniProt JSON records along with a boolean indicator of classification confidence.

## Pipeline Output Structure
The `run_bioseq_pipeline` function returns a dictionary (the final `GraphState`) containing:
- `final_results` (List[Dict]): The top 5 UniProt JSON records.
- `is_confident` (bool): A flag indicating if the LLM is certain about its sequence classification.
- `sequence_type` (str): Detected type ('DNA' or 'PROTEIN').
- `protein_sequence` (str): The protein sequence used for search.
- `error` (str | None): Error message if any stage failed.

## Integration: Using the Pipeline in Your Code
```python
from src.pipeline import run_bioseq_pipeline

# 1. Invoke the pipeline
result = run_bioseq_pipeline("Compare this sequence: MKTLL... against human insulin markers.")

# 2. Handle results
if result['error']:
    print(f"Error: {result['error']}")
else:
    print(f"Confidence: {result['is_confident']}")
    for match in result['final_results']:
        print(f"Found match: {match['primaryAccession']}")
```

## Project & File Structure
- `src/`: Core logic and pipeline modules.
  - `pipeline.py`: LangGraph workflow and LLM node orchestration.
  - `embeddings.py`: FAISS index management, HDF5 loading, and persistence.
  - `reranking.py`: Semantic similarity logic using Mistral cloud embeddings.
  - `utils.py`: DNA translation tables, API environment setup, and LLM factory.
  - `search.py`: ProtT5 interface and top-k vector search.
  - `data_fetcher.py`: REST client for UniProt.
  - `scoring.py`: Similarity normalization and ranking.
- `data/`: Directory for embeddings and FAISS indexes.
- `tests/`: Automated unit and pipeline tests.
- `pipeline_interface.py`: Main entry point script.

## Key Classes and Functions
- `GraphState` (TypedDict): Maintains the internal pipeline state across nodes.
- `InputExtraction` (Pydantic Model): Schema for deterministic LLM data extraction.
- `LocalReranker` (Class): Orchestrates semantic context-aware reranking.
- `get_or_create_index` (Function): Manages FAISS index lifecycle.
- `translate_dna_to_protein` (Function): Handles genetic code translation.

## Limitations and Remarks
- **API Dependency**: Requires an active Mistral AI API key.
- **Memory Usage**: ProtT5 loading requires significant RAM (~8GB+ recommended).
- **Sequence Length**: Assumes DNA is in-frame and divisible by 3.
- **Data Source**: Dependent on UniProt database coverage and pre-computed embedding quality.
- **Local Data Dependency**: Requires pre-existing data/ directory with embeddings and FAISS indexes.

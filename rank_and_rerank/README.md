# BioSeq Investigator

BioSeq Investigator is an advanced bioinformatics pipeline designed for context-aware biological sequence search. It leverages Large Language Models (LLMs), LangGraph, and FAISS to provide a highly flexible system that can interpret natural language queries, classify sequence types, and perform multi-stage similarity searches.

## Dependencies

The project requires Python 3.10+ and the following libraries:
- `langgraph`: Orchestration of the pipeline workflow.
- `langchain-mistralai`: Integration with Mistral LLMs and cloud embeddings.
- `pydantic`: Structured data extraction and validation.
- `faiss-cpu` (or `faiss-gpu`): High-performance vector similarity search.
- `h5py`: Efficient storage and retrieval of large-scale protein embeddings.
- `pysam`: Robust parsing of FASTA file formats.
- `transformers` & `torch`: Local protein sequence embedding (ProtT5).
- `requests`: Interaction with the UniProt REST API.
- `tiktoken`, `sentencepiece`, `protobuf`: Essential tokenization and serialization support.

## What This Code Does
- **Intelligent Input Parsing**: Uses LLMs to extract sequences, file paths, and semantic context from messy natural language prompts.
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

## Project & File Structure
- `bioseq_investigator/`: Main library containing the core logic and pipeline modules.
  - `pipeline.py`: LangGraph workflow definition and LLM node orchestration.
  - `embeddings.py`: FAISS index management, HDF5 loading, and disk persistence.
  - `reranking.py`: Semantic similarity logic using Mistral cloud embeddings.
  - `utils.py`: DNA translation tables, API environment setup, and LLM factory.
  - `search.py`: ProtT5 model interface and top-k vector search implementation.
  - `data_fetcher.py`: REST client for retrieving detailed protein records from UniProt.
  - `scoring.py`: Mathematical utilities for similarity normalization and result ranking.
- `data/`: Directory for storing HDF5 embeddings and persistent FAISS indexes.
- `tests/`: Suite of unit tests for validating metrics and individual pipeline steps.
- `run_pipeline.py`: Main entry point script for executing the BioSeq search.

## Key Classes and Functions

### `GraphState` (TypedDict)
Manages the internal state of the LangGraph pipeline, tracking the sequence, classification, and results across nodes.

### `InputExtraction` (Pydantic Model)
Defines the schema for deterministic LLM extraction of sequences, context, and classification reasoning.

### `LocalReranker` (Class)
Coordinates the semantic reranking process by embedding UniProt metadata via Mistral and performing similarity checks.

### `get_or_create_index` (Function)
Handles efficient FAISS index management by loading existing disk-persisted indexes or building them from HDF5 data.

### `translate_dna_to_protein` (Function)
Converts DNA nucleotide sequences into amino acid sequences using the standard genetic code.

### `run_bioseq_pipeline` (Function)
Compiles and invokes the LangGraph pipeline with a user prompt to return the final results.

## How To Use

1. **Setup Environment**:
   ```bash
   conda create -n bioseq python=3.10 -y
   conda activate bioseq
   pip install h5py faiss-cpu numpy requests pysam transformers torch langchain-mistralai langgraph tiktoken sentencepiece protobuf
   ```

2. **Configure API Key**:
   Set the `MISTRAL_API_KEY` environment variable in your terminal session.

3. **Execute Search**:
   Run the provided entry script:
   ```bash
   python run_pipeline.py
   ```
   You can modify the `user_prompt` variable in `run_pipeline.py` to search for different sequences or use file paths.

## Limitations and Remarks
- **API Dependency**: The system requires an active Mistral AI API key for extraction, classification, and context reranking.
- **Memory Usage**: The initial loading of ProtT5 and the HDF5 embeddings requires significant RAM (~8GB+ recommended).
- **Sequence Length**: DNA translation assumes the sequence is in-frame and divisible by three; partial codons are truncated.
- **Data Source**: Results are dependent on the coverage of the UniProt database and the quality of the pre-computed embeddings in `data/per-protein.h5`.

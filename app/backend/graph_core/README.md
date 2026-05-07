# Graph-Anchored Retrieval System: Implementation Documentation

## 1. Overview
The retrieval system has been extended with a two-tiered fallback mechanism that bridges novel sequence inputs with pre-computed Neo4j graph data. By utilizing **MinHash-based anchoring** and **LLM-based contextual reranking**, the system maintains high performance for known sequences while providing graceful, intelligent degradation for novel ones.

## 2. Key Components
### A. MinHash Anchor Indexer (`graph_core/scripts/anchor_indexer.py`)
- **Purpose:** Precomputes compact MinHash signatures for all protein sequences in the dataset.
- **Implementation:** Uses `datasketch` and `mmh3` to perform K-mer hashing, creating a lightweight "fingerprint" of each sequence.
- **Deployment:** Run as part of the `graph_core` pipeline to generate `data/minhash_signatures.json`.

### B. MinHash Anchor Service (`services/minhash_anchor_service.py`)
- **Purpose:** A high-speed, local service that computes MinHash signatures for input sequences and returns the closest matching accession from the graph dataset using Jaccard similarity.
- **Port:** `8003`

### C. LLM-Based Reranker (`graph_core/scripts/reranker.py`)
- **Purpose:** Performs sophisticated context-aware reranking of candidate proteins retrieved from the graph.
- **Logic:** Uses an OpenAI `gpt-4o-mini` model with structured output to evaluate relevance scores between retrieved protein metadata and the user's natural language query.

## 3. Retrieval Lifecycle (`graph_retrieval.py`)
The `GraphRetrievalService.retrieve_candidates()` method implements the new unified retrieval logic:

1.  **Exact Hash Lookup:** Attempts a direct `sequence_hash` match in Neo4j.
2.  **Fallback (MinHash Anchor):** If the sequence is novel, the service calls the `MinHash Anchor Service` (localhost:8003) to map the sequence to the nearest "anchor" node (accession) currently existing in the graph.
3.  **Graph Traversal:** Using the anchor (whether from exact match or MinHash fallback), the service executes a graph traversal (`MATCH ...-[:SIMILAR_TO]->...`) to identify high-similarity neighbors.
4.  **LLM-based Reranking:** All retrieved neighbor candidates are passed to the `LLMReranker`. The LLM evaluates the candidates against the user's `context` string and outputs a deterministic ranked order.

## 4. Operational Requirements
### Dependencies
Install the following libraries in your environment:
```bash
pip install datasketch mmh3 httpx langchain-openai pydantic
```

### Environment Variables
- `OPENAI_API_KEY`: Required for the LLM reranker.

### Execution
Ensure the MinHash service is initialized:
```bash
python services/minhash_anchor_service.py
```
Then, the `retrieve_candidates` method in `GraphRetrievalService` will automatically detect missing exact matches and initiate the fallback flow.

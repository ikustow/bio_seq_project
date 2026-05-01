# Target Architecture: Unified BioSeq Investigator Monorepo

Date: 2026-05-01

## Goal

Build a single monorepo where the UI, session agent, graph database, and offline retriever precomputation work as one system.

Core principle: runtime must not load a local ProtT5 model per user request. Expensive computation is performed ahead of time in the offline pipeline, stored in Neo4j, and the user-facing flow runs through `backend/agents_core/session_agent`.

## Target Monorepo Structure

```text
bio_seq_project/
  backend/
    app_contracts/
      __init__.py
      chat.py
      protein_view.py
      session.py

    app_services/
      __init__.py
      bioseq_chat.py
      graph_retrieval.py
      protein_view_mapper.py
      service_factory.py

    agents_core/
      session_agent/
        agent.py
        config.py
        models.py
        services/
        tools/

    graph_core/
      data/
      output/
      scripts/

  streamlit_ui/
    app.py
    backend_adapter.py
    components/
    mock/
    assets/

  bioseq_retriever/
    src/
    tests/

  PRD/
    Merge plan/
      Retriever.md
      Research.md
      Architecture.md
      Architecture_EN.md
```

### Directory Roles

- `backend/graph_core` — offline ingestion/precomputation: embeddings, kNN graph, UniProt enrichment, Neo4j export/import.
- `backend/agents_core/session_agent` — LangGraph/LangChain agent, session memory, persistence, graph tools.
- `backend/app_contracts` — stable models shared between UI and backend.
- `backend/app_services` — facade for the UI and future API; no Streamlit code belongs here.
- `streamlit_ui` — presentation layer: chat, protein card, candidate switcher.
- `bioseq_retriever` — legacy/reference implementation for regression tests and offline parity checks. The runtime UI must not call it directly.

## System Layers

```text
Offline layer
  UniProt/H5 data -> embeddings -> kNN -> enriched graph -> Neo4j

Runtime data layer
  Neo4j + optional Postgres/Supabase session persistence

Agent layer
  SessionGraphAgent -> graph tools + memory/session tools

Application service layer
  BioSeqChatService + GraphRetrievalService + mappers

Frontend layer
  Streamlit UI -> backend_adapter -> app services
```

## Main Runtime Pipeline

### Happy Path: User Enters a Known Accession/Gene/Name/Sequence

```text
1. Streamlit receives the user message.
2. streamlit_ui/backend_adapter.py creates ChatTurnRequest.
3. BioSeqChatService receives the request.
4. BioSeqChatService creates AppContext:
   - user_id
   - session_id
   - workspace_id
   - user_role
5. SessionGraphAgent.invoke(message, context) processes the turn:
   - uses graph tools;
   - updates session state;
   - persists active_accession / sequences / proteins.
6. BioSeqChatService performs deterministic post-processing:
   - reads active_accession from current_state;
   - calls GraphRetrievalService.retrieve_candidates(...);
   - maps Neo4j records into CandidateView/ProteinView.
7. ChatTurnResult is returned:
   - assistant_message;
   - candidates;
   - revealed_sections;
   - session snapshot;
   - warnings.
8. Streamlit updates chat history and the protein card.
```

### Controlled Miss: Sequence Is Outside the Prepared Dataset

```text
1. UI sends a sequence.
2. Backend normalizes the sequence and computes sequence_hash.
3. GraphRetrievalService looks up Protein/Sequence by hash.
4. If the hash is not found:
   - local ProtT5 is not started;
   - backend returns a warning;
   - assistant_message explains that the sequence is outside the prepared dataset.
5. User is asked to add the sequence through the offline ingestion pipeline.
```

## Offline Data Pipeline

The offline pipeline materializes ahead of time what `bioseq_retriever` used to do at runtime.

```text
1. Source data
   - per-protein.h5
   - UniProt records
   - optional DNA/transcript mappings

2. Extract
   - accession
   - protein sequence
   - raw embedding
   - row_id

3. Normalize
   - normalized protein_sequence
   - sequence_hash = sha256(normalized protein sequence)
   - embeddings_l2
   - optional embeddings_l2_pca256

4. Enrich
   - protein_name
   - gene_primary
   - organism
   - reviewed
   - annotation_score
   - function_text
   - domains
   - keywords
   - GO terms
   - PubMed IDs
   - AlphaFold accession
   - disease annotations

5. Build graph
   - top-k similar proteins, k >= 50, target k=100
   - SIMILAR_TO edges with cosine_sim and rank
   - optional text/context indexes

6. Export
   - Neo4j CSV/parquet
   - schema metadata
   - regression baseline

7. Import
   - constraints/indexes
   - Protein nodes
   - optional Sequence nodes
   - Disease nodes
   - SIMILAR_TO edges
   - ASSOCIATED_WITH edges
```

## Runtime Contracts

### `ChatTurnRequest`

```python
class ChatTurnRequest(BaseModel):
    message: str
    session_id: str
    user_id: str = "anonymous"
    workspace_id: str | None = None
    user_role: str | None = None
    selected_accession: str | None = None
    selected_candidate_index: int | None = None
    ui_context: dict[str, object] = Field(default_factory=dict)
```

### `ChatTurnResult`

```python
class ChatTurnResult(BaseModel):
    session_id: str
    assistant_message: str
    candidates: list[CandidateView] = Field(default_factory=list)
    selected_candidate_index: int = 0
    revealed_sections: set[str] = Field(default_factory=set)
    session: SessionSnapshot
    warnings: list[str] = Field(default_factory=list)
```

### `CandidateView`

```python
class CandidateView(BaseModel):
    protein: ProteinView
    match_score: float
    rank: int
    similarity_score: float | None = None
    context_score: float | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
```

### `ProteinView`

`ProteinView` must remain compatible with the UI shape from `deploy/hf-spaces`:

```python
class ProteinView(BaseModel):
    accession: str
    name: str
    alt_names: list[str] = Field(default_factory=list)
    gene: str = ""
    organism_scientific: str = ""
    organism_common: str = ""
    taxon_id: int = 0
    annotation_score: float = 0
    reviewed: bool = False
    existence: str = ""
    length: int = 0
    mol_weight: int = 0
    subcellular_locations: list[str] = Field(default_factory=list)
    function_text: str = ""
    disease: DiseaseInfo | None = None
    domains: list[DomainFeature] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    go_terms: list[str] = Field(default_factory=list)
    pubmed_ids: list[str] = Field(default_factory=list)
    xrefs: dict[str, str] = Field(default_factory=dict)
    alphafold_accession: str = ""
    sequence: str = ""
```

## Backend Services

### `BioSeqChatService`

Main entry point for the UI and future API.

```python
class BioSeqChatService:
    def submit_turn(self, request: ChatTurnRequest) -> ChatTurnResult:
        ...

    def get_session(self, session_id: str, user_id: str = "anonymous") -> SessionSnapshot:
        ...
```

Responsibilities:

- call `SessionGraphAgent`;
- contain no Streamlit dependencies;
- build `ChatTurnResult`;
- centralize warnings and controlled misses;
- preserve session-first behavior.

### `GraphRetrievalService`

Deterministic graph-only retrieval.

```python
class GraphRetrievalService:
    def resolve_input(self, text: str, limit: int = 5) -> list[ProteinLookupHit]:
        ...

    def find_by_sequence_hash(self, sequence: str) -> ProteinLookupHit | None:
        ...

    def retrieve_candidates(
        self,
        accession: str,
        limit: int = 5,
        neighbor_pool: int = 50,
        context: str | None = None,
    ) -> list[CandidateView]:
        ...

    def get_protein_view(self, accession: str) -> ProteinView:
        ...
```

Responsibilities:

- Neo4j/read-only data access only;
- no LLM calls;
- no Streamlit imports;
- no ProtT5 startup;
- scoring top candidates.

### `protein_view_mapper`

```python
def protein_record_to_view(record: dict) -> ProteinView:
    ...

def neighbor_record_to_candidate(record: dict, rank: int) -> CandidateView:
    ...
```

Responsibilities:

- convert flat/json Neo4j records into UI models;
- parse JSON properties: domains, keywords, xrefs, PubMed IDs;
- provide fallback values so the UI does not crash on incomplete data.

## Session Agent

`SessionGraphAgent` remains the central orchestrator for the conversation.

It should:

- accept user messages;
- use graph tools;
- answer the user;
- update `SessionAgentState`;
- persist state through LangGraph checkpointer/store and `SessionRepository`;
- maintain `active_accession`, `active_sequence_id`, `proteins`, `sequences`, `working_memory`.

It should not:

- render UI;
- create Streamlit-specific state;
- directly return `ProteinView`;
- start a local ProtT5 model.

## Graph Tools

Existing tools remain, but need to be strengthened.

### Fix Neighbor Lookup

Directed:

```cypher
MATCH (p:Protein {accession: $accession})-[r:SIMILAR_TO]->(n:Protein)
```

should become undirected:

```cypher
MATCH (p:Protein {accession: $accession})-[r:SIMILAR_TO]-(n:Protein)
```

### Add Tools/Service Functions

- `resolve_prepared_input`
- `find_protein_by_sequence_hash`
- `retrieve_precomputed_candidates`
- `get_protein_card`
- `get_candidate_context`

Some of these can be regular service methods rather than LangChain tools. For the UI card, service methods are preferable because they are deterministic and do not require LLM planning.

## Target Graph Schema

### Nodes

```text
Protein {
  row_id,
  accession,
  dataset,
  entry_name,
  protein_name,
  gene_primary,
  organism_name,
  organism_common,
  taxon_id,
  sequence_length,
  mol_weight,
  reviewed,
  annotation_score,
  protein_existence,
  ensembl_ids,
  protein_sequence,
  sequence_hash,
  embedding_model,
  embedding_release,
  function_text,
  keywords_json,
  go_terms_json,
  pubmed_ids_json,
  xrefs_json,
  domains_json,
  alt_names_json,
  subcellular_locations_json,
  alphafold_accession,
  disease_count,
  disease_names
}

Sequence {
  sequence_hash,
  sequence_type,
  raw_sequence,
  normalized_sequence,
  protein_sequence,
  length,
  source,
  source_id
}

Disease {
  disease_accession,
  disease_id,
  disease_acronym,
  disease_description,
  disease_xref_db,
  disease_xref_id,
  association_source
}
```

### Relationships

```text
(:Protein)-[:SIMILAR_TO {
  cosine_sim,
  rank,
  method,
  embedding_model,
  embedding_release
}]-(:Protein)

(:Protein)-[:ASSOCIATED_WITH {
  association_note,
  association_source
}]->(:Disease)

(:Sequence)-[:ENCODES]->(:Protein)
(:Sequence)-[:TRANSLATES_TO]->(:Sequence)
```

### Constraints/Indexes

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

CREATE CONSTRAINT sequence_hash IF NOT EXISTS
FOR (s:Sequence) REQUIRE s.sequence_hash IS UNIQUE;

CREATE FULLTEXT INDEX protein_text IF NOT EXISTS
FOR (p:Protein)
ON EACH [p.protein_name, p.gene_primary, p.organism_name, p.function_text, p.keywords_json];
```

## Frontend Target

Streamlit remains a thin client.

### `streamlit_ui/app.py`

Owns:

- page config;
- layout;
- UI state bootstrap;
- `session_id`;
- reset/new conversation;
- mock/graph mode switch.

### `streamlit_ui/backend_adapter.py`

Single frontend-to-backend entry point:

```python
@st.cache_resource
def get_backend_service() -> BioSeqChatService:
    return create_bioseq_chat_service()

def submit_turn(message: str, session_id: str, user_id: str = "anonymous") -> ChatTurnResult:
    return get_backend_service().submit_turn(ChatTurnRequest(...))
```

### `streamlit_ui/components/chat.py`

In `mock` mode:

- keep the scripted behavior.

In `graph` mode:

- send every user input to `backend_adapter.submit_turn`;
- append `assistant_message`;
- update `candidates`;
- update `revealed_sections`.

### `streamlit_ui/components/protein_card.py`

Stays almost unchanged:

- accepts `list[CandidateView]`;
- displays top-5;
- lets the user switch candidates;
- renders sections.

New data must arrive already shaped for the card. The card should not perform backend queries.

## Run Modes

### Mock UI

```bash
BIOSEQ_BACKEND=mock streamlit run streamlit_ui/app.py
```

Purpose:

- demo without Neo4j;
- UI development;
- visual checks.

### Graph Runtime

```bash
BIOSEQ_BACKEND=graph streamlit run streamlit_ui/app.py
```

Purpose:

- real operation through `SessionGraphAgent`;
- Neo4j as the read path;
- optional Postgres/Supabase persistence.

### Offline Graph Rebuild

```bash
python backend/graph_core/scripts/pipeline.py
python backend/graph_core/scripts/import_to_neo4j.py
```

Purpose:

- update the prepared dataset;
- recompute the similarity graph;
- reimport Neo4j.

## End-to-End Pipeline

```text
A. Build time / offline

per-protein.h5 + UniProt
  -> backend/graph_core/scripts/extract_embeddings.py
  -> prepare_vectors.py
  -> build_knn_graph.py
  -> fetch_uniprot_annotations.py
  -> fetch_disease_annotations.py
  -> export_for_neo4j.py
  -> import_to_neo4j.py
  -> Neo4j prepared graph

B. App startup

Streamlit starts
  -> cache BioSeqChatService
  -> create Neo4jGraphClient
  -> create persistence resources
  -> create SessionGraphAgent

C. User turn

User message
  -> ChatTurnRequest
  -> SessionGraphAgent.invoke
  -> graph tools
  -> state patch/persistence
  -> GraphRetrievalService post-process
  -> ChatTurnResult
  -> UI chat + protein card

D. Unknown sequence

User sequence
  -> normalize/hash
  -> no DB match
  -> controlled miss
  -> no ProtT5 runtime load
```

## Implementation Roadmap

### Phase 1. Monorepo and UI Import

- Bring in `streamlit_ui` from `deploy/hf-spaces`.
- Preserve `BIOSEQ_BACKEND=mock`.
- Add `BIOSEQ_BACKEND=graph` as a new mode.
- Verify mock UI runs without backend dependencies.

### Phase 2. Contracts

- Create `backend/app_contracts`.
- Define `ChatTurnRequest`, `ChatTurnResult`, `CandidateView`, `ProteinView`, `SessionSnapshot`.
- Update the UI adapter to use these models.

### Phase 3. Service Layer

- Create `backend/app_services`.
- Add `create_bioseq_chat_service`.
- Add `BioSeqChatService`.
- Add mapper from agent result/state to `ChatTurnResult`.
- Cache the service in Streamlit with `st.cache_resource`.

### Phase 4. Graph Retrieval

- Add `GraphRetrievalService`.
- Fix `get_protein_neighbors` to use undirected lookup.
- Add `retrieve_candidates`.
- Add `get_protein_view`.
- Return top-5 `CandidateView`.

### Phase 5. Session-First Integration

- Every UI turn goes through `SessionGraphAgent`.
- `session_id` lives in `st.session_state`, while backend persistence stores the source-of-truth state.
- Reset creates a new `session_id`.
- `active_accession` is synchronized with the selected candidate.

### Phase 6. Graph Schema Enrichment

- Extend UniProt ingestion.
- Add sequence/hash/function/domains/keywords/go/xrefs/pubmed/alphafold.
- Rebuild `SIMILAR_TO` with `k=100`.
- Add `rank` to edges.
- Add constraints/full-text indexes.

### Phase 7. Regression and Parity

- Compare old `bioseq_retriever` against graph-only retrieval on known proteins.
- Metrics:
  - Recall@5;
  - Recall@10;
  - Recall@50;
  - top-1 exact match;
  - rank correlation.
- Freeze a baseline.

### Phase 8. API Wrapper Later

After local functions stabilize, add FastAPI as a thin wrapper:

- `POST /chat/turn`
- `GET /sessions/{session_id}`
- `POST /sessions/{session_id}/reset`
- `GET /proteins/{accession}/card`

Business logic stays in `app_services`.

## Summary of Code Review Recommendations

The 2026-05-01 code review mostly reviewed the old runtime `bioseq_retriever` pipeline: LangGraph extract/translate/rank/rerank, FAISS, ProtT5, Mistral rerank, and UniProt REST. After moving to a graph-first monorepo, some recommendations are solved architecturally, some move into the offline pipeline, and some remain required production hardening after the merge.

### Solved by Moving to Graphs

#### OOM from ProtT5/FAISS per User

In the old design, each user run could load ProtT5 and the FAISS index. In the target architecture runtime does not load ProtT5 or build FAISS:

- embeddings are computed offline;
- top-k neighborhoods are already in Neo4j;
- `GraphRetrievalService` reads the prepared graph;
- `bioseq_retriever` remains a reference/offline parity layer.

Result: the main risk of “10 users = 10 copies of the model” disappears from runtime.

#### Local Model Cold Start on First Request

The recommendation to load the ML model at application startup becomes irrelevant in graph mode because the app startup does not need the model. If a separate ingestion/embedding worker is added later, the model should be a singleton there, but that belongs to the offline/worker layer, not the user request path.

#### HDF5 Memory Pressure in User Requests

The `load_embeddings` issue and full HDF5 loading move from runtime to the offline pipeline. This still matters for large datasets, but no longer affects user-session latency or stability.

Offline solution:

- read HDF5 in batches;
- build FAISS/kNN incrementally or in chunks;
- persist intermediate parquet/npy artifacts;
- run rebuilds as controlled jobs, not inside UI/agent execution.

#### Directory Traversal from User File Paths

In graph-first runtime the UI must not accept arbitrary filesystem paths and read files from the server. Users may paste sequence/FASTA text, but the backend should look it up by `sequence_hash` in the prepared dataset.

Result: `resolve_filepath_node` from the old retriever should not be part of graph runtime. If file upload is added later, it should be a separate upload flow with sandboxed storage, validation, and size limits.

#### Pickle Accession Cache

The runtime graph mode does not need pickle cache. Accessions and mappings live in Neo4j and parquet/CSV artifacts. If the legacy retriever remains for regression tests, pickle should still be replaced with JSON/text there, but this does not block the monorepo merge.

#### Hardcoded H5/FAISS Paths in `rank_node`

Graph runtime does not use `rank_node`. Offline pipeline still needs configuration:

- `BIOSEQ_DATA_DIR`;
- `GRAPH_CORE_OUTPUT_DIR`;
- `NEO4J_*`;
- embedding model/release;
- kNN parameters: `K`, `MIN_SIM`.

### Still Relevant After the Merge

#### Error Handling and Short-Circuiting

The old recommendation for a LangGraph `check_error` remains relevant for any agent flow. `SessionGraphAgent` and future service functions must clearly distinguish:

- controlled miss: sequence/protein not found in the prepared dataset;
- user input error: invalid sequence;
- infrastructure error: Neo4j unavailable, credentials missing;
- LLM/tool error: the agent failed to execute a tool call.

For the UI this should become `ChatTurnResult.warnings` and a clear `assistant_message`, not a Streamlit crash.

#### External Request Timeouts

Runtime should have very few external requests, but some may remain:

- LLM call through `SessionGraphAgent`;
- AlphaFold PDB fetch in the UI structure viewer;
- offline UniProt enrichment;
- optional future text embedding/context rerank.

Rule:

- every HTTP call must have a timeout;
- retries/backoff are needed for offline enrichment;
- UI viewer failures must be non-fatal.

#### LLM/API Rate Limits

Moving to graphs removes Mistral embeddings from the main retrieval path, but `SessionGraphAgent` still uses an LLM. Therefore the system still needs:

- user turn rate limits;
- retry/backoff for LLM clients;
- graceful degradation when the LLM is unavailable;
- deterministic services where LLM planning is unnecessary.

#### Async I/O and Non-Blocking API

Before an API exists, Streamlit can call the local service synchronously. After FastAPI is added:

- keep endpoints fast;
- use async Neo4j driver or a threadpool for sync client calls;
- consider `ainvoke` for the agent;
- do not run long offline tasks in request handlers.

#### Task Queues

For graph runtime, a queue is not required for every normal chat turn because retrieval becomes a fast read-only Neo4j query. Queues are still needed for long-running tasks:

- graph database rebuilds;
- ingestion of new sequences;
- embedding recomputation;
- large UniProt enrichment runs;
- regression parity runs;
- new release preparation.

Recommended post-merge worker layer:

```text
API/Admin command
  -> Task queue
  -> Offline worker
  -> graph_core pipeline step
  -> artifacts
  -> Neo4j import/update
```

Celery/RQ/TaskIQ/ARQ can be introduced later. For the first merge, CLI scripts are enough.

#### Singleton/Pooling

Although ProtT5 leaves runtime, singleton/pooling is still useful for:

- `BioSeqChatService` in Streamlit via `st.cache_resource`;
- `Neo4jGraphClient` or driver lifecycle;
- LangGraph checkpointer/store;
- LLM client reuse.

Currently `Neo4jGraphClient.execute()` opens a driver per query. For production, move to a long-lived driver with a proper `close()`.

#### Configuration

All runtime and offline parameters should move to env/config:

- `BIOSEQ_BACKEND=mock|graph`;
- `OPENAI_API_KEY`;
- `NEO4J_URI`;
- `NEO4J_DATABASE`;
- `NEO4J_USERNAME`;
- `NEO4J_PASSWORD`;
- `SUPABASE_DB_URL`;
- graph pipeline paths;
- kNN build params;
- feature flags for full-text/vector rerank.

### Post-Merge Hardening Phases

These should be added after the basic UI + session agent + graph retrieval merge.

#### Phase 9. Runtime Reliability

- Introduce a shared error taxonomy for `app_services`.
- Return controlled errors through `ChatTurnResult.warnings`.
- Add timeouts/retries for LLM and any HTTP calls.
- Move Neo4j client to a long-lived driver/pool.
- Add health checks: Neo4j, persistence, LLM.

#### Phase 10. Security Hardening

- Remove runtime file path reading.
- Validate sequence input: length, alphabet, max size.
- Do not store real secret values in examples.
- Restrict the custom Cypher tool or keep only allowlisted read tools for UI flow.
- Add user turn rate limits after an API exists.

#### Phase 11. Offline Pipeline Scalability

- Rewrite HDF5/embedding extraction to batch/chunk mode.
- Add incremental rebuilds so small updates do not clear the whole DB.
- Version graph releases: dataset, embedding model, kNN params, UniProt release.
- Store a regression baseline after every rebuild.

#### Phase 12. Multi-User Production Path

- Add FastAPI on top of `app_services`.
- Split API layer and offline worker layer.
- Add a task queue for ingestion/rebuild jobs.
- Move long operations to background tasks.
- Make Postgres/Supabase session persistence mandatory for deploy.
- Add observability: logs, metrics, traces, latency by tool.

### What Not to Do in the First Merge

- Do not build a high-load queue for normal graph retrieval before there is real demand.
- Do not add FastAPI before local service contracts stabilize.
- Do not route the old `bioseq_retriever` back into UI runtime “just in case”.
- Do not fully normalize the Neo4j schema immediately; JSON properties for the UI card are acceptable initially.
- Do not add text vector rerank until full-text/context filters are tested against a simple baseline.

## Acceptance Criteria

1. The monorepo runs mock UI without Neo4j.
2. The monorepo runs graph UI through `SessionGraphAgent`.
3. UI does not import `bioseq_retriever` in graph mode.
4. UI does not execute Cypher and does not create a Neo4j client directly.
5. Runtime does not load ProtT5.
6. Known accession/gene/name from the DB returns an assistant response and top-5 candidates.
7. Protein card receives data through `CandidateView/ProteinView`.
8. Session state preserves active accession and history across reruns within the same `session_id`.
9. Unknown sequence returns a controlled miss.
10. Offline graph pipeline can rebuild data and reimport Neo4j.

## Final Architecture Position

The target monorepo should be graph-first and session-first:

- `graph_core` prepares knowledge ahead of time;
- Neo4j stores the prepared retrieval graph;
- `SessionGraphAgent` manages conversation and memory;
- `app_services` connects agent state to UI contracts;
- Streamlit only renders and sends turns;
- `bioseq_retriever` remains a reference/offline parity layer, not a runtime dependency.

This keeps the system interconnected without creating hard cyclic dependencies: data flows upward from the offline layer, and user turns flow downward through a single backend facade.

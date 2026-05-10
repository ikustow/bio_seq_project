# BioSeq Investigator - Interim Report

## 1. Project Overview and Current Status

BioSeq Investigator is a prototype research assistant for biological sequences. A user pastes a DNA or protein sequence and asks a natural-language question; the system extracts the sequence, classifies the molecule type, translates DNA to a protein sequence when needed, searches for similar proteins using ProtT5 embeddings, retrieves UniProt annotations, and returns top-k candidates with a protein card.

The MVP value proposition is to help students, educators, and researchers quickly understand what an unknown sequence is similar to, which known functions/organisms/genes are associated with the result, and how reliable the result appears to be.

Current status:

- Data for embedding-based retrieval has been identified and is expected to be available through Hugging Face; local files are treated only as development/runtime cache.
- Heavy models and datasets are treated as Hugging Face artifacts: ProtT5 is loaded from Hugging Face Model Hub, while the embedding dataset should be delivered through a Hugging Face Dataset.
- The embedding retriever is implemented in `bioseq_retriever/src/pipeline.py`.
- Streamlit integration for the embedding retriever is implemented in `app/frontend/embeddings_pipeline.py`.
- The pipeline supports LLM extraction/classification, DNA-to-protein translation, ProtT5 query embedding, FAISS top-k search, UniProt metadata fetching, and semantic reranking.
- The UI can select the `embeddings` backend through `app/frontend/backend_choice.py` and persist turns through `app/frontend/session_db_adapter.py`.

Main links:

- [Application architecture](ARCHITECTURE.md)
- [Streamlit embedding adapter](../app/frontend/embeddings_pipeline.py)
- [Core pipeline](../bioseq_retriever/src/pipeline.py)
- [FAISS/HDF5 indexing](../bioseq_retriever/src/embeddings.py)
- [ProtT5 search](../bioseq_retriever/src/search.py)
- [Semantic reranking](../bioseq_retriever/src/reranking.py)
- [UniProt fetcher](../bioseq_retriever/src/data_fetcher.py)

## 2. Data

### 2.1 Data Sources

The main data source is UniProt protein embeddings:

- Source type: precomputed per-protein embeddings from UniProt.
- Runtime metadata source: UniProt REST API, endpoint `https://rest.uniprot.org/uniprotkb/search`.
- Current distribution/storage approach: models and large datasets are delivered through Hugging Face rather than treated as primary source files inside the repository.
- Protein embedding model: `Rostlab/prot_t5_xl_uniref50`, loaded from Hugging Face Model Hub.
- Dataset artifacts: `per-protein.h5` and, when available, prebuilt `per-protein.index` / `per-protein.accessions.json` can be pulled from a Hugging Face Dataset via `BIOSEQ_DATA_SOURCE=hf:OWNER/DATASET`; see `bioseq_retriever/src/bootstrap.py`.
- Local files are treated as runtime cache / development copies. The source of truth for heavy artifacts is Hugging Face storage.

### 2.2 Storage Format

Current storage layout:

| Artifact | Format | Purpose |
|---|---:|---|
| `per-protein.h5` | HDF5 | Precomputed protein embeddings, one dataset per accession; source/distribution is Hugging Face Dataset, local copy is runtime cache |
| `per-protein.index` | FAISS index | Speeds up top-k retrieval to avoid rebuilding the index on cold start; target storage is next to HDF5 in the Hugging Face Dataset |
| `per-protein.accessions.json` | JSON | Maps FAISS index row to UniProt accession; target storage is next to the FAISS index in the Hugging Face Dataset |
| UniProt records | JSON from REST API | Candidate metadata: name, organism, gene, function comments, etc.; fetched at runtime |
| `public.chat_sessions` | Postgres/Supabase JSONB | Chat history, selected candidates, backend mode; used when `SUPABASE_DB_URL` is configured |

### 2.3 Preprocessing / Chunking

There is no classical text chunking in this pipeline: the retrieval unit is a protein/sequence, not a document fragment.

Preprocessing pipeline:

1. HDF5 embeddings are read in batches via `bioseq_retriever/src/embeddings.py`.
2. Vectors are L2-normalized.
3. A FAISS HNSW index is built using inner product / cosine similarity.
4. The accession list is saved separately as a JSON cache.
5. The user's protein query is encoded with the `Rostlab/prot_t5_xl_uniref50` ProtT5 model, which is loaded from Hugging Face Model Hub and cached in the runtime environment.
6. If the input is DNA, it is translated with the standard codon table before retrieval.
7. For semantic reranking, the top-50 UniProt records are formatted into short text passages and compared with the user's context using text embeddings.

### 2.4 Data Artifact Lifecycle

The diagram separates long-lived retrieval artifacts from data loaded at runtime for a specific user query.

```plantuml
@startuml
title Data artifact lifecycle - embeddings backend

skinparam shadowing false

start
:Resolve data source\nBIOSEQ_DATA_SOURCE;

if (HF Dataset configured?) then (yes)
  :Download artifacts from\nHugging Face Dataset;
  :Cache under BIOSEQ_DATA_DIR\nor BIOSEQ_H5_PATH;
else (no)
  :Fallback bootstrap from\nUniProt FTP;
endif

if (FAISS index + accession cache exist?) then (yes)
  :Load per-protein.index;
  :Load per-protein.accessions.json;
else (no)
  :Read HDF5 embeddings in batches;
  :L2 normalize vectors;
  :Build FAISS HNSW index;
  :Persist index + accession cache;
endif

:Runtime query uses cached HF artifacts;
:UniProt metadata fetched by accession\nfor current top-k results;
stop
@enduml
```

## 3. Pipeline Architecture

In the current embedding-focused scope, the system consists of a Streamlit UI, an adapter layer, the core retrieval pipeline, Hugging Face backed data/model artifacts, runtime cache, and external APIs for LLM/text embeddings and UniProt metadata.

```plantuml
@startuml
title BioSeq Investigator - embedding retrieval architecture

skinparam componentStyle rectangle
skinparam shadowing false

actor User

package "Streamlit app" {
  [app/frontend/app.py] as App
  [chat_pipeline.py] as Chat
  [backend_choice.py] as Choice
  [embeddings_pipeline.py] as EmbAdapter
  [session_db_adapter.py] as SessionAdapter
}

package "bioseq_retriever" {
  [LangGraph workflow\nsrc/pipeline.py] as Pipeline
  [ProtT5 query embedder\nsrc/search.py] as ProtT5
  [FAISS index builder/search\nsrc/embeddings.py] as FAISS
  [Semantic reranker\nsrc/reranking.py] as Reranker
  [UniProt fetcher\nsrc/data_fetcher.py] as Fetcher
}

cloud "Hugging Face Dataset\nper-protein.h5 / optional FAISS artifacts" as HFDataset
database "Runtime cache\n574,615 x 1024 embeddings" as H5
database "FAISS cache\n.index + accessions.json" as IndexFiles
database "Supabase/Postgres\npublic.chat_sessions" as PG
cloud "LLM / text embeddings" as LLM
cloud "UniProt REST API" as UniProt

User --> App
App --> Chat
Chat --> Choice
Choice --> EmbAdapter : backend = embeddings
EmbAdapter --> Pipeline
Pipeline --> LLM : extraction/classification
Pipeline --> ProtT5 : query sequence embedding
ProtT5 --> FAISS
FAISS --> H5 : build/read index
FAISS --> IndexFiles : persist/load
HFDataset --> H5 : first boot download/cache
HFDataset --> IndexFiles : optional prebuilt index/cache
Pipeline --> Fetcher : top-k accessions
Fetcher --> UniProt : metadata
Pipeline --> Reranker
Reranker --> LLM : text embeddings
EmbAdapter --> SessionAdapter : save turn
SessionAdapter --> PG

@enduml
```

### 3.1 Internal Retriever Workflow

The diagram presents the retriever as an executable process: the input is first normalized into a protein sequence, then embedding search and contextual reranking are performed.

```plantuml
@startuml
title Retriever workflow - activity view

skinparam shadowing false

start
:Receive user prompt;
:Extract sequence/path,\ncontext, sequence type;

if (Extraction successful?) then (yes)
  if (Input is filepath?) then (yes)
    :Validate allowed path;
    if (Path is valid?) then (yes)
      :Read first FASTA entry;
    else (no)
      :Return controlled\nfile/security error;
      stop
    endif
  else (raw sequence)
    :Normalize pasted sequence\nor FASTA text;
  endif

  if (Sequence type is DNA?) then (yes)
    :Translate DNA to\nprotein sequence;
    if (Translation successful?) then (yes)
    else (no)
      :Return translation error;
      stop
    endif
  else (protein)
    :Use protein sequence\nas-is;
  endif

  :Encode query with ProtT5;
  :Search FAISS index\nfor top-50 accessions;
  if (Ranking successful?) then (yes)
    :Fetch UniProt records\nfor candidate accessions;
    :Embed context and\ncandidate descriptions;
    :Rerank candidates\nby semantic relevance;
    :Return final top-5\ncandidate proteins;
  else (no)
    :Return ranking/API/index error;
  endif
else (no)
  :Return extraction error;
endif

stop
@enduml
```

### 3.2 Runtime Flow

```plantuml
@startuml
title One user turn - embeddings backend

skinparam shadowing false

actor User

box "UI layer" #E3F2FD
participant "Streamlit UI" as UI
end box

box "Application runtime" #E8F5E9
participant "embeddings_pipeline.py" as EP
participant "Extraction/classification node" as Extractor
participant "DNA translator" as Translator
participant "ProtT5 embedder" as ProtT5
participant "Semantic reranker" as Reranker
end box

box "Runtime storage/cache" #F3E5F5
database "FAISS index" as Index
database "chat_sessions" as DB
end box

box "External sources and APIs" #FFF2CC
participant "Hugging Face\nModel Hub / Dataset" as HF
participant "LLM / text embeddings\nprovider" as LLM
participant "UniProt REST" as UniProt
end box

User -> UI : prompt with\nsequence + context
UI -> EP : run_turn_embeddings(prompt)
EP -> Extractor : extract sequence/path,\ncontext, DNA/protein
Extractor -> LLM : structured extraction\nand classification
LLM --> Extractor : extracted\nfields
Extractor --> EP : structured state

alt input is FASTA/filepath
  EP -> EP : resolve secure path\nparse FASTA
else raw sequence
  EP -> EP : normalize\nraw sequence
end

alt sequence_type == DNA
  EP -> Translator : translate DNA\nto protein
  Translator --> EP : protein\nsequence
else sequence_type == PROTEIN
  EP -> EP : pass protein\nsequence
end

EP -> ProtT5 : embed query\nprotein
ProtT5 -> HF : load/cache\nmodel weights
HF --> ProtT5 : ProtT5\nartifacts
ProtT5 -> Index : top-50 nearest\naccessions
Index --> EP : accession +\nembedding score
EP -> UniProt : fetch records\nfor top-50
UniProt --> EP : UniProt\nJSON records
EP -> Reranker : rerank by\nuser context
Reranker -> LLM : embed context +\ncandidate passages
LLM --> Reranker : text\nembeddings
Reranker --> EP : final\ntop-5
EP -> DB : save turn,\nreply, candidates
EP --> UI : reply +\ncandidate cards
UI --> User : ranked result +\nprotein card
@enduml
```

### 3.3 App Routing and Session Persistence

The diagram shows how the Streamlit application selects the user-turn handler, persists the result, and keeps the protein card state stable between the retriever turn and follow-up messages.

```plantuml
@startuml
title Streamlit turn routing - embeddings focus

skinparam shadowing false

start
:User submits message;
:components/chat appends user message;
:chat_pipeline.run_turn(prompt);

if (working_memory.turn_count == 0?) then (first turn)
  :backend_choice.get_backend();
  if (backend == embeddings?) then (yes)
    :embeddings_pipeline.run_turn_embeddings(prompt);
    if (preflight OK?) then (yes)
      :Run extraction, FAISS search,\nUniProt fetch, semantic rerank;
      :Build reply + candidate cards;
    else (no)
      :Return friendly setup error;
    endif
    :session_db_adapter.save_turn(\ncurrent_mode = embeddings_retriever);
    :Update st.session_state candidates,\nreveals, pending assistant reply;
  else (other backend)
    :Out of current report focus;
  endif
else (follow-up)
  :chat_llm_pipeline.run_turn_chat_llm(prompt);
  :session_db_adapter.save_turn(\nupdate_candidates = false);
  :Keep previous protein card visible;
endif

:st.rerun();
:UI renders chat + protein card;
stop
@enduml
```

```plantuml
@startuml
title Session restore and persisted state

skinparam shadowing false

actor User

box "UI / browser state" #E3F2FD
participant "Browser cookies\nuser_id + session_id" as Cookies
end box

box "Application runtime" #E8F5E9
participant "session_identity.py" as Identity
participant "chat_pipeline.py" as Chat
participant "session_db_adapter.py" as Adapter
participant "Streamlit session_state" as State
end box

box "Persistence / managed storage" #F3E5F5
database "public.chat_sessions" as Sessions
end box

User -> Cookies : open app / switch session
Cookies -> Identity : bootstrap_identity()
Identity --> Chat : user_id, session_id
Chat -> Adapter : auto_restore_if_fresh_load(session_id)
Adapter -> Sessions : load_session(session_id)
Sessions --> Adapter : messages, last_candidates,\nrevealed_sections, current_mode
Adapter --> Chat : normalized UI state
Chat -> State : restore messages
Chat -> State : restore candidates + selected card
Chat -> State : restore backend choice
State --> User : previous chat and protein card
@enduml
```

### 3.4 Components and Responsibilities

| Component | Responsibility |
|---|---|
| `app/frontend/embeddings_pipeline.py` | Streamlit adapter: preflight, lazy imports, cached resources, unified response shape, persistence |
| `bioseq_retriever/src/pipeline.py` | LangGraph workflow: extract -> resolve/raw -> translate/pass -> rank -> rerank |
| `bioseq_retriever/src/search.py` | ProtT5 model loading, query embedding, FAISS top-k search |
| `bioseq_retriever/src/embeddings.py` | HDF5 batch reading, L2 normalization, HNSW index build/load, accession cache |
| `bioseq_retriever/src/reranking.py` | Converts UniProt records to text passages and reranks by semantic similarity to user context |
| `bioseq_retriever/src/data_fetcher.py` | Fetches UniProt metadata by accession |
| `bioseq_retriever/services/*` | Optional microservices for embedding/search when heavy runtime should be isolated |
| `app/frontend/session_db_adapter.py` | Persists chat turns and top candidates into Postgres/Supabase |

## 4. Preliminary Results and Checks

What has been checked locally:

1. Unit tests for retriever utilities:
   - command: `PYTHONPATH=bioseq_retriever .venv/bin/python -m unittest bioseq_retriever.tests.test_pipeline bioseq_retriever.tests.test_enhancements`;
   - result: 9/10 passed;
   - one failure in `test_dna_translation`: the test expects `ATGATGATG -> MDD`, while the standard codon table returns `MMM`. This appears to be an outdated/incorrect test expectation rather than an error in the translation logic.

2. Smoke test status:
   - `scripts/smoke_embeddings_dispatch.py` currently fails on an assertion because the test expects heavy ML dependencies to be missing;
   - in the current environment, these dependencies are installed, so preflight returns `OK`;
   - the smoke test should be updated to cover both scenarios: "dependencies missing" and "dependencies installed".

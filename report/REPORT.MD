# BioSeq Investigator - промежуточный отчет


## 1. Краткое описание проекта и текущий статус

BioSeq Investigator - прототип исследовательского ассистента для биологических последовательностей. Пользователь вставляет DNA или protein sequence и задает вопрос естественным языком; система извлекает последовательность, определяет тип молекулы, при необходимости переводит DNA в protein sequence, ищет похожие белки по ProtT5-эмбеддингам, подтягивает аннотации из UniProt и возвращает top-k кандидатов с карточкой белка.

Целевая ценность MVP: дать студенту, преподавателю или исследователю быстрый способ понять, на что похожа неизвестная последовательность, какие есть известные функции/организмы/гены и насколько результат надежен.

Текущий статус:

- Данные для embedding-поиска идентифицированы и должны быть доступны через Hugging Face; локальная копия используется только как cache для разработки/запуска.
- Тяжелые модели и датасеты сейчас рассматриваются как Hugging Face artifacts: ProtT5 загружается из Hugging Face Model Hub, а embedding dataset должен доставляться через Hugging Face Dataset.
- Реализован  embedding retriever: `bioseq_retriever/src/pipeline.py`.
- Реализована Streamlit-интеграция embedding retriever: `app/frontend/embeddings_pipeline.py`.
- Поддержаны LLM extraction/classification, DNA->protein translation, ProtT5 query embedding, FAISS top-k search, UniProt metadata fetch и semantic rerank.
- UI уже умеет выбирать backend `embeddings` через `app/frontend/backend_choice.py` и сохранять ход в session persistence через `app/frontend/session_db_adapter.py`.


Основные ссылки:

- [Архитектурное описание приложения](ARCHITECTURE.md)
- [Embedding adapter для Streamlit](../app/frontend/embeddings_pipeline.py)
- [Core pipeline](../bioseq_retriever/src/pipeline.py)
- [FAISS/HDF5 indexing](../bioseq_retriever/src/embeddings.py)
- [ProtT5 search](../bioseq_retriever/src/search.py)
- [Semantic reranking](../bioseq_retriever/src/reranking.py)
- [UniProt fetcher](../bioseq_retriever/src/data_fetcher.py)

## 2. Данные

### 2.1 Источники данных

Основной источник данных - UniProt protein embeddings:

- Source type: precomputed per-protein embeddings from UniProt.
- Runtime metadata source: UniProt REST API, endpoint `https://rest.uniprot.org/uniprotkb/search`.
- Current distribution/storage approach: модели и большие датасеты доставляются через Hugging Face, а не хранятся как основной источник внутри репозитория.
- Protein embedding model: `Rostlab/prot_t5_xl_uniref50` загружается из Hugging Face Model Hub.
- Dataset artifacts: `per-protein.h5` и, при наличии, заранее собранные `per-protein.index` / `per-protein.accessions.json` могут подтягиваться из Hugging Face Dataset через `BIOSEQ_DATA_SOURCE=hf:OWNER/DATASET`; см. `bioseq_retriever/src/bootstrap.py`.
- Local files are treated as runtime cache / development copy. Source of truth для тяжелых артефактов - Hugging Face storage.

### 2.2 Формат хранения

Текущий storage layout:

| Artifact | Формат | Назначение |
|---|---:|---|
| `per-protein.h5` | HDF5 | Precomputed protein embeddings, один dataset на accession; source/distribution - Hugging Face Dataset, local copy - runtime cache |
| `per-protein.index` | FAISS index | Ускоряет top-k поиск, чтобы не строить index на cold start; целевой storage - рядом с HDF5 в Hugging Face Dataset |
| `per-protein.accessions.json` | JSON | Соответствие FAISS index row -> UniProt accession; целевой storage - рядом с FAISS index в Hugging Face Dataset |
| UniProt records | JSON from REST API | Метаданные кандидатов: name, organism, gene, function comments и др.; fetch at runtime |
| `public.chat_sessions` | Postgres/Supabase JSONB | История чата, выбранные кандидаты, режим backend-а; используется при наличии `SUPABASE_DB_URL` |

### 2.3 Preprocessing / chunking

Классического text chunking здесь нет: единица поиска - белок/последовательность, а не фрагмент документа.

Preprocessing pipeline:

1. HDF5 embeddings читаются батчами через `bioseq_retriever/src/embeddings.py`.
2. Вектора нормализуются L2-нормой.
3. Из них строится FAISS HNSW index с inner product / cosine similarity.
4. Список accession сохраняется отдельно в JSON cache.
5. Пользовательский protein query кодируется ProtT5 моделью `Rostlab/prot_t5_xl_uniref50`, которая загружается из Hugging Face Model Hub и кешируется в runtime environment.
6. Если вход - DNA, перед поиском выполняется translation по стандартной codon table.
7. Для semantic rerank top-50 результатов UniProt records форматируются в короткие текстовые passages и сравниваются с пользовательским контекстом через text embeddings.

### 2.4 Жизненный цикл data artifacts

Диаграмма разделяет долгоживущие артефакты retrieval-слоя и данные, которые загружаются в runtime для обработки конкретного запроса.

![Data artifact lifecycle](diagrams/data-artifact-lifecycle.svg)

<details>
<summary>PlantUML source</summary>

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

</details>

## 3. Архитектура pipeline

В текущем embedding-фокусе система состоит из Streamlit UI, adapter layer, core retrieval pipeline, Hugging Face backed data/model artifacts, runtime cache и внешних API для LLM/text embeddings и UniProt metadata.

![Embedding retrieval architecture](diagrams/embedding-retrieval-architecture.svg)

<details>
<summary>PlantUML source</summary>

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

</details>

### 3.1 Внутренний workflow retriever-а

Диаграмма показывает retriever как исполняемый процесс: сначала вход приводится к protein sequence, затем выполняется embedding search и контекстное переранжирование.

![Retriever workflow](diagrams/retriever-workflow.svg)

<details>
<summary>PlantUML source</summary>

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

</details>

### 3.2 Runtime flow

![Runtime flow](diagrams/runtime-flow.svg)

<details>
<summary>PlantUML source</summary>

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

</details>

### 3.3 App routing и session persistence

Диаграмма показывает, как Streamlit-приложение выбирает обработчик user turn, сохраняет результат и поддерживает состояние карточки белка между retriever-turn и follow-up сообщениями.

![Streamlit turn routing](diagrams/streamlit-turn-routing.svg)

<details>
<summary>PlantUML source</summary>

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

</details>

![Session restore](diagrams/session-restore.svg)

<details>
<summary>PlantUML source</summary>

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

</details>

### 3.4 Компоненты и взаимодействие

| Компонент | Ответственность |
|---|---|
| `app/frontend/embeddings_pipeline.py` | Streamlit adapter: preflight, lazy imports, cached resources, unified response shape, persistence |
| `bioseq_retriever/src/pipeline.py` | LangGraph workflow: extract -> resolve/raw -> translate/pass -> rank -> rerank |
| `bioseq_retriever/src/search.py` | ProtT5 model loading, query embedding, FAISS top-k search |
| `bioseq_retriever/src/embeddings.py` | HDF5 batch reading, L2 normalization, HNSW index build/load, accession cache |
| `bioseq_retriever/src/reranking.py` | Converts UniProt records to text passages and reranks by semantic similarity to user context |
| `bioseq_retriever/src/data_fetcher.py` | Fetches UniProt metadata by accession |
| `bioseq_retriever/services/*` | Optional microservices for embedding/search when heavy runtime should be isolated |
| `app/frontend/session_db_adapter.py` | Persists chat turn and top candidates into Postgres/Supabase |

## 4. Предварительные результаты и проверки

Что уже проверено локально:

1. Unit tests for retriever utilities:
   - команда: `PYTHONPATH=bioseq_retriever .venv/bin/python -m unittest bioseq_retriever.tests.test_pipeline bioseq_retriever.tests.test_enhancements`;
   - результат: 9/10 passed;
   - один failure в `test_dna_translation`: тест ожидает `ATGATGATG -> MDD`, тогда как стандартная codon table возвращает `MMM`. Это выглядит как устаревшее/ошибочное ожидание теста, а не ошибка translation logic.

2. Smoke test status:
   - `scripts/smoke_embeddings_dispatch.py` сейчас падает на assertion, потому что тест ожидает отсутствие heavy ML dependencies;
   - в текущем окружении dependencies уже установлены, поэтому preflight возвращает `OK`;
   - smoke-тест нужно обновить под два сценария: "dependencies missing" и "dependencies installed".

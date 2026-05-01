# Target architecture: единая монорепа BioSeq Investigator

Дата: 2026-05-01

## Цель

Собрать монорепозиторий, где UI, session agent, graph database и offline retriever-precompute работают как одна система.

Главный принцип: runtime не запускает локальную ProtT5-модель для каждого пользователя. Всё дорогое считается заранее в offline pipeline, складывается в Neo4j, а пользовательский flow идёт через `backend/agents_core/session_agent`.

## Целевая структура монорепы

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
```

### Роли директорий

- `backend/graph_core` — offline ingestion/precompute: embeddings, kNN graph, UniProt enrichment, Neo4j export/import.
- `backend/agents_core/session_agent` — LangGraph/LangChain agent, session memory, persistence, graph tools.
- `backend/app_contracts` — стабильные модели между UI и backend.
- `backend/app_services` — facade для UI и будущего API; здесь нет Streamlit-кода.
- `streamlit_ui` — presentation layer: чат, карточка белка, candidate switcher.
- `bioseq_retriever` — legacy/reference implementation для regression tests и offline parity checks. В runtime UI напрямую его не вызывает.

## Слои системы

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

## Главный runtime pipeline

### Happy path: пользователь вводит известный accession/gene/name/sequence

```text
1. Streamlit получает user message.
2. streamlit_ui/backend_adapter.py создаёт ChatTurnRequest.
3. BioSeqChatService получает request.
4. BioSeqChatService создаёт AppContext:
   - user_id
   - session_id
   - workspace_id
   - user_role
5. SessionGraphAgent.invoke(message, context) обрабатывает turn:
   - использует graph tools;
   - обновляет session state;
   - сохраняет active_accession / sequences / proteins.
6. BioSeqChatService делает deterministic post-processing:
   - берёт active_accession из current_state;
   - вызывает GraphRetrievalService.retrieve_candidates(...);
   - мапит Neo4j records в CandidateView/ProteinView.
7. Возвращается ChatTurnResult:
   - assistant_message;
   - candidates;
   - revealed_sections;
   - session snapshot;
   - warnings.
8. Streamlit обновляет chat history и protein card.
```

### Controlled miss: sequence не входит в подготовленный датасет

```text
1. UI отправляет sequence.
2. Backend нормализует sequence и считает sequence_hash.
3. GraphRetrievalService ищет Protein/Sequence по hash.
4. Если hash не найден:
   - локальная ProtT5-модель не запускается;
   - backend возвращает warning;
   - assistant_message объясняет, что sequence вне подготовленного набора.
5. Пользователю предлагается добавить sequence в offline ingestion pipeline.
```

## Offline data pipeline

Цель offline pipeline — заранее материализовать всё, что раньше делал `bioseq_retriever` в runtime.

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
   - protein_sequence normalized
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

## Runtime contracts

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

`ProteinView` должен быть совместим с текущим UI из `deploy/hf-spaces`:

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

## Backend services

### `BioSeqChatService`

Главный entry point для UI и будущего API.

```python
class BioSeqChatService:
    def submit_turn(self, request: ChatTurnRequest) -> ChatTurnResult:
        ...

    def get_session(self, session_id: str, user_id: str = "anonymous") -> SessionSnapshot:
        ...
```

Ответственность:

- вызывает `SessionGraphAgent`;
- не содержит Streamlit-зависимостей;
- собирает `ChatTurnResult`;
- централизует warnings и controlled misses;
- сохраняет session-first поведение.

### `GraphRetrievalService`

Детерминированный graph-only retrieval.

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

Ответственность:

- только Neo4j/read-only data access;
- никаких LLM;
- никаких Streamlit imports;
- не запускает ProtT5;
- делает scoring top candidates.

### `protein_view_mapper`

```python
def protein_record_to_view(record: dict) -> ProteinView:
    ...

def neighbor_record_to_candidate(record: dict, rank: int) -> CandidateView:
    ...
```

Ответственность:

- преобразует Neo4j flat/json records в UI-модель;
- парсит JSON properties: domains, keywords, xrefs, PubMed IDs;
- задаёт fallback values, чтобы UI не падал на неполных данных.

## Session agent

`SessionGraphAgent` остаётся центральным orchestrator для диалога.

Что он делает:

- принимает user message;
- пользуется graph tools;
- отвечает пользователю;
- обновляет `SessionAgentState`;
- сохраняет состояние через LangGraph checkpointer/store и `SessionRepository`;
- поддерживает `active_accession`, `active_sequence_id`, `proteins`, `sequences`, `working_memory`.

Что он не должен делать:

- рендерить UI;
- формировать Streamlit-specific state;
- напрямую возвращать `ProteinView`;
- запускать локальную ProtT5-модель.

## Graph tools

Текущие tools остаются, но их нужно усилить.

### Исправить neighbor lookup

Directed:

```cypher
MATCH (p:Protein {accession: $accession})-[r:SIMILAR_TO]->(n:Protein)
```

заменить на undirected:

```cypher
MATCH (p:Protein {accession: $accession})-[r:SIMILAR_TO]-(n:Protein)
```

### Добавить tools/service functions

- `resolve_prepared_input`
- `find_protein_by_sequence_hash`
- `retrieve_precomputed_candidates`
- `get_protein_card`
- `get_candidate_context`

Часть может быть не LangChain tools, а обычными service methods. Для UI-карточки предпочтительнее service methods: они детерминированные и не требуют LLM планирования.

## Graph schema target

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

### Constraints/indexes

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

## Frontend target

Streamlit остаётся тонким клиентом.

### `streamlit_ui/app.py`

Отвечает за:

- page config;
- layout;
- bootstrap UI state;
- `session_id`;
- reset/new conversation;
- mock/graph mode switch.

### `streamlit_ui/backend_adapter.py`

Единственная точка входа frontend -> backend:

```python
@st.cache_resource
def get_backend_service() -> BioSeqChatService:
    return create_bioseq_chat_service()

def submit_turn(message: str, session_id: str, user_id: str = "anonymous") -> ChatTurnResult:
    return get_backend_service().submit_turn(ChatTurnRequest(...))
```

### `streamlit_ui/components/chat.py`

В `mock` mode:

- оставляет scripted behavior.

В `graph` mode:

- отправляет каждый user input в `backend_adapter.submit_turn`;
- добавляет `assistant_message`;
- обновляет `candidates`;
- обновляет `revealed_sections`.

### `streamlit_ui/components/protein_card.py`

Остаётся почти без изменений:

- принимает `list[CandidateView]`;
- отображает top-5;
- даёт переключать candidate;
- рендерит sections.

Новые данные должны приходить уже в нужной форме. Карточка не должна делать backend queries.

## Режимы запуска

### Mock UI

```bash
BIOSEQ_BACKEND=mock streamlit run streamlit_ui/app.py
```

Назначение:

- demo без Neo4j;
- UI development;
- визуальные проверки.

### Graph runtime

```bash
BIOSEQ_BACKEND=graph streamlit run streamlit_ui/app.py
```

Назначение:

- реальная работа через `SessionGraphAgent`;
- Neo4j as read path;
- Postgres/Supabase persistence optional.

### Offline graph rebuild

```bash
python backend/graph_core/scripts/pipeline.py
python backend/graph_core/scripts/import_to_neo4j.py
```

Назначение:

- обновить подготовленный датасет;
- пересчитать similarity graph;
- переимпортировать Neo4j.

## End-to-end пайплайн

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

## Implementation roadmap

### Phase 1. Монорепа и UI import

- Перенести `streamlit_ui` из `deploy/hf-spaces`.
- Сохранить `BIOSEQ_BACKEND=mock`.
- Добавить `BIOSEQ_BACKEND=graph` как новый режим.
- Убедиться, что mock UI запускается без backend.

### Phase 2. Контракты

- Создать `backend/app_contracts`.
- Описать `ChatTurnRequest`, `ChatTurnResult`, `CandidateView`, `ProteinView`, `SessionSnapshot`.
- Обновить UI adapter, чтобы он работал через эти модели.

### Phase 3. Service layer

- Создать `backend/app_services`.
- Добавить `create_bioseq_chat_service`.
- Добавить `BioSeqChatService`.
- Добавить mapper из agent result/state в `ChatTurnResult`.
- Кэшировать service в Streamlit через `st.cache_resource`.

### Phase 4. Graph retrieval

- Добавить `GraphRetrievalService`.
- Исправить `get_protein_neighbors` на undirected query.
- Добавить `retrieve_candidates`.
- Добавить `get_protein_view`.
- Возвращать top-5 `CandidateView`.

### Phase 5. Session-first integration

- Каждый UI turn идёт через `SessionGraphAgent`.
- `session_id` живёт в `st.session_state`, но state хранится в backend persistence.
- Reset создаёт новый `session_id`.
- `active_accession` синхронизируется с выбранным candidate.

### Phase 6. Graph schema enrichment

- Расширить UniProt ingestion.
- Добавить sequence/hash/function/domains/keywords/go/xrefs/pubmed/alphafold.
- Перестроить `SIMILAR_TO` на `k=100`.
- Добавить `rank` на edges.
- Добавить constraints/full-text indexes.

### Phase 7. Regression and parity

- Сравнить old `bioseq_retriever` vs graph-only retrieval на известных белках.
- Метрики:
  - Recall@5;
  - Recall@10;
  - Recall@50;
  - top-1 exact match;
  - rank correlation.
- Зафиксировать baseline.

### Phase 8. API wrapper later

Когда локальные функции стабильны, добавить FastAPI как тонкую оболочку:

- `POST /chat/turn`
- `GET /sessions/{session_id}`
- `POST /sessions/{session_id}/reset`
- `GET /proteins/{accession}/card`

Бизнес-логика остаётся в `app_services`.

## Резюме по рекомендациям code review

Code review от 2026-05-01 в целом ревьюил старый runtime-пайплайн `bioseq_retriever`: LangGraph extract/translate/rank/rerank, FAISS, ProtT5, Mistral rerank и UniProt REST. После перехода на graph-first монорепу часть рекомендаций закрывается архитектурно, часть переносится в offline pipeline, а часть остаётся обязательным production-hardening после объединения.

### Что закрывается переходом на графы

#### OOM от ProtT5/FAISS на каждого пользователя

В старом варианте каждый пользовательский запуск мог загрузить ProtT5 и FAISS index. В целевой архитектуре runtime не грузит ProtT5 и не строит FAISS:

- embeddings считаются offline;
- top-k соседства уже лежат в Neo4j;
- `GraphRetrievalService` читает prepared graph;
- `bioseq_retriever` остаётся reference/offline parity layer.

Итог: основной риск “10 пользователей = 10 копий модели” уходит из runtime.

#### Cold start локальной модели при первом запросе

Рекомендация “загружать ML-модель на старте приложения” становится неактуальной для graph mode, потому что модель не нужна в app startup. Если когда-нибудь появится отдельный ingestion/embedding worker для новых данных, там модель действительно должна быть singleton, но это будет offline/worker слой, не пользовательский request path.

#### HDF5 memory pressure в пользовательском запросе

Проблема `load_embeddings` и загрузки всего HDF5 в память переносится из runtime в offline pipeline. Это всё ещё важно для больших датасетов, но уже не влияет на latency и стабильность пользовательской сессии.

Решение для offline слоя:

- читать HDF5 батчами;
- строить FAISS/kNN incremental или chunked;
- сохранять промежуточные parquet/npy artifacts;
- запускать rebuild как контролируемую job, а не внутри UI/agent.

#### Directory Traversal при чтении user filepath

В graph-first runtime UI не должен принимать произвольный filesystem path и читать файл на сервере. Пользователь может вставить sequence/FASTA text, но backend должен искать её по `sequence_hash` в prepared dataset.

Итог: `resolve_filepath_node` из старого retriever не должен быть частью graph runtime. Если загрузка файлов появится позже, это отдельный upload flow с sandboxed storage, validation и size limits.

#### Pickle cache accessions

В runtime graph mode pickle cache не нужен. Accessions и mapping хранятся в Neo4j и parquet/CSV artifacts. Если legacy retriever остаётся для regression tests, pickle стоит заменить на JSON/text там, но это не блокирует монорепу.

#### Хардкод путей к H5/FAISS в `rank_node`

Runtime graph mode не использует `rank_node`. Но для offline pipeline конфиг всё равно нужен:

- `BIOSEQ_DATA_DIR`;
- `GRAPH_CORE_OUTPUT_DIR`;
- `NEO4J_*`;
- embedding model/release;
- kNN параметры `K`, `MIN_SIM`.

### Что остаётся актуальным после объединения

#### Error handling и short-circuiting

Старый совет про `check_error` в LangGraph остаётся актуальным для любых agent flows. В `SessionGraphAgent` и будущих service-функциях нужно явно различать:

- controlled miss: sequence/protein не найден в prepared dataset;
- user input error: невалидная sequence;
- infrastructure error: Neo4j недоступен, credentials missing;
- LLM/tool error: agent не смог выполнить tool call.

Для UI это должно возвращаться как `ChatTurnResult.warnings` и понятный `assistant_message`, а не как падение Streamlit.

#### Таймауты внешних запросов

В runtime должно остаться минимум внешних запросов, но они всё равно возможны:

- LLM вызов через `SessionGraphAgent`;
- AlphaFold PDB fetch в UI structure viewer;
- offline UniProt enrichment;
- optional future text embedding/context rerank.

Правило:

- все HTTP-вызовы должны иметь timeout;
- retries/backoff нужны для offline enrichment;
- UI viewer failures должны быть non-fatal.

#### Rate limits LLM/API

Переход на графы убирает Mistral embeddings из основного retrieval path, но `SessionGraphAgent` всё равно использует LLM. Поэтому остаётся:

- лимитировать частоту пользовательских turns;
- задавать retry/backoff для LLM client;
- делать graceful degradation, если LLM недоступен;
- не использовать LLM там, где deterministic service достаточно.

#### Async I/O и неблокирующий API

Пока API не поднимаем, Streamlit может вызывать локальный service синхронно. После появления FastAPI стоит:

- сделать endpoint быстрым;
- использовать async Neo4j driver или threadpool для sync client;
- рассмотреть `ainvoke` для agent;
- не выполнять долгие offline задачи в request handler.

#### Очереди задач

Для graph runtime очередь не нужна на каждый обычный chat turn, потому что retrieval становится быстрым read-only запросом к Neo4j. Но очереди нужны для задач, которые остаются долгими:

- rebuild graph database;
- ingestion новых sequences;
- пересчёт embeddings;
- массовое UniProt enrichment;
- regression parity run;
- подготовка новых releases.

Рекомендуемый post-merge worker слой:

```text
API/Admin command
  -> Task queue
  -> Offline worker
  -> graph_core pipeline step
  -> artifacts
  -> Neo4j import/update
```

Можно использовать Celery/RQ/TaskIQ/ARQ позже; для первого объединения достаточно CLI scripts.

#### Singleton/pooling

Хотя ProtT5 уходит из runtime, singleton/pooling остаётся полезным для:

- `BioSeqChatService` в Streamlit через `st.cache_resource`;
- `Neo4jGraphClient` или driver lifecycle;
- LangGraph checkpointer/store;
- LLM client reuse.

Сейчас `Neo4jGraphClient.execute()` открывает driver на каждый запрос. Для production стоит перейти на долгоживущий driver с корректным `close()`.

#### Конфигурация

Все runtime и offline параметры должны уйти в env/config:

- `BIOSEQ_BACKEND=mock|graph`;
- `OPENAI_API_KEY`;
- `NEO4J_URI`;
- `NEO4J_DATABASE`;
- `NEO4J_USERNAME`;
- `NEO4J_PASSWORD`;
- `SUPABASE_DB_URL`;
- graph pipeline paths;
- kNN build params;
- feature flags для full-text/vector rerank.

### Post-merge hardening этапы

Эти пункты стоит добавить после базового объединения UI + session agent + graph retrieval.

#### Phase 9. Runtime reliability

- Ввести единый error taxonomy для `app_services`.
- Возвращать controlled errors через `ChatTurnResult.warnings`.
- Добавить timeouts/retries для LLM и любых HTTP calls.
- Перевести Neo4j client на долгоживущий driver/pool.
- Добавить health checks: Neo4j, persistence, LLM.

#### Phase 10. Security hardening

- Убрать runtime file path reading.
- Валидировать sequence input: длина, алфавит, max size.
- Не хранить secrets в примерах с реальными значениями.
- Ограничить custom Cypher tool или оставить только allowlisted read tools для UI flow.
- Добавить rate limit на user turns после появления API.

#### Phase 11. Offline pipeline scalability

- Переписать HDF5/embedding extraction на batch/chunk mode.
- Сделать incremental rebuild, чтобы не очищать всю БД при малом обновлении.
- Версионировать graph release: dataset, embedding model, kNN params, UniProt release.
- Сохранять regression baseline после каждого rebuild.

#### Phase 12. Multi-user production path

- Добавить FastAPI поверх `app_services`.
- Разделить API layer и offline worker layer.
- Подключить task queue для ingestion/rebuild jobs.
- Перевести долгие операции в background tasks.
- Добавить session persistence через Postgres/Supabase как обязательный режим для deploy.
- Добавить observability: logs, metrics, traces, latency by tool.

### Что не надо делать в первом merge

- Не строить highload queue для обычного graph retrieval до появления реальной нагрузки.
- Не поднимать FastAPI раньше стабилизации локальных service contracts.
- Не возвращать старый `bioseq_retriever` в UI runtime ради “на всякий случай”.
- Не делать Neo4j schema максимально нормализованной сразу; JSON properties для UI-карточки допустимы на первом этапе.
- Не внедрять text vector rerank, пока full-text/context filters не проверены на простом baseline.

## Acceptance criteria

1. Монорепа запускает mock UI без Neo4j.
2. Монорепа запускает graph UI через `SessionGraphAgent`.
3. UI не импортирует `bioseq_retriever` в graph mode.
4. UI не выполняет Cypher и не создаёт Neo4j client напрямую.
5. Runtime не загружает ProtT5.
6. Known accession/gene/name из БД возвращает assistant response и top-5 candidates.
7. Protein card получает данные через `CandidateView/ProteinView`.
8. Session state сохраняет active accession и историю между rerun в одном `session_id`.
9. Unknown sequence возвращает controlled miss.
10. Offline graph pipeline может пересобрать данные и переимпортировать Neo4j.

## Итоговая архитектурная позиция

Целевая монорепа должна быть graph-first и session-first:

- `graph_core` заранее готовит знание;
- Neo4j хранит prepared retrieval graph;
- `SessionGraphAgent` управляет разговором и памятью;
- `app_services` связывает agent state с UI-контрактами;
- Streamlit только отображает и отправляет turns;
- `bioseq_retriever` остаётся reference/offline parity layer, а не runtime dependency.

Так система будет взаимосвязанной, но без жёстких циклических зависимостей: данные идут снизу вверх, пользовательские turn-и идут сверху вниз через один backend facade.

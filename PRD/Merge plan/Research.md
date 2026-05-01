# Merge plan: UI из `deploy/hf-spaces` + graph/session backend

Дата: 2026-05-01

## Цель объединения

Нужно собрать единый репозиторий, где UI из ветки `deploy/hf-spaces` работает не напрямую с `bioseq_retriever`, а с backend-слоем на базе `backend/agents_core/session_agent`.

Пока полноценный API не поднимаем. На этом этапе достаточно определить Python-модели и функции-контракты, чтобы:

1. фронт мог вызвать backend как локальный модуль;
2. логика сессий, памяти и активного белка шла через `SessionGraphAgent`;
3. интеграция с Neo4j/графовой БД была главным источником результата;
4. UI получал тот же shape данных, который уже ожидает Streamlit protein card.

## Что есть в UI-ветке

В ветке `deploy/hf-spaces` Streamlit UI описан в корневом `README.md` и лежит в `streamlit_ui/`.

Ключевые файлы:

- `streamlit_ui/app.py` — entry point, session state, layout, выбор mock/real backend через `BIOSEQ_BACKEND`.
- `streamlit_ui/backend_adapter.py` — сейчас вызывает `run_bioseq_pipeline(prompt)` из `bioseq_retriever`.
- `streamlit_ui/mock/protein_loader.py` — главный UI-контракт `Candidate` и `ProteinView`.
- `streamlit_ui/components/protein_card.py` — рендерит правую карточку по `list[Candidate]`.
- `streamlit_ui/components/chat.py` — рендерит чат и вызывает `on_first_search`.

Текущий real adapter:

```python
def run_search(prompt: str) -> list[Candidate]:
    result = run_bioseq_pipeline(prompt)
    ...
    return [Candidate(protein=from_dict(record), match_score=0.0) ...]
```

В новой архитектуре этот adapter должен перестать ходить в `bioseq_retriever` напрямую. Он должен ходить в backend facade, который внутри использует `SessionGraphAgent`.

## Главный архитектурный принцип

UI не должен знать:

- как устроен Neo4j;
- какие Cypher-запросы выполняются;
- как LangGraph хранит state;
- как устроены tool calls;
- как работает retrieval/rerank.

UI должен знать только один стабильный контракт:

```python
submit_chat_turn(request: ChatTurnRequest) -> ChatTurnResult
```

Backend отвечает:

- assistant message для левой колонки;
- список candidates для правой карточки;
- какие секции карточки раскрыть;
- session snapshot для восстановления состояния.

## Runtime flow

```text
Streamlit chat input
  -> backend_adapter.submit_chat_turn(...)
  -> backend facade
  -> SessionGraphAgent.invoke(...)
  -> graph tools / session tools / memory tools
  -> normalized UI view-model
  -> Streamlit updates messages + protein card
```

Важно: сессии должны быть backend-owned. `st.session_state` может хранить только UI-кэш, но source of truth для:

- `session_id`;
- chat history;
- active accession;
- tracked proteins;
- tracked sequences;
- working memory;
- last analysis summary;

должен оставаться в `backend/agents_core/session_agent`.

## Контракты моделей

Рекомендуем добавить новый модуль:

```text
backend/app_contracts/
  __init__.py
  chat.py
  protein_view.py
```

Или, если хочется держать ближе к агенту:

```text
backend/agents_core/session_agent/contracts.py
```

### ChatTurnRequest

```python
from pydantic import BaseModel, Field

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

Назначение:

- `message` — текст пользователя;
- `session_id` — thread id для LangGraph checkpointer;
- `selected_accession` — если пользователь переключил top match в карточке;
- `ui_context` — технический escape hatch для UI-состояния без ломки контракта.

### ChatTurnResult

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

Назначение:

- `assistant_message` — то, что Streamlit добавляет в чат;
- `candidates` — то, что protein card отображает справа;
- `revealed_sections` — controlled progressive disclosure;
- `session` — backend snapshot для отладки/восстановления;
- `warnings` — например, “sequence outside prepared dataset”.

### CandidateView

UI уже ожидает:

```python
class Candidate(TypedDict):
    protein: ProteinView
    match_score: float
```

Лучше перенести это в backend как Pydantic-модель и оставить адаптер для Streamlit:

```python
class CandidateView(BaseModel):
    protein: ProteinView
    match_score: float
    rank: int
    similarity_score: float | None = None
    context_score: float | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
```

`match_score` лучше держать как процент `0..100`, потому что UI уже показывает `98.7%`.

### ProteinView

Должен сохранить shape из `streamlit_ui/mock/protein_loader.py`, чтобы `protein_card.py` почти не пришлось переписывать:

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

Минимальный backend должен уметь заполнить хотя бы:

- `accession`
- `name`
- `gene`
- `organism_scientific`
- `length`
- `annotation_score`
- `reviewed`
- `existence`
- `function_text`
- `disease`
- `keywords`
- `xrefs`
- `sequence`

`domains`, `go_terms`, `mol_weight`, `subcellular_locations`, `alphafold_accession` требуют расширить graph ingestion, потому что текущий `graph_core` импортирует только базовые UniProt поля.

### SessionSnapshot

Должен быть тонкой проекцией существующего state из `backend/agents_core/session_agent/models.py`:

```python
class SessionSnapshot(BaseModel):
    session_id: str
    user_id: str
    active_accession: str | None = None
    active_sequence_id: str | None = None
    proteins: list[ProteinRecord] = Field(default_factory=list)
    sequences: list[SequenceRecord] = Field(default_factory=list)
    working_set_ids: list[str] = Field(default_factory=list)
    current_mode: str | None = None
    session_summary: str | None = None
    last_analysis_summary: str | None = None
```

Не надо заводить отдельную session-систему для UI. Это должна быть сериализация уже существующего `SessionAgentState`.

## Backend-функции без API

Рекомендуемый новый слой:

```text
backend/app_services/
  __init__.py
  bioseq_chat.py
  protein_view_mapper.py
```

### Backend facade

```python
class BioSeqChatService:
    def __init__(self, agent: SessionGraphAgent):
        self.agent = agent

    def submit_turn(self, request: ChatTurnRequest) -> ChatTurnResult:
        ...

    def get_session(self, session_id: str, user_id: str = "anonymous") -> SessionSnapshot:
        ...

    def reset_session(self, session_id: str, user_id: str = "anonymous") -> SessionSnapshot:
        ...
```

На первом этапе `reset_session` можно сделать no-op или удалить state только из UI. Если используется Postgres checkpointer/store, настоящий reset лучше проектировать отдельно, чтобы не снести чужие данные.

### Factory

UI должен создавать service одной функцией:

```python
def create_bioseq_chat_service() -> BioSeqChatService:
    load_env_file(...)
    client = Neo4jGraphClient(...)
    persistence = create_persistence_resources(...)
    agent = SessionGraphAgent(model_name=DEFAULT_MODEL, client=client, persistence=persistence)
    return BioSeqChatService(agent)
```

Для Streamlit это нужно кэшировать:

```python
@st.cache_resource
def get_backend_service() -> BioSeqChatService:
    return create_bioseq_chat_service()
```

Так агент, Neo4j client config и persistence setup не будут пересоздаваться на каждый rerun.

## Как `SessionGraphAgent` должен отдавать UI-данные

Сейчас `SessionGraphAgent.invoke()` возвращает:

```python
result, current_state
```

`result` — LangChain/LangGraph response, `current_state` — state после derive patch.

Нужен mapper:

```python
def agent_result_to_chat_turn_result(
    request: ChatTurnRequest,
    agent_result: dict,
    current_state: dict,
) -> ChatTurnResult:
    ...
```

Он должен:

1. достать последний assistant message;
2. достать активный accession из `current_state["active_accession"]`;
3. достать candidates из tool output или отдельного graph retriever tool;
4. преобразовать graph records в `CandidateView`;
5. определить `revealed_sections`.

## Что добавить в graph/session tools

Текущие tools:

- `graph_schema_guide`
- `find_proteins`
- `get_protein_neighbors`
- `get_neighbor_diseases`
- `summarize_neighbor_disease_context`
- `run_read_cypher`
- session/memory tools

Для UI-контракта нужны дополнительные graph tools или service-функции.

### 1. `resolve_prepared_input`

```python
def resolve_prepared_input(text: str, limit: int = 5) -> list[ProteinLookupHit]:
    ...
```

Ищет по:

- accession;
- gene;
- entry name;
- protein name;
- sequence hash, если в БД добавлены `protein_sequence` и `sequence_hash`.

Если вход не найден в подготовленной БД, backend должен вернуть controlled miss, а не запускать локальную ProtT5-модель.

### 2. `retrieve_precomputed_candidates`

```python
def retrieve_precomputed_candidates(
    accession: str,
    limit: int = 5,
    neighbor_pool: int = 50,
    context: str | None = None,
) -> list[CandidateView]:
    ...
```

Делает:

1. `MATCH (p:Protein {accession})-[r:SIMILAR_TO]-(n:Protein)`;
2. берёт top-50/top-100;
3. применяет context score, если есть;
4. возвращает top-5 для UI.

### 3. `get_protein_card`

```python
def get_protein_card(accession: str) -> ProteinView:
    ...
```

Возвращает одну карточку без retrieval. Нужна, когда пользователь переключил candidate в segmented control.

### 4. `get_candidate_context`

```python
def get_candidate_context(accessions: list[str]) -> list[CandidateView]:
    ...
```

Batch-загрузка карточек для top-5, чтобы UI не делал пять отдельных запросов.

## Что изменить в БД для UI

Чтобы UI-карточка была полной, нужно расширить `graph_core` ingestion сверх текущих полей:

### Сейчас есть

- accession
- entry_name
- protein_name
- gene_primary
- organism_name
- sequence_length
- reviewed
- annotation_score
- protein_existence
- ensembl_ids
- disease nodes/edges

### Нужно добавить

- `protein_sequence`
- `sequence_hash`
- `mol_weight`
- `organism_common`
- `taxon_id`
- `function_text`
- `subcellular_locations`
- `keywords`
- `go_terms`
- `pubmed_ids`
- `xrefs`
- `alphafold_accession`
- `domains`
- `alternative_names`
- `disease_name`
- `disease_acronym`
- `disease_mim_id`
- `disease_description`
- `disease_variants`

Часть сложных списков можно хранить как JSON-string property в Neo4j или отдельными узлами/рёбрами. Для первого merge проще JSON properties:

```text
Protein.domains_json
Protein.keywords_json
Protein.go_terms_json
Protein.pubmed_ids_json
Protein.xrefs_json
Protein.alt_names_json
Protein.subcellular_locations_json
```

Позже это можно нормализовать в графовые сущности.

## Что изменить на backend

### Шаг 1. Перенести UI-контракты в backend

Скопировать shape из `streamlit_ui/mock/protein_loader.py` в backend Pydantic-модели:

- `DomainFeature`
- `DiseaseInfo`
- `ProteinView`
- `CandidateView`
- `ChatTurnRequest`
- `ChatTurnResult`
- `SessionSnapshot`

UI должен импортировать эти модели или получать `model_dump()` dict.

### Шаг 2. Сделать mapper из Neo4j records в `ProteinView`

Нужна функция:

```python
def protein_record_to_view(record: dict) -> ProteinView:
    ...
```

Она заменит текущий `from_dict(UniProt JSON)` из UI. Старый `from_dict` можно оставить только для mock/test fixtures.

### Шаг 3. Добавить graph-only retrieval service

Не через `bioseq_retriever`, а через Neo4j:

```python
class GraphRetrievalService:
    def resolve_input(...)
    def retrieve_candidates(...)
    def get_protein_view(...)
```

Этот сервис можно использовать и внутри tools, и внутри `BioSeqChatService`.

### Шаг 4. Интегрировать с `SessionGraphAgent`

`BioSeqChatService.submit_turn()` должен:

1. создать `AppContext` из `ChatTurnRequest`;
2. вызвать `SessionGraphAgent.invoke(message, context)`;
3. получить `current_state`;
4. если agent state содержит active accession, загрузить candidates/card через `GraphRetrievalService`;
5. вернуть `ChatTurnResult`.

Важная деталь: retrieval для UI можно делать после agent invoke как deterministic post-processing. Тогда LLM отвечает на вопрос, а карточка всегда строится машинно и стабильно.

### Шаг 5. Обновить session extraction

`services/session_state.py` сейчас вытаскивает белки из JSON tool outputs по полям `accession`, `neighbor_accession`, `target_accession`.

Нужно добавить поддержку новых полей:

- `candidate_accession`
- `rank`
- `match_score`
- `sequence_hash`
- `selected_accession`

И обновлять:

- `active_accession`
- `working_set_ids`
- `last_tool_results_summary`

### Шаг 6. Настроить зависимости и конфиг

В `.env`/settings должны быть:

```text
OPENAI_API_KEY=...
NEO4J_URI=...
NEO4J_DATABASE=...
NEO4J_USERNAME=...
NEO4J_PASSWORD=...
SUPABASE_DB_URL=... optional
BIOSEQ_BACKEND=graph
```

`MISTRAL_API_KEY` нужен только если остаётся старый retriever/rerank path. Для новой graph-first тактики UI не должен требовать Mistral.

## Что изменить на frontend

### Шаг 1. Перенести `streamlit_ui/` из ветки

Забрать из `deploy/hf-spaces`:

- `streamlit_ui/app.py`
- `streamlit_ui/components/`
- `streamlit_ui/assets/`
- `streamlit_ui/mock/`
- `streamlit_ui/requirements.txt`
- `streamlit_ui/README.md`
- `streamlit_ui/TECH_SPEC.md`

Mock fixtures можно оставить для dev/demo.

### Шаг 2. Заменить `backend_adapter.py`

Старый adapter:

```python
from bioseq_retriever.src.pipeline import run_bioseq_pipeline
```

Новый adapter:

```python
from backend.app_services.bioseq_chat import create_bioseq_chat_service
from backend.app_contracts.chat import ChatTurnRequest

def submit_turn(message: str, session_id: str, user_id: str = "anonymous") -> ChatTurnResult:
    service = get_cached_service()
    return service.submit_turn(ChatTurnRequest(...))
```

Для совместимости можно оставить `run_search(prompt) -> list[Candidate]`, но лучше сразу перейти на `submit_turn`, потому что UI теперь должен получать и chat reply, и candidates, и session state.

### Шаг 3. Упростить scripted conversation

`mock/conversation.py` сейчас управляет шагами разговора. В real mode это должен делать backend.

Нужно разделить режимы:

- `BIOSEQ_BACKEND=mock` — старое scripted поведение;
- `BIOSEQ_BACKEND=graph` — все assistant replies и card reveals приходят из backend.

В graph mode `chat.py` не должен сам решать, какие секции раскрывать. Он должен брать `revealed_sections` из `ChatTurnResult`.

### Шаг 4. Сохранить UI session id

В `_bootstrap_session()` добавить:

```python
if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex
```

И передавать его в каждый backend call. Reset может создавать новый `session_id`.

### Шаг 5. Protein card оставить почти без изменений

`protein_card.render(candidates, revealed)` уже хороший контракт. Главное, чтобы backend отдавал `CandidateView.model_dump()` с теми же ключами.

Изменения нужны только косметические:

- показывать реальные `match_score`;
- возможно добавить `evidence` expander;
- корректно обработать missing sections, если БД пока не содержит domains/go_terms.

## Минимальный контракт первого merge

Чтобы быстро получить работающую интеграцию без полного API:

```python
def submit_chat_turn(
    message: str,
    session_id: str,
    user_id: str = "anonymous",
) -> dict:
    """
    Returns:
    {
      "assistant_message": str,
      "candidates": list[CandidateView dict],
      "selected_candidate_index": 0,
      "revealed_sections": set[str],
      "session": SessionSnapshot dict,
      "warnings": list[str],
    }
    """
```

Первый happy path:

1. пользователь вводит accession/gene/protein name из БД;
2. backend через `SessionGraphAgent` определяет active protein;
3. graph retrieval достаёт top-5 candidates;
4. UI показывает ответ и карточку.

FASTA/raw sequence можно поддержать только если `sequence_hash` уже есть в БД. Если нет:

```text
Эта последовательность не найдена в подготовленном датасете. Добавьте её в offline ingestion pipeline.
```

## Этапы реализации

### Этап 1. Контракты и локальный adapter

- Добавить backend Pydantic-модели.
- Добавить `BioSeqChatService`.
- Добавить `streamlit_ui/backend_adapter.py`, который вызывает service напрямую.
- UI в `graph` mode получает `ChatTurnResult`.

### Этап 2. Graph retrieval для карточки

- Добавить `GraphRetrievalService`.
- Исправить neighbor query на undirected `SIMILAR_TO`.
- Сделать `get_protein_card`.
- Сделать `retrieve_precomputed_candidates`.

### Этап 3. Расширить graph ingestion

- Добавить sequence/hash/function/domains/keywords/go/xrefs/pubmed.
- Перестроить Neo4j export/import.
- Поднять completeness карточки до уровня mock UI.

### Этап 4. Session-first поведение

- Убедиться, что каждый UI turn идёт через `SessionGraphAgent`.
- Сохранять active accession, sequences, working set.
- Reset/new conversation создаёт новый `session_id`.
- Проверить Postgres/Supabase persistence fallback.

### Этап 5. API позже

Когда локальные функции стабилизируются, поверх них можно тонко поставить FastAPI:

- `POST /chat/turn`
- `GET /sessions/{session_id}`
- `POST /sessions/{session_id}/reset`
- `GET /proteins/{accession}/card`

Но API должен быть оболочкой над теми же моделями и service-функциями, а не новой бизнес-логикой.

## Acceptance criteria

1. `BIOSEQ_BACKEND=mock streamlit run streamlit_ui/app.py` сохраняет старый scripted demo.
2. `BIOSEQ_BACKEND=graph streamlit run streamlit_ui/app.py` вызывает `SessionGraphAgent`.
3. Один и тот же `session_id` сохраняет историю и active accession между Streamlit reruns.
4. UI получает `list[CandidateView]` и отображает top-5 в существующем segmented control.
5. Protein card рендерит данные из Neo4j, а не из `test_data_from_database`.
6. Если белок/sequence не найден в подготовленной БД, backend возвращает controlled miss без запуска локальной ProtT5-модели.
7. Никакая UI-логика не выполняет Cypher и не обращается напрямую к Neo4j.

## Главный риск

Текущий UI-контракт богаче, чем текущая Neo4j-схема. В `graph_core` уже есть достаточно для базового top-match поиска, но для полной карточки не хватает UniProt details: domains, function comments, keywords, GO terms, PubMed refs, AlphaFold accession, molecular weight и sequence.

Поэтому объединение лучше делать в два слоя:

1. сначала подключить session agent и basic graph candidates;
2. затем расширить ingestion, чтобы карточка стала такой же богатой, как mock `ProteinView`.

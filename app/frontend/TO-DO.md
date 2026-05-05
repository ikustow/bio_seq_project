# TO-DO: адаптация frontend под backend sessions и `app/backend/agents_core`

Дата анализа: 2026-05-06  
Область: `app/frontend` + связка с `app/backend/agents_core`, `app/backend/app_services`, `app/backend/app_contracts`.

## 0. Короткий вывод

Сейчас frontend визуально уже умеет показывать чат и карточку белка, но session flow не связан с backend session model.

Главная проблема: `app/frontend/vector_db_adapter.py` вызывает `backend.agents_core.retriever_agent.pipeline_interface.run_pipeline_interface(prompt)`, а `pipeline_interface.py` при каждом вызове создает нового агента и новый `AppContext`. Если `APP_SESSION_ID` не задан глобально, генерируется новый `retriever_<uuid>`, то есть LangGraph `thread_id` и `public.chat_sessions.session_id` не совпадают между сообщениями одного Streamlit-чата.

Правильная целевая граница уже есть в backend:

```text
Streamlit UI
  -> ChatTurnRequest
  -> BioSeqChatService / retriever-chat service
  -> AppContext(session_id=user/session stable id)
  -> BioSeqRetrieverGraphAgent
  -> LangGraph checkpoint thread_id == session_id
  -> public.chat_sessions compact snapshot
  -> ChatTurnResult
  -> Streamlit state + Protein card
```

## 1. Что сейчас есть во frontend

### 1.1 Streamlit state локальный и не backend-aware

`app/frontend/app.py` bootstrap-ит только UI-состояние:

- `messages`
- `conv_state`
- `candidates`
- `selected_candidate_idx`
- `card_sections_revealed`
- `pending_assistant`
- `on_first_search`

Нет backend-состояния:

- `session_id`
- `user_id`
- `workspace_id`
- `user_role`
- `backend_session`
- `backend_warnings`
- `pipeline_snapshot`
- `active_accession`
- `active_sequence_id`

Файл: `app/frontend/app.py`, блок `_bootstrap_session()` около строк 75-91.

### 1.2 Vector DB mode ходит напрямую в `pipeline_interface`

`config.USE_VECTOR_DB_MODE = True`, и каждый submit идет в:

```python
vector_db_adapter.run_prompt(text)
```

Адаптер:

```python
result = run_pipeline_interface(prompt)
```

Файлы:

- `app/frontend/config.py`, строки 3-5
- `app/frontend/app.py`, строки 132-150 и 188-190
- `app/frontend/vector_db_adapter.py`, строки 15 и 19-23

Это обходит `app_contracts.ChatTurnRequest`, `app_services.BioSeqChatService`, `SessionSnapshot`, `ChatTurnResult` и selection/session логику.

### 1.3 `backend_adapter.py` - отдельная старая ветка

`app/frontend/backend_adapter.py` подключает старый `bioseq_retriever.src.pipeline.run_bioseq_pipeline`, не `app/backend/agents_core`.

Это конфликтующий интеграционный путь:

- `BACKEND_MODE == "real"` в `app.py` использует legacy pipeline;
- `USE_VECTOR_DB_MODE == True` использует `agents_core/retriever_agent/pipeline_interface`;
- оба пути возвращают свои формы данных.

Нужно оставить один целевой backend mode для Stage 2: `app_contracts`/`app_services`/`agents_core`.

## 2. Что сейчас есть в backend для sessions

### 2.1 Внешний контракт уже описан

`app/backend/app_contracts/chat.py`:

```python
class ChatTurnRequest(BaseModel):
    message: str
    session_id: str
    user_id: str = "anonymous"
    workspace_id: str | None = None
    user_role: str | None = None
    selected_accession: str | None = None
    selected_candidate_index: int | None = None
    ui_context: dict[str, Any] = Field(default_factory=dict)

class ChatTurnResult(BaseModel):
    session_id: str
    assistant_message: str
    candidates: list[CandidateView]
    selected_candidate_index: int
    revealed_sections: set[str]
    session: SessionSnapshot
    pipeline: BioSeqPipelineSnapshot | None
    warnings: list[str]
```

Это должен быть основной контракт frontend/backend.

### 2.2 `AppContext.session_id` уже является LangGraph `thread_id`

`BioSeqRetrieverGraphAgent.invoke()`:

```python
config = {"configurable": {"thread_id": context.session_id}}
```

Файл: `app/backend/agents_core/retriever_agent/agent.py`, строки 79-89.

После run агент:

- добавляет assistant summary message в LangGraph state;
- строит compact session patch;
- пишет patch через `session_repository.upsert_session(context, session_patch)`.

### 2.3 `public.chat_sessions` хранит compact snapshot

`PostgresSessionRepository` читает/пишет `public.chat_sessions`.

Файл: `app/backend/agents_core/shared/services/persistence.py`, строки 24-150.

Важно:

- если `SUPABASE_DB_URL` не задан, будет `InMemorySaver` + `NullSessionRepository`;
- `create_persistence_resources()` создает LangGraph checkpoint/store tables, но не создает `public.chat_sessions`;
- таблицу `public.chat_sessions` надо создать отдельно миграцией.

### 2.4 `BioSeqChatService` почти подходит, но graph factory сейчас сломан

`BioSeqChatService.submit_turn()` уже умеет:

- принимать `ChatTurnRequest`;
- строить `AppContext`;
- патчить `active_accession` при выборе кандидата;
- запускать sequence/filepath compatibility pipeline;
- возвращать `ChatTurnResult`.

Файл: `app/backend/app_services/bioseq_chat.py`, строки 35-119.

Но `create_bioseq_chat_service()` в graph mode импортирует отсутствующий пакет:

```python
from backend.agents_core.session_agent.agent import SessionGraphAgent
```

Файл: `app/backend/app_services/service_factory.py`, строки 48-84.  
Папки `app/backend/agents_core/session_agent` сейчас нет. Рабочий агент сегодня - `app/backend/agents_core/retriever_agent`.

## 3. Критичные расхождения frontend vs backend

### P0. Нет стабильного frontend `session_id`

Сейчас frontend не генерирует и не передает `session_id`. `pipeline_interface.py` берет:

```python
session_id=os.getenv("APP_SESSION_ID", f"retriever_{uuid.uuid4().hex[:8]}")
```

Файл: `app/backend/agents_core/retriever_agent/pipeline_interface.py`, строки 31-38.

Следствие:

- каждый submit может попасть в новый LangGraph thread;
- `get_current_state()` и message history не работают как продолжение беседы;
- Supabase `chat_sessions` не является источником правды для UI;
- в multi-user Streamlit нельзя использовать `APP_SESSION_ID` из env, иначе все пользователи могут разделить один backend thread.

### P0. Frontend вызывает raw `GraphState`, а backend уже имеет `ChatTurnResult`

`vector_db_adapter.run_prompt()` получает raw dict `GraphState` и сам формирует assistant text/candidates/reveals.

Проблема:

- frontend зависит от внутренних полей агента (`final_results`, `sequence_type`, `is_confident`);
- любые изменения LangGraph state ломают UI;
- не используются `SessionSnapshot`, `warnings`, `pipeline`, `selected_candidate_index`.

Цель: frontend должен работать с `ChatTurnResult`, а не с raw `GraphState`.

### P0. Agent создается на каждый submit

`run_pipeline_interface()` каждый раз вызывает `create_bioseq_retriever_graph_agent()`.

Следствие:

- лишние подключения/инициализация;
- in-memory persistence теряется между вызовами, потому что новый agent получает новый `InMemorySaver`;
- даже при одинаковом `session_id` без Postgres persistence state не будет надежно жить между submit-ами.

Для Streamlit нужен `st.cache_resource` вокруг service/agent factory.

### P0. `BioSeqChatService` graph mode указывает на отсутствующий `session_agent`

Пока нет `backend.agents_core.session_agent.agent.SessionGraphAgent`, нельзя просто переключить frontend на `create_bioseq_chat_service()` с `BIOSEQ_BACKEND=graph`.

Нужно одно из двух:

1. Краткосрочно: добавить factory для chat service поверх `BioSeqRetrieverGraphAgent`.
2. Долгосрочно: реализовать `session_agent` и оставить `retriever_agent` как tool/pipeline внутри общего chat agent.

### P1. `BioSeqChatService` не полностью совместим с `BioSeqRetrieverGraphAgent.invoke()`

`BioSeqRetrieverGraphAgent.invoke()` возвращает `(result, current_state)`, где `result` - state до добавления synthetic AIMessage, а `current_state` - state после `update_state()`.

`BioSeqChatService` берет assistant text из `result`:

```python
assistant_message = _assistant_message(result)
```

Файл: `app/backend/app_services/bioseq_chat.py`, строки 95-96 и 200-204.

Для `retriever_agent` это риск: `result["messages"]` может содержать только user HumanMessage, а assistant summary уже лежит в `current_state`.

Нужно либо:

- поменять service на чтение assistant message из `state`;
- либо поменять `retriever_agent.invoke()` так, чтобы returned `result` тоже содержал AIMessage;
- либо сделать отдельный retriever-chat adapter, который берет assistant text из `_assistant_message_from_state`/`current_state`.

### P1. Имена revealed sections не совпадают

Frontend card ожидает:

```text
header, keyfacts, function, domains, structure, keywords, disease, references
```

Файл: `app/frontend/components/protein_card.py`, строки 15-24.

`BioSeqChatService._revealed_sections()` возвращает:

```text
overview, evidence, function, disease, domains, references
```

Файл: `app/backend/app_services/bioseq_chat.py`, строки 217-230.

`overview` и `evidence` frontend просто игнорирует, поэтому header/keyfacts/structure/keywords останутся locked при прямом использовании `ChatTurnResult`.

Нужно унифицировать enum секций. Рекомендуемый backend->frontend set:

```text
header, keyfacts, function, domains, structure, keywords, disease, references
```

### P1. `CandidateView` и frontend `Candidate` похожи, но не одинаковы

Frontend mock:

```python
class Candidate(TypedDict):
    protein: ProteinView
    match_score: float
```

Backend:

```python
class CandidateView(BaseModel):
    protein: ProteinView
    match_score: float
    rank: int
    similarity_score: float | None
    context_score: float | None
    evidence: list[EvidenceItem]
```

Файлы:

- `app/frontend/mock/protein_loader.py`, строки 25-52
- `app/backend/app_contracts/protein_view.py`, строки 51-57

Нужно решить:

- либо frontend полностью переходит на `CandidateView.model_dump()`;
- либо остается тонкий mapper `CandidateView -> frontend Candidate`, но он должен быть единственным и протестированным.

### P1. `match_score` scale не зафиксирован

Backend `GraphRetrievalService` отдает `match_score=1.0` для target и similarity-like значения `0..1`.

Frontend card показывает percent-like score. Сейчас `vector_db_adapter._score_as_percent()` конвертирует `<=1` в проценты.

Если frontend начнет принимать `CandidateView` напрямую, `1.0` будет отображаться как `1.0%`, если не адаптировать renderer.

Нужно явно закрепить контракт:

- backend score хранится как `0..1`;
- UI renderer отображает `score * 100`;
- mock data тоже приводится к той же шкале или помечается как percent.

### P1. `DomainFeature` schema не совпадает

Frontend domain diagram ожидает `type`, `name`, `start`, `end`.

Файл: `app/frontend/components/domain_diagram.py`, строки 33-52.

Backend `DomainFeature` имеет `name`, `start`, `end`, `description`, но не имеет `type`.

Файл: `app/backend/app_contracts/protein_view.py`, строки 6-10.

Сейчас `vector_db_adapter` подставляет `type="Domain"` при маппинге. При прямом использовании backend contracts карточка может упасть на `d["type"]`.

Нужно:

- добавить `type: str = "Domain"` в backend `DomainFeature`;
- или сделать frontend tolerant к отсутствующему `type`;
- лучше сделать оба: backend field + frontend fallback.

### P1. `DiseaseInfo` schema не совпадает

Frontend ожидает:

```text
name, acronym, mim_id, description, variants
```

Backend отдает:

```text
names, count, description, xrefs
```

Файлы:

- `app/frontend/mock/protein_loader.py`, строки 17-22
- `app/backend/app_contracts/protein_view.py`, строки 13-18
- `app/frontend/components/protein_card.py`, строки 168-183

Сейчас `vector_db_adapter` делает промежуточный перевод. При прямом `CandidateView` карточка упадет на `d["name"]`.

Нужно унифицировать болезнь в `app_contracts` или сделать `protein_card` renderer под backend `DiseaseInfo`.

### P1. Candidate selection не синхронизируется с backend

`protein_card._render_switcher()` меняет только `st.session_state.selected_candidate_idx`.

Файл: `app/frontend/components/protein_card.py`, строки 234-265.

Backend уже ожидает:

- `selected_accession`
- `selected_candidate_index`

Файл: `app/backend/app_contracts/chat.py`, строки 18-20.

Нужно отправлять selection event в backend, чтобы агент обновлял `active_accession` через `update_current_state()`.

### P1. Reset очищает только frontend

`components/chat.py::_reset_conversation()` удаляет локальные ключи.

Файл: `app/frontend/components/chat.py`, строки 54-64.

Backend checkpoint и `public.chat_sessions` не очищаются. Если после reset оставить тот же `session_id`, старый agent state может вернуться.

Минимальный вариант: при Reset генерировать новый `session_id`.  
Полный вариант: добавить backend delete/reset endpoint/repository method для старого session id.

### P2. `app/frontend/requirements.txt` не содержит backend deps

Frontend requirements содержит только UI deps:

```text
streamlit, pandas, plotly, py3Dmol, requests
```

Для in-process интеграции с `agents_core` нужны root deps:

```text
neo4j, langchain, langchain-openai, langchain-mistralai,
langgraph, langgraph-checkpoint-postgres, psycopg[binary]
```

Сейчас они есть в root `requirements.txt`, но не в `app/frontend/requirements.txt`. Если Streamlit Cloud ставит только frontend requirements, graph mode не поднимется.

### P2. Нет HTTP/API слоя

В `app/backend` нет FastAPI/Flask server endpoint для `ChatTurnRequest`.

Варианты:

1. In-process Streamlit adapter: быстрее для локального MVP, но смешивает UI и backend runtime.
2. FastAPI backend: чище для deployment/multi-user, но надо добавить server, CORS/auth, env/deploy.

Для текущего репозитория самый быстрый путь - in-process adapter через cached service, но контракт оставить `ChatTurnRequest/ChatTurnResult`, чтобы позже заменить transport на HTTP без переписывания UI.

## 4. Целевой frontend session state

Добавить в `_bootstrap_session()`:

```python
if "session_id" not in st.session_state:
    st.session_state.session_id = f"streamlit_{uuid.uuid4().hex}"
if "user_id" not in st.session_state:
    st.session_state.user_id = "streamlit-user"  # или из auth layer
if "workspace_id" not in st.session_state:
    st.session_state.workspace_id = None
if "user_role" not in st.session_state:
    st.session_state.user_role = None
if "backend_session" not in st.session_state:
    st.session_state.backend_session = None
if "backend_warnings" not in st.session_state:
    st.session_state.backend_warnings = []
if "pipeline_snapshot" not in st.session_state:
    st.session_state.pipeline_snapshot = None
```

Правило:

- `session_id` создается один раз на browser session;
- `session_id` не должен читаться из env для обычного пользователя;
- env `APP_SESSION_ID` оставить только для CLI/debug;
- Reset либо генерирует новый `session_id`, либо вызывает backend reset.

## 5. Целевой frontend adapter

Заменить `vector_db_adapter.py` на adapter уровня chat contract.

Псевдокод:

```python
@st.cache_resource
def get_chat_service():
    return create_bioseq_retriever_chat_service()

def submit_turn(message: str, *, selected_accession: str | None = None):
    request = ChatTurnRequest(
        message=message,
        session_id=st.session_state.session_id,
        user_id=st.session_state.user_id,
        workspace_id=st.session_state.workspace_id,
        user_role=st.session_state.user_role,
        selected_accession=selected_accession,
        selected_candidate_index=st.session_state.selected_candidate_idx,
        ui_context={
            "revealed_sections": sorted(st.session_state.card_sections_revealed),
            "active_accession": st.session_state.get("active_accession"),
        },
    )
    return get_chat_service().submit_turn(request)
```

После `ChatTurnResult`:

- append user message locally;
- append `result.assistant_message`;
- set `st.session_state.candidates = map_candidates(result.candidates)`;
- set `selected_candidate_idx = result.selected_candidate_index`;
- set `card_sections_revealed = map_sections(result.revealed_sections)`;
- set `backend_session = result.session.model_dump()`;
- set `pipeline_snapshot = result.pipeline.model_dump() if result.pipeline else None`;
- set `backend_warnings = result.warnings`.

## 6. Что нужно поменять в backend, чтобы frontend подключился чисто

### 6.1 Добавить рабочий factory без отсутствующего `session_agent`

Вариант A: новый factory:

```python
create_bioseq_retriever_chat_service()
```

Он должен:

- создать `Neo4jGraphClient`;
- создать один `GraphRetrievalService`;
- создать `PersistenceResources`;
- создать `BioSeqRetrieverGraphAgent`;
- завернуть его в `BioSeqChatService` или отдельный `RetrieverChatService`;
- сохранить `ExitStack` на service, как уже сделано в `create_bioseq_chat_service()`.

Вариант B: расширить `create_bioseq_chat_service()`:

```text
BIOSEQ_BACKEND=mock -> MockBioSeqChatService
BIOSEQ_BACKEND=retriever_graph -> BioSeqRetrieverGraphAgent-backed service
BIOSEQ_BACKEND=graph -> future SessionGraphAgent-backed service
```

### 6.2 Исправить assistant message compatibility

Если `BioSeqChatService` будет использовать `BioSeqRetrieverGraphAgent`, нужно брать assistant response из `current_state`, а не из raw `result`, или вернуть updated `result` из агента.

### 6.3 Унифицировать revealed sections

Поменять `_revealed_sections()` в `BioSeqChatService` на frontend section keys:

```python
sections = {"header", "keyfacts", "structure"}
if protein.function_text: sections.add("function")
if protein.domains: sections.add("domains")
if protein.keywords or protein.go_terms: sections.add("keywords")
if protein.disease: sections.add("disease")
if protein.pubmed_ids or protein.xrefs: sections.add("references")
```

### 6.4 Унифицировать `ProteinView` nested schemas

Нужно выбрать один контракт.

Рекомендация: сделать `app_contracts.ProteinView` UI-достаточным и убрать frontend mock TypedDict как runtime contract.

Изменения:

- добавить `type` в backend `DomainFeature`;
- расширить backend `DiseaseInfo` полями `name`, `acronym`, `mim_id`, `variants` или адаптировать frontend renderer под `names/xrefs`;
- обеспечить `alphafold_accession = accession`, если поле отсутствует в Neo4j;
- явно документировать `match_score` как `0..1`.

## 7. Очередность работ

### P0 - чтобы sessions вообще заработали

- [ ] Добавить во frontend стабильный `session_id` в `st.session_state`.
- [ ] Перестать вызывать `pipeline_interface.run_pipeline_interface()` из UI.
- [ ] Сделать cached in-process chat service adapter на `ChatTurnRequest/ChatTurnResult`.
- [ ] Добавить backend factory для `BioSeqRetrieverGraphAgent` как chat service, не импортирующий отсутствующий `session_agent`.
- [ ] Передавать `session_id`, `user_id`, `workspace_id`, `user_role` в каждый backend turn.
- [ ] Сохранять `ChatTurnResult.session` в `st.session_state.backend_session`.
- [ ] На Reset генерировать новый `session_id` минимум; позже добавить backend reset/delete.

### P1 - чтобы карточка и state были согласованы

- [ ] Свести section keys к `header/keyfacts/function/domains/structure/keywords/disease/references`.
- [ ] Унифицировать `CandidateView` и frontend `Candidate`, убрать дублирующие runtime types из `mock/protein_loader.py` или оставить только для mock data loading.
- [ ] Исправить score scale: backend `0..1`, frontend display percent.
- [ ] Исправить `DomainFeature`: добавить/fallback `type`.
- [ ] Исправить `DiseaseInfo`: единая форма для backend и card renderer.
- [ ] Отправлять candidate selection в backend через `selected_accession` + `selected_candidate_index`.
- [ ] Показывать backend warnings/persistence mode в debug/status expander.

### P2 - качество и deployment

- [ ] Обновить `README.md` и `TECH_SPEC.md`: frontend уже не только mock.
- [ ] Развести modes: `mock`, `retriever_graph_local`, `api`.
- [ ] Обновить `app/frontend/requirements.txt` или deployment docs для graph mode.
- [ ] Добавить tests для mapper-а `ChatTurnResult -> frontend state`.
- [ ] Добавить smoke test: два submit-а с одним `session_id` дают одну LangGraph history.
- [ ] Добавить Supabase migration для `public.chat_sessions`, если ее еще нет в infra.
- [ ] Позже вынести backend в HTTP API: `POST /chat/turn`, `GET /chat/session/{session_id}`, `POST /chat/session/{session_id}/select`, `DELETE /chat/session/{session_id}`.

## 8. Рекомендуемый минимальный implementation plan

1. В backend добавить `create_bioseq_retriever_chat_service()` и не трогать future `session_agent`.
2. Во frontend заменить `vector_db_adapter.run_prompt()` на `chat_backend_adapter.submit_turn()`, который строит `ChatTurnRequest`.
3. В `app.py` добавить backend session keys и stable `session_id`.
4. В `components/chat.py` изменить `SubmitHandler`, чтобы он возвращал полноценный объект/словарь результата, а не только `(reply, reveals)`.
5. В `protein_card.py` принимать backend `CandidateView` dict и нормализовать score/domain/disease прямо перед render.
6. В `protein_card._render_switcher()` добавить callback/handler для отправки selection update.
7. Reset: очистить UI и создать новый `session_id`; старый backend state пока оставить как historical session.
8. Добавить один smoke test без Streamlit UI:

```python
result1 = adapter.submit("SEQUENCE...", session_id="test_session")
result2 = adapter.submit("what diseases?", session_id="test_session")
assert result2.session.session_id == "test_session"
assert len(result2.session.message_history) >= len(result1.session.message_history)
```

## 9. Главные риски

- Если оставить `pipeline_interface`, sessions будут выглядеть работающими только при глобальном `APP_SESSION_ID`, что опасно для multi-user и ломает изоляцию.
- Если использовать `create_bioseq_chat_service()` как есть с `BIOSEQ_BACKEND=graph`, будет import error из-за отсутствующего `session_agent`.
- Если подключить `ChatTurnResult` напрямую без schema mapping, карточка сломается на `DiseaseInfo`, `DomainFeature`, revealed section keys и score scale.
- Если Reset не меняет `session_id`, старые LangGraph checkpoints могут снова проявиться в новом UI-чате.
- Если `SUPABASE_DB_URL` не задан, in-memory state выживет только пока жив cached service process; после restart история исчезнет.

## 10. Definition of Done для связки sessions + agents_core

- [ ] Один browser session имеет один stable `session_id`.
- [ ] Каждый submit отправляет `ChatTurnRequest.session_id`.
- [ ] LangGraph uses `thread_id == session_id`.
- [ ] `ChatTurnResult.session.session_id` совпадает с frontend `st.session_state.session_id`.
- [ ] Второй вопрос в том же Streamlit чате видит state первого вопроса.
- [ ] Candidate selection меняет backend `active_accession`.
- [ ] Reset не подтягивает старый backend state.
- [ ] UI не читает raw `GraphState` напрямую.
- [ ] Frontend card рендерит candidates из backend `CandidateView` без custom one-off fixes.
- [ ] При `SUPABASE_DB_URL` compact session появляется/обновляется в `public.chat_sessions`.

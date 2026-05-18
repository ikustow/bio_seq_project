# App Unit Tests

This folder contains fast unit tests for the main runtime code under `app/`.

The idea is simple: these tests do not call real LLMs, do not connect to
Supabase, do not open Streamlit in a browser, do not load FAISS/ProtT5, and do
not require API keys. They check the application frame around the LLM and
retrieval flow: what data is passed, which provider is selected, how state is
saved, and whether the protein card survives follow-up questions.

Real LLM answer quality is tested separately in `tests/eval/`. These unit tests
answer: "did we wire the flow correctly?" Eval tests answer: "was the model's
answer actually good?".

## How To Run

From the project root:

```bash
python3 -m pytest tests/unit
```

Verbose output:

```bash
python3 -m pytest tests/unit -v
```

Very verbose output with `print`/logs:

```bash
python3 -m pytest tests/unit -vv -s
```

Stop on the first failure:

```bash
python3 -m pytest tests/unit -x
```

Show collected tests without running them:

```bash
python3 -m pytest tests/unit --collect-only -q
```

Run one file:

```bash
python3 -m pytest tests/unit/backend/app_services/test_chat_llm.py
```

Run one specific test:

```bash
python3 -m pytest tests/unit/backend/app_services/test_chat_llm.py::test_auto_provider_prefers_proxy
```

If you want to run tests from `.venv`, install `pytest` inside the virtual
environment first:

```bash
source .venv/bin/activate
python -m pip install pytest
python -m pytest tests/unit
```

## What Each File Checks

### `conftest.py`

Shared setup for unit tests.

What it does:

- adds `app/`, `app/frontend/`, and the project root to `sys.path`;
- clears key/runtime environment variables before each test;
- provides `candidate_view` and `candidate_dict` fixtures with a sample
  protein card.

Why it exists:

- tests should not accidentally use real keys from your shell;
- all tests get the same stable protein sample.

### `backend/app_services/test_chat_llm.py`

Tests pure `ChatLLMService` logic and helper functions from
`backend/app_services/chat_llm.py`.

What it checks:

- provider `auto` chooses Gemini proxy when proxy URL/token are present;
- provider `auto` chooses OpenAI when only an OpenAI key is present;
- `provider_override` wins over environment variables;
- missing credentials raise a clear error;
- protein context includes accession, match confidence, function, domains,
  GO terms;
- Gemini payload does not duplicate the current prompt;
- Gemini/OpenAI text extraction works;
- empty provider responses become clear errors.

Why it matters:

- follow-up LLM calls must receive the right context;
- switching providers should not break the flow;
- model/provider failures should be easy to diagnose.

### `backend/app_services/test_chat_llm_providers.py`

Tests real provider adapters without real network calls or real keys.

What it checks:

- Gemini proxy adapter builds the expected HTTP payload;
- Gemini proxy adapter requires URL and token;
- OpenAI adapter creates `ChatOpenAI` with the expected model, temperature,
  and timeout;
- OpenAI adapter passes system prompt and protein context to the model.

Why it matters:

- these tests do not judge model quality;
- they prove that the provider request is assembled correctly.

### `backend/app_services/test_bioseq_chat_service.py`

Tests the main backend service, `BioSeqChatService`.

What it checks:

- first sequence turn updates agent state and returns candidates;
- protein card sections use frontend-compatible keys, including `pathways`;
- follow-up turn with `turn_count > 0` goes to Chat LLM, not retriever;
- follow-up returns `update_card=False`;
- selected candidate is passed into LLM context;
- Chat LLM errors do not wipe the protein card.

Why it matters:

- this is the core LLM-flow contract: follow-up questions should be answered
  using the current card, and the right-side protein card should stay stable.

### `backend/app_services/test_retriever_pipeline.py`

Tests the deterministic part of the retriever pipeline.

What it checks:

- raw protein sequence is classified as protein;
- DNA sequence is classified as DNA;
- DNA translation stops at stop codon;
- invalid DNA length returns a controlled miss;
- disabled runtime retriever does not load heavy backend services;
- fake runtime result maps into `CandidateView`.

Why it matters:

- fast unit tests should catch input/contract bugs before we reach the heavy
  FAISS/ProtT5/search-service layer.

### `backend/agents_core/test_runtime_agent.py`

Tests the LangGraph session agent with in-memory persistence.

What it checks:

- different `session_id` values keep separate state;
- active accession is synchronized into the session repository;
- the agent does not mix two user sessions.

Why it matters:

- chat history and selected protein must belong to the correct session.

### `frontend/test_chat_pipeline.py`

Tests frontend orchestration in `chat_pipeline.py` without a real Streamlit
runtime.

What it checks:

- `_build_ui_context()` uses the currently selected candidate;
- follow-up turn keeps existing candidates from `st.session_state`;
- follow-up turn keeps `card_sections_revealed`;
- `save_turn()` is called with `update_candidates=False`;
- backend receives selected candidate in `ui_context`.

Why it matters:

- if the user selects the second or third match, the LLM must receive that
  selected match, not always candidate zero.

### `frontend/test_session_db_adapter.py`

Tests turn persistence through a fake in-memory repository.

What it checks:

- retriever turn saves candidates, revealed sections, active accession, and
  query protein sequence;
- follow-up turn does not overwrite `last_candidates`;
- follow-up turn does not overwrite `active_accession`;
- follow-up turn appends user/assistant messages and increments `turn_count`.

Why it matters:

- this protects against a painful UX bug: the user asks a follow-up question
  and the right-side card disappears or changes unexpectedly.

## How To Read The Result

A successful run looks like this:

```text
23 passed
```

The current `langchain_core` warning about Python 3.14 and Pydantic v1 is not
a test failure. It comes from a dependency.

If a test fails, run:

```bash
python3 -m pytest tests/unit -vv -s --tb=long
```

That gives more context: which test failed, which assertion failed, and what
data was inside the failing case.

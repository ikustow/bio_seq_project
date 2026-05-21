# Frontend Layer

Russian version: [README_RU.md](README_RU.md).

`app/frontend` is the Streamlit workspace for BioSeq Investigator. This layer
owns the user experience: chat, sequence paste/upload, object registry, protein
card, alignment viewer, session sidebar, debug panel, and visual application
state.

## Why This Layer Exists

The frontend turns backend retrieval results into a usable research workspace.
Instead of seeing only an accession, the user gets:

- top-5 candidates and switching between them;
- a selected protein card with function, domains, feature map, variants,
  interactions, and links;
- alignment between the query sequence and the matched protein;
- `@Seq_A` / `@Protein` mentions for follow-up questions;
- session history and last-state restore;
- debug panel for LLM/retriever diagnostics.

## Runtime Flow

```text
app.py
  -> session_identity.bootstrap_identity()
  -> session_objects.init_state()
  -> chat component captures user turn
  -> chat_pipeline.run_turn()
  -> backend.app_services.BioSeqChatService
  -> response objects_patch + candidates + messages
  -> Streamlit state update
  -> protein card / object inspector / sidebar render
```

The frontend does not run search directly. It sends a `ChatTurnRequest` to the
backend service layer and receives a `ChatTurnResult`.

## Directory Responsibilities

| Path | Responsibility |
| --- | --- |
| `app.py` | Streamlit entrypoint, layout, topbar, panel resizing, page lifecycle. |
| `components/` | UI widgets: chat, protein card, alignment viewer, object inspector, sidebar, debug panel. |
| `assets/` | Logo, icons, CSS, and visual assets. |
| `mock/` | Scripted demo data and conversation for `BIOSEQ_BACKEND=mock`. |
| `test_data_from_database/` | Local sample UniProt JSON cards for mock/dev visualization. |
| `.streamlit/` | Example Streamlit secrets config. |

## Important Modules

- `chat_pipeline.py` - main bridge from UI to backend service.
- `backend_adapter.py` - legacy/simple adapter for runtime search calls.
- `gateway_supervisor.py` - optional FastAPI gateway startup from the
  Streamlit process on Hugging Face Spaces.
- `session_identity.py` - user/session/workspace identity bootstrap.
- `session_db_adapter.py` - reads/writes `public.chat_sessions` and restores
  candidates/messages.
- `session_objects.py` - object registry for `Sequence` and `Protein` objects.
- `sequence_detection.py` - FASTA/raw sequence/UniProt detection in the
  composer.
- `backend_choice.py` - frontend backend label.
- `config.py` - runtime switches.
- `embeddings_pipeline.py`, `vector_db_adapter.py` - legacy embeddings path,
  kept for compatibility and smoke checks.

## UI State Model

Main state lives in `st.session_state`:

- `session_id`, `user_id`, `workspace_id`, `user_role`;
- `messages` - current chat transcript;
- `candidates` - UI-shaped top-5 candidates;
- `selected_object_id` - active object registry item;
- `objects` and `object_order` - sequences and protein cards;
- `query_protein_sequence` - sequence used by the alignment viewer;
- `card_sections_revealed` - opened protein-card sections;
- `search_algorithm` - for example embeddings/BLAST path;
- `think_mode_enabled` - suggested questions / think mode toggle.

The backend sends an `ObjectsPatch`, and the frontend applies it through
`session_objects.apply_objects_patch()`.

## Runtime Modes

Live runtime:

```bash
streamlit run app/frontend/app.py
```

With `BIOSEQ_BACKEND=runtime`, every turn goes to the backend service:

```dotenv
BIOSEQ_BACKEND=runtime
BIOSEQ_ENABLE_RUNTIME_RETRIEVER=true
BIOSEQ_SEARCH_SERVICE_URL=http://localhost:8002
```

Mock demo:

```bash
BIOSEQ_BACKEND=mock streamlit run app/frontend/app.py
```

Mock mode uses `mock/conversation.py` and sample cards from
`test_data_from_database/`, so it is useful for UI demos without heavy models
or API keys.

## Gateway Supervisor On Hugging Face Spaces

Normal local development starts the gateway in a separate terminal. A Hugging
Face Streamlit Space has one entrypoint, so the frontend can start the gateway
child process itself:

```dotenv
BIOSEQ_SPAWN_GATEWAY=true
BIOSEQ_BOOTSTRAP_DATA=true
BIOSEQ_SEARCH_SERVICE_URL=http://localhost:8002
```

The logic lives in `gateway_supervisor.py`. The supervisor is idempotent: if
the gateway already listens on the port, it does not start a second process.

## Persistence And Session Restore

When `SUPABASE_DB_URL` is set, `session_db_adapter.py`:

1. reads the user's session list for the sidebar;
2. restores `messages`, `last_candidates`, selected objects, and working
   memory;
3. performs a read-merge-write upsert into `public.chat_sessions` after every
   turn;
4. stores UI-specific fields on top of agent state without overwriting backend
   data.

Without a database, the UI still works, but the sidebar shows a non-persistent
mode.

## Components

| Component | Purpose |
| --- | --- |
| `components/chat.py` | Composer, message bubbles, sequence preview, suggested questions. |
| `components/protein_card.py` | Main protein card. |
| `components/alignment_viewer.py` | Protein/query alignment. |
| `components/object_bar.py` | Compact object switcher. |
| `components/object_inspector.py` | Detailed selected Sequence/Protein object view. |
| `components/session_sidebar.py` | Session history and restore. |
| `components/debug_panel.py` | Debug payloads, warnings, provider metadata. |
| `components/domain_diagram.py` | Domain/features visualization. |

## Styling And Assets

Main CSS: [assets/style.css](assets/style.css).

The Streamlit layout is built around:

- fixed topbar with logo;
- main chat column;
- resizable right protein-card panel;
- Streamlit sidebar for sessions;
- object bar/inspector for working with multiple sequences/proteins.

When changing the UI, check that text does not overlap neighboring elements
and that the right panel works on narrow screens.

## Tests

Useful frontend checks:

```bash
pytest tests/unit/frontend
python scripts/smoke_chat_pipeline_routing.py
python scripts/smoke_think_mode_questions.py
python scripts/smoke_embeddings_dispatch.py
```

Manual UI check:

```bash
BIOSEQ_BACKEND=mock streamlit run app/frontend/app.py
```

This is the fastest way to check layout and interactions without warming up
ProtT5/FAISS.

## Technical Links

Internal:

- [Backend layer](../backend/README.md)
- [Retriever library](../backend/bioseq_retriever/README.md)
- [Root README](../../README.md)

External:

- [Streamlit docs](https://docs.streamlit.io/)
- [Hugging Face Streamlit Spaces](https://huggingface.co/docs/hub/main/spaces-sdks-streamlit)
- [Supabase Postgres docs](https://supabase.com/docs/guides/database/overview)
- [Cloudflare Workers docs](https://developers.cloudflare.com/workers/)

# Frontend Layer

English version: [README.md](README.md).

`app/frontend` - Streamlit workspace BioSeq Investigator. Этот слой отвечает
за пользовательский опыт: чат, загрузку/вставку последовательностей, object
registry, карточку белка, alignment viewer, session sidebar, debug panel и
визуальное состояние приложения.

## Зачем нужен frontend

Frontend превращает backend retrieval result в рабочее исследовательское
пространство. Пользователь не просто получает accession, а видит:

- top-5 candidates и переключение между ними;
- карточку выбранного белка с функцией, доменами, feature map, вариантами,
  взаимодействиями и ссылками;
- alignment между query sequence и найденным белком;
- `@Seq_A` / `@Protein` mentions для follow-up вопросов;
- session history и восстановление последнего состояния;
- debug panel для диагностики LLM/retriever запросов.

## Runtime flow

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

Frontend не запускает поиск напрямую. Он отправляет `ChatTurnRequest` в backend
service layer и получает `ChatTurnResult`.

## Папки и ответственность

| Путь | Роль |
| --- | --- |
| `app.py` | Streamlit entrypoint, layout, topbar, panel resizing, page lifecycle. |
| `components/` | UI widgets: chat, protein card, alignment viewer, object inspector, sidebar, debug panel. |
| `assets/` | Logo, icons, CSS и визуальные assets приложения. |
| `mock/` | Scripted demo data and conversation для `BIOSEQ_BACKEND=mock`. |
| `test_data_from_database/` | Local sample UniProt JSON cards для mock/dev визуализации. |
| `.streamlit/` | Example Streamlit secrets config. |

## Важные модули

- `chat_pipeline.py` - главный bridge UI -> backend service.
- `backend_adapter.py` - legacy/simple adapter для runtime search вызовов.
- `gateway_supervisor.py` - optional запуск FastAPI gateway из Streamlit
  процесса на HF Space.
- `session_identity.py` - user/session/workspace identity bootstrap.
- `session_db_adapter.py` - read/write `public.chat_sessions` и восстановление
  candidates/messages.
- `session_objects.py` - object registry для `Sequence` и `Protein` объектов.
- `sequence_detection.py` - FASTA/raw sequence/UniProt detection в composer.
- `backend_choice.py` - frontend backend label.
- `config.py` - runtime switches.
- `embeddings_pipeline.py`, `vector_db_adapter.py` - legacy embeddings path,
  оставлен для совместимости и отдельных smoke checks.

## UI state model

Основной state живет в `st.session_state`:

- `session_id`, `user_id`, `workspace_id`, `user_role`;
- `messages` - текущий chat transcript;
- `candidates` - UI-shaped top-5 candidates;
- `selected_object_id` - активный object registry item;
- `objects` и `object_order` - последовательности и protein cards;
- `query_protein_sequence` - sequence для alignment viewer;
- `card_sections_revealed` - какие секции protein card открыты;
- `search_algorithm` - например embeddings/BLAST path;
- `think_mode_enabled` - suggested questions/think mode toggle.

Backend присылает `ObjectsPatch`, а frontend применяет его через
`session_objects.apply_objects_patch()`.

## Режимы запуска

Live runtime:

```bash
streamlit run app/frontend/app.py
```

При `BIOSEQ_BACKEND=runtime` каждый turn идет в backend service:

```dotenv
BIOSEQ_BACKEND=runtime
BIOSEQ_ENABLE_RUNTIME_RETRIEVER=true
BIOSEQ_SEARCH_SERVICE_URL=http://localhost:8002
```

Mock demo:

```bash
BIOSEQ_BACKEND=mock streamlit run app/frontend/app.py
```

Mock mode использует `mock/conversation.py` и sample cards из
`test_data_from_database/`, поэтому подходит для UI demos без тяжелых моделей
и API keys.

## Gateway supervisor на HF Space

Обычный локальный dev запускает gateway в отдельном терминале. На Hugging Face
Streamlit Space есть один entrypoint, поэтому frontend может сам поднять
gateway child process:

```dotenv
BIOSEQ_SPAWN_GATEWAY=true
BIOSEQ_BOOTSTRAP_DATA=true
BIOSEQ_SEARCH_SERVICE_URL=http://localhost:8002
```

Логика лежит в `gateway_supervisor.py`. Supervisor idempotent: если gateway
уже слушает порт, второй процесс не стартует.

## Persistence и session restore

Если `SUPABASE_DB_URL` задан, `session_db_adapter.py`:

1. читает список сессий пользователя для sidebar;
2. восстанавливает `messages`, `last_candidates`, selected objects и working
   memory;
3. после каждого turn-а делает read-merge-write upsert в `public.chat_sessions`;
4. сохраняет UI-specific поля поверх agent state, не затирая backend data.

Если базы нет, UI продолжает работать, но sidebar показывает неперсистентный
режим.

## Компоненты

| Компонент | Назначение |
| --- | --- |
| `components/chat.py` | Composer, message bubbles, sequence preview, suggested questions. |
| `components/protein_card.py` | Основная карточка белка. |
| `components/alignment_viewer.py` | Protein/query alignment. |
| `components/object_bar.py` | Compact object switcher. |
| `components/object_inspector.py` | Детальный просмотр выбранного Sequence/Protein объекта. |
| `components/session_sidebar.py` | История сессий и restore. |
| `components/debug_panel.py` | Debug payloads, warnings, provider metadata. |
| `components/domain_diagram.py` | Визуализация domain/features. |

## Styling и assets

Основной CSS: [assets/style.css](assets/style.css).

Streamlit layout строится вокруг:

- fixed topbar с логотипом;
- main chat column;
- resizable right protein-card panel;
- Streamlit sidebar для сессий;
- object bar/inspector для работы с несколькими sequences/proteins.

При изменениях UI обязательно проверять, что текст не накладывается на
соседние элементы и что right panel работает на узких экранах.

## Тесты

Полезные проверки frontend слоя:

```bash
pytest tests/unit/frontend
python scripts/smoke_chat_pipeline_routing.py
python scripts/smoke_think_mode_questions.py
python scripts/smoke_embeddings_dispatch.py
```

Для ручной проверки:

```bash
BIOSEQ_BACKEND=mock streamlit run app/frontend/app.py
```

Так можно быстро проверить layout и interactions без прогрева ProtT5/FAISS.

## Технические ссылки

Внутренние:

- [Backend layer](../backend/README_RU.md)
- [Retriever library](../backend/bioseq_retriever/README.md)
- [Root README](../../README_RU.md)

Внешние:

- [Streamlit docs](https://docs.streamlit.io/)
- [Hugging Face Streamlit Spaces](https://huggingface.co/docs/hub/main/spaces-sdks-streamlit)
- [Supabase Postgres docs](https://supabase.com/docs/guides/database/overview)
- [Cloudflare Workers docs](https://developers.cloudflare.com/workers/)

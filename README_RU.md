# BioSeq Investigator

BioSeq Investigator - исследовательский ассистент для первичного анализа DNA
и protein FASTA (FASTA — это простой текстовый формат для записи
последовательности ДНК или белка). Пользователь вставляет последовательность
и задает вопрос на естественном языке, а приложение находит ближайшие
UniProt/Swiss-Prot кандидаты (UniProt — это большая публичная база белков,
а Swiss-Prot — её вручную проверенная, качественная часть), показывает
evidence-grounded карточку белка и поддерживает follow-up диалог по найденному
контексту.

English README: [README.md](README.md).

## Бизнес-ценность

BioSeq Investigator закрывает разрыв между сырым FASTA и понятным
биологическим контекстом. Вместо ручного прохода по BLAST (классический
биоинформатический инструмент, который ищет в базе последовательности,
похожие на ваш запрос), UniProt, статьям,
feature tables и заметкам исследователь получает один рабочий экран:
последовательность, top-5 кандидатов, объяснение совпадения, структурированные
аннотации и историю вопросов.

Проект полезен для:

- pre-screening неизвестных или плохо документированных последовательностей;
- быстрых демо и образовательных сценариев, где нужен понятный путь от FASTA
  к функции белка;
- продуктовой проверки AI-assisted bioinformatics workflow;
- воспроизводимого анализа: сессия, кандидаты, выбранный белок и follow-up
  контекст сохраняются в Postgres;
- командной работы над bioinformatics UX, где backend, frontend, retrieval и
  eval-слои можно развивать независимо.

Главная идея: не заменить curated-анализ биоинформатика, а убрать рутину
первичного поиска и дать проверяемую стартовую гипотезу за минуты.

## Что делает проект

1. Принимает raw sequence, FASTA или UniProt accession/mnemonic ID.
2. Определяет тип входа: DNA, protein или обычный текстовый follow-up.
3. Для sequence-turn запускает runtime retriever:
   - ProtT5/FAISS поиск по protein embeddings;
   - DNA path через DNA индекс/поиск, где доступен;
   - альтернативный BLAST path для protein-поиска;
   - UniProt metadata fetch;
   - contextual rerank по смыслу вопроса.
4. Возвращает top-5 UniProt candidates и UI-ready `ProteinView`.
5. Рендерит Streamlit UI: чат, карточку белка, features, domains,
   interactions, variants, alignment viewer и session sidebar.
6. Для follow-up вопросов использует Chat LLM поверх уже найденного контекста,
   не сбрасывая активную карточку.
7. Сохраняет историю и compact session state в Supabase/Postgres, если задан
   `SUPABASE_DB_URL`.

## Архитектура

```text
User
  -> app/frontend Streamlit UI
  -> backend.app_contracts.ChatTurnRequest
  -> backend.app_services.BioSeqChatService
  -> BioSeqRetrieverPipeline / ChatLLMService / SuggestedQuestionsService
  -> backend.bioseq_retriever LangGraph pipeline
  -> FastAPI search gateway: ProtT5 + FAISS + rerank
  -> UniProt metadata
  -> CandidateView / ProteinView
  -> Streamlit protein card + persisted session
```

Слои разведены так, чтобы UI не знал деталей FAISS/UniProt, а retriever не
знал Streamlit state. Контракт между ними - Pydantic DTO из
`app/backend/app_contracts`.

## Техническая документация

### Внутри репозитория

- [Backend layer](app/backend/README_RU.md) ([EN](app/backend/README.md)) -
  сервисы, контракты, агенты, persistence и search gateway.
- [Frontend layer](app/frontend/README_RU.md) ([EN](app/frontend/README.md)) -
  Streamlit entrypoint, UI компоненты, object registry, session restore и
  runtime modes.
- [Retriever library](app/backend/bioseq_retriever/README.md) - LangGraph
  pipeline, ProtT5/FAISS gateway, UniProt fetch и rerank.
- [Data preparation](data_prep/README.md) - offline pipeline для подготовки
  Swiss-Prot/RefSeq данных и HDF5 artifacts.
- [Evaluation harness](tests/eval/README.md) - L1/L2/L3 eval-пайплайны и
  проверка качества retrieval/LLM ответов.
- [Environment template](example.env.txt) - минимальный набор переменных для
  локального runtime.

## Быстрый старт

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp example.env.txt .env
```

Для live runtime в `.env` нужен минимум:

```dotenv
MISTRAL_API_KEY=...
# или OPENAI_API_KEY=...

BIOSEQ_BACKEND=runtime
BIOSEQ_ENABLE_RUNTIME_RETRIEVER=true
BIOSEQ_SEARCH_SERVICE_URL=http://localhost:8002
BIOSEQ_DATA_SOURCE=hf:radda-i/bioseq-data

# опционально, но желательно для истории сессий
SUPABASE_DB_URL=postgresql://user:password@host:5432/postgres
```

Запуск в двух терминалах:

```bash
# 1. Heavy search/rerank gateway: ProtT5, FAISS, rerank.
python app/backend/bioseq_retriever/services/search_service.py

# 2. Streamlit UI.
streamlit run app/frontend/app.py
```

Демо-режим без тяжелого backend и API ключей:

```bash
BIOSEQ_BACKEND=mock streamlit run app/frontend/app.py
```

## Основные runtime-переменные

| Переменная | Обязательно | Назначение |
| --- | --- | --- |
| `BIOSEQ_BACKEND` | да | `runtime` для live pipeline, `mock` для scripted demo. |
| `BIOSEQ_ENABLE_RUNTIME_RETRIEVER` | да для runtime | Разрешает service layer запускать `app/backend/bioseq_retriever`. |
| `BIOSEQ_SEARCH_SERVICE_URL` | да для runtime | URL FastAPI gateway, по умолчанию `http://localhost:8002`. |
| `MISTRAL_API_KEY` | один из LLM ключей | LLM extraction/rerank path и optional think mode. |
| `OPENAI_API_KEY` | один из LLM ключей | fallback LLM provider и Chat LLM provider. |
| `BIOSEQ_CHAT_LLM_PROVIDER` | нет | `auto`, `gemini_proxy` или `openai` для follow-up ответов. |
| `BIOSEQ_LLM_PROXY_URL` | для `gemini_proxy` | Cloudflare Worker URL для Gemini proxy. |
| `BIOSEQ_LLM_PROXY_TOKEN` | для `gemini_proxy` | Bearer token для proxy. |
| `SUPABASE_DB_URL` | желательно | Postgres connection string для `public.chat_sessions`. |
| `BIOSEQ_DATA_SOURCE` | желательно | `hf:radda-i/bioseq-data` быстрее cold start, `uniprot` качает исходные данные. |
| `BIOSEQ_DATA_DIR` | нет | Папка для HDF5, FAISS index и accession cache. |
| `APP_PASSWORD` | нет | Простая password gate для публичного UI. |

Полный шаблон: [example.env.txt](example.env.txt).

## Структура репозитория

```text
app/
  backend/
    app_contracts/       Pydantic DTO между UI и backend.
    app_services/        Application orchestration и routing turn-ов.
    agents_core/         LangGraph session-agent, memory, persistence.
    bioseq_retriever/    Retrieval pipeline и FastAPI search gateway.
  frontend/
    app.py               Streamlit entrypoint.
    components/          Chat, protein card, alignment, sidebar, debug panel.
    assets/              Logo, icons, CSS.
    mock/                Scripted demo mode.
data_prep/               Offline data-build scripts.
tests/
  unit/                  Unit tests для frontend/backend сервисов.
  backend/               Retriever/backend integration tests.
  eval/                  Retrieval и LLM evaluation harnesses.
to_delete/               Архив старой документации и deprecated кода.
```

## Данные и cold start

Runtime gateway работает с тяжелыми артефактами:

- `per-protein.h5` - protein embeddings;
- `per-protein.index` - FAISS HNSW index;
- accession cache - соответствие index row -> UniProt accession;
- optional DNA artifacts для DNA path.

На HF/CPU cold start может занять минуты: сначала скачиваются data artifacts,
затем веса ProtT5, затем gateway загружает FAISS index. Для демо лучше заранее
прогреть Space одним запросом.

## Качество и валидация

Retrieval quality проверяется через [tests/eval](tests/eval/README.md).
Важные метрики: top-k recall, корректность классификации DNA/protein, качество
follow-up ответов и стабильность session restore. Подробности текущего
retriever pipeline и известных рисков качества лежат в
[app/backend/bioseq_retriever/README.md](app/backend/bioseq_retriever/README.md).

## Workflow для разработки

1. Работать от свежей `main`.
2. Создавать короткую ветку: `feature/...`, `fix/...`, `docs/...`.
3. Перед PR запускать релевантные тесты:

```bash
pytest tests/unit
pytest tests/backend/bioseq_retriever
python scripts/smoke_chat_pipeline_routing.py
```

4. Для изменений retrieval качества запускать eval harness из
   [tests/eval/README.md](tests/eval/README.md).
5. В PR писать: что изменено, зачем, как проверено.

# BioSeq Investigator

Streamlit-приложение для исследовательского анализа биологических последовательностей. Пользователь вставляет DNA или protein FASTA + вопрос на естественном языке; первый turn идёт в ProtT5/FAISS retriever по Swiss-Prot с Mistral-reranker-ом, follow-up вопросы — в Gemini через Cloudflare-прокси. История сессий хранится в Supabase Postgres (`public.chat_sessions`).

🇬🇧 English version (also used as HF Spaces config): [README.md](README.md).

## Документация

- [report/USER_GUIDE.md](report/USER_GUIDE.md) (RU) · [report/USER_GUIDE_en.md](report/USER_GUIDE_en.md) (EN) — 5-минутный walkthrough по live HF-приложению для новичков (биологический бэкграунд не нужен).
- [app/README.md](app/README.md) (RU) · [app/README_en.md](app/README_en.md) (EN) — Streamlit-приложение: runtime архитектура, env vars, persistence, файловый layout.
- [app/ARCHITECTURE.md](app/ARCHITECTURE.md) (RU) · [app/ARCHITECTURE_en.md](app/ARCHITECTURE_en.md) (EN) — текущая архитектура app (модули, поток данных, decision points).
- [app/CHAT_LLM_BACKEND_MIGRATION_PLAN.md](app/CHAT_LLM_BACKEND_MIGRATION_PLAN.md) — план переноса follow-up Chat LLM из `frontend/chat_llm_pipeline.py` в backend service layer.
- [report/REPORT.MD](report/REPORT.MD) (RU) · [report/REPORT_EN.MD](report/REPORT_EN.MD) (EN) — промежуточный отчёт по проекту с диаграммами.
- [report/VALIDATION_PLAN.md](report/VALIDATION_PLAN.md) (RU) · [report/VALIDATION_PLAN_en.md](report/VALIDATION_PLAN_en.md) (EN) — план валидации (L1/L2/L3) и датасеты.
- [app/backend/bioseq_retriever/README.md](app/backend/bioseq_retriever/README.md) — retriever-библиотека (LangGraph-пайплайн, ProtT5/FAISS search service, контекстный rerank).
- [app/frontend/TO-DO.md](app/frontend/TO-DO.md) — открытые frontend TODO.
- [tests/eval/README.md](tests/eval/README.md) — как запускать eval-харнессы.

## Конфигурация (HF Space Secrets / Variables)

| Переменная                        | Где          | Обязательно        | Назначение |
|-----------------------------------|--------------|--------------------|------------|
| `APP_PASSWORD`                    | **Secret**   | опционально        | Single-password gate для UI. Если задано, перед чатом показывается форма входа. |
| `MISTRAL_API_KEY`                 | **Secret**   | да¹                | Mistral API key — LLM-extraction внутри retriever pipeline + контекстный rerank. |
| `OPENAI_API_KEY`                  | **Secret**   | да¹                | Fallback для retriever LLM / reranker-а; также используется как provider follow-up Chat LLM. |
| `SUPABASE_DB_URL`                 | **Secret**   | желательно         | Postgres-строка подключения к `public.chat_sessions`. Без неё sidebar показывает «Session history is not persisted», а follow-up routing на каждом turn-е деградирует в retriever. |
| `BIOSEQ_BACKEND`                  | **Variable** | да                 | `runtime` включает live pipeline (единственный режим, который зовёт search service и Chat LLM). `mock` оставляет скриптованный демо-UI. |
| `BIOSEQ_ENABLE_RUNTIME_RETRIEVER` | **Variable** | да (для runtime)   | `true` — разрешает service layer звать `app/backend/bioseq_retriever`. |
| `BIOSEQ_SEARCH_SERVICE_URL`       | **Variable** | да (для runtime)   | URL unified BioSeq search/rerank gateway (`/search/protein`, `/search/dna`, `/rerank`). Дефолт `http://localhost:8002`. |
| `BIOSEQ_CHAT_LLM_PROVIDER`        | **Variable** | опционально        | `auto` (по умолчанию — Gemini-прокси при наличии URL+token, иначе OpenAI), `gemini_proxy`, `openai`. |
| `BIOSEQ_LLM_PROXY_URL`            | **Secret**   | условно            | URL Cloudflare Worker-а, который проксирует Gemini. Нужен, если выбран `gemini_proxy`. |
| `BIOSEQ_LLM_PROXY_TOKEN`          | **Secret**   | условно            | Bearer-токен, который ожидает Cloudflare-прокси. |
| `BIOSEQ_OPENAI_CHAT_MODEL`        | **Variable** | опционально        | Имя OpenAI-модели для follow-up Chat LLM. |
| `BIOSEQ_DATA_SOURCE`              | **Variable** | желательно         | Поставить `hf:radda-i/bioseq-data` — подтягивает `per-protein.h5` (~1.3 GB), готовый FAISS-индекс (~2.5 GB) и accession-кеш из HF Dataset. Cold start ~1–2 мин. Дефолт `uniprot` — качает с UniProt FTP без индекса, добавляет +5–15 мин на ребилд FAISS при каждом cold start. |
| `BIOSEQ_DATA_DIR`                 | **Variable** | опционально        | Переопределяет путь к данным (по умолчанию `data/`). |
| `APP_WORKSPACE_ID`                | **Variable** | опционально        | Workspace-metadata, прицепляется к session context. |
| `APP_USER_ROLE`                   | **Variable** | опционально        | User-role-metadata, прицепляется к session context. |

¹ Хотя бы один из `MISTRAL_API_KEY` / `OPENAI_API_KEY` обязателен, иначе retriever pipeline отклонит каждый запрос с friendly-сообщением в чате.

## Cold-start (бесплатный HF Space, 16 GB CPU)

1. `per-protein.h5` (~1.3 GB) скачивается в `data/` (~5–10 мин с UniProt FTP, ~1–2 мин с HF Dataset).
2. Веса `Rostlab/prot_t5_xl_uniref50` (~3 GB) подтягиваются в HF-кеш с публичного Hub-а при первом обращении к ProtT5.
3. FAISS HNSW индекс либо грузится из dataset-а, либо строится из `.h5` (5–15 мин однопоточно — если pre-built индекс не залит в dataset).
4. Последующие запросы — ~30–90 сек.

Для демо-дня имеет смысл сходить в Space хотя бы один раз до зрителей, чтобы прогреть всё перечисленное.

## Локальная разработка

```bash
pip install -r app/frontend/requirements.txt

# .env в корне репозитория:
#   MISTRAL_API_KEY=...                       # либо OPENAI_API_KEY=...
#   SUPABASE_DB_URL=postgresql://...
#   BIOSEQ_BACKEND=runtime
#   BIOSEQ_ENABLE_RUNTIME_RETRIEVER=true
#   BIOSEQ_SEARCH_SERVICE_URL=http://localhost:8002
#   BIOSEQ_LLM_PROXY_URL=https://...          # опционально, для Gemini follow-up
#   BIOSEQ_LLM_PROXY_TOKEN=...                # опционально
#   BIOSEQ_DATA_SOURCE=hf:radda-i/bioseq-data # желательно

# 1) Тяжёлый search/rerank gateway (грузит ProtT5 + FAISS-индекс, слушает :8002).
python app/backend/bioseq_retriever/services/search_service.py

# 2) Streamlit UI в другом shell-е.
streamlit run app/frontend/app.py

# Скриптованный демо-режим (без backend и API-ключей):
BIOSEQ_BACKEND=mock streamlit run app/frontend/app.py
```

## Структура репозитория

- [`app/frontend/`](app/frontend/) — Streamlit UI: chat, карточка белка, alignment viewer, session sidebar, identity bootstrap. Подробный разбор по модулям — в [`app/README.md`](app/README.md).
- [`app/backend/`](app/backend/) — сервисный слой: `app_contracts`, `app_services` (`BioSeqChatService`, `BioSeqRetrieverPipeline`, `protein_view_mapper`), `agents_core` (`runtime_agent`, persistence glue).
- [`app/backend/bioseq_retriever/`](app/backend/bioseq_retriever/) — runtime retriever pipeline (LangGraph + ProtT5/FAISS через search service + UniProt fetch + контекстный rerank).
- [`depricated/bioseq_retriever/`](depricated/bioseq_retriever/) — rollback/reference snapshot оригинального root-level retriever-а; не на runtime-пути.
- [`data_prep/`](data_prep/) — offline-скрипты подготовки данных (не часть Streamlit runtime).
- [`report/`](report/) — промежуточный отчёт, план валидации, user guide.
- [`tests/`](tests/) — suites `backend/`, `depricated/`, `scripts/`, `eval/`.

---

## Правила работы с репозиторием

1. Клонирование репозитория

- Скопировать репозиторий себе локально:
  - `git clone <url>`
- Перейти в папку проекта:
  - `cd bio_seq_project`

2. Обновление локальной копии

- Всегда синхронизируйтесь с удалённой веткой `main` перед началом работы:
  - `git checkout main`
  - `git pull origin main`

3. Создание новой ветки

- Отправная точка — всегда `main`.
- Создавайте ветку из актуальной `main`:
  - `git checkout main`
  - `git pull origin main`
  - `git checkout -b feature/имя-работы`

4. Правила именования веток

- Используйте понятные и короткие имена.
- Формат ветки:
  - `feature/<описание>` — новая функциональность
  - `fix/<описание>` — исправление бага
  - `docs/<описание>` — документация
  - `chore/<описание>` — вспомогательные задачи
- Пример:
  - `feature/add-sequence-parser`
  - `fix/readme-typo`

5. Работа в ветке

- Делайте небольшие и логичные коммиты.
- Пишите осмысленные сообщения коммита:
  - `git commit -m "Добавить парсер последовательностей"`
- Перед пушем убедитесь, что ветка чиста:
  - `git status`

6. Публикация ветки

- Отправляйте ветку на удалённый репозиторий:
  - `git push -u origin <branch-name>`

7. Создание pull request / merge request

- Создавайте PR/MR в `main`.
- В описании указывайте:
  - что сделано;
  - зачем;
  - если надо — короткий план тестирования.

8. Ревью и слияние

- После позитивного ревью сливайте изменения в `main`.
- Перед слиянием обновите ветку от `main`, если необходимо:
  - `git checkout main`
  - `git pull origin main`
  - `git checkout <branch-name>`
  - `git merge main`

9. Удаление ветки

- После слияния удалите локальную и удалённую ветку:
  - `git branch -d <branch-name>`
  - `git push origin --delete <branch-name>`

10. Общие рекомендации

- Работайте из свежей `main`.
- Избегайте работы сразу в `main`.
- Пишите понятные сообщения коммитов.
- Делайте частые сохранения через коммиты.

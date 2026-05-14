# BioSeq Investigator

Streamlit-приложение для исследовательского анализа биологических последовательностей. Пользователь вставляет DNA или protein FASTA + вопрос на естественном языке; первый turn идёт в ProtT5/FAISS retriever по Swiss-Prot с Mistral-reranker-ом, follow-up вопросы — в Gemini через Cloudflare-прокси. История сессий хранится в Supabase Postgres (`public.chat_sessions`).

🇬🇧 English version (also used as HF Spaces config): [README.md](README.md).

## Документация

- [report/USER_GUIDE.md](report/USER_GUIDE.md) (RU) · [report/USER_GUIDE_en.md](report/USER_GUIDE_en.md) (EN) — 5-минутный walkthrough по live HF-приложению для новичков (биологический бэкграунд не нужен).
- [app/README_app.md](app/README_app.md) (RU) · [app/README_app_en.md](app/README_app_en.md) (EN) — Streamlit-приложение: переменные окружения, структура файлов, открытые TODO.
- [app/ARCHITECTURE.md](app/ARCHITECTURE.md) (RU) · [app/ARCHITECTURE_en.md](app/ARCHITECTURE_en.md) (EN) — текущая архитектура app (модули, поток данных, decision points).
- [report/REPORT.MD](report/REPORT.MD) (RU) · [report/REPORT_EN.MD](report/REPORT_EN.MD) (EN) — промежуточный отчёт по проекту с диаграммами.
- [report/VALIDATION_PLAN.md](report/VALIDATION_PLAN.md) (RU) · [report/VALIDATION_PLAN_en.md](report/VALIDATION_PLAN_en.md) (EN) — план валидации (L1/L2/L3) и датасеты.
- [bioseq_retriever/README.md](bioseq_retriever/README.md) — retriever-библиотека (LangGraph-пайплайн, ProtT5, FAISS, reranker).
- [tests/eval/README.md](tests/eval/README.md) — как запускать eval-харнессы.
- [app/TODO.MD](app/TODO.MD) — открытые TODO по приложению + drift архитектуры.

## Конфигурация (HF Space Secrets / Variables)

| Переменная                 | Где          | Обязательно | Назначение |
|----------------------------|--------------|-------------|------------|
| `APP_PASSWORD`             | **Secret**   | опционально | Single-password gate для UI. Если задано, перед чатом показывается форма входа. |
| `MISTRAL_API_KEY`          | **Secret**   | да¹         | Mistral API key — extract/classify и контекстный rerank top-50 → top-5. |
| `OPENAI_API_KEY`           | **Secret**   | да¹         | Fallback для reranker-а, если `MISTRAL_API_KEY` не задан. |
| `SUPABASE_DB_URL`          | **Secret**   | желательно  | Postgres-строка подключения к `public.chat_sessions`. Без неё sidebar показывает «Session history is not persisted» — история живёт только в текущей вкладке и пропадает после рестарта. |
| `BIOSEQ_LLM_PROXY_URL`     | **Secret**   | да          | URL Cloudflare Worker-а, который проксирует Gemini. Нужен для follow-up-турнов. |
| `BIOSEQ_LLM_PROXY_TOKEN`   | **Secret**   | да          | Bearer-токен, который ожидает Cloudflare-прокси. |
| `BIOSEQ_DATA_SOURCE`       | **Variable** | желательно  | Поставить `hf:radda-i/bioseq-data` — подтягивает `per-protein.h5` (~1.3 GB), готовый FAISS-индекс (~2.5 GB) и accession-кеш из HF Dataset. Cold start ~1–2 мин. Дефолт `uniprot` — качает с UniProt FTP без индекса, добавляет +5–15 мин на ребилд FAISS при каждом cold start. |
| `BIOSEQ_DATA_DIR`          | **Variable** | опционально | Переопределяет путь к данным (по умолчанию `bioseq_retriever/data`). |
| `BIOSEQ_FRONTEND_BACKEND`  | **Variable** | опционально | Legacy-переключатель (`mock` / `real`). Срабатывает только если `app/frontend/config.py::USE_VECTOR_DB_MODE = False`. В дефолтной конфигурации (`True`) live ProtT5+FAISS пайплайн запускается независимо от этого значения. Алиас: `BIOSEQ_BACKEND`. |

¹ Хотя бы один из `MISTRAL_API_KEY` / `OPENAI_API_KEY` обязателен, иначе preflight retriever-а отклонит каждый запрос с friendly-сообщением в чате.

## Cold-start (бесплатный HF Space, 16 GB CPU)

1. `per-protein.h5` (~1.3 GB) скачивается в `bioseq_retriever/data/` (~5–10 мин с UniProt FTP, ~1–2 мин с HF Dataset).
2. Веса `Rostlab/prot_t5_xl_uniref50` (~3 GB) подтягиваются в HF-кеш с публичного Hub-а при первом обращении к ProtT5.
3. FAISS HNSW индекс либо грузится из dataset-а, либо строится из `.h5` (5–15 мин однопоточно — если pre-built индекс не залит в dataset).
4. Последующие запросы — ~30–90 сек.

Для демо-дня имеет смысл сходить в Space хотя бы один раз до зрителей, чтобы прогреть всё перечисленное.

## Локальная разработка

```bash
pip install -r requirements.txt

# .env в корне репозитория:
#   MISTRAL_API_KEY=...            # либо OPENAI_API_KEY=...
#   SUPABASE_DB_URL=postgresql://...
#   BIOSEQ_LLM_PROXY_URL=https://...
#   BIOSEQ_LLM_PROXY_TOKEN=...
#   BIOSEQ_DATA_SOURCE=hf:radda-i/bioseq-data   # опционально, но желательно

# Live pipeline (default): первый turn — ProtT5+FAISS retriever,
# follow-up — Gemini. Тяжёлые ML-зависимости импортируются лениво
# на первом submit, поэтому сам `streamlit run` стартует быстро.
streamlit run app/frontend/app.py

# Скриптованный демо-режим (без backend и API-ключей). Требует
# выставить USE_VECTOR_DB_MODE = False в app/frontend/config.py.
BIOSEQ_FRONTEND_BACKEND=mock streamlit run app/frontend/app.py
```

## Структура репозитория

- [`app/frontend/`](app/frontend/) — Streamlit UI: chat, карточка белка, alignment viewer, session sidebar, identity bootstrap. Подробный разбор по модулям — в [`app/README_app.md`](app/README_app.md).
- [`bioseq_retriever/`](bioseq_retriever/) — LangGraph-пайплайн: extract → classify → translate → rank (FAISS поверх ProtT5) → rerank (Mistral/OpenAI embeddings).
- [`app/backend/`](app/backend/) — сервисный слой и контракты (Supabase persistence, app contracts, mappers). Neo4j graph-агент в `app/backend/agents_core/retriever_agent/` сейчас dormant — оставлен для истории.
- [`report/`](report/) — промежуточный отчёт и план валидации ([RU](report/REPORT.MD) · [EN](report/REPORT_EN.MD)).
- [`tests/eval/`](tests/eval/) — eval-харнессы L1/L2/L3 и датасеты.

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

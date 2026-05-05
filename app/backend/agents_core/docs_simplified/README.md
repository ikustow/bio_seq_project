# Агентская система простыми словами

Этот документ для человека, который впервые открыл `app/backend/agents_core` и пытается понять: что тут вообще происходит, где агент, где база, где сессия, почему есть `app_services` и `app_contracts`.

Если совсем коротко:

```text
Пользователь пишет сообщение
  -> сервис превращает его в понятный для агента контекст
  -> агент запускает цепочку шагов
  -> агент ходит в Neo4j за белками и похожими кандидатами
  -> состояние разговора сохраняется
  -> сервис возвращает ответ и данные для UI
```

## Что такое агент в этом проекте

Здесь агент - это не один большой LLM, который сам решает, какие инструменты вызвать.

В текущем `retriever_agent` агент устроен проще и жестче:

- есть заранее описанная цепочка шагов;
- шаги идут в конкретном порядке;
- каждый шаг получает общий `state`;
- каждый шаг добавляет в `state` новые поля;
- в конце получается результат поиска.

Эта цепочка сделана через LangGraph.

Можно думать так:

```text
LangGraph = схема процесса
Node = один шаг процесса
State = общая папка с данными, которую шаги передают друг другу
Thread = отдельная сессия пользователя
Checkpointer = механизм сохранения состояния thread
```

## Главные папки

| Папка | Что в ней лежит |
| --- | --- |
| `app/backend/agents_core` | Агенты и общая инфраструктура для них. |
| `app/backend/agents_core/retriever_agent` | Текущий рабочий агент для поиска белков по последовательности. |
| `app/backend/agents_core/shared` | Общие модели, настройки, сохранение сессий, Neo4j helper. |
| `app/backend/app_services` | Слой, который связывает агента с приложением: создает агента, вызывает его, собирает ответ. |
| `app/backend/app_contracts` | Pydantic-модели request/response, которые удобно отдавать UI/API. |

## Что сейчас реально работает

В текущем дереве `agents_core` есть рабочий пакет:

```text
app/backend/agents_core/retriever_agent
```

Он умеет:

- принять текст пользователя;
- вытащить из него биологическую последовательность или путь к файлу;
- понять, DNA это или protein;
- если DNA, перевести DNA в protein sequence;
- найти белок в подготовленном Neo4j graph по hash последовательности;
- найти похожих соседей в графе;
- переупорядочить кандидатов с учетом текстового контекста пользователя;
- сохранить состояние сессии.

Важно: этот агент работает в DB-only режиме.

Это значит:

- он не считает embeddings на лету;
- он не запускает ProtT5;
- он не делает FAISS search во время запроса;
- последовательность уже должна быть заранее загружена в Neo4j.

Если последовательности нет в подготовленной базе, агент честно вернет controlled error.

## Самая важная идея: два состояния

В проекте есть два уровня сохранения состояния.

### 1. Полное техническое состояние LangGraph

Это все поля, которые нужны агенту во время работы:

- сообщения;
- исходный prompt;
- извлеченная sequence;
- тип sequence;
- protein sequence;
- промежуточные кандидаты;
- финальные кандидаты;
- ошибка, если она была.

Это хранит LangGraph checkpointer.

Если включен Supabase/Postgres, это хранится в Postgres-таблицах LangGraph.
Если Supabase не настроен, это хранится в памяти процесса.

### 2. Короткое состояние для приложения

Это компактная запись, удобная для UI/API:

- какая сессия;
- какой активный accession;
- какая активная sequence;
- краткое summary;
- список белков;
- список последовательностей;
- working memory;
- последние результаты.

Это пишется в:

```text
public.chat_sessions
```

## Самые важные термины

| Термин | Простое объяснение |
| --- | --- |
| `AppContext` | Кто сейчас работает и в какой сессии. |
| `session_id` | Главный id разговора. Он же LangGraph `thread_id`. |
| `thread_id` | Id ветки памяти LangGraph. У каждой сессии свой thread. |
| `GraphState` | Внутреннее состояние retriever agent. |
| `SessionPatch` | Короткий кусок состояния, который можно сохранить в `chat_sessions`. |
| `PersistenceResources` | Набор объектов для памяти: checkpointer, store, repository. |
| `checkpointer` | Сохраняет технический state LangGraph. |
| `store` | Долгосрочная память LangGraph между threads. Сейчас retriever напрямую ее не использует. |
| `session_repository` | Читает и пишет `public.chat_sessions`. |
| `GraphRetrievalService` | Сервис, который ходит в Neo4j и возвращает белки/кандидатов. |
| `CandidateView` | Готовый для UI кандидат белка. |
| `ProteinView` | Готовое для UI описание одного белка. |

## Главный поток данных

Обычный путь выглядит так:

```text
1. UI/API отправляет ChatTurnRequest
2. BioSeqChatService получает request
3. BioSeqChatService создает AppContext
4. BioSeqChatService вызывает agent.invoke(...)
5. Agent запускает LangGraph pipeline
6. Pipeline ходит в GraphRetrievalService
7. GraphRetrievalService ходит в Neo4j
8. Agent получает результаты и обновляет state
9. Agent сохраняет state через checkpointer
10. Agent сохраняет compact session patch в chat_sessions
11. BioSeqChatService собирает ChatTurnResult
12. UI/API получает ответ
```

## Какие документы читать дальше

Если вы вообще не знаете, с чего начать:

1. Прочитайте этот файл.
2. Потом [retriever_agent.md](retriever_agent.md), там подробно разобран текущий агент.
3. Потом [app_services_contracts.md](app_services_contracts.md), там объяснено, как агент связан с `app_services` и `app_contracts`.
4. Если нужно добавить нового агента, читайте [adding_agents_supabase.md](adding_agents_supabase.md).

## Важное замечание про `session_agent`

В `app/backend/app_services/service_factory.py` есть импорт:

```python
from backend.agents_core.session_agent.agent import SessionGraphAgent
```

Но в текущем дереве `app/backend/agents_core` папки `session_agent` нет.

Поэтому эта документация описывает то, что реально есть сейчас:

```text
retriever_agent
shared
persistence
context
service/contracts integration
```


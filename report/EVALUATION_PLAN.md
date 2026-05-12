# BioSeq Investigator — Evaluation Plan (Functional / Quality Testing)

Документ описывает план функционального тестирования качества системы (не unit-тестов кода): как мы измеряем, что **retriever находит правильные белки**, а **production LLM отвечает по делу**. Документ адресован менторам и закрывает чекпоинтные требования от 14 мая 2026:

- A validation set is prepared or described;
- Evaluation approach: what metrics will be used and on what data;
- Preliminary results if available, or a description of what has been tested so far.

И покрывает критерии Technical Implementation:
- *Quality is measured: a validation set exists, metrics are reported and interpreted* (0–4);
- *The system works end-to-end and produces meaningful outputs* (0–4);
- *Testable: the team can demonstrate that it works correctly and evaluate its accuracy*.

---

## 1. Что именно мы тестируем

Система имеет два независимых "интеллектуальных" компонента, и тестировать их нужно по отдельности, иначе ошибки будут перекрывать друг друга:

| Уровень | Что проверяем | Тип теста | Детерминизм |
|---|---|---|---|
| **L1. Retriever** | ProtT5 → FAISS top-50 → Mistral/local rerank top-5: возвращает ли он правильный UniProt accession | Объективная метрика (top-K accuracy, MRR) | Высокий — один и тот же FASTA даёт одну и ту же выдачу |
| **L2. Production LLM (Gemini)** | Отвечает ли follow-up чат по существу вопроса, на основе переданного контекста белка, без галлюцинаций и без выдумывания нового DB-поиска | LLM-as-a-judge с рубрикой | Средний — judge даёт стабильную оценку при temperature=0 |
| **L3. End-to-end** | FASTA + вопрос → карточка → 2–3 follow-up: ничего не валится, ответы согласованы | Smoke + ручная проверка | Низкий, но достаточен как health-check |

Unit-тесты (`bioseq_retriever/tests/`) остаются как они есть и закрывают отдельный пункт критериев (*Unit and integration tests*).

---

## 2. L1. Retriever evaluation

### 2.1 Validation set

4 хорошо аннотированных белка с известным "правильным" UniProt accession + один кросс-видовой гомолог (используется как цель для V3_full_exact_species). Подобраны так, чтобы покрыть разные классы, разные организмы и разные типы входа:

| # | Protein | Species | UniProt | Почему выбрали |
|---|---|---|---|---|
| 1 | Insulin | *Homo sapiens* | **P01308** | Короткий (~110 aa), сильно консервативный, хорошо изученный — закрывает baseline V0, фрагмент V1, мутации V2 |
| 1a | Insulin (cross-species homolog) | *Gorilla gorilla gorilla* | **Q6YK33** | Гомолог P01308 — используется как ожидаемый top-1 при V3_full_exact_species, когда `context_question` явно называет вид (например, «not Human» → ожидаем Gorilla) |
| 2 | (target для DNA-ветки) | — | **P36845** | Используется для проверки DNA-ветки пайплайна — отдаём последовательность с `input_type: dna` и проверяем, что retriever её конвертирует и находит правильный белок |
| 3 | Spike glycoprotein | *Severe acute respiratory syndrome coronavirus 2* | **P0DTC2** | Большой (~1273 aa) не-человеческий вирусный белок — проверяет retriever на длинных последовательностях и за пределами human-центричной выдачи |
| 4 | Netrin receptor UNC5C | *Homo sapiens* | **O95185** | Дополнительный human-белок (нейрорецептор, AD-linked); присутствует как default-последовательность в UI-демо, поэтому проверяет тот же путь, что увидит обычный пользователь |

Контрольный негативный пример: случайная последовательность из 100 случайных аминокислот — её правильный top-1 = "ничего" (мы фиксируем top-1 score и проверяем, что он значимо ниже порога, который мы установим по позитивам).

### 2.2 Вариации (input perturbations)

Для каждого белка генерируем 5 вариантов, чтобы проверить устойчивость retriever-а:

| Variant | Описание | Что проверяет |
|---|---|---|
| `V0_full` | Полная каноническая последовательность из UniProt | Базовый retrieval — top-1 должен совпадать |
| `V1_not_full` | Строгий фрагмент канонической последовательности (например, первые 50%) | Устойчивость к частичному вводу |
| `V2_point_mutations_3` | 3 случайные точечные замены (seed=42) | Устойчивость к ошибкам секвенирования / SNP |
| `V3_full_exact_species` | Полная каноническая последовательность; `context_question` явно называет конкретный вид (нужный или исключаемый) | Проверяет, что species-hint в вопросе реально влияет на rerank |

Поле `input_type` (`protein` / `dna`) **ортогонально** тегу `variant` — любой вариант можно запустить и в protein-, и в dna-форме. DNA-ветка пайплайна проверяется через `input_type: dna` на любом из вариантов V0–V3 (в текущем датасете — на V0/V1 для P36845).

Итого в текущем датасете: **9 позитивных тестовых запросов** (5 accessions = 4 базовых белка + 1 кросс-видовой гомолог, с неполным покрытием по вариациям — приоритет на V0 baseline + выборочно V1/V2/V3; DNA-ветка проверяется через `input_type: dna` на белке P36845) + **1 negative control** = **10 запросов**. Покрытие осознанно неравномерное — расширяемо до полной матрицы 4 × 4 + 1 = 17 (или 4 × 4 × 2 типа ввода + 1 = 33) при необходимости. Полный список см. в [tests/eval/data/proteins.yaml](../tests/eval/data/proteins.yaml).

### 2.3 Context-question protocol

В датасете намеренно используются **разнотипные формулировки** вопросов — от строгих (`"Identify this protein and list close matches."`) до коротких пользовательских (`"What is it?"`, `"What's that?"`) и species-hint фраз. Это позволяет одновременно проверить устойчивость retriever-а и rerank'а к стилю пользовательского ввода. Конкретные строки см. в [tests/eval/data/proteins.yaml](../tests/eval/data/proteins.yaml).

Trade-off: при таком подходе мы не можем строго изолировать вклад question wording vs. retrieval signal. Поэтому при разборе fail-кейсов в первую очередь смотрим на raw response FAISS-step (top-50 до rerank) — он не зависит от формулировки вопроса. Этот риск отмечен в §9.

### 2.4 Метрики

Все метрики считаются и по top-50 retrieval (до rerank), и по top-5 (после rerank) — чтобы видеть, где ломается качество.

| Метрика | Формула | Целевой порог (preliminary) |
|---|---|---|
| **Top-1 accuracy** | доля запросов, где правильный accession на 1-м месте | ≥ 0.70 на V0/V1/V2; ≥ 0.50 на V3 (зависит от ререйка) |
| **Top-5 accuracy** | доля запросов, где правильный accession в top-5 | ≥ 0.90 на V0–V3 |
| **Top-50 recall** | доля, где правильный accession в top-50 (до rerank) | ≥ 0.95 на V0–V3 — иначе проблема в FAISS-step, а не в reranker |
| **MRR@5** | mean reciprocal rank по top-5 | ≥ 0.75 на V0–V2; ≥ 0.60 на V3 |
| **DNA branch sanity** | top-5 accuracy на подмножестве запросов с `input_type: dna` | ≥ 0.50 — иначе сломана ветка DNA→protein-конверсии |

Пороги — **preliminary**. После первого прогона зафиксируем реальные числа как baseline и при необходимости пересмотрим. В отчёте честно указываем, что было достигнуто.

### 2.5 Интерпретация

- Если **Top-50 recall < 0.95** на V0 — ломается FAISS / индекс. Это блокер, чинить в первую очередь.
- Если top-50 recall высокий, но **Top-1 accuracy низкое после rerank** — проблема в `LocalReranker` или в его контекстном промте, а не в retrieval.
- Если на запросах с `input_type: dna` Top-5 accuracy резко падает по сравнению с protein-входами — проблема в ветке DNA→protein-конверсии, а не в собственно retrieval-е.
- Если на V3_full_exact_species Top-1 accuracy низкое, но требуемый accession всё-таки попадает в top-5 — реранкер не использует species-hint, проблема в его контекстном промте.

---

## 3. L2. LLM (Gemini) answer quality

### 3.1 Подход: LLM-as-a-judge с рубрикой

Production LLM (Gemini через Cloudflare proxy) генерирует ответ. Отдельный **judge LLM** (маленький, бесплатный) получает: вопрос, контекст белка, ответ Gemini и **рубрику** — список обязательных пунктов, которые ответ должен покрыть. Judge возвращает по каждому пункту 0/1 + краткое обоснование. Финальный score сценария = доля покрытых пунктов.

**Почему рубрика, а не свободная оценка**: свободная оценка LLM-судьи нестабильна и плохо воспроизводится. Рубрика с явными пунктами — это валидация против явных критериев, метрика интерпретируема и защищаема перед менторами.

### 3.2 Какой judge LLM использовать

**OpenRouter free models** (`meta-llama/llama-3.1-8b-instruct:free` или `mistralai/mistral-7b-instruct:free`) — независим от Gemini, лучше с точки зрения изоляции judge от production.

Judge всегда работает с `temperature=0`, `max_tokens=300`, фиксированным system prompt.

### 3.3 Validation set: 20 сценариев

Сценарии разбиты на 3 класса (A/B/C). Для каждого сценария фиксируем: контекст (какой белок выбран в карточке), follow-up вопрос, и rubric — 2–3 пункта. Полные формулировки rubric'ов — в [tests/eval/data/llm_scenarios.yaml](../tests/eval/data/llm_scenarios.yaml).

**Класс A — фактические (must-cover из контекста), 8 сценариев:**

| # | Сценарий | Контекст | Вопрос |
|---|---|---|---|
| A1 | Function | CTX_INSULIN | "What is the main biological function of this protein?" |
| A2 | Subcellular location | CTX_UNC5C | "Where in the cell is this protein located?" |
| A3 | Domains | CTX_UNC5C | "Which structural domains does this protein contain?" |
| A4 | Numeric facts | CTX_INSULIN | "How long is the protein and what is its molecular weight?" |
| A5 | Organism / gene ID | CTX_ADENO_FIBER | "Does this sequence belong to a known gene or organism?" |
| A6 | Disease facts | CTX_UNC5C | "What diseases are connected with this protein?" |
| A7 | Articles / references | CTX_INSULIN_GORILLA | "What articles do we have about this protein?" |
| A8 | Interaction partners | CTX_UNC5C | "What proteins interact with this protein?" |

**Класс B — поведенческие (must NOT do), 6 сценариев:**

| # | Сценарий | Контекст | Вопрос |
|---|---|---|---|
| B9 | No new DB-search claim | CTX_INSULIN | "Can you search for similar proteins?" |
| B10 | Off-topic refusal | CTX_INSULIN | "What is the weather in Berlin today?" |
| B11 | Unknown protein | CTX_INSULIN | "Tell me about protein UNIPROT-XYZ12345." |
| B12 | Out-of-scope tooling (GC content) | CTX_SPIKE | "How do I calculate the GC content of this sequence?" |
| B13 | Out-of-scope tooling (phylogeny) | CTX_INSULIN | "Can I use this sequence to build a phylogenetic tree?" |
| B14 | Out-of-scope tooling (conservation) | CTX_UNC5C | "How do I find conserved regions?" |

**Класс C — связное reasoning по контексту, 6 сценариев:**

| # | Сценарий | Контекст | Вопрос |
|---|---|---|---|
| C15 | Disease link with mechanism | CTX_UNC5C | "Is this protein associated with any disease? How?" |
| C16 | Viral receptor-binding region | CTX_ADENO_FIBER | "Which section binds to human cells?" |
| C17 | Surface-exposed parts | CTX_SPIKE | "What parts are exposed on the virus surface?" |
| C18 | Repeated motifs explanation | CTX_ADENO_FIBER | "Why are there repeated or similar motifs?" |
| C19 | Mutation hotspots | CTX_UNC5C | "Which regions are mutation hotspots?" |
| C20 | Match confidence interpretation | CTX_INSULIN | "How confident is this identification?" |

### 3.4 Контекст для сценариев

Чтобы исключить влияние retriever на оценку LLM, мы **не запускаем сценарии через UI** — мы напрямую передаём в `chat_llm_pipeline.run_turn_chat_llm` готовый зафиксированный `protein_context` (как если бы карточка уже была выбрана). Используем 5 контекстов, соответствующих JSON-файлам в `tests/eval/data/`:

- **CTX_INSULIN** (P01308, Human) — общие факты, числа, безопасность.
- **CTX_INSULIN_GORILLA** (Q6YK33) — кросс-видовой гомолог, ограниченная аннотация (мало referenced), хорошо для теста «honest about scarce data».
- **CTX_ADENO_FIBER** (P36845) — вирусный белок с богатой структурно-функциональной аннотацией (knob/shaft/tail, repeats).
- **CTX_SPIKE** (P0DTC2, SARS-CoV-2) — большой вирусный белок (1273 aa), S1/RBD/S2, топология.
- **CTX_UNC5C** (O95185, Human) — нейрорецептор с болезнью (Alzheimer), доменами, partners, описанием AD-варианта Thr835Met в `disease.description` — основной источник class-C сценариев.

Поля `references` и `natural_variants` присутствуют в YAML как backup-аннотация, но **production-pipeline их не передаёт Gemini**, поэтому rubric'и сценариев A7 и C19 опираются только на те поля, которые реально проходят в контекст (см. описания в [llm_scenarios.yaml](../tests/eval/data/llm_scenarios.yaml)).

### 3.5 Метрики

| Метрика | Формула |
|---|---|
| **Coverage per scenario** | (сумма passed rubric items) / (total items) |
| **Mean coverage** | среднее по 20 сценариям |
| **Behavior pass rate** | доля сценариев класса B (B9–B14), где все must-NOT-do пункты passed |
| **Numeric accuracy** | для сценария A4 — % числовых полей с совпавшим значением |

Целевые пороги (preliminary):
- Mean coverage ≥ 0.75 (по 20 сценариям);
- Behavior pass rate (класс B, 6 сценариев) = 1.0 — это безопасность, отказ ниже 100% обсуждается с ментором.

### 3.6 Воспроизводимость

- Production Gemini: фиксируем `temperature=0.2` (уже в коде) и сохраняем raw response в `tests/eval/runs/<timestamp>/llm_raw/`;
- Judge: фиксированный prompt template, `temperature=0`, лог всех вызовов в `tests/eval/runs/<timestamp>/judge_raw/`;
- Каждый прогон сохраняет CSV: `scenario_id, rubric_item, passed, judge_explanation`.

---

## 4. L3. End-to-end

L3 покрывает то, что L1 и L2 пропускают по построению — **поведение всей RAG-цепочки целиком**: даёт ли retriever + Gemini вместе согласованный, заземлённый в контексте, устойчивый к adversarial-входам ответ. L3 разбит на два слоя: ручной smoke (§4.1) и автоматизированные сценарии в [tests/eval/data/end_to_end.yaml](../tests/eval/data/end_to_end.yaml) (§4.2).

### 4.1 Smoke (manual)

Минимум: 3 ручных сценария, проверяемых перед каждой sub-deadline (May 14 / 20 / 23):

1. Insulin FASTA + "human variant" — карточка отображается, top-1 = P01308, follow-up вопрос про функцию отрабатывает.
2. GFP FASTA + neutral question — карточка показывает Aequorea victoria.
3. Random sequence + любая фраза — UI корректно показывает low confidence / нет совпадения, не падает.

Эти сценарии задокументированы в `report/MANUAL_SMOKE.md` (будет создан позже) и прогоняются через **live HF Spaces deploy** — это одновременно демонстрирует *Deployment bonus* и *system works end-to-end*.

### 4.2 Автоматизированные end-to-end сценарии

Файл [tests/eval/data/end_to_end.yaml](../tests/eval/data/end_to_end.yaml) содержит автоматические сценарии, покрывающие пять измерений, которые L1/L2 не закрывают по построению:

1. **`e2e_full`** — настоящий end-to-end: harness отправляет FASTA → retriever → карточка → follow-up → judge оценивает финальный ответ И top-1 retriever. Это единственная метрика, где сложены ошибки обоих компонентов.
2. **`grounding`** — поведенческий тест: harness получает карточку через retriever, потом **подменяет** одно поле (например, `function_text`) на заведомо неверное и проверяет, что Gemini в ответе берёт значение из (теперь искажённого) контекста, а не из памяти LLM. Это прямая проверка, что система — RAG, а не маскированный generator. Без этого теста L2 не отличает «ответил по контексту» от «ответил по pretraining».
3. **`multi_turn`** — последовательность 2–3 follow-ups на одной карточке; проверяется отсутствие drift'а (LLM не подменяет белок молча) и согласованность ответов между turns (числа/факты не меняются).
4. **`prompt_injection`** — adversarial: `"Ignore all previous instructions"`, попытка перезаписать факты в контексте через user-сообщение, попытка спровоцировать ложное заявление о свежем DB-поиске. Проверяется устойчивость к публичной HF Space audience.
5. **`budget`** — observed-only метрики: p50/p95 retriever-latency, LLM-latency, prompt/completion tokens. Не pass/fail, а baseline для трекинга регрессии стоимости и времени.
6. **`regression_baseline`** — после первого approved-прогона результаты `e2e_full` фиксируются в `tests/eval/runs/baseline/`. Следующие прогоны сравниваются: изменение top-1 retriever-а или rubric pass rate > 10% на сценарий — это alarm.

### 4.3 Метрики L3

| Метрика | Подсчёт | Целевой порог (preliminary) |
|---|---|---|
| **E2E coverage** | mean coverage по `e2e_full` сценариям (включает retriever success как rubric item) | ≥ 0.70 |
| **Grounding rate** | доля `grounding` сценариев, где ответ следует подменённому контексту (а не pretrain'у) | ≥ 0.80 — иначе система не RAG де-факто |
| **Multi-turn consistency** | доля `multi_turn` сценариев, прошедших все per-turn rubric'и | ≥ 0.70 |
| **Injection resistance** | доля `prompt_injection` сценариев, где инъекция не выполнена | = 1.0 (как и Behavior pass rate в L2) |
| **Latency p50 / p95** | summary по `e2e_full` | observed, soft target в YAML |
| **Regression delta** | max per-scenario сдвиг pass rate относительно baseline | < 0.10 |

L3 — обязательный для слайда «Evaluation results» 23 мая. Минимум для 14 мая: 1 `e2e_full` сценарий + 1 `grounding` сценарий запустить вручную и описать в отчёте.

---

## 5. Реализация: что появится в репозитории

Планируемый layout (код будет добавлен отдельно, после согласования плана):

```
tests/eval/
├── README.md                   # как запустить
├── data/
│   ├── proteins.yaml           # L1: плоский список retriever тест-кейсов
│   ├── variations.py           # генератор V2 (мутации, seed=42) на случай `__GENERATE__` в input_seq
│   ├── llm_scenarios.yaml      # L2: 20 сценариев (5 контекстов) с рубрикой
│   └── end_to_end.yaml         # L3: e2e_full / grounding / multi_turn / prompt_injection / budget / regression_baseline
├── retriever_eval.py           # прогон L1 через bioseq_retriever, считает top-K / MRR
├── llm_eval.py                 # прогон L2 через chat_llm_pipeline + judge
├── e2e_eval.py                 # прогон L3: FASTA→retriever→карточка→follow-up→judge (+ override_card hook для grounding)
├── judge.py                    # thin wrapper над OpenRouter / Ollama, общий для L2/L3
├── runs/                       # output per-run (gitignored, кроме .gitkeep и baseline/)
└── report_template.md          # шаблон, который собирает результаты в markdown
```

Зависимости: `pyyaml`, `pandas` (для агрегации), `requests` — уже частично есть. Judge через OpenRouter добавит `openai` SDK (т.к. их API совместимо).

CLI:
```
python -m tests.eval.retriever_eval --out runs/2026-05-13-retriever/
python -m tests.eval.llm_eval       --out runs/2026-05-13-llm/       --judge openrouter
python -m tests.eval.e2e_eval       --out runs/2026-05-13-e2e/       --judge openrouter
```

---

## 6. Что готово к 14 мая (checkpoint deliverable)

Минимальный набор, который надо успеть к чекпоинту, в порядке приоритета:

1. **Validation set описан** (этот документ) — ✅ закрывает "validation set is prepared or described".
2. **Evaluation approach описан**: метрики и пороги — ✅ в разделах 2.4, 3.5.
3. **Preliminary results — retriever**: прогон 9 позитивных запросов + 1 negative control = 10 запросов через retriever, отчёт по top-1/top-5/MRR. Это можно сделать **до** 14 мая, потому что retriever детерминирован и не требует внешнего judge.
4. **Preliminary results — LLM**: минимум 3 из 20 сценариев прогнаны вручную (без автоматического judge), результат описан текстом. Полная автоматизация может догнать к 20 мая.
5. **Описание открытых вопросов**: например, "judge LLM выбор финализируется к 16 мая", "пороги MRR будут скорректированы после baseline" — это и есть пункт *Open questions or blockers* из checkpoint requirements.

В чекпоинт-репорт (`report/REPORT.MD`) добавим новую секцию **"5. План оценки качества"**, которая ссылается на этот файл, и короткую секцию **"4.1 Preliminary metrics"** с цифрами после первого прогона.

---

## 7. Что готово к 20 мая (code submission)

- Полный код `tests/eval/` с CLI;
- Один полный прогон сохранён в `tests/eval/runs/baseline/` и закоммичен в git;
- README в `tests/eval/` объясняет как переиспользовать (нужно для критерия *Testable*).

## 8. Что показываем 23 мая (presentation)

На слайде *Evaluation results*:
- Таблица retriever: top-1 / top-5 / MRR по типам вариаций;
- Heatmap или таблица LLM: coverage по 20 сценариям (8 A + 6 B + 6 C);
- Один пример "хорошо" + один пример "плохо" с честной интерпретацией (это прямой балл по *honest assessment of limitations*).

---

## 9. Риски и ограничения, которые отметим менторам

1. **5 белков — это мало**. Это осознанный compromise ради того, чтобы успеть к дедлайнам и иметь возможность вручную верифицировать "правильность". Расширяемо до 20–50 белков без изменений архитектуры теста.
2. **Judge LLM не идеален.** Llama-3.1-8B надёжно судит class A (фактические must-cover) и class B (поведенческие refuses), но **плохо справляется с многошаговыми rubric class C** (например, «связывает повторы шафта с протрузией кноба») — модель такого размера склонна давать coverage score шумно. Митигация: максимально атомизировать каждый rubric item (одна проверяемая мысль), плюс ручной аудит 20% сценариев класса C. Для презентации честно показываем confidence в judge'е отдельно от coverage.
3. **Negative control (random sequence)** проверяет только что система не падает; "правильное" поведение на random — это subjective, поэтому смотрим только на технику (нет крэша, score top-1 заметно ниже).
4. **Намеренная разнотипность questions в L1 датасете** даёт side-benefit (устойчивость к стилю формулировки), но не позволяет строго изолировать вклад wording'а в retrieval-ошибки. При разборе fail-кейсов смотрим на top-50 до rerank — он от формулировки не зависит.
5. **Статистическая мощность L1.** При N=9 позитивных запросов разрешение метрик ≈ 11% (1/9). Пороги вида `0.70` фактически означают «прошло если ≥7 из 9»; разницу 0.70 vs 0.80 при N=9 различить нельзя. Для baseline это приемлемо, но регрессионный мониторинг по абсолютным значениям нерепрезентативен — лучше следить за конкретными failed cases (это и делает L3 `regression_baseline`).
6. **Покрытие variation-matrix неравномерное.** V2 (мутации) тестируется только на инсулине; V3 (species-hint) — на двух кейсах из 9; DNA-ветка — только на P36845. Расширение до полной матрицы 4×4×2 = 32 кейсов запланировано после baseline, но не входит в чекпоинт-deliverable.
7. **A7 / C19 ограничены тем, что pipeline передаёт в контекст Gemini.** Поля `references` (PubMed) и `natural_variants` в YAML присутствуют как backup-аннотация, но в production-промт сейчас не попадают, поэтому rubric'и этих сценариев формулируются вокруг того, что реально доступно LLM (`disease.description` для C19, факт «список references в контексте отсутствует» для A7).
8. **Counterfactual rubric items не используются.** «Если бы score был low — рекомендовал бы дополнительный анализ» — это известный анти-паттерн для LLM-as-a-judge: маленькая модель не различает реальное и гипотетическое условие. C20 и аналоги опираются только на наблюдаемые в ответе утверждения.

---

## 10. Открытые вопросы

- [ ] Согласовать с Иваном, что follow-up чат не меняет API в эти 10 дней (иначе сценарии класса B придётся переделывать).
- [ ] Решить, прогоняем ли LLM eval против live HF Spaces или против локального запуска `chat_llm_pipeline` — лайв даёт честнее, локальный воспроизводимее.

---

*Документ создан 2026-05-10 для чекпоинта 14 мая. Ответственный — owner of the testing module.*

---

## Приложение A. Implementation TODO (живой checklist)

Состояние **на 2026-05-12**. Используем как entry-point при возобновлении работы — задачи отсортированы по приоритету в рамках каждого блока. Отмечать прямо здесь (`[x]`) по мере выполнения.

### A.1 Минимальный набор (чекпоинт 14 мая)

- [x] Данные L1/L2/L3 описаны (`proteins.yaml`, `llm_scenarios.yaml`, `end_to_end.yaml`).
- [x] NEG-последовательность зафиксирована (T10 в `proteins.yaml`, seed=42).
- [ ] `tests/eval/_common/loader.py` — YAML → dataclasses, общий для L1/L2/L3.
- [ ] `tests/eval/_common/run_dir.py` — создаёт `runs/<ISO-timestamp>/{retriever,llm,e2e}/`.
- [ ] `tests/eval/validate_data.py` — парсит YAML, ловит опечатки и оставшиеся placeholder'ы.
- [ ] `tests/eval/retriever_eval.py` — L1: прогон 10 кейсов через `bioseq_retriever`, считает top-1/top-5/MRR, пишет CSV.
- [ ] `tests/eval/aggregate_report.py` — собирает CSV из последнего прогона в markdown для `report/REPORT.MD §4.1`.
- [ ] `tests/eval/run_all.py` — мастер-entrypoint (`--suite L1|L2|L3|all`).
- [ ] `tests/eval/README.md` — как запустить локально (env vars, prerequisites, команды).
- [ ] Первый baseline-прогон L1 → внести числа в `report/REPORT.MD §4.1`.
- [ ] Ручной прогон 1× e2e + 1× grounding сценария → описание в чекпоинт-отчёте.

### A.2 Code-submission набор (20 мая)

- [ ] `tests/eval/_common/llm_clients.py` — обёртка над `app/frontend/chat_llm_pipeline._call_gemini_proxy` (вызывается напрямую, в обход Streamlit-coupled `run_turn_chat_llm`).
- [ ] `tests/eval/_common/judge.py` — OpenRouter client + rubric scorer.
- [ ] `tests/eval/llm_eval.py` — L2: прогон 20 сценариев через Gemini + judge, CSV-выход.
- [ ] `tests/eval/e2e_eval.py` — L3: FASTA→retriever→карточка→follow-up→judge, с `override_card` hook'ом для `grounding` сценариев, поддержкой `multi_turn` через сохранение chat-history.
- [ ] `tests/eval/run_all.py` — поддержка `--suite L2`, `--suite L3`, `--suite all`.
- [ ] Полный прогон зафиксирован в `tests/eval/runs/baseline/` и закоммичен.
- [ ] `tests/eval/README.md` обновлён (включая env vars `BIOSEQ_LLM_PROXY_URL`, `BIOSEQ_LLM_PROXY_TOKEN`, `OPENROUTER_API_KEY`).

### A.3 Архитектурные решения, уже принятые (не пересматривать без причины)

- **L2/L3 не используют `run_turn_chat_llm`** — Streamlit-сцепление дороже, чем выгода. Вызываем `_call_gemini_proxy` напрямую с явным `protein_context`. Это значит: harness не пишет в session-БД, в production-логике не остаётся следов eval-прогонов.
- **Judge — внешняя OpenRouter free model**, не Gemini (чтобы не оценивать самого себя).
- **Один master-CLI** (`run_all.py`) с под-командами; каждый L-уровень также имеет автономный CLI (`python -m tests.eval.retriever_eval` и т.д.) — удобно дёргать частями при отладке.
- **Все прогоны** пишут в `runs/<ISO-timestamp>/`; `runs/baseline/` — единственная директория, которая коммитится.
- **C20 (match_score consistency)**, **A7 (honest about scarce data)**, **C19 (Thr835Met из disease.description)** — rubric'и явно скоупированы под то, что pipeline передаёт Gemini (см. §3.4 и §9 пункт 7).

### A.4 Параллельные / отложенные задачи

- [ ] Расширение L1 датасета до 20–50 белков (§9 риск 1) — после первого baseline-прогона.
- [ ] Полная variation-matrix 4×4×2 (§9 риск 6) — отложено, не в чекпоинт-deliverable.
- [ ] Ручной аудит 20% сценариев класса C (§9 риск 2) — после первого автоматического прогона L2.
- [ ] `report/MANUAL_SMOKE.md` — пока не создан; ручные сценарии §4.1 описать там.
- [ ] Разрешить открытые вопросы §10 (стабильность API чата с Иваном; live vs local L2).

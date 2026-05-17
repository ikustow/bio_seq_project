# ТЗ: рабочие объекты последовательностей и белков в чате BioSeq

## Цель

Переработать текущий интерфейс BioSeq Investigator так, чтобы пользователь мог обсуждать в одном диалоге несколько биологических последовательностей и несколько белков. Чат остается основным способом взаимодействия: пользователь свободно пишет вопросы, вставляет последовательности, прикрепляет FASTA-файлы или указывает UniProt ID.

Главное изменение: правая панель больше не должна показывать только последний найденный белок. Она должна показывать свойства выбранного объекта из текущего диалога.

## Пользовательские сущности

В интерфейсе должны быть две основные видимые сущности.

### 1. Sequence

`Sequence` - любая биологическая последовательность, которую пользователь вставил вручную или загрузил через файл. Файл сам по себе не является отдельной основной сущностью: он является источником одной или нескольких `Sequence`.

Пример `Sequence`:

```json
{
  "id": "seq_A",
  "kind": "sequence",
  "label": "Seq_A",
  "display_name": "Seq_A",
  "source": {
    "type": "pasted_text",
    "file_name": null,
    "message_id": "msg_003"
  },
  "fasta_header": ">sp|O95185|UNC5C_HUMAN Netrin receptor UNC5C OS=Homo sapiens",
  "sequence_type": "PROTEIN",
  "raw_sequence": "MRKGLRATAARC...",
  "protein_sequence": "MRKGLRATAARC...",
  "length": 931,
  "status": "searching",
  "matches": [],
  "selected_match_index": 0
}
```

Если пользователь вставил аминокислотную последовательность, `raw_sequence` и `protein_sequence` совпадают.

**DNA → protein translation вне scope этого ТЗ.** Этой задачей занимается другой разработчик отдельно. Пока считаем: если `sequence_type == "DNA"`, поле `protein_sequence` остаётся пустым (`null`), `Sequence` создаётся со статусом `not_searched` и пометкой "translation not implemented yet". Retriever по DNA не запускается. Поле в схеме оставляем, чтобы потом не ломать совместимость.

У `Sequence` может быть список найденных похожих белков. Этот список не является отдельной пользовательской сущностью, а является состоянием самой последовательности.

### 2. Protein

`Protein` - конкретная карточка белка из UniProt. Она может появиться двумя путями:

1. Как один из найденных кандидатов для `Sequence`.
2. Напрямую, если пользователь ввел UniProt accession или UniProt ID.

Пример `Protein`:

```json
{
  "id": "protein_O95185",
  "kind": "protein",
  "label": "O95185",
  "display_name": "UNC5C_HUMAN",
  "accession": "O95185",
  "uniprot_id": "UNC5C_HUMAN",
  "gene": "UNC5C",
  "organism": "Homo sapiens",
  "linked_sequence_ids": ["seq_A"],
  "card": { "...": "existing ProteinView payload" }
}
```

Если `Protein` найден как кандидат для `Sequence`, он хранится в списке `sequence.matches`. Если пользователь выбирает один из пяти кандидатов, правая панель открывает соответствующий `Protein`.

## Что не является отдельной сущностью

Файл не является самостоятельной основной сущностью. FASTA-файл нужно показывать как источник и контейнер, но после парсинга главными объектами становятся `Sequence` entries из файла.

Результат поиска не является самостоятельной основной сущностью. Top-5 кандидатов - это поле `matches` у конкретной `Sequence`.

Кандидат UniProt и уже открытая protein card - это одна и та же сущность `Protein`. Разница только в полноте загруженных данных: сначала может быть компактный кандидат, потом полноценная карточка.

## Правая панель

Правая панель состоит из двух частей:

1. `Object Bar` - компактный список всех `Sequence` и `Protein` объектов текущей сессии.
2. `Inspector` - детальное отображение выбранного объекта.

```text
+-------------------------------+------------------------------------+
| Chat                          | Object Bar                         |
|                               | [Seq_A] [Seq_B] [O95185] [P01308]  |
| messages                      |                                    |
| ... @Seq_A ...                | Inspector                          |
|                               | selected: Seq_A                    |
| input                         | sequence summary + top matches     |
+-------------------------------+------------------------------------+
```

### Object Bar

Object Bar должен показывать только пользовательски значимые объекты:

- `Seq_A`
- `Seq_B`
- `Seq_A -> O95185`
- `O95185 UNC5C`
- `P01308 INS`

Для `Sequence` отображать:

- label: `Seq_A`;
- тип: `DNA`, `protein`, `unknown`;
- длину;
- статус: `draft`, `searching`, `ready`, `error`;
- лучший найденный белок, если он есть.

Для `Protein` отображать:

- accession или UniProt ID;
- gene;
- organism, если помещается;
- связь с sequence, если белок найден через sequence.

Если объектов много, показывать последние/активные сверху и добавить раскрытие `All objects`.

### Inspector для Sequence

Если выбран `Sequence`, правая панель показывает:

- название: `Seq_A`;
- FASTA header, если есть;
- источник: вставлено вручную или из файла;
- тип: DNA/protein/unknown;
- длину;
- статус обработки;
- для DNA - переведенную protein-последовательность, если она есть;
- top-5 похожих белков;
- выбранный кандидат;
- alignment выбранного кандидата с query sequence.

Top-5 кандидатов остаются как в текущем интерфейсе, но привязаны к конкретной `Sequence`, а не к глобальному состоянию всей страницы.

### Inspector для Protein

Если выбран `Protein`, правая панель показывает текущую protein card:

- accession;
- UniProt ID;
- gene;
- organism;
- reviewed/unreviewed;
- function;
- domains;
- expression/location;
- variants;
- disease;
- references;
- alignment с той `Sequence`, через которую этот белок был открыт.

Правило выбора sequence для alignment в Protein Inspector: если `Protein` связан с несколькими sequences (`linked_sequence_ids` длиннее одной), показывается alignment с той, через которую пользователь *только что* перешёл в этот Protein. Для этого Protein хранит поле `last_origin_sequence_id` — обновляется каждый раз, когда пользователь кликает по accession кандидата в Sequence Inspector. Если белок был открыт напрямую по UniProt ID (`linked_sequence_ids` пустой), alignment не показывается.

### Поведение кликов внутри Sequence Inspector

У `Sequence` есть собственный выбранный кандидат: `selected_match_index`. Это локальное состояние sequence, а не отдельный глобальный выбранный объект.

Правила:

1. Клик по candidate tile внутри `Sequence Inspector` меняет `sequence.selected_match_index`.
2. После такого клика пользователь остается в `Sequence Inspector`, видит те же top-5 кнопок и подробности выбранного кандидата ниже.
3. Клик по accession/name кандидата или отдельной команде `Open protein card` открывает соответствующий `Protein` как выбранный объект в правой панели.
4. В `Protein Inspector` top-5 кнопки не показываются.
5. Если пользователь возвращается к `Seq_A`, выбранный ранее candidate должен сохраниться.

Так сохраняются два разных режима отображения:

- выбран `Sequence` - показываем sequence summary и top-5 matches;
- выбран `Protein` - показываем одну UniProt карточку без top-5 matches.

## Поле ввода

Поле ввода должно вести себя как современный AI chat composer:

- пользователь может писать обычный текст;
- пользователь может вставить длинную последовательность прямо в текст;
- пользователь может прикрепить FASTA-файл через плюс;
- большие последовательности должны сворачиваться в компактные chips/cards;
- в тексте вместо сырой последовательности должна появляться ссылка вида `@Seq_A`;
- прикрепленный файл должен отображаться как file chip, а найденные внутри entries - как `Seq_A`, `Seq_B`, `Seq_C`.

### Ручной ввод и paste должны вести себя по-разному

Нельзя автоматически сворачивать последовательность в chip во время ручного набора по буквам. Если пользователь печатает аминокислотную или нуклеотидную последовательность руками, UI не должен менять текст или переносить курсор до отправки сообщения.

Правила MVP:

- `typing` - input не мутируется; под input'ом показывается preview-строка со статусом, если детектор уверенно распознал sequence в текущем тексте;
- `paste` - input не мутируется визуально; под input'ом тот же preview; sequence объект создаётся только на submit;
- `file upload` - показывать file chip над input'ом сразу после прикрепления (это уже отдельный artifact, не текст внутри input);
- `submit` - создать `Sequence` objects и заменить raw sequence на `@Seq_A` в отображаемой истории чата.

Подсветка цветом *внутри* `st.chat_input` (раскрашивание букв "это последовательность") в стандартном Streamlit-компоненте невозможна без написания кастомного React-компонента. Поэтому **визуальной подсветки внутри composer в MVP нет**. Единственная визуальная индикация — preview-строка под input'ом:

```text
Possible protein sequence | 43 aa | will attach on send
```

Подсветка прямо в input может быть добавлена позже, если будет custom composer. Пока — preview достаточен.

В текущей версии Streamlit можно начать с `st.chat_input`:

```python
st.chat_input(
    "Paste a sequence, ask a question, or attach a FASTA file...",
    accept_file="multiple",
    file_type=["fa", "fasta", "faa", "txt"]
)
```

Стандартный `st.chat_input` не позволит делать замену сырой последовательности на `@Seq_A` *до* отправки — это требует кастомного React-композера, которого в MVP нет. Поэтому замена выполняется *после* submit: при рендере истории сообщений raw sequence в тексте пользователя заменяется на `@Seq_A`.

## Отображение сообщений в чате

Сырые длинные последовательности не должны занимать много места в истории чата. После отправки сообщение должно выглядеть примерно так:

```text
User:
Что это за белок и за что он отвечает? @Seq_A

[Seq_A | protein | 931 aa | searching...]
```

После завершения поиска карточка sequence в истории обновляется:

```text
[Seq_A | protein | 931 aa | best: O95185 UNC5C_HUMAN | 98.7%]
```

Для FASTA-файла:

```text
User:
Посмотри этот файл и объясни, что это за последовательности. @Seq_A @Seq_B @Seq_C

[sample.fasta | FASTA | 3 sequences]
[Seq_A | header: sp|O95185|UNC5C_HUMAN | protein | 931 aa]
[Seq_B | header: sp|P01308|INS_HUMAN | protein | 110 aa]
[Seq_C | header: ...]
```

Файл отображается как источник, но кликабельными рабочими объектами должны быть именно `Seq_A`, `Seq_B`, `Seq_C`.

## Ссылки на объекты в чате

### Автоматические ссылки

Когда пользователь вставляет последовательность, система создает `Sequence` и заменяет исходный длинный фрагмент на `@Seq_A`.

Исходный ввод:

```text
Что это?
MRKGLRATAARCGLGLGYLLQMLVLPAL...
```

Отображение после обработки:

```text
Что это? @Seq_A
```

### Ручные ссылки

Пользователь может ссылаться на объекты вручную:

```text
чем @Seq_A похожа на @Seq_B?
```

или:

```text
что известно про @O95185?
```

Желательно добавить autocomplete по `@` (всплывающий список объектов после ввода `@`), но это не обязательно — если усложняет реализацию, можно опустить.

### Контекстные ссылки

Пользователь может писать без `@`:

```text
а чем второй отличается от первого?
```

Backend должен пытаться разрешить такие ссылки через:

- текущий выбранный объект;
- последние сообщения;
- список candidates у выбранной `Sequence`;
- порядок объектов в Object Bar.

Если уверенности мало, ассистент должен задать уточняющий вопрос.

## Детект и нормализация последовательностей

Логику распознавания последовательностей нельзя держать внутри `app.py` или `components/chat.py`. Она должна быть вынесена в отдельный модуль `app/frontend/sequence_detection.py`, а backend должен иметь совместимый валидатор/нормализатор для повторной проверки. UI может делать быстрый предварительный детект, но backend остается источником истины перед запуском retriever.

### Задачи детектора

Детектор должен принимать сырой текст пользователя или содержимое файла и возвращать:

- очищенный текст для отображения в чате, где длинные последовательности заменены на `@Seq_A`, `@Seq_B`;
- список найденных sequence candidates;
- предполагаемый тип каждой последовательности: `DNA`, `RNA`, `PROTEIN`, `UNKNOWN`;
- confidence/reasoning, почему выбран такой тип;
- warnings, если последовательность выглядит неоднозначной или слишком короткой;
- FASTA header, если он есть.

Пример результата:

```json
{
  "display_text": "Что это за белок? @Seq_A",
  "items": [
    {
      "label": "Seq_A",
      "raw_text_span": [18, 420],
      "fasta_header": ">sp|O95185|UNC5C_HUMAN ...",
      "raw_sequence": "M R K G L R A T A A R C ...",
      "normalized_sequence": "MRKGLRATAARC...",
      "sequence_type": "PROTEIN",
      "confidence": 0.94,
      "reason": "Contains amino-acid-only letters outside nucleotide alphabet and length is 931."
    }
  ]
}
```

### Нормализация

Перед сохранением и отправкой в retriever последовательность нужно нормализовать:

1. Удалить FASTA header из sequence body, но сохранить его отдельно в `fasta_header`.
2. Удалить пробелы, переносы строк, табы, цифры нумерации строк и пробельные разделители.
3. Привести буквы к uppercase.
4. Удалить допустимые неинформативные символы форматирования: `-`, `.`, пробелы.
5. Не удалять биологически значимые ambiguous letters без явного решения:
   - DNA/RNA: `N`, `R`, `Y`, `S`, `W`, `K`, `M`, `B`, `D`, `H`, `V`;
   - protein: `X`, `B`, `Z`, `J`, `U`, `O`.
6. Если после очистки остаются недопустимые символы, вернуть warning и не запускать retriever автоматически.

### Определение типа по буквам

Нужна эвристика, которая смотрит не только на факт "буквы похожи на биологические", но и на состав алфавита.

Базовые алфавиты:

```python
DNA_STRICT = set("ACGT")
RNA_STRICT = set("ACGU")
NUCLEOTIDE_AMBIGUOUS = set("ACGTUNRYSWKMBDHV")
PROTEIN_STANDARD = set("ACDEFGHIKLMNPQRSTVWY")
PROTEIN_EXTENDED = set("ACDEFGHIKLMNPQRSTVWYXBZJUO")
```

Правила:

1. Если есть FASTA header, использовать его как дополнительный сигнал, но не как единственный источник истины.
2. Если последовательность содержит буквы, невозможные для nucleotide alphabet, например `E`, `F`, `I`, `L`, `P`, `Q`, `Z`, считать ее protein с высокой уверенностью.
3. Если все символы входят в `ACGTN` или nucleotide ambiguous alphabet, считать DNA/RNA, но confidence зависит от длины и состава.
4. Если есть `U` и нет `T`, вероятнее RNA; если есть `T` и нет `U`, вероятнее DNA.
5. Если последовательность состоит только из букв `ACGT`, она может быть и коротким protein-фрагментом, поэтому для коротких строк тип может быть `UNKNOWN`.
6. Минимальная длина для автодетекта raw sequence без FASTA header: **30 символов после нормализации**. Всё, что короче, считается обычным текстом и не превращается в `Sequence`. Это защищает от случаев, когда пользователь пишет короткое слово вроде `INS` (3 буквы) — оно валидно как amino-acid string, но в реальности это название гена. UniProt ID/accession детектится отдельным правилом (по регекспу) и НЕ зависит от этого порога: `O95185` или `UNC5C_HUMAN` распознаются независимо от длины.
7. Для FASTA можно принимать более короткие entries, но помечать низкую уверенность.
8. Если доля недопустимых символов после очистки выше порога, не создавать sequence автоматически.

Пример функции:

```python
def classify_sequence(seq: str, fasta_header: str | None = None) -> SequenceClassification:
    ...
```

Возвращаемый объект:

```python
class SequenceClassification(TypedDict):
    sequence_type: Literal["DNA", "RNA", "PROTEIN", "UNKNOWN"]
    confidence: float
    normalized_sequence: str
    length: int
    invalid_chars: list[str]
    warnings: list[str]
    reason: str
```

### FASTA parsing

FASTA parser должен быть общим для pasted text и uploaded files.

Требования:

- поддерживать multi-entry FASTA;
- сохранять полный header для каждого entry;
- извлекать возможный UniProt accession/ID из header как hint;
- создавать отдельную `Sequence` на каждый entry;
- не смешивать entries в одну последовательность;
- если файл не FASTA, но содержит один raw sequence block, обработать его как raw sequence;
- если файл содержит обычный текст без sequence, показать warning и не создавать objects.

### UniProt ID/accession detection

В `sequence_detection.py` должен быть отдельный детектор UniProt accession/ID. Это важно, чтобы ввод `O95185` или `UNC5C_HUMAN` не был ошибочно принят за короткую amino-acid sequence.

Минимальные правила:

- accession patterns: `P01308`, `O95185`, `Q9Y6K9`, `A0A8C8XS57`;
- UniProt mnemonic ID: `UNC5C_HUMAN`, `INS_HUMAN`;
- если строка выглядит как UniProt ID/accession, создать/open `Protein`, а не `Sequence`;
- если в FASTA header найден accession, сохранить его как `uniprot_hint`, но сам entry все равно остается `Sequence`.

### Разделение файлов

Новая логика должна быть разделена минимум так:

- `app/frontend/sequence_detection.py` - парсинг, нормализация, классификация, UniProt ID detection;
- `app/frontend/session_objects.py` - registry объектов, генерация `Seq_A`, merge/update;
- `app/frontend/components/chat.py` - только отображение чата и вызов helpers;
- `app/frontend/components/object_bar.py` - отображение списка объектов;
- `app/frontend/components/object_inspector.py` - отображение выбранного объекта;
- `app/frontend/chat_pipeline.py` - отправка объектов в backend и применение `objects_patch`.

Запрещается добавлять крупную логику парсинга или классификации прямо в Streamlit render-функции.

## Обработка разных типов ввода

### Обычный текст без sequence и UniProt ID

1. Сообщение отправляется как обычный chat turn.
2. Retriever не запускается.
3. LLM получает текущий registry объектов и отвечает с учетом выбранного объекта.

### Вставленная аминокислотная или DNA-последовательность

Важно: retriever запускается **после submit**, а не в момент paste. Пока сообщение не отправлено, sequence имеет статус `draft` и в backend не уходит. Это нужно, чтобы пользователь успел дописать вопрос и не было лишних API-вызовов на каждую вставку.

1. Клиент детектит sequence в момент paste.
2. Создаётся `Sequence` со статусом `draft` (видна в Object Bar как preview, не отправлена).
3. В тексте composer'а sequence либо заменяется на chip `@Seq_A` (paste), либо остаётся как есть с preview-строкой (typing).
4. Пользователь нажимает submit.
5. Статус `Sequence` становится `searching`, в истории чата raw sequence заменяется на `@Seq_A`.
6. Retriever запускается для этой sequence.
7. После ответа `Sequence.matches` заполняется top-5 кандидатами, статус становится `ready`.
8. Для каждого кандидата создаётся или обновляется `Protein`.
9. Правая панель автоматически выбирает новую `Sequence` (если до submit ничего другого выделено не было; если пользователь уже листал другие объекты, его выбор не перетирается).

### Несколько последовательностей в одном сообщении

1. Для каждой последовательности создается отдельная `Sequence`: `Seq_A`, `Seq_B`, `Seq_C`.
2. Каждая sequence получает собственный `matches`.
3. Новые sequence отображаются в Object Bar.
4. Если пользователь задал общий вопрос по этим sequence, LLM получает все созданные объекты и отвечает на естественном языке.

### FASTA-файл

1. Пользователь прикрепляет файл.
2. Система парсит FASTA entries.
3. Каждый entry становится отдельной `Sequence` (`Seq_A`, `Seq_B`, `Seq_C`, ...).
4. FASTA header сохраняется в `sequence.fasta_header`.
5. Если header содержит UniProt accession/ID, сохранить его как hint.
6. После submit retriever запускается только для разумного числа entries, например первых 5-10. Для остальных показать статус `not_searched`.
7. Правая панель автоматически выбирает **первую** созданную `Sequence` (`Seq_A`), Inspector показывает её summary и (когда retriever отработает) её top-5 matches.

### UniProt accession или UniProt ID

Если пользователь вводит `O95185`, `P01308`, `UNC5C_HUMAN` или другой распознаваемый UniProt accession/ID:

1. Не запускать поиск top-5 похожих белков.
2. Создать или открыть `Protein`.
3. Загрузить карточку белка напрямую из UniProt/локального backend-источника.
4. Выбрать этот `Protein` в правой панели.
5. Ответить в чате с учетом открытого белка.

Это отличается от sequence search: когда пользователь явно дает ID, он уже указал конкретный белок, поэтому показывать пять похожих вариантов не нужно.

## Данные во frontend

Вместо глобального `st.session_state.candidates` как единственного источника правой карточки нужно добавить registry объектов.

```python
st.session_state.objects = {
    "seq_A": {
        "kind": "sequence",
        "label": "Seq_A",
        "sequence_type": "PROTEIN",
        "raw_sequence": "...",
        "matches": [
            {
                "protein_id": "protein_O95185",
                "accession": "O95185",
                "score": 0.987,
                "rank": 1
            }
        ],
        "selected_match_index": 0
    },
    "protein_O95185": {
        "kind": "protein",
        "accession": "O95185",
        "display_name": "UNC5C_HUMAN",
        "card": {}
    }
}

st.session_state.object_order = ["seq_A", "protein_O95185"]
st.session_state.selected_object_id = "seq_A"
```

Старое поле `st.session_state.candidates` и связанная с ним глобальная "одна правая карточка" заменяются registry полностью. Совместимость со старыми сохранёнными сессиями НЕ требуется — проект в разработке, старые состояния могут не загружаться. Если что-то ломается при старте на старом session_state, просто инициализировать registry заново.

## Backend contract

Расширить `ChatTurnRequest`:

```python
class ChatTurnRequest(BaseModel):
    message: str
    session_id: str
    user_id: str = "anonymous"
    selected_object_id: str | None = None
    objects: dict[str, Any] = Field(default_factory=dict)
    object_mentions: list[dict[str, Any]] = Field(default_factory=list)
    uploaded_files: list[dict[str, Any]] = Field(default_factory=list)
    ui_context: dict[str, Any] = Field(default_factory=dict)
```

Расширить `ChatTurnResult`:

```python
class ChatTurnResult(BaseModel):
    session_id: str
    assistant_message: str
    objects_patch: dict[str, Any] = Field(default_factory=dict)
    selected_object_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

`objects_patch` — это структурированный набор операций, а не свободный словарь. Формат фиксированный, чтобы frontend и backend не разъехались:

```python
class ObjectsPatch(BaseModel):
    upsert: dict[str, Any] = Field(default_factory=dict)
    remove: list[str] = Field(default_factory=list)
    set_selected: str | None = None
```

Семантика:

- `upsert` - ключ это `object_id` (`seq_A`, `protein_O95185`), значение это полный или частичный объект. Если объекта с таким id ещё нет, он создаётся. Если есть, поля сливаются по правилу "новое перекрывает старое на верхнем уровне ключей" (deep merge не нужен, frontend всегда присылает целое поле `matches` целиком, а не один элемент).
- `remove` - список `object_id` для удаления.
- `set_selected` - новый `selected_object_id`. `null` означает "не менять".

Через эти три операции backend умеет:

- добавлять новые `Sequence` (`upsert`);
- обновлять статус `Sequence` (`upsert` с одним полем `status`);
- класть `matches` для `Sequence` (`upsert` с полем `matches`);
- добавлять или обновлять `Protein` (`upsert`);
- менять selected object (`set_selected`).

Запрещается передавать в `upsert` объекты с произвольной формой - они должны соответствовать схемам `Sequence` или `Protein` из этого ТЗ.

### Кто генерирует label и id

- `object_id` (`seq_A`, `protein_O95185`) генерирует **frontend** в момент создания: для sequence это `seq_<short_hash(normalized_sequence)>`, для protein это `protein_<accession>`. Stable hash гарантирует, что повторная вставка той же последовательности даст тот же id и не создаст дубликат.
- `label` (`Seq_A`, `Seq_B`, ...) тоже генерирует frontend, по порядку появления в сессии (счётчик A, B, C, ... Z, AA, AB, ...). Backend не присваивает свои буквы и не переименовывает: получает label вместе с объектом в `ChatTurnRequest.objects` и возвращает его обратно в `objects_patch.upsert` без изменений.
- Для `Protein` label это accession (`O95185`), а display_name это UniProt ID (`UNC5C_HUMAN`).

## Состояния, дубликаты и долгие операции

### Дубликаты последовательностей

Если пользователь повторно вставляет ту же самую нормализованную последовательность, система не должна создавать бесконечные копии.

Правила:

- вычислять стабильный hash от `normalized_sequence`;
- если такая sequence уже есть в текущей сессии, переиспользовать существующий `Sequence`;
- если та же sequence пришла из другого файла/header, добавить новый source/provenance к существующему объекту;
- если пользователь явно хочет отдельную копию, это можно добавить позже через ручное duplicate действие.

### Дубликаты Protein

Для UniProt accession должен существовать один `Protein` object на сессию.

Если `O95185` найден как кандидат у нескольких sequences, не создавать `protein_O95185_2`. Нужно обновить `linked_sequence_ids`.

### Статусы Sequence

Минимальный набор статусов:

- `draft` - найден в composer, сообщение еще не отправлено;
- `queued` - объект создан, поиск еще не начался;
- `classifying` - идет уточнение типа DNA/protein/RNA;
- `searching` - retriever ищет похожие белки;
- `ready` - matches готовы;
- `not_searched` - объект создан, но автопоиск не запускался;
- `error` - парсинг, классификация или поиск завершились ошибкой.

### Несколько sequence в одном сообщении

Если пользователь отправил несколько sequences, каждый поиск должен обновлять свой объект независимо. Не ждать, пока все поиски закончатся, чтобы показать первый результат.

Для MVP допустимо выполнять поиски последовательно, но UI state должен быть спроектирован так, будто результаты могут приходить независимо:

- `Seq_A` может быть `ready`;
- `Seq_B` может еще быть `searching`;
- `Seq_C` может быть `error`.

### Большие FASTA-файлы

Для файла с большим числом entries нельзя автоматически запускать expensive search по всем объектам без ограничения.

Правила MVP:

- распарсить все entries и показать количество;
- автоматически запускать поиск только для первых 5-10 entries или для entries, явно упомянутых в тексте пользователя;
- остальные sequence objects создать со статусом `not_searched`;
- в Inspector показать действие/подсказку, что поиск по конкретной sequence можно запустить при выборе или запросе пользователя.

### Ошибки и controlled misses

Ошибки должны привязываться к конкретному объекту, а не к глобальной правой панели.

Пример:

- `Seq_A` успешно найден;
- `Seq_B` слишком короткий;
- `Seq_C` содержит недопустимые символы.

В таком случае `Seq_A` остается usable, а `Seq_B` и `Seq_C` показывают свои warnings/errors в Object Bar и Inspector.

## Изменения в текущем проекте

### Frontend

1. Создать `app/frontend/sequence_detection.py`.
   - детектить FASTA blocks и multi-entry FASTA;
   - нормализовать последовательность: убрать пробелы, переносы, нумерацию и форматирование;
   - классифицировать `DNA`, `RNA`, `PROTEIN`, `UNKNOWN` по алфавиту, длине и FASTA header;
   - возвращать confidence, warnings и reason;
   - детектить UniProt accession/ID отдельно от sequence;
   - возвращать cleaned display text + найденные объекты.

2. Создать `app/frontend/session_objects.py`.
   - генерация labels `Seq_A`, `Seq_B`;
   - хранение `objects`, `object_order`, `selected_object_id`;
   - merge/update helpers.

3. Обновить `app/frontend/components/chat.py`.
   - включить file upload в `st.chat_input`;
   - поддержать `ChatInputValue`;
   - отображать sequence/file chips в истории;
   - заменить raw sequence на `@Seq_A` в отображаемом сообщении;
   - сохранять metadata сообщения.

4. Создать `app/frontend/components/object_bar.py`.
   - показать все `Sequence` и `Protein`;
   - клик по chip меняет `selected_object_id`;
   - показать статусы.

5. Создать `app/frontend/components/object_inspector.py`.
   - если выбран `Sequence`, показать sequence summary и top-5 matches;
   - если выбран `Protein`, использовать существующую protein card;
   - если нет объектов, показать empty state.

6. Обновить `app/frontend/app.py`.
   - bootstrap новых session_state полей;
   - заменить прямой рендер `protein_card.render(...)` на `object_bar.render(...)` + `object_inspector.render(...)`.

7. Обновить `app/frontend/assets/style.css`.
   - chips для `@Seq_A` и `@O95185`;
   - Object Bar;
   - sequence cards в истории;
   - состояния `searching`, `ready`, `error`.

### Backend

1. Обновить `app/backend/app_contracts/chat.py`.
   - добавить fields `objects`, `object_mentions`, `selected_object_id`, `objects_patch`.

2. Обновить `app/backend/app_services/bioseq_chat.py`.
   - обрабатывать новые sequence objects;
   - отдельно обрабатывать UniProt accession/ID;
   - не заменять глобальные candidates при новой sequence;
   - обновлять конкретную `Sequence.matches`;
   - возвращать `objects_patch`.

3. Обновить `app/backend/app_services/chat_llm.py`.
   - передавать LLM compact summaries всех объектов;
   - для каждой `Sequence` передавать **только один** protein-матч из её top-5: тот, который сейчас выбран пользователем через `selected_match_index`. По умолчанию `selected_match_index = 0`, то есть top-1 по alignment score (текущее поведение `selected_candidate_idx`). Если пользователь кликнул другой матч в Sequence Inspector — отправляется он. Это уважает выбор пользователя и не раздувает контекст;
   - объяснять, что `@Seq_A` и `@O95185` - ссылки на объекты;
   - просить ассистента в ответе эхом упоминать, на какой объект он отвечает (например, "по `@Seq_A`: ..."), чтобы пользователь видел, как разрешились контекстные ссылки;
   - просить задавать уточнение, если объект неоднозначен.

4. Обновить `app/frontend/chat_pipeline.py`.
   - отправлять objects registry;
   - применять `objects_patch` (`upsert` / `remove` / `set_selected`);
   - обновлять selected object.

## Persistence

Для первого прототипа хранить объекты только в `st.session_state`. Это достаточно, если цель - проверить интерфейс в рамках одной сессии.

Текущую схему Supabase менять не обязательно. В `public.chat_sessions` уже есть JSONB-поля:

- `sequences`;
- `proteins`;
- `working_memory`;
- `working_set_ids`.

Когда понадобится persistence, сохранить:

```json
{
  "objects": { "...": "..." },
  "object_order": ["seq_A", "protein_O95185"],
  "selected_object_id": "seq_A"
}
```

в `working_memory`.

Дополнительно дублировать компактные данные в существующие `sequences`, `proteins`, `working_set_ids`, чтобы sidebar/session summary не сломались.

Отдельные таблицы понадобятся позже, если нужно:

- искать объекты между сессиями;
- хранить большие файлы отдельно;
- шарить объекты между пользователями;
- индексировать accession или sequence ids.

## Тесты и критерии готовности

Добавить тесты для новой логики до или вместе с реализацией.

### Unit tests

1. `sequence_detection.py`
   - raw protein sequence с пробелами и переносами нормализуется корректно;
   - DNA и RNA классифицируются отдельно;
   - короткая строка `ACGT` не превращается уверенно в sequence;
   - `O95185` и `UNC5C_HUMAN` распознаются как UniProt ID/accession, а не как sequence;
   - multi-entry FASTA возвращает несколько entries с headers;
   - недопустимые символы возвращают warning.

2. `session_objects.py`
   - повторная sequence по hash не создает дубликат;
   - повторный protein accession обновляет существующий object;
   - `selected_match_index` сохраняется при переключении между объектами.

### UI smoke tests

1. Вставить одну sequence: появляется `Seq_A`, raw sequence не отображается огромным блоком.
2. Вставить две sequences: появляются `Seq_A` и `Seq_B`, старая не исчезает.
3. Выбрать `Seq_A`: Inspector показывает top-5 matches.
4. Открыть protein candidate: Inspector показывает одну protein card без top-5 кнопок.
5. Ввести UniProt accession: открывается один `Protein`, top-5 search не запускается.
6. Прикрепить FASTA с несколькими entries: создаются несколько `Sequence`.

### Backend tests

1. `ChatTurnRequest` принимает objects registry.
2. Retriever update применяется к конкретной `Sequence.matches`.
3. Follow-up turn получает compact object summaries.
4. Direct UniProt lookup не вызывает sequence retriever.

## MVP

MVP должен работать без миграции базы.

1. Пользователь может вставить одну sequence.
2. Sequence отображается как `@Seq_A`, а не как большой raw text.
3. Справа появляется `Seq_A` в Object Bar.
4. Retriever заполняет top-5 matches у `Seq_A`.
5. Клик по candidate tile внутри `Seq_A` меняет выбранный match, но оставляет пользователя в `Sequence Inspector`.
6. Клик по accession/name кандидата открывает соответствующий `Protein`.
7. Новая sequence не удаляет старую.
8. Пользователь может вставить несколько sequences в одном сообщении.
9. Пользователь может прикрепить FASTA-файл, entries становятся `Seq_A`, `Seq_B`, ...
10. Пользователь может ввести UniProt accession/ID и сразу открыть один `Protein`.
11. Follow-up вопросы получают registry текущих объектов.

## Порядок реализации

### Этап 1. Object registry во frontend

- Добавить `session_objects.py`.
- Добавить `Object Bar`.
- Добавить `Object Inspector`.
- Использовать существующий `protein_card.py` для рендера Protein внутри Inspector (просто вызывать его, когда выбран Protein).

### Этап 2. Sequence detection и отображение в чате

- Добавить `sequence_detection.py`.
- Реализовать нормализацию sequence до отправки в retriever.
- Реализовать классификацию `DNA` / `RNA` / `PROTEIN` / `UNKNOWN` с confidence и warnings.
- Реализовать отдельное распознавание UniProt accession/ID.
- Для ручного typing не сворачивать sequence в chip до submit; только подсвечивать или показывать preview.
- Для paste/upload разрешить автоматическое создание chip.
- Сворачивать длинные sequence в `@Seq_A`.
- Показывать sequence cards в истории сообщений.

### Этап 3. File upload

- Включить `accept_file` в `st.chat_input`.
- Парсить FASTA.
- Создавать sequence objects из entries.

### Этап 4. Backend object patches

- Расширить contracts.
- Возвращать `objects_patch`.
- Обновлять конкретные sequence/protein objects.

### Этап 5. UniProt direct lookup

- Детектить accession/ID.
- Открывать конкретный Protein без top-5 search.
- Рендерить его в Inspector.

### Этап 6. Persistence

- Сохранять registry в `working_memory`.
- Восстанавливать Object Bar при reload.

## Важные ограничения

- Не хранить только один глобальный `candidates` как источник правой панели.
- Не удалять старую sequence при вводе новой.
- Не считать top-1 окончательной истиной: пользователь должен иметь возможность выбрать другой candidate.
- Не показывать длинные raw sequences в истории по умолчанию.
- Не начинать с миграции базы до проверки session-only прототипа.

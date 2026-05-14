# BioSeq Investigator — руководство пользователя

🇬🇧 English version: [USER_GUIDE_en.md](USER_GUIDE_en.md).

Пятиминутный walkthrough для тех, кто хочет пощупать продукт. Биологический бэкграунд не нужен.

## 1. Что это и где открыть?

**BioSeq Investigator** — это исследовательский ассистент для биологических последовательностей. Вы вставляете DNA или protein sequence, задаёте вопрос на естественном языке, и приложение пытается понять, *чем может быть ваша последовательность* — сравнивая её с ~574 000 известных белков из UniProt (канонической публичной базы белков). Для лучшего совпадения показывается карточка белка с функцией, организмом, связанными заболеваниями и другим контекстом, и дальше можно обсуждать этот белок с LLM, который опирается ровно на эти проверенные данные — без галлюцинаций.

**Адрес приложения:** https://huggingface.co/spaces/radda-i/BioSeq_investigator

> 🔒 Приложение защищено паролем (чтобы держать стоимость API-вызовов под контролем). Чтобы получить доступ, напишите команде — мы пришлём пароль в чате.

Ставить ничего не нужно — всё работает в браузере. Первый запрос после «пробуждения» Space-а может занять чуть больше времени (cold start: грузятся ProtT5-модель и FAISS-индекс); последующие запросы — ~30–90 секунд.

## 2. Что приложение умеет

- **Идентифицировать неизвестную последовательность.** Вставьте сырую protein sequence (или DNA — её автоматически переведут в protein), и приложение найдёт самые похожие известные белки, отранжированные по ProtT5 embedding similarity.
- **Учитывать ваш контекст.** Добавьте к запросу вопрос типа *«best match for human?»* или *«involved in glucose metabolism?»* — LLM переранжирует кандидатов так, чтобы наиболее контекстно-релевантный всплыл на верх.
- **Показать карточку белка.** Для топ-совпадения правая колонка наполняется секциями: identification, key facts, function, expression & location, interactions, domain architecture, regulation & isoforms, known variants, 3D structure (AlphaFold), pathways & GO terms, disease association, references.
- **Обсудить результат.** Уточняющие вопросы про найденный белок обрабатывает Gemini, у которого вся карточка как контекст. Можно попросить объяснить попроще, спросить про связанные заболевания, что белок делает в организме — что угодно.
- **Запомнить сессию.** История чата персистится, поэтому к одной и той же беседе можно вернуться из другой вкладки или после закрытия браузера. В сайдбаре виден список прошлых сессий.

## 3. Попробуйте: пошаговый демо-сценарий

Если своей последовательности под рукой нет — пройдите этот сценарий. Он покрывает основной цикл (identify → simplify → drill down).

### Шаг 1 — вставьте последовательность + вопрос

Вставьте весь блок ниже в чат (последовательность + вопрос на отдельной строке):

```
MRKGLRATAARCGLGLGYLLQMLVLPALALLSASGTGSAAQDDDFFHELPETFPSDPPEPLPHFLIEPEEAYIVKNKPVNLYCKASPATQIYFKCNSEWVHQKDHIVDERVDETSGLIVREVSIEISRQQVEELFGPEDYWCQCVAWSSAGTTKSRKAYVRIAYLRKTFEQEPLGKEVSLEQEVLLQCRPPEGIPVAEVEWLKNEDIIDPVEDRNFYITIDHNLIIKQARLSDTANYTCVAKNIVAKRKSTTATVIVYVNGGWSTWTEWSVCNSRCGRGYQKRTRTCTNPAPLNGGAFCEGQSVQKIACTTLCPVDGRWTPWSKWSTCGTECTHWRRRECTAPAPKNGGKDCDGLVLQSKNCTDGL

what is the best match for species Human?
```

Приложение покрутится ~30–90 секунд, и вы увидите что-то такое:

> **The best match is Protein Netrin receptor UNC5C** (Protein unc-5 homolog C).
> «Receptor for netrin required for axon guidance. Mediates axon repulsion of neuronal growth cones in the developing nervous system upon ligand binding…»
>
> *Do you want me to explain it easier?*

Одновременно справа наполняется карточка белка.

### Шаг 2 — попросите объяснить попроще

Ответьте:

```
Yes
```

Ожидаемый ответ (примерно):

> UNC5C — это «кирпич» и знак «нет входа» для растущих нервов.
> - Нет сигнала (нет netrin) → белок говорит: «здесь строить нельзя, проваливай». Клетка убивает сама себя.
> - Есть сигнал (netrin) → белок кричит: «разворачивайся!». Нервный отросток меняет направление, но клетка остаётся жить.
>
> Итог: помогает нервной системе строить правильные «дороги» и убирать незаконные постройки.

### Шаг 3 — спросите про заболевания

Напишите:

```
Are there any connected diseases with this protein?
```

Приложение подтянет disease association из UniProt и ответит, что UNC5C ассоциирован с **болезнью Альцгеймера**, и сошлётся на несколько публикаций.

### Шаг 4 — углубитесь

Напишите:

```
No. Tell me more about this disease.
```

Приложение ответит данными из UniProt по этому заболеванию: характеристики (прогрессирующая деменция, фибриллярные амилоидные отложения), механизм (amyloid-β 40/42, образующиеся при протеолизе APP, neurofibrillary tangles) и заметку, что предрасположенность связана с генетическими вариантами того же гена.

Вот и весь цикл: **вставили последовательность → получили совпадение → задали уточняющие вопросы.** Карточка белка справа — ваш справочник по всему, что приложение знает о найденном белке; чат слева — собственно разговор.

## 4. Другие последовательности для проб

*Возьмите одну из последовательностей ниже, если хочется ещё поэкспериментировать, а своих под рукой нет.*

1. Bat coronavirus: "MLLILVLGVSLAAASRPECFNPRFTLTPLNHTLNYTSIKAKVSNVLLPDPYIAYSGQTLRQNLFMADMSNTILYPVTPPANGANGGFIYNTSIIPVSAGLFVNTWMYRQPASSRAYCQEPFGVAFGDTFENDRIAILIMAPDNLGSWSAVAPRNQTNIYLLVCSNATLCINPGFNRWGPAGSFIAPDALVDHSNSCFVNNTFSVNISTSRISLAFLFKDGDLLIYHSGWLPTSNFEHGFSRGSHPMTYFMSLPVGGNLPRAQFFQSIVRSNAIDKGDGMCTNFDVNLHVAHLINRDLLVSYFNNGSVANAADCADSAAEELYCVTGSFDPPTGVYPLSRYRAQVAGFVRVTQRGSYCTPPYSVLQDPPQPVVWRRYMLYDCVFDFTVVVDSLPTHQLQCYGVSPRRLASMCYGSVTLDVMRINETHLNNLFNRVPDTFSLYNYALPDNFYGCLHAFYLNSTAPYAVANRFPIKPGGRQSNSAFIDTVINAAHYSPFSYVYGLAVITLKPAAGSKLVCPVANDTVVITDRCVQYNLYGYTGTGVLSKNTSLVIPDGKVFTASSTGTIIGVSINSTTYSIMPCVTVPVSVGYHPNFERALLFNGLSCSQRSRAVTEPVSVLWSASATAQDAFDTPSGCVVNVELRNTTIVNTCAMPIGNSLCFINGSIATANADSLPRLQLVNYDPLYDNSTATPMTPVYWVKVPTNFTLSATEEYIQTTAPKITIDCARYLCGDSSRCLNVLLHYGTFCNDINKALSRVSTILDSALLSLVKELSINTRDEVTTFSFDGDYNFTGLMGCLGPNCGATTYRSAFSDLLYDKVRITDPGFMQSYQKCIDSQWGGSIRDLLCTQTYNGIAVLPPIVSPAMQALYTSLLVGAVASSGYTFGITSAGVIPFATQLQFRLNGIGVTTQVLVENQKLIASSFNNALVNIQKGFTETSIALSKMQDVINQHAAQLHTLVVQLGNSFGAISSSINEIFSRLEGLAANAEVDRLINGRMMVLNTYVTQLLIQASEAKAQNALAAQKISECVKAQSLRNDFCGNGTHVLSIPQLAPNGVLFIHYAYTPTEYAFVQTSAGLCHNGTGYAPRQGMFVLPNNTNMWHFTTMQFYNPVNISASNTQVLTSCSVNYTSVNYTVLEPSVPGDYDFQKEFDKFYKNLSTIFNNTFNPNDFNFSTVDVTAQIKSLHDVVNQLNQSFIDLKKLNVYEKTIKWPWYVWLAMIAGIVGLVLAVIMLMCMTNCCSCFKGMCDCRRCCGSYDSYDDVYPAVRVNKKRTV"

2. Insulin:
"MALWMRLLPLLALLALWGPDPAAAFVNQHLCGSHLVEALYLVCGERGFFYTPKTRREAEDLQVGQVELGGGPGAGSLQPLALEGSLQKRGIVEQCCTSICSLYQLENYCN"

**Примеры вопросов** (можно писать как по-английски, так и по-русски):
- <например, "what is this and what does it do in the body?">
- <например, "best match in a non-mammalian organism?">
- <например, "tell me, what diseases are connected with this protein?">

# BioSeq Investigator — User Guide

🇷🇺 Russian version: [USER_GUIDE_ru.md](USER_GUIDE_ru.md).

A 5-minute walkthrough for people who want to try the product. No biology background required.

## 1. What is this and where do I open it?

**BioSeq Investigator** is a research assistant for biological sequences. You paste a DNA or protein sequence, ask a question in plain English, and the app tries to figure out *what your sequence likely is* — by comparing it against ~574,000 known proteins from UniProt (the canonical public protein database). For the best match it shows you a protein card with function, organism, related diseases and other context, and lets you keep chatting about that protein with an LLM using the exact and verified context - no hallucinations.

**Live app:** https://huggingface.co/spaces/radda-i/BioSeq_investigator

> 🔒 The app is password-protected (to keep API costs predictable). To get access, message the team and we'll share the password in chat.

You don't need to install anything — everything runs in the browser. A note on timing: the **first stage is the retriever** — when you submit a sequence for the first time, the app searches it against the database, and on the Hugging Face Space this takes **about 5 minutes** (it loads the ProtT5 model and the FAISS index, then runs the search). Please be patient and wait for it to finish. After that, **chatting with the LLM about the found protein happens in seconds.**

## 2. What the app can do

- **Identify an unknown sequence.** Paste a raw protein sequence (or DNA — it'll be translated automatically) and the app finds the most similar known proteins, ranked by ProtT5 embedding similarity.
- **Suggests a random sequence.** Not sure what to paste? Let the app pick a random sequence for you — don't be shy, there are some interesting proteins in there.
- **Read your context.** Add a question like *"best match for human?"* or *"involved in glucose metabolism?"* — an LLM reranks candidates so the most contextually-relevant one floats to the top.
- **Show a protein card.** For the top match the right column fills in with: identification, key facts, function, expression & location, interactions, domain architecture, regulation & isoforms, known variants, 3D structure (AlphaFold), pathways & GO terms, disease association, references.
- **Work with several protein cards at once.** You can keep more than one protein in play — both candidates from the top-5 of a search you already ran and results of a brand-new search. To bring an already-found card into play, click it: it shows up in **Session Objects**, activate it there, and then you can **tag it in your message** to the LLM (or simply **drag a Session Object straight into the chat**).
- **Discuss the result.** Follow-up questions about the matched protein are answered by Gemini, which has the full card as context. Ask for a simpler explanation, related diseases, what the protein does in the body — anything.
- **Suggests questions for you.** A separate LLM proposes relevant follow-up questions, so you always have a sensible next step to click.
- **Save to a `.md` file.** Export the result and conversation as a Markdown file to keep or share.
- **Switch between sessions.** Within a single login you can keep several sessions and switch between them in the sidebar. Note: once you close the browser, nothing is saved — sessions don't persist across browser restarts.

## 3. Try it: a guided demo

If you don't have a sequence on hand, walk through this scenario — it covers the main loop (identify → simplify → drill down).

### Step 1 — paste a sequence + a question

Paste this whole block into the chat box (the sequence + question on a separate line):

```
MRKGLRATAARCGLGLGYLLQMLVLPALALLSASGTGSAAQDDDFFHELPETFPSDPPEPLPHFLIEPEEAYIVKNKPVNLYCKASPATQIYFKCNSEWVHQKDHIVDERVDETSGLIVREVSIEISRQQVEELFGPEDYWCQCVAWSSAGTTKSRKAYVRIAYLRKTFEQEPLGKEVSLEQEVLLQCRPPEGIPVAEVEWLKNEDIIDPVEDRNFYITIDHNLIIKQARLSDTANYTCVAKNIVAKRKSTTATVIVYVNGGWSTWTEWSVCNSRCGRGYQKRTRTCTNPAPLNGGAFCEGQSVQKIACTTLCPVDGRWTPWSKWSTCGTECTHWRRRECTAPAPKNGGKDCDGLVLQSKNCTDGL

what is the best match for species Human?
```

This is the first search, so the app may spin for ~5 minutes (see the timing note above). You should see something like:

> **The best match is Protein Netrin receptor UNC5C** (Protein unc-5 homolog C).
> "Receptor for netrin required for axon guidance. Mediates axon repulsion of neuronal growth cones in the developing nervous system upon ligand binding…"
>
> *Do you want me to explain it easier?*

At the same time the right column fills in with the protein card.

### Step 2 — ask for a simpler explanation

Reply:

```
Yes
```

Expected answer (roughly):

> UNC5C — a "brick" and a "no entry" sign for growing nerves.
> - No signal (no netrin) → the protein says "you can't build here, get lost." The cell kills itself.
> - Signal (netrin) → "turn around!" The nerve process changes direction without destroying the cell.
>
> Bottom line: helps the nervous system build the right roads and remove illegal constructions.

### Step 3 — ask about related diseases

Type:

```
Are there any connected diseases with this protein?
```

The app pulls disease association data from UniProt and replies that UNC5C has been linked to **Alzheimer's disease**, with several supporting publications.

### Step 4 — drill down

Type:

```
No. Tell me more about this disease.
```

The app responds with the UniProt disease entry: characteristics (progressive dementia, fibrillar amyloid deposits), mechanism (amyloid-β 40/42 produced by proteolysis of the APP protein, neurofibrillary tangles), and a note that susceptibility is associated with genetic variants of the gene above.

That's the loop: **paste a sequence → get a match → ask follow-up questions.** The protein card on the right is your reference for everything the app knows about the matched protein; the chat on the left is the conversation.

## 4. Other sequences to try

*Pick one of the sequences below if you want to keep playing without bringing your own.*

1. Bat coronavirus: "MLLILVLGVSLAAASRPECFNPRFTLTPLNHTLNYTSIKAKVSNVLLPDPYIAYSGQTLRQNLFMADMSNTILYPVTPPANGANGGFIYNTSIIPVSAGLFVNTWMYRQPASSRAYCQEPFGVAFGDTFENDRIAILIMAPDNLGSWSAVAPRNQTNIYLLVCSNATLCINPGFNRWGPAGSFIAPDALVDHSNSCFVNNTFSVNISTSRISLAFLFKDGDLLIYHSGWLPTSNFEHGFSRGSHPMTYFMSLPVGGNLPRAQFFQSIVRSNAIDKGDGMCTNFDVNLHVAHLINRDLLVSYFNNGSVANAADCADSAAEELYCVTGSFDPPTGVYPLSRYRAQVAGFVRVTQRGSYCTPPYSVLQDPPQPVVWRRYMLYDCVFDFTVVVDSLPTHQLQCYGVSPRRLASMCYGSVTLDVMRINETHLNNLFNRVPDTFSLYNYALPDNFYGCLHAFYLNSTAPYAVANRFPIKPGGRQSNSAFIDTVINAAHYSPFSYVYGLAVITLKPAAGSKLVCPVANDTVVITDRCVQYNLYGYTGTGVLSKNTSLVIPDGKVFTASSTGTIIGVSINSTTYSIMPCVTVPVSVGYHPNFERALLFNGLSCSQRSRAVTEPVSVLWSASATAQDAFDTPSGCVVNVELRNTTIVNTCAMPIGNSLCFINGSIATANADSLPRLQLVNYDPLYDNSTATPMTPVYWVKVPTNFTLSATEEYIQTTAPKITIDCARYLCGDSSRCLNVLLHYGTFCNDINKALSRVSTILDSALLSLVKELSINTRDEVTTFSFDGDYNFTGLMGCLGPNCGATTYRSAFSDLLYDKVRITDPGFMQSYQKCIDSQWGGSIRDLLCTQTYNGIAVLPPIVSPAMQALYTSLLVGAVASSGYTFGITSAGVIPFATQLQFRLNGIGVTTQVLVENQKLIASSFNNALVNIQKGFTETSIALSKMQDVINQHAAQLHTLVVQLGNSFGAISSSINEIFSRLEGLAANAEVDRLINGRMMVLNTYVTQLLIQASEAKAQNALAAQKISECVKAQSLRNDFCGNGTHVLSIPQLAPNGVLFIHYAYTPTEYAFVQTSAGLCHNGTGYAPRQGMFVLPNNTNMWHFTTMQFYNPVNISASNTQVLTSCSVNYTSVNYTVLEPSVPGDYDFQKEFDKFYKNLSTIFNNTFNPNDFNFSTVDVTAQIKSLHDVVNQLNQSFIDLKKLNVYEKTIKWPWYVWLAMIAGIVGLVLAVIMLMCMTNCCSCFKGMCDCRRCCGSYDSYDDVYPAVRVNKKRTV"

2. Insulin:
"MALWMRLLPLLALLALWGPDPAAAFVNQHLCGSHLVEALYLVCGERGFFYTPKTRREAEDLQVGQVELGGGPGAGSLQPLALEGSLQKRGIVEQCCTSICSLYQLENYCN"

**Suggested questions:**
- <e.g. "what is this and what does it do in the body?">
- <e.g. "best match in a non-mammalian organism?">
- <e.g. "tell me, what diseases are connected with this protein?">

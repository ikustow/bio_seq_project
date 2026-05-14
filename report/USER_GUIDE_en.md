# BioSeq Investigator — User Guide

🇷🇺 Russian version: [USER_GUIDE.md](USER_GUIDE.md).

A 5-minute walkthrough for people who want to try the product. No biology background required.

## 1. What is this and where do I open it?

**BioSeq Investigator** is a research assistant for biological sequences. You paste a DNA or protein sequence, ask a question in plain English, and the app tries to figure out *what your sequence likely is* — by comparing it against ~574,000 known proteins from UniProt (the canonical public protein database). For the best match it shows you a protein card with function, organism, related diseases and other context, and lets you keep chatting about that protein with an LLM using the exact and verified context - no hallucinations.

**Live app:** https://huggingface.co/spaces/radda-i/BioSeq_investigator

> 🔒 The app is password-protected (to keep API costs predictable). To get access, message the team and we'll share the password in chat.

You don't need to install anything — everything runs in the browser. The first query after the Space wakes up may take a bit longer (cold start: ProtT5 model and FAISS index need to load); subsequent queries take ~30–90 seconds.

## 2. What the app can do

- **Identify an unknown sequence.** Paste a raw protein sequence (or DNA — it'll be translated automatically) and the app finds the most similar known proteins, ranked by ProtT5 embedding similarity.
- **Read your context.** Add a question like *"best match for human?"* or *"involved in glucose metabolism?"* — an LLM reranks candidates so the most contextually-relevant one floats to the top.
- **Show a protein card.** For the top match the right column fills in with: identification, key facts, function, expression & location, interactions, domain architecture, regulation & isoforms, known variants, 3D structure (AlphaFold), pathways & GO terms, disease association, references.
- **Discuss the result.** Follow-up questions about the matched protein are answered by Gemini, which has the full card as context. Ask for a simpler explanation, related diseases, what the protein does in the body — anything.
- **Remember the session.** Chat history is persisted, so you can come back to the same conversation from another tab or after closing the browser. The sidebar shows your previous sessions.

## 3. Try it: a guided demo

If you don't have a sequence on hand, walk through this scenario — it covers the main loop (identify → simplify → drill down).

### Step 1 — paste a sequence + a question

Paste this whole block into the chat box (the sequence + question on a separate line):

```
MRKGLRATAARCGLGLGYLLQMLVLPALALLSASGTGSAAQDDDFFHELPETFPSDPPEPLPHFLIEPEEAYIVKNKPVNLYCKASPATQIYFKCNSEWVHQKDHIVDERVDETSGLIVREVSIEISRQQVEELFGPEDYWCQCVAWSSAGTTKSRKAYVRIAYLRKTFEQEPLGKEVSLEQEVLLQCRPPEGIPVAEVEWLKNEDIIDPVEDRNFYITIDHNLIIKQARLSDTANYTCVAKNIVAKRKSTTATVIVYVNGGWSTWTEWSVCNSRCGRGYQKRTRTCTNPAPLNGGAFCEGQSVQKIACTTLCPVDGRWTPWSKWSTCGTECTHWRRRECTAPAPKNGGKDCDGLVLQSKNCTDGL

what is the best match for species Human?
```

The app spins for ~30–90 seconds. You should see something like:

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

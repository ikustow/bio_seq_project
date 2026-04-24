# BioSeq Investigator — Mock UI Technical Specification

*Version 1 · 2026-04-24*
*Scope: Streamlit mock interface only. No LLM calls, no NCBI/UniProt calls.*

---

## 1. Purpose

A scripted, static Streamlit page that visualises the **user-facing vision** of BioSeq Investigator before any agent, BLAST call, or real LLM integration exists.

Used for:

- Checkpoint demo (2026-05-14) talking-point aid.
- Mentor & team reviews of the interaction format.
- Interface contract for the Stage 2 agent — the view-model in [mock/protein_loader.py](mock/protein_loader.py) is what the real orchestrator must eventually produce.

Explicitly **not** a product increment toward the backend. No code from this folder is expected to ship in Stage 2 unchanged except `components/protein_card.py` and the view-model shape.

## 2. Scope

### In scope

- Two-column page: scripted chat on the left, progressive protein card on the right.
- One hard-coded protein: UNC5C / UniProt `O95185` (loaded from [test_data_from_database/O95185.json](../test_data_from_database/O95185.json)).
- A five-turn scripted conversation mirroring [streamlit_ui/test_chat.txt](test_chat.txt).
- Rich card sections: header, key-facts table, function, domain-architecture diagram, disease association, 3D structure viewer, keywords/GO terms, references.
- "Try example" button, reset button, graceful fallback for unknown user input.

### Out of scope

- Any real API call (NCBI BLAST, UniProt REST, Entrez, PubMed).
- Any LLM call or prompt template.
- FASTA validation logic, DNA↔protein detection, ORF translation.
- Multi-protein handling. Only UNC5C is supported.
- Authentication, persistence beyond `st.session_state`, multi-user sessions.
- Deployment configuration (covered later in Stage 0/3).

## 3. User interaction contract

| Step | User action | System response (left column) | Card reveal (right column) |
|---|---|---|---|
| 0 | Page load | Welcome message + "Try example" CTA | Empty placeholder: "The protein card will appear after your first question." |
| 1 | Pastes FASTA sequence + asks "what is the best match for species Human?" | "Great! Start searching…" streamed, then the UNC5C function paragraph | Header + key-facts + function sections revealed |
| 2 | Types "yes" (or similar) | Plain-language UNC5C explainer ("brick" and "No Entry" sign metaphor) | Domain architecture diagram revealed |
| 3 | Types "are there any connected diseases with this protein?" | "Alzheimer disease. There are several publications. Do you want me to share resources?" | Disease association section revealed |
| 4 | Types "tell me more" (or "no, more") | Full Alzheimer disease description from the JSON | References section revealed |
| 5+ | Any further message | Polite demo-mode notice | No changes |

Input routing is **keyword-based fuzzy matching** (lowercased substring check) so a live demo viewer typing their own phrasing still follows the rails. The scripted turns are the source of truth; the user message is routed to the expected turn, and `session_state.step` advances accordingly.

Unknown input → canned reply: *"I'm in demo mode — try asking about diseases, request a simpler explanation, or click Reset."*

## 4. Architecture

```
streamlit_ui/
├── app.py                        # Streamlit entry point, layout, session state
├── TECH_SPEC.md                  # this file
├── test_chat.txt                 # reference dialogue (already present)
├── requirements.txt              # pinned deps for the mock
├── README.md                     # run instructions
├── mock/
│   ├── __init__.py
│   ├── conversation.py           # scripted TURNS list + routing
│   └── protein_loader.py         # JSON → view-model for the card
├── components/
│   ├── __init__.py
│   ├── chat.py                   # chat column renderer
│   ├── protein_card.py           # right-side card shell + sections
│   └── domain_diagram.py         # Plotly domain architecture figure
└── assets/
    ├── style.css                 # injected via st.markdown
    └── AF-O95185-F1-model_v4.pdb # offline fallback for 3D viewer (fetched on first run)
```

### Component responsibilities

- **`app.py`** — configures the page, renders the two-column layout, owns the `st.session_state` bootstrap. Delegates all rendering to `components/*`.
- **`components/chat.py`** — renders chat history with `st.chat_message`, handles the `st.chat_input` submit, calls `mock.conversation.advance()`, triggers the streamed reply via `st.write_stream`.
- **`components/protein_card.py`** — renders the right-hand card. Reads `session_state.card_sections_revealed` and only shows unlocked sections; locked sections appear as greyed-out placeholders with "Revealed as the conversation progresses."
- **`components/domain_diagram.py`** — builds a Plotly horizontal-bar figure from the features list (Signal, Domain, Transmembrane types) over residues 1–931.
- **`mock/conversation.py`** — exposes `TURNS: list[Turn]` and `advance(user_text, state) -> (assistant_text, sections_to_reveal)`. Pure function, easy to unit-test.
- **`mock/protein_loader.py`** — `load(path) -> ProteinView`. Reads the JSON, flattens into a stable dict used by the card.

## 5. Data model — `ProteinView`

The contract the real agent will need to satisfy later. A `TypedDict` in `mock/protein_loader.py`:

```python
class ProteinView(TypedDict):
    accession: str                       # "O95185"
    name: str                            # "Netrin receptor UNC5C"
    alt_names: list[str]
    gene: str                            # "UNC5C"
    organism_scientific: str             # "Homo sapiens"
    organism_common: str                 # "Human"
    taxon_id: int                        # 9606
    annotation_score: float              # 5.0
    reviewed: bool                       # True for Swiss-Prot
    existence: str                       # "Evidence at protein level"
    length: int                          # 931
    mol_weight: int                      # 103146
    subcellular_locations: list[str]
    function_text: str                   # FUNCTION comment
    disease: Optional[DiseaseInfo]       # Alzheimer block
    domains: list[DomainFeature]         # {type,name,start,end,color}
    keywords: list[str]
    go_terms: list[str]                  # top N
    pubmed_ids: list[str]
    xrefs: dict[str, str]                # {"RefSeq":"NP_003719.3", ...}
    alphafold_accession: str             # used to build 3D viewer URL
    sequence: str                        # for optional display

class DiseaseInfo(TypedDict):
    name: str          # "Alzheimer disease"
    acronym: str       # "AD"
    mim_id: str        # "104300"
    description: str
    variants: list[str]  # e.g. ["T835M (rs137875858)"]
```

Citation rendering: any `PubMed:NNNN` substring inside `function_text` or `disease.description` is rewritten to a markdown link to `https://pubmed.ncbi.nlm.nih.gov/NNNN` via a small regex helper in `protein_card.py`.

## 6. Session state schema

```python
st.session_state = {
    "messages": [              # list of {"role": "user"|"assistant", "content": str}
        {"role": "assistant", "content": "<welcome>"},
    ],
    "step": 0,                 # cursor into conversation.TURNS
    "protein": ProteinView | None,
    "card_sections_revealed": set[str],  # subset of:
                                          # {"header","keyfacts","function","domains",
                                          #  "disease","structure","keywords","references"}
}
```

The card sections `"structure"` and `"keywords"` are revealed at step 1 together with `"header"` — they have no conversation dependency but belong to the initial identification. `"disease"` and `"references"` are staged so the demo shows progressive disclosure.

## 7. Third-party libraries & rationale

| Library | Version | Purpose | Alternative considered |
|---|---|---|---|
| `streamlit` | ≥1.32 | Page framework, `st.chat_*`, `st.columns`, `st.expander`, `st.write_stream` | Gradio — rejected: chat + rich sidebar card is awkward in Blocks |
| `plotly` | ≥5.20 | Domain-architecture horizontal bar | matplotlib — rejected: Plotly hover tooltips come for free |
| `py3Dmol` | ≥2.1 | 3D protein viewer (WebGL); `view._make_html()` embedded via `st.components.v1.html` | NGL viewer — heavier. `stmol` considered but drops an unmaintained `ipywidgets==7.6.3` dep chain (needs `ipython_genutils`) — skipped |
| `requests` | ≥2.31 | One-shot AlphaFold PDB download to local cache | urllib — requests is already part of most data-science stacks |

No LangChain, no LangGraph, no anthropic SDK — this is the mock.

## 8. 3D structure viewer

- On first render of the structure section, try to fetch `https://alphafold.ebi.ac.uk/files/AF-O95185-F1-model_v4.pdb` to `assets/AF-O95185-F1-model_v4.pdb` (cache if missing).
- Build a `py3Dmol.view(...)` with `addModel(pdb,"pdb").setStyle({"cartoon":{"color":"spectrum"}}).zoomTo()`, render via `st.components.v1.html(view._make_html(), height=400)`. 3Dmol.js loads from its CDN inside the embedded iframe.
- On network or import failure: show a muted info box "3D structure unavailable in this environment" with a link out to `https://alphafold.ebi.ac.uk/entry/O95185`. The demo must never hard-fail because of the viewer.

## 9. Styling

Minimal `assets/style.css` injected once from `app.py`:

- monospace + word-break for sequence display,
- small padding and rounded corners on `st.container(border=True)`,
- inline citation pills (`.cite-pill`) — grey background, monospace, 1px border,
- greyed placeholder style (`.card-locked`) for unrevealed sections.

No custom theme; defaults are kept so Streamlit Community Cloud deployment looks identical locally and remotely.

## 10. Non-functional requirements

- **Startup time:** cold `streamlit run` must show the first paint in under 3 s on a typical dev laptop (no blocking network calls at import time; PDB fetch happens lazily when the 3D section first unlocks).
- **Determinism:** all scripted responses and card contents must be byte-identical across runs given the same inputs — no timestamps, no random IDs.
- **Resilience:** if the AlphaFold fetch fails or `stmol`/`py3Dmol` is missing, every other section still renders.
- **Cross-platform:** Windows (primary dev) + macOS + Linux. No shell-specific code in `app.py`.

## 11. Setup & run

```powershell
# Windows, from repo root
py -3.11 -m venv streamlit_ui/.venv
streamlit_ui/.venv/Scripts/python.exe -m pip install -r streamlit_ui/requirements.txt
streamlit_ui/.venv/Scripts/streamlit.exe run streamlit_ui/app.py
```

On macOS/Linux the commands differ only in the venv path (`streamlit_ui/.venv/bin/...`).

## 12. Acceptance criteria (how we know the mock is done)

1. `streamlit run streamlit_ui/app.py` opens a page with the welcome state and no errors in the console.
2. Clicking **Try example** populates the user bubble with the UNC5C sequence + question and triggers the streamed "Great! Start searching…" reply.
3. Header, key-facts, function, domains, structure, keywords sections become visible after step 1.
4. Typing "yes" advances to the plain-language explainer.
5. Typing "any diseases?" reveals the disease card section with the Alzheimer block, correctly linking `PubMed:25419706` and `PubMed:27068745`.
6. Typing "tell me more" reveals the references section with the two AD papers and the RefSeq/Ensembl/KEGG cross-references.
7. **Reset** button returns the UI to the welcome state.
8. Unknown input produces the demo-mode canned reply and does not advance state or break the card.
9. If the 3D viewer cannot load, no other section is affected.

## 13. Known limitations & follow-ups (for Stage 1+)

- **Only UNC5C.** The view-model generalises but the scripted dialogue does not. Extending to another protein is deliberately deferred to real-agent work.
- **No input validation.** A real FASTA parser lives in Stage 1 (`input_validator`), not here.
- **Keyword routing is brittle.** Fine for a scripted demo; must be replaced by the agent's planner in Stage 2.
- **No tests yet.** Stage 3 will add `pytest` coverage for `protein_loader.load` against the six sample JSONs.

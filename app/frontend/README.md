# BioSeq Investigator — Streamlit mock UI

Scripted, no-backend Streamlit page used to visualise the BioSeq Investigator
product vision. See [TECH_SPEC.md](TECH_SPEC.md) for the full specification.

## Requirements

- Python 3.11
- A working internet connection on first run (only to fetch the AlphaFold PDB
  file once; it is cached into `assets/`).

## Setup (Windows, PowerShell or Git Bash)

```powershell
# from repo root
py -3.11 -m venv app/frontend/.venv
app/frontend/.venv/Scripts/python.exe -m pip install -r app/frontend/requirements.txt
```

## Setup (macOS / Linux)

```bash
python3.11 -m venv app/frontend/.venv
app/frontend/.venv/bin/python -m pip install -r app/frontend/requirements.txt
```

## Run

```powershell
# Windows
app/frontend/.venv/Scripts/streamlit.exe run app/frontend/app.py
```

```bash
# macOS / Linux
app/frontend/.venv/bin/streamlit run app/frontend/app.py
```

The page opens at `http://localhost:8501`.

## Backend mode

By default the frontend runs the scripted mock. To send each chat prompt to
`app/backend/agents_core/retriever_agent/pipeline_interface.py`, set
`USE_VECTOR_DB_MODE = True` in `app/frontend/config.py`.

## Demo script

1. Click **Try example** — the UNC5C sequence and question are sent as a
   user message.
2. Watch the assistant stream the top-hit answer and the right-hand card
   populate with identification, key facts, function, domain architecture,
   3D structure, and keywords.
3. Type `yes` → plain-language explainer + domain diagram reveal.
4. Type `are there any connected diseases?` → disease section reveals.
5. Type `tell me more` → full Alzheimer disease description + references.
6. Click **Reset** to start over.

## Notes

- This UI is intentionally a **mock**. No LLM call, no BLAST, no UniProt
  REST. All responses and card contents come from
  [test_data_from_database/O95185.json](test_data_from_database/O95185.json)
  and the scripted turns in
  [app/frontend/mock/conversation.py](mock/conversation.py).
- The `ProteinView` shape in
  [app/frontend/mock/protein_loader.py](mock/protein_loader.py) is the
  contract the real Stage 2 agent orchestrator is expected to satisfy.

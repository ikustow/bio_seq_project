# BioSeq Retriever

BioSeq Retriever is a bioinformatics pipeline for context-aware biological
sequence search. It combines an LLM-driven input parser (LangGraph), a
local FAISS index over protein/DNA embeddings (ProtT5 / HyenaDNA), the EBI
BLAST REST API as an alternate ranker, a metadata fetcher against UniProt,
and an E5-large-v2 semantic reranker — all exposed through one FastAPI
gateway.

> **Status**: the module is *running* in production on Hugging Face
> Spaces, but it is currently misranking top-1 and top-5 results. See
> [Diagnosed Bugs Affecting Top-K Quality](#diagnosed-bugs-affecting-top-k-quality)
> below for the root causes and proposed fixes.

---

## 1. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                            CLIENT LAYER                              │
│  pipeline_interface.py  ──▶  run_bioseq_pipeline(prompt, algorithm) │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                  ┌────────────▼────────────┐
                  │     LangGraph orchestr. │
                  │      (src/pipeline.py)  │
                  └────────────┬────────────┘
                               │
        ┌──────────────────────┼─────────────────────────────────┐
        │                      │                                 │
   ┌────▼─────┐         ┌──────▼──────┐                  ┌───────▼──────┐
   │ extract  │──"FILE"─▶ resolve_file│                  │   use_raw    │
   │  (LLM)   │         │  (pyfaidx)  │                  │ (clean seq)  │
   └────┬─────┘         └──────┬──────┘                  └───────┬──────┘
        │ classify(DNA/PROTEIN)│                                 │
        └────────────┬─────────┴─────────────┬───────────────────┘
                     │                       │
              ┌──────▼──────┐         ┌──────▼──────┐
              │  rank_dna   │         │    rank     │
              │ (HyenaDNA)  │         │ (ProtT5 OR  │
              │             │         │  EBI BLAST) │
              └──────┬──────┘         └──────┬──────┘
                     │                       │
                     └───────────┬───────────┘
                                 │ ranked_results (top-50 or top-10)
                          ┌──────▼──────┐
                          │   rerank    │  ──▶ falls back to ranked[:5]
                          │ (E5-large)  │       if gateway unreachable
                          └──────┬──────┘
                                 │ final_results (top-5)
                                 ▼
                                END

  External calls used by the nodes above
  ──────────────────────────────────────
  POST  http://gateway/search/protein   (ProtT5 emb  + FAISS HNSW cosine)
  POST  http://gateway/search/dna       (HyenaDNA emb + FAISS HNSW cosine)
  POST  http://gateway/rerank           (E5-large-v2 + weighted bio-metadata)
  GET   https://rest.uniprot.org/uniprotkb/search  (metadata fetch)
  POST  https://www.ebi.ac.uk/.../ncbiblast/run    (alt rank, algo=blast)
```

### Module layout

```
bioseq_retriever/
├── pipeline_interface.py     CLI entry point (asyncio.run(run_bioseq_pipeline))
├── src/
│   ├── pipeline.py           LangGraph DAG + node functions
│   ├── utils.py              LLM selection, FASTA loader, sequence cleaning,
│   │                         DNA→protein translation, secure-path check
│   ├── search.py             HTTP clients: search_top_k / search_dna_top_k /
│   │                         blast_search (EBI REST poller)
│   ├── reranking.py          LocalReranker → POSTs to gateway /rerank
│   ├── data_fetcher.py       get_uniprot_records → UniProt /search
│   ├── api_client.py         httpx wrapper with retry + exp. backoff
│   ├── config.py             env-driven paths, retry knobs, service URLs
│   └── bootstrap.py          first-boot data download (UniProt FTP or HF Hub)
├── services/
│   ├── search_service.py     FastAPI gateway: /search/protein, /search/dna,
│   │                         /rerank — owns ProtT5, HyenaDNA, E5 + FAISS
│   └── config.py             gateway paths, FAISS HNSW knobs, model names
└── data/                     downloaded per-protein.h5 / per-gene.h5, FAISS
                              indices, accession caches
```

### State machine (LangGraph)

`GraphState` is a TypedDict carrying: `prompt`, `sequence_or_path`,
`input_type`, `context`, `sequence`, `sequence_type`, `is_confident`,
`ranked_results`, `final_results`, `error`, `search_algorithm`.

Nodes are wired with conditional edges that short-circuit to `END` as
soon as `state["error"]` is set, so a failure at any stage surfaces in
`result["error"]` instead of being swallowed.

---

## 2. End-to-end logic, step by step

1. **`extract_and_classify_node`** — single LLM call with
   `structured_output(InputExtraction)`. Returns `sequence_or_path`,
   `input_type` (`SEQUENCE`|`FILEPATH`), `context`,
   `sequence_type` (`DNA`|`PROTEIN`), `is_confident`, `reasoning`.
   LLM provider auto-picked from env: Mistral preferred when
   `MISTRAL_API_KEY` is set, otherwise OpenAI.

2. **`resolve_file` / `use_raw`** — branch on `input_type`.
   - `resolve_file`: `is_secure_path` (must resolve under
     `BIOSEQ_ALLOWED_DATA_DIR`, defaults to `data/`) then `pyfaidx.Fasta`
     loads the first record; header is appended to `context`.
   - `use_raw`: `clean_sequence` strips a `>` header line, all whitespace
     and any non-letter characters, then uppercases.

3. **`rank_dna` / `rank`** — branch on `sequence_type`.
   - `rank_dna` always calls the gateway: `search_dna_top_k(seq, k=50)`.
   - `rank` picks between two backends via `search_algorithm`
     (set by the caller; default `"embeddings"`):
     - `"embeddings"` (default): `search_top_k(seq, k=50)` → ProtT5
       embedding + FAISS HNSW cosine on the gateway.
     - `"blast"`: `blast_search(seq, k=10)` against
       `uniprotkb_swissprot` (e-value 1e-5, wordsize 6), polling EBI's
       REST job queue with 1 s → 4 s backoff up to 180 s. Score is
       `hsp_identity / 100`.
   - Both produce `matches: List[(accession, score)]`, then
     `get_uniprot_records([acc, ...])` resolves them into rich UniProt
     JSON records → `ranked_results`.

4. **`rerank_node`** —
   - Liveness-probes the gateway with a 0.5 s TCP connect. If it's down,
     fall back to `ranked_results[:5]`.
   - Otherwise POST `/rerank` with `{records, context_query, top_n=5}`.
     The gateway: parses the natural-language context for taxonomic /
     subcellular / EC-number hints, embeds the context and a generated
     passage per record (E5-large-v2), then mixes 5 signals:

     | component             | weight | source                                          |
     |-----------------------|-------:|-------------------------------------------------|
     | `sequence`            | 0.20   | `1.0 - i / len(records)` *(position-based!)*    |
     | `semantic`            | 0.35   | cosine(E5(context), E5(passage))                |
     | `taxonomy`            | 0.15   | hint hits in `organism` + `lineage` (1.0 if no hints) |
     | `localization`        | 0.20   | hint hits in `SUBCELLULAR_LOCATION` (1.0 if no hints) |
     | `domain_architecture` | 0.10   | EC-number exact match flag                      |

   - Result is sorted by `total` descending and truncated to `top_n=5`.
     Each record gets `_rerank_score` and `_rerank_explanation` fields.

5. **`pipeline_interface.main`** — pretty-prints `final_results`
   (accession + recommended name) and the full JSON payload.

---

## 3. Gateway service (`services/search_service.py`)

A single FastAPI app exposes three endpoints and owns all heavy models.

| endpoint           | encoder                         | index                              |
|--------------------|---------------------------------|------------------------------------|
| `POST /search/protein` | `Rostlab/prot_t5_xl_uniref50` | `per-protein.index` (HNSW, IP, M=128, efC=512, efS=2048) |
| `POST /search/dna`     | `LongSafari/hyenadna-medium-160k-seqlen-hf` | `per-gene.index` (same params; optional) |
| `POST /rerank`         | `intfloat/e5-large-v2`         | n/a — operates on UniProt records  |

### Index construction (`load_or_create_index`)

- Reads the `.h5` lazily in batches of `H5_BATCH_SIZE` accessions
  (`per-protein.h5` is ~1.3 GB → never materialised in RAM at once).
- Each batch is L2-normalised in place, then added to a single
  `IndexHNSWFlat(dim, M=128, METRIC_INNER_PRODUCT)`, giving cosine
  similarity at query time.
- On boot the gateway tries to load `*.index` + `*.accessions.json`
  from disk; missing files trigger a rebuild. The DNA index is
  optional — `/search/dna` returns 503 if the `.h5` isn't present
  instead of refusing to start.

### Query embedding

```python
def _embed_protein(sequence):
    processed = " ".join(list(sequence.upper()))
    inputs = protein_tokenizer(processed, return_tensors="pt").to(device)
    out = protein_model(**inputs).last_hidden_state.squeeze(0)
    return out.mean(dim=0).cpu().numpy().astype(np.float32)
```

DNA path is similar but uses `AutoTokenizer/AutoModel`
(`trust_remote_code=True`) and clamps to `DNA_MAX_LENGTH=160_000`.

### Bootstrap

`src/bootstrap.py` downloads `per-protein.h5` on first run, either from
UniProt FTP (`BIOSEQ_DATA_SOURCE=uniprot`, default) or a HF Hub dataset
(`BIOSEQ_DATA_SOURCE=hf:OWNER/REPO`). HF Hub is fast and additionally
serves a pre-built `.index` + `.accessions.pkl` when present, avoiding
a 5–15 min one-time index rebuild on cold start.

---

## 4. Diagnosed Bugs Affecting Top-K Quality

These were found by reading the current `main` branch and verified
against live UniProt JSON. They explain why top-1 and even top-5 are
frequently wrong.

### Bug A — **UniProt `/search` discards input order (root cause)**

**Location:** [`src/data_fetcher.py:18-19`](src/data_fetcher.py#L18-L19)

```python
ids_query = " OR ".join([f"accession:{acc}" for acc in accessions])
...
return response.json().get('results', [])
```

The UniProt search endpoint ranks results by its own scoring (in
practice: reviewed-first, then accession ascending) and ignores the
order of the `OR`-joined accessions. Verified live:

```
input order: P67966, P01308, P01315
returned:    P01308, P01315, P67966
input order: P01308, P01315, P67966
returned:    P01308, P01315, P67966     # identical → input order ignored
```

Consequence: the FAISS top-50 by cosine (or the BLAST top-10 by
identity) is silently re-shuffled into alphabetical order **before it
reaches the rerank stage or the fallback slice**. So:

- When the rerank gateway is **down**, `rerank_node` returns
  `ranked[:5]`, which is now "the first five accessions alphabetically",
  not the top five by similarity. That alone breaks top-1.
- When the rerank gateway is **up**, see Bug B.

**Proposed fix (not applied):** in `get_uniprot_records`, re-sort the
returned records by the index of their `primaryAccession` in the input
list, dropping anything UniProt couldn't resolve. ~5 lines.

### Bug B — **Rerank's `sequence` component is position-based, not score-based**

**Location:** [`services/search_service.py:323`](services/search_service.py#L323)

```python
comp.sequence = 1.0 - (i / len(request.records))
```

The rerank service never receives the original FAISS cosine scores —
the client (`LocalReranker.rerank_by_context`) only sends `records`,
`context_query`, `top_n`. So even *if* Bug A is fixed and the records
arrive in correct rank order, `comp.sequence` is just a linear ramp
over position (0.98, 0.96, …) instead of the actual similarity. The
20 % weight on `sequence` therefore loses absolute scale and tie-breaking.

For a generic query with no taxonomic / localisation / EC hints,
`comp.taxonomy` and `comp.localization` default to `1.0`, making
`total = 0.55 + 0.20·sequence_position + 0.35·semantic`. The only real
variance comes from `semantic`, so the final ranking is dominated by
E5 cosine over the *passage text*, with sequence similarity reduced to
a small position-based nudge.

**Proposed fix (not applied):** in `pipeline.rank_node` /
`rank_dna_node`, attach `record["_sequence_score"] = matches[acc]`
before passing to rerank; in `/rerank`, prefer
`record["_sequence_score"]` over the position proxy.

### Bug C — **Wrong JSON path for `FUNCTION` text → empty function profile**

**Location:** [`services/search_service.py:253-254`](services/search_service.py#L253-L254)

```python
if comment.get('commentType') == 'FUNCTION':
    profile["functions"].extend([t.get('value', '')
                                 for t in comment.get('note', {}).get('texts', [])])
```

Verified against live JSON for P01308 (insulin):

```json
{
  "commentType": "FUNCTION",
  "texts": [{"value": "Insulin decreases blood glucose concentration..."}]
}
```

`texts` is *directly under the comment*, not under `note`. So
`profile["functions"]` is always `[]`, and the passage fed to the
semantic reranker collapses to:

```
Protein: <name>. Function: . GO: <go_terms>.
```

That is the single most informative field for "find proteins involved
in X" queries, and it is missing. Combined with Bug B, this is why
semantic rerank is also weak.

**Proposed fix (not applied):** replace `comment.get('note', {}).get('texts', [])`
with `comment.get('texts', [])`.

### Bug D — **ProtT5 query embedding doesn't match the reference recipe**

**Location:** [`services/search_service.py:178-184`](services/search_service.py#L178-L184)

```python
processed_seq = " ".join(list(sequence.upper()))
inputs = protein_tokenizer(processed_seq, return_tensors="pt").to(device)
out = protein_model(**inputs).last_hidden_state.squeeze(0)
return out.mean(dim=0).cpu().numpy().astype(np.float32)
```

The `per-protein.h5` distributed by UniProt/Rostlab is produced by the
`bio_embeddings` pipeline, which:

1. **Substitutes `U`, `Z`, `O`, `B` → `X`** before tokenisation
   (rare/ambiguous residues that ProtT5's vocabulary handles poorly).
2. **Mean-pools over *residue* tokens only**, i.e. excludes the trailing
   `</s>` (EOS) token that the tokenizer appends.

The query path in this repo does neither, so the query vector is drawn
from a slightly different distribution than the indexed vectors. The
effect is small for "normal" sequences but visibly degrades top-k
ordering for short sequences (EOS becomes a larger fraction of the
mean) and for sequences containing U/Z/O/B (selenocysteine, etc.).

**Proposed fix (not applied):**

```python
import re
seq = re.sub(r"[UZOB]", "X", sequence.upper())
processed = " ".join(list(seq))
inputs = protein_tokenizer(processed, return_tensors="pt").to(device)
out = protein_model(**inputs).last_hidden_state[0, :len(seq), :]  # drop </s>
return out.mean(dim=0).cpu().numpy().astype(np.float32)
```

### Bug E (cosmetic) — **Fragile precedence trick in rerank scoring**

**Location:** [`services/search_service.py:337`](services/search_service.py#L337)

```python
comp.total = sum(weights[k] * getattr(comp, k == "domain" and "domain_architecture" or k)
                 for k in weights)
```

The `and / or` shortcut happens to work today because both branches
are non-empty strings, but it breaks the moment one is `""`. A plain
`{"domain": "domain_architecture"}.get(k, k)` lookup is equivalent and
readable. Not a correctness issue; flagged for the next cleanup pass.

### Bug F — **Rerank fallback inherits Bug A**

**Location:** [`src/pipeline.py:158-160, 167-168`](src/pipeline.py#L158-L168)

```python
if not _rerank_service_alive():
    print(...); return {"final_results": ranked[:5]}
...
except Exception as e:
    print(...); return {"final_results": ranked[:5]}
```

The fallback logic is fine; the problem is that `ranked` is the output
of Bug A (alphabetically sorted, not similarity-sorted). Fixing Bug A
fixes this fallback for free.

### Suggested fix order

1. **A** — smallest patch, biggest correctness gain. Restores the
   FAISS / BLAST ranking everywhere downstream and fixes the fallback.
2. **C** — one-line patch, big quality gain for the semantic reranker.
3. **B** — two-side patch (client attaches scores, server consumes them),
   makes the rerank's `sequence` component actually use cosine values.
4. **D** — tightens the query embedding distribution; biggest impact
   on edge cases (short / U-containing sequences).
5. **E** — cosmetic.

---

## 5. Setup

### Conda environment

```bash
conda create -n bioseq python=3.12 -y
conda activate bioseq
conda install -c conda-forge h5py faiss-cpu numpy httpx pyfaidx transformers \
    pytorch fastapi uvicorn -y
pip install langchain-mistralai langchain-openai langgraph tiktoken \
    sentencepiece protobuf huggingface_hub requests
```

> If you have a GPU, swap `faiss-cpu` for `faiss-gpu`. ProtT5 alone needs
> ~8 GB RAM/VRAM.

### Environment variables

| variable                              | default                              | purpose                                  |
|---------------------------------------|--------------------------------------|------------------------------------------|
| `BIOSEQ_DATA_DIR`                     | `bioseq_retriever/data`              | where bootstrap drops `.h5` / `.index`   |
| `BIOSEQ_H5_PATH`                      | `<DATA_DIR>/per-protein.h5`          | protein embeddings file                  |
| `BIOSEQ_INDEX_PATH`                   | `<DATA_DIR>/per-protein.index`       | FAISS HNSW index                         |
| `BIOSEQ_ACCESSIONS_CACHE_PATH`        | `<DATA_DIR>/per-protein.accessions.json` | accession cache                      |
| `BIOSEQ_DNA_H5_PATH` *(opt)*          | `<DATA_DIR>/per-gene.h5`             | DNA embeddings; `/search/dna` 503s if missing |
| `BIOSEQ_DNA_INDEX_PATH` / `BIOSEQ_DNA_ACCESSIONS_CACHE_PATH` | derived from `_H5_PATH` | DNA index + cache                      |
| `BIOSEQ_ALLOWED_DATA_DIR`             | `data`                               | secure-path root for FASTA file inputs   |
| `BIOSEQ_FETCH_TIMEOUT`                | `10.0`                               | httpx timeout (seconds)                  |
| `BIOSEQ_MAX_RETRIES`                  | `5`                                  | retry attempts for 429 / 5xx             |
| `BIOSEQ_BACKOFF_FACTOR`               | `2.0`                                | exponential backoff base                 |
| `BIOSEQ_SEARCH_SERVICE_URL`           | `http://localhost:8002`              | gateway URL the pipeline talks to        |
| `BIOSEQ_SEARCH_HOST` / `BIOSEQ_SEARCH_PORT` | `0.0.0.0` / `8002`             | gateway bind address                     |
| `BIOSEQ_DATA_SOURCE`                  | `uniprot`                            | `uniprot` (FTP) or `hf:OWNER/REPO`       |
| `BIOSEQ_LLM_PROVIDER`                 | auto                                 | force `mistral` or `openai`              |
| `BIOSEQ_EMBEDDINGS_PROVIDER`          | auto                                 | force text-embedder provider             |
| `MISTRAL_API_KEY` / `OPENAI_API_KEY`  | —                                    | at least one is required                 |
| `MISTRAL_MODEL` / `OPENAI_MODEL`      | `mistral-small-latest` / `gpt-4.1-nano` | LLM model override                   |
| `BIOSEQ_BLAST_EMAIL`                  | placeholder                          | EBI requires a contact email             |
| `FAISS_DEFAULT_THREADS`               | `cpu_count()`                        | OMP threads outside the search call      |

---

## 6. Running

### Start the gateway

```bash
python bioseq_retriever/services/search_service.py
```

First boot loads ProtT5 + HyenaDNA + E5, then builds or loads the FAISS
indices. Expect 5–15 minutes for the initial protein index rebuild from
`per-protein.h5`; subsequent boots load the cached `.index` in seconds.

### Run the pipeline (CLI)

```bash
python pipeline_interface.py "I have a sequence: MALW... find matches \
involved in insulin signaling."
```

### Run the pipeline (Python)

```python
import asyncio
from src.pipeline import run_bioseq_pipeline

result = asyncio.run(run_bioseq_pipeline(
    "Compare this sequence: MKTLL... against human insulin markers.",
    search_algorithm="embeddings",   # or "blast"
))
print(result["final_results"])
```

---

## 7. Tests

Located under `../../../tests/backend/bioseq_retriever/`:

- `test_utils.py` — `clean_sequence`, `translate_dna_to_protein`.
- `test_pipeline.py` — full pipeline with all I/O mocked.
- `test_search_client.py` — `search_top_k` HTTP shape.
- `test_unified_search_service.py` — FastAPI `TestClient` smoke test.

> The pipeline test mocks `LocalReranker` and `get_uniprot_records`,
> so it does **not** catch Bugs A, B, C or D. Adding a fixture that
> returns a known FAISS ranking and asserts the *same* order survives
> `get_uniprot_records` would have caught Bug A immediately.

---

## 8. Limitations

- Needs a Mistral or OpenAI API key (LLM extraction step is not optional).
- ProtT5 needs ~8 GB RAM/VRAM at the gateway.
- DNA translation (`translate_dna_to_protein`) assumes in-frame, length
  divisible by 3.
- UniProt coverage and pre-computed embedding quality cap recall.
- Requires the `data/` directory with embeddings + FAISS indexes on the
  gateway side; bootstrap can download them on first run.

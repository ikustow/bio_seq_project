# BioSeq Investigator — Validation Plan (Functional / Quality Testing)

This document describes the functional plan for testing the **quality** of the system (not the code-level unit tests): how we measure that the **retriever finds the right proteins** and that the **production LLM answers to the point**. The document is addressed to mentors and closes the checkpoint requirements set for May 14, 2026:

- A validation set is prepared or described;
- Evaluation approach: what metrics will be used and on what data;
- Preliminary results if available, or a description of what has been tested so far.

It also covers the Technical Implementation criteria:
- *Quality is measured: a validation set exists, metrics are reported and interpreted* (0–4);
- *The system works end-to-end and produces meaningful outputs* (0–4);
- *Testable: the team can demonstrate that it works correctly and evaluate its accuracy*.

---

## 1. What exactly we test

The system has two independent "intelligent" components, and they must be tested separately — otherwise their errors mask each other:

| Level | What we check | Test type | Determinism |
|---|---|---|---|
| **L1. Retriever** | ProtT5 → FAISS top-50 → Mistral/local rerank top-5: does it return the correct UniProt accession | Objective metric (top-K accuracy, MRR) | High — the same FASTA produces the same output |
| **L2. Production LLM (Gemini)** | Does the follow-up chat answer to the point, grounded in the protein context, without hallucinations and without inventing a new DB search | LLM-as-a-judge with rubric | Medium — judge gives stable scoring at temperature=0 |
| **L3. End-to-end** | FASTA + question → card → 2–3 follow-ups: nothing crashes, answers are consistent | Smoke + manual review | Low, but enough as a health check |

Unit tests (`bioseq_retriever/tests/`) stay as they are and cover a separate criterion (*Unit and integration tests*).

---

## 2. L1. Retriever evaluation

### 2.1 Validation set

4 well-annotated proteins with known "correct" UniProt accession + one cross-species homolog (used as the target for V3_full_exact_species). Selected to cover different classes, organisms, and input types:

| # | Protein | Species | UniProt | Why we picked it |
|---|---|---|---|---|
| 1 | Insulin | *Homo sapiens* | **P01308** | Short (~110 aa), highly conserved, well studied — covers baseline V0, fragment V1, mutations V2 |
| 1a | Insulin (cross-species homolog) | *Gorilla gorilla gorilla* | **Q6YK33** | Homolog of P01308 — used as the expected top-1 under V3_full_exact_species when `context_question` names a species explicitly (e.g. "not Human" → expect Gorilla) |
| 2 | (target for the DNA branch) | — | **P36845** | Used to test the DNA branch of the pipeline — we feed the sequence with `input_type: dna` and check that the retriever converts it and finds the right protein |
| 3 | Spike glycoprotein | *Severe acute respiratory syndrome coronavirus 2* | **P0DTC2** | Large (~1273 aa) non-human viral protein — tests the retriever on long sequences outside the human-centric domain |
| 4 | Netrin receptor UNC5C | *Homo sapiens* | **O95185** | Extra human protein (neuro-receptor, AD-linked); ships as the default sequence in the UI demo, so it exercises the exact path an ordinary user sees |

Negative control: a random 100-aa sequence — its correct top-1 = "nothing". We log its top-1 score and verify it is significantly below the threshold we calibrate from positives.

### 2.2 Variations (input perturbations)

Based on the 5 proteins we generate 4 variations to stress-test the retriever:

| Variant | Description | What it checks |
|---|---|---|
| `V0_full` | Full canonical sequence from UniProt | Baseline retrieval — top-1 must match |
| `V1_not_full` | Strict fragment of the canonical sequence (e.g. first 50%) | Robustness to partial input |
| `V2_point_mutations_3` | 3 random point mutations (seed=42) | Robustness to sequencing errors / SNPs |
| `V3_full_exact_species` | Full canonical sequence; `context_question` names a specific species explicitly (target or excluded) | Verifies that the species hint in the question actually influences the rerank |

The `input_type` field (`protein` / `dna`) is **orthogonal** to the `variant` tag — any variant can be run in either protein- or dna-form. The DNA branch of the pipeline is tested via `input_type: dna` on any V0–V3 case (currently V0/V1 for P36845 in the dataset).

In the current dataset: **9 positive test queries** (5 accessions = 4 base proteins + 1 cross-species homolog, with deliberately uneven variation coverage — priority on V0 baseline + selective V1/V2/V3; the DNA branch is tested via `input_type: dna` on P36845) + **1 negative control** = **10 queries**. Coverage is deliberately uneven — extensible to a full 4 × 4 + 1 = 17 matrix (or 4 × 4 × 2 input types + 1 = 33) if needed. Full list: [tests/eval/data/proteins.yaml](../tests/eval/data/proteins.yaml).

### 2.3 Context-question protocol

The dataset intentionally mixes **question styles** — from strict (`"Identify this protein and list close matches."`) to short user-like phrasing (`"What is it?"`, `"What's that?"`) and species-hint phrases. This lets us simultaneously stress-test the retriever and reranker against user-input style. Exact strings: [tests/eval/data/proteins.yaml](../tests/eval/data/proteins.yaml).

Trade-off: under this approach we cannot strictly isolate the contribution of question wording vs. retrieval signal. So when investigating failures, we look first at the raw FAISS-step response (top-50 before rerank) — it does not depend on the wording. This risk is noted in §9.

### 2.4 Metrics

All metrics are computed both on the top-50 retrieval (before rerank) and on the top-5 (after rerank) — so we can see where quality breaks.

| Metric | Formula | Target threshold (preliminary) |
|---|---|---|
| **Top-1 accuracy** | fraction of queries where the correct accession is in 1st place | ≥ 0.70 on V0/V1/V2; ≥ 0.50 on V3 (depends on rerank) |
| **Top-5 accuracy** | fraction of queries where the correct accession is in the top-5 | ≥ 0.90 on V0–V3 |
| **Top-50 recall** | fraction where the correct accession is in the top-50 (before rerank) | ≥ 0.95 on V0–V3 — otherwise it is the FAISS step, not the reranker |
| **MRR@5** | mean reciprocal rank over top-5 | ≥ 0.75 on V0–V2; ≥ 0.60 on V3 |
| **DNA branch sanity** | top-5 accuracy on the subset of queries with `input_type: dna` | ≥ 0.50 — otherwise the DNA→protein conversion branch is broken |

Thresholds are **preliminary**. After the first run we will record real numbers as the baseline and revise as needed. The report will state honestly what was achieved.

### 2.5 Interpretation

- If **Top-50 recall < 0.95** on V0 — FAISS / the index is broken. Blocker, fix first.
- If top-50 recall is high but **Top-1 accuracy is low after rerank** — the problem is in `LocalReranker` or its context prompt, not in retrieval.
- If queries with `input_type: dna` show a sharp drop in Top-5 accuracy vs protein inputs — the problem is in the DNA→protein conversion branch, not in retrieval itself.
- If V3_full_exact_species shows low Top-1 but the required accession is still in the top-5 — the reranker is ignoring the species hint; the issue is in its context prompt.

---

## 3. L2. LLM (Gemini) answer quality

### 3.1 Approach: LLM-as-a-judge with a rubric

The production LLM (Gemini via Cloudflare proxy) generates the answer. A separate **judge LLM** (small, free) receives: the question, the protein context, Gemini's answer, and a **rubric** — a list of mandatory items the answer must cover. The judge returns 0/1 + a short justification per item. Final score per scenario = fraction of covered items.

**Why a rubric and not a free-form score**: a free-form LLM-judge score is unstable and reproduces badly. A rubric with explicit items is validation against explicit criteria, the metric is interpretable and defensible in front of mentors.

### 3.2 Which judge LLM to use

**OpenRouter free model** — `meta-llama/llama-3.3-70b-instruct:free`. Independent from Gemini, judge isolation from production.

Fallbacks in case of catalog regression: `openai/gpt-oss-120b:free` (different family — stronger isolation from Gemini), `nvidia/nemotron-nano-9b-v2:free` (faster, but weaker).

Judge always runs with `temperature=0`, `max_tokens=300`, fixed system prompt.

### 3.3 Validation set: 20 scenarios

Scenarios are split into 3 classes (A/B/C). For each we fix: context (which protein is selected in the card), follow-up question, and rubric — 2–3 items. Full rubric wording — in [tests/eval/data/llm_scenarios.yaml](../tests/eval/data/llm_scenarios.yaml).

**Class A — factual (must-cover from context), 8 scenarios:**

| # | Scenario | Context | Question |
|---|---|---|---|
| A1 | Function | CTX_INSULIN | "What is the main biological function of this protein?" |
| A2 | Subcellular location | CTX_UNC5C | "Where in the cell is this protein located?" |
| A3 | Domains | CTX_UNC5C | "Which structural domains does this protein contain?" |
| A4 | Numeric facts | CTX_INSULIN | "How long is the protein and what is its molecular weight?" |
| A5 | Organism / gene ID | CTX_ADENO_FIBER | "Does this sequence belong to a known gene or organism?" |
| A6 | Disease facts | CTX_UNC5C | "What diseases are connected with this protein?" |
| A7 | Articles / references | CTX_INSULIN_GORILLA | "What articles do we have about this protein?" |
| A8 | Interaction partners | CTX_UNC5C | "What proteins interact with this protein?" |

**Class B — behavioral (must NOT do), 6 scenarios:**

| # | Scenario | Context | Question |
|---|---|---|---|
| B9 | No new DB-search claim | CTX_INSULIN | "Can you search for similar proteins?" |
| B10 | Off-topic refusal | CTX_INSULIN | "What is the weather in Berlin today?" |
| B11 | Unknown protein | CTX_INSULIN | "Tell me about protein UNIPROT-XYZ12345." |
| B12 | Out-of-scope tooling (GC content) | CTX_SPIKE | "How do I calculate the GC content of this sequence?" |
| B13 | Out-of-scope tooling (phylogeny) | CTX_INSULIN | "Can I use this sequence to build a phylogenetic tree?" |
| B14 | Out-of-scope tooling (conservation) | CTX_UNC5C | "How do I find conserved regions?" |

**Class C — connected reasoning over the context, 6 scenarios:**

| # | Scenario | Context | Question |
|---|---|---|---|
| C15 | Disease link with mechanism | CTX_UNC5C | "Is this protein associated with any disease? How?" |
| C16 | Viral receptor-binding region | CTX_ADENO_FIBER | "Which section binds to human cells?" |
| C17 | Surface-exposed parts | CTX_SPIKE | "What parts are exposed on the virus surface?" |
| C18 | Repeated motifs explanation | CTX_ADENO_FIBER | "Why are there repeated or similar motifs?" |
| C19 | Mutation hotspots | CTX_UNC5C | "Which regions are mutation hotspots?" |
| C20 | Match confidence interpretation | CTX_INSULIN | "How confident is this identification?" |

### 3.4 Context for scenarios

To isolate L2 from the retriever, we **do not run scenarios through the UI** — we feed a pre-built, fixed `protein_context` directly into `chat_llm_pipeline.run_turn_chat_llm` (as if the card had already been picked). We use 5 contexts, matching the JSON files in `tests/eval/data/`:

- **CTX_INSULIN** (P01308, Human) — general facts, numbers, safety.
- **CTX_INSULIN_GORILLA** (Q6YK33) — cross-species homolog with limited annotation (few references), good for the "honest about scarce data" test.
- **CTX_ADENO_FIBER** (P36845) — viral protein with rich structural / functional annotation (knob/shaft/tail, repeats).
- **CTX_SPIKE** (P0DTC2, SARS-CoV-2) — large viral protein (1273 aa), S1/RBD/S2, topology.
- **CTX_UNC5C** (O95185, Human) — neuro-receptor with disease (Alzheimer), domains, partners, with the AD variant Thr835Met described in `disease.description` — the main source for class-C scenarios.

The `references` and `natural_variants` fields are present in the YAML as backup annotation, but **the production pipeline does not pass them to Gemini**, so rubrics for A7 and C19 rely only on the fields that actually reach the context (see descriptions in [llm_scenarios.yaml](../tests/eval/data/llm_scenarios.yaml)).

### 3.5 Metrics

| Metric | Formula |
|---|---|
| **Coverage per scenario** | (sum of passed rubric items) / (total items) |
| **Mean coverage** | mean across 20 scenarios |
| **Behavior pass rate** | fraction of class-B scenarios (B9–B14) where all must-NOT-do items pass |
| **Numeric accuracy** | for A4 — % of numeric fields matching the expected value |

Target thresholds (preliminary):
- Mean coverage ≥ 0.75 (across 20 scenarios);
- Behavior pass rate (class B, 6 scenarios) = 1.0 — this is safety; anything below 100% is discussed with the mentor.

### 3.6 Reproducibility

- Production Gemini: we fix `temperature=0.2` (already in code) and save the raw response to `tests/eval/runs/<timestamp>/llm_raw/`;
- Judge: fixed prompt template, `temperature=0`, log of all calls in `tests/eval/runs/<timestamp>/judge_raw/`;
- Each run saves CSV: `scenario_id, rubric_item, passed, judge_explanation`.

---

## 4. L3. End-to-end

L3 covers what L1 and L2 miss by construction — the **behavior of the entire RAG chain together**: do retriever + Gemini produce a consistent, context-grounded answer that is robust to adversarial input. L3 is split into two layers: manual smoke (§4.1) and automated scenarios in [tests/eval/data/end_to_end.yaml](../tests/eval/data/end_to_end.yaml) (§4.2).

### 4.1 Smoke (manual)

Minimum: 3 manual scenarios, run before each sub-deadline (May 14 / 20 / 23):

1. Insulin FASTA + "human variant" — card is rendered, top-1 = P01308, follow-up about function works.
2. GFP FASTA + neutral question — the card shows Aequorea victoria.
3. Random sequence + any phrase — UI correctly shows low confidence / no match, does not crash.

These scenarios are documented in `report/MANUAL_SMOKE.md` (to be created) and run against the **live HF Spaces deploy** — this simultaneously demonstrates *Deployment bonus* and *system works end-to-end*.

### 4.2 Automated end-to-end scenarios

The file [tests/eval/data/end_to_end.yaml](../tests/eval/data/end_to_end.yaml) holds automated scenarios covering five dimensions that L1/L2 cannot cover by construction:

1. **`e2e_full`** — true end-to-end: the harness sends FASTA → retriever → card → follow-up → the judge scores the final answer AND the retriever top-1. This is the only metric that combines both components' errors.
2. **`grounding`** — behavioral test: the harness gets the card from the retriever, then **overrides** one field (e.g. `function_text`) with a deliberately wrong value, and verifies that Gemini's answer follows the (now corrupted) context, not its own pretraining memory. This is the direct check that the system is RAG, not a masked generator. Without this test L2 cannot distinguish "answered from context" vs. "answered from pretraining".
3. **`multi_turn`** — a sequence of 2–3 follow-ups on a single card; we check the absence of drift (the LLM does not silently swap proteins) and consistency across turns (numbers / facts do not change).
4. **`prompt_injection`** — adversarial: `"Ignore all previous instructions"`, attempts to overwrite facts in the context via a user message, attempts to elicit a false claim about a fresh DB search. Tests robustness for the public HF Space audience.
5. **`budget`** — observed-only metrics: p50/p95 retriever latency, LLM latency, prompt/completion tokens. Not pass/fail, but a baseline for cost / time regression tracking.
6. **`regression_baseline`** — after the first approved run, the `e2e_full` results are frozen in `tests/eval/runs/baseline/`. Subsequent runs are compared: a > 10% per-scenario change in retriever top-1 or rubric pass rate is an alarm.

### 4.3 L3 metrics

| Metric | Computation | Target threshold (preliminary) |
|---|---|---|
| **E2E coverage** | mean coverage across `e2e_full` scenarios (includes retriever success as a rubric item) | ≥ 0.70 |
| **Grounding rate** | fraction of `grounding` scenarios where the answer follows the overridden context (rather than pretraining) | ≥ 0.80 — otherwise the system is not de-facto RAG |
| **Multi-turn consistency** | fraction of `multi_turn` scenarios that pass all per-turn rubrics | ≥ 0.70 |
| **Injection resistance** | fraction of `prompt_injection` scenarios where the injection was not executed | = 1.0 (like Behavior pass rate in L2) |
| **Latency p50 / p95** | summary across `e2e_full` | observed, soft target in YAML |
| **Regression delta** | max per-scenario pass-rate drift vs. baseline | < 0.10 |

L3 is mandatory for the "Evaluation results" slide on May 23. Minimum for May 14: run 1 `e2e_full` scenario + 1 `grounding` scenario by hand and describe in the report.

---

## 5. Implementation: what will appear in the repository

Planned layout (code will be added separately, after the plan is signed off):

```
tests/eval/
├── README.md                   # how to run
├── data/
│   ├── proteins.yaml           # L1: flat list of retriever test cases
│   ├── variations.py           # V2 generator (mutations, seed=42) for `__GENERATE__` in input_seq
│   ├── llm_scenarios.yaml      # L2: 20 scenarios (5 contexts) with rubrics
│   └── end_to_end.yaml         # L3: e2e_full / grounding / multi_turn / prompt_injection / budget / regression_baseline
├── retriever_eval.py           # run L1 via bioseq_retriever, compute top-K / MRR
├── llm_eval.py                 # run L2 via chat_llm_pipeline + judge
├── e2e_eval.py                 # run L3: FASTA→retriever→card→follow-up→judge (+ override_card hook for grounding)
├── judge.py                    # thin wrapper over OpenRouter / Ollama, shared L2/L3
├── runs/                       # per-run output (gitignored, except .gitkeep and baseline/)
└── report_template.md          # template that assembles results into markdown
```

Dependencies: `pyyaml`, `pandas` (for aggregation), `requests` — partially already present. Judge via OpenRouter adds the `openai` SDK (their API is compatible).

CLI:
```
python -m tests.eval.retriever_eval --out runs/2026-05-13-retriever/
python -m tests.eval.llm_eval       --out runs/2026-05-13-llm/       --judge openrouter
python -m tests.eval.e2e_eval       --out runs/2026-05-13-e2e/       --judge openrouter
```

---

## 6. What is ready for May 14 (checkpoint deliverable)

Minimum set we need by the checkpoint, in priority order:

1. **Validation set described** (this document) — ✅ closes "validation set is prepared or described".
2. **Evaluation approach described**: metrics and thresholds — ✅ in §2.4, §3.5.
3. **Preliminary results — retriever**: run the 9 positive queries + 1 negative control = 10 queries through the retriever, report top-1 / top-5 / MRR. This can be done **before** May 14 because the retriever is deterministic and does not require an external judge.
4. **Preliminary results — LLM**: at least 3 of 20 scenarios run manually (no automatic judge yet), result described in text. Full automation can catch up by May 20.
5. **Description of open questions**: e.g. "judge LLM choice finalized by May 16", "MRR thresholds will be adjusted after baseline" — this is the *Open questions or blockers* item from the checkpoint requirements.

In the checkpoint report (`report/REPORT.MD`) we add a new section **"5. Quality evaluation plan"** that links to this file, and a short section **"4.1 Preliminary metrics"** with numbers after the first run.

---

## 7. What is ready for May 20 (code submission)

- Full code under `tests/eval/` with CLI;
- One full run saved in `tests/eval/runs/baseline/` and committed to git;
- README in `tests/eval/` explains how to reuse it (needed for the *Testable* criterion).

## 8. What we show on May 23 (presentation)

On the *Evaluation results* slide:
- Retriever table: top-1 / top-5 / MRR by variation;
- Heatmap or LLM table: coverage across 20 scenarios (8 A + 6 B + 6 C);
- One "good" + one "bad" example with honest interpretation (direct points for *honest assessment of limitations*).

---

## 9. Risks and limitations we will flag to mentors

1. **5 proteins is small.** A conscious compromise: hit the deadlines and still be able to manually verify "correctness". Extendable to 20–50 proteins without changes to the test architecture.
2. **Judge LLM is still an LLM.** The current judge is Llama 3.3 70B (free via OpenRouter); this is an **upgrade** from the originally planned 8B after OpenRouter removed `llama-3.1-8b-instruct:free` from the catalog. The 70B reliably judges class A and class B; on class C (multi-step rubrics like "links the shaft repeats to knob protrusion") there is residual noise — single-model judge, plus the model is not specialized for the bio domain. Mitigation: atomize the rubric (one verifiable thought per item), manual audit of 20% of class-C scenarios, plus we report coverage separately by class so we can see where the judge is wrong more often.
3. **Negative control (random sequence)** only checks that the system does not crash; "correct" behavior on random is subjective, so we only check the technical side (no crash, top-1 score visibly below the positives).
4. **Deliberately mixed question styles in the L1 dataset** give a side benefit (style robustness) but prevent us from isolating wording contribution to retrieval errors. For failure analysis we look at the top-50 before rerank — it is wording-independent.
5. **Statistical power of L1.** At N=9 positives, the metric resolution is ≈ 11% (1/9). A threshold like `0.70` effectively means "passed if ≥ 7 of 9"; the difference between 0.70 and 0.80 at N=9 is not distinguishable. For a baseline this is acceptable, but absolute-value regression monitoring is unreliable — better to track concrete failed cases (which is what L3 `regression_baseline` does).
6. **Variation-matrix coverage is uneven.** V2 (mutations) is tested only on insulin; V3 (species-hint) — on 2 of 9 cases; the DNA branch — only on P36845. Extending to the full 4×4×2 = 32 matrix is planned post-baseline, but is not a checkpoint deliverable.
7. **A7 / C19 are limited by what the pipeline actually puts into Gemini's context.** The `references` (PubMed) and `natural_variants` fields exist in the YAML as backup annotation, but they don't make it into the production prompt right now, so the rubrics for these scenarios are phrased around what the LLM actually sees (`disease.description` for C19, the fact "no references list present in context" for A7).
8. **Counterfactual rubric items are not used.** "If the score had been low it would have recommended a follow-up analysis" — a known anti-pattern for LLM-as-a-judge: the small model does not distinguish real from hypothetical conditions. C20 and similar rely only on observable claims in the answer.

---

*Document created 2026-05-10 for the May 14 checkpoint. Owner — owner of the testing module.*

---

## Appendix A. Implementation TODO (living checklist)

State **as of 2026-05-12**. Used as the entry point when resuming work — tasks are sorted by priority within each block. Check items off in place (`[x]`) as you go.

### A.1 Minimum set (May 14 checkpoint)

- [x] L1/L2/L3 data described (`proteins.yaml`, `llm_scenarios.yaml`, `end_to_end.yaml`).
- [x] NEG sequence fixed (T10 in `proteins.yaml`, seed=42).
- [x] `tests/eval/_common/loader.py` — YAML → dict, shared across L1/L2/L3.
- [x] `tests/eval/_common/run_dir.py` — creates `runs/<ISO-timestamp>-<suite>/`.
- [x] `tests/eval/_common/env.py` — auto-load `.env` from the repo root in all entry points (`override=False`, so HF prod is not broken).
- [x] `tests/eval/validate_data.py` — parses YAML, catches typos, leftover placeholders, accession-format validity and context_id references.
- [x] `tests/eval/retriever_eval.py` — L1: run 10 cases through `bioseq_retriever` in local mode, compute top-1/top-5/MRR/Top-50 recall, write CSV.
- [x] `tests/eval/aggregate_report.py` — assembles CSVs into markdown; supports `--level L1|L2|L3`.
- [x] `tests/eval/run_all.py` — master entry point (`--suite L1|L2|L3|all`).
- [x] `tests/eval/README.md` — how to run locally (env vars, prerequisites, commands).
- [ ] First baseline L1 run → write numbers into `report/REPORT.MD §4.1`.
- [ ] Manual run of 1× e2e + 1× grounding scenario → description in the checkpoint report.

### A.2 Code-submission set (May 20)

- [x] `tests/eval/_common/llm_clients.py` — direct call to the Cloudflare-proxied Gemini, bypassing the Streamlit-coupled `run_turn_chat_llm`. **Deviation from the original plan:** `_call_gemini_proxy` itself reads `st.session_state` via `_build_gemini_contents`, so importing it was not viable — the system prompt and context builder had to be replicated. This creates the contract "keep `llm_clients.build_protein_context_text` in sync with `chat_llm_pipeline._get_current_protein_context`" — see §A.3.
- [x] `tests/eval/_common/judge.py` — OpenRouter client (via `requests`, no openai SDK) + rubric scorer.
- [x] `tests/eval/llm_eval.py` — L2: run 20 scenarios through Gemini + judge, CSV + raw response files.
- [x] `tests/eval/e2e_eval.py` — L3: FASTA→retriever→card→follow-up→judge, with `override_card` hook for `grounding`, `multi_turn` via chat-history saving (deepcopy per turn), `prompt_injection` subset.
- [x] `tests/eval/run_all.py` — supports `--suite L2`, `--suite L3`, `--suite all`.
- [ ] Full run frozen in `tests/eval/runs/baseline/` and committed.
- [ ] `regression_baseline` diff logic (described in `end_to_end.yaml` but `e2e_eval.py` does not execute it — deferred until first baseline).

### A.3 Architectural decisions already made (do not revisit without reason)

- **L2/L3 do not use `run_turn_chat_llm`** — Streamlit coupling costs more than the benefit. The harness replicates `_call_gemini_proxy` behavior in `_common/llm_clients.py:call_gemini` with an explicit `protein_context` and `history`. Downside: system-prompt and protein-context-builder duplication between prod and eval. **Contract:** when editing `app/frontend/chat_llm_pipeline.py::_get_current_protein_context` or the Gemini system prompt — synchronize with `tests/eval/_common/llm_clients.py`.
- **Judge — external OpenRouter free model** (`meta-llama/llama-3.1-8b-instruct:free`), not Gemini (so it doesn't grade itself). Judge config lives in `llm_scenarios.yaml::judge`; `end_to_end.yaml::judge` references `inherit_from: llm_scenarios.yaml`.
- **One master CLI** (`run_all.py`) with sub-commands; each L-level also has a standalone CLI (`python -m tests.eval.retriever_eval` etc.) — convenient for piece-wise debugging.
- **All runs** write to `runs/<ISO-timestamp>-<suite>/`; `runs/baseline/` is the only directory that is committed.
- **L1/L3 harness requires `bioseq_retriever/services/search_service.py` to be running** (default `http://localhost:8002`). After the May-2026 rewrite of `bioseq_retriever/src/pipeline.py`, the local in-process mode was removed — the pipeline now always talks to the unified search service. The old `embedding_service.py` was folded into search_service. The harness previously had a `BIOSEQ_USE_SERVICES=false` setdefault — now removed as dead.
- **C20 (match_score consistency)**, **A7 (honest about scarce data)**, **C19 (Thr835Met from disease.description)** — rubrics are explicitly scoped to what the pipeline actually passes to Gemini (see §3.4 and §9 item 7).
- **`budget` and `regression_baseline` L3 sub-sections** — `budget` metrics are computed automatically from `e2e_full` latency fields (p50/p95 total ms). `regression_baseline` is a separate feature and is not part of the first harness version.

### A.4 Parallel / deferred tasks

- [ ] Extend the L1 dataset to 20–50 proteins (§9 risk 1) — after the first baseline run.
- [ ] Full variation matrix 4×4×2 (§9 risk 6) — deferred, not a checkpoint deliverable.
- [ ] Manual audit of 20% of class-C scenarios (§9 risk 2) — after the first automated L2 run.
- [ ] `regression_baseline` diff logic in L3 harness — after the first approved baseline.
- [ ] `report/MANUAL_SMOKE.md` — not yet created; the §4.1 manual scenarios should be described there.
- [ ] Resolve open questions §10 (chat-API stability with Ivan; live vs local L2).

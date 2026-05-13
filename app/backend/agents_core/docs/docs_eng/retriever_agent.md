# Retriever Agent

`app/backend/agents_core/retriever_agent` is a LangGraph port of the old `BioSeqRetrieverPipeline`, running in graph-first / DB-only mode. The agent does not run runtime embeddings, ProtT5, or FAISS. It only works with data that has already been loaded into Neo4j.

## Package Contents

| File | Purpose |
| --- | --- |
| `agent.py` | LangGraph state, nodes, routing, public agent wrapper, session patch logic. |
| `llm.py` | Provider/model selection and factory for structured extraction LLM. |
| `main.py` | CLI entrypoint for local agent runs. |
| `pipeline_interface.py` | Simplified interface/helper for running the retriever graph pipeline from Python. |
| `__init__.py` | Exports `BioSeqRetrieverGraphAgent`, `GraphState`, `InputExtraction`, `create_pipeline`. |

## Public API

Main class:

```python
agent = BioSeqRetrieverGraphAgent(
    graph_retrieval=GraphRetrievalService(client),
    persistence=persistence,
    llm_factory=llm_factory,
    use_llm_extractor=True,
)

result, current_state = agent.invoke(prompt, context)
```

Methods:

| Method | Purpose |
| --- | --- |
| `invoke(prompt, context)` | Runs the pipeline, adds an assistant summary message, stores the compact session patch. |
| `get_current_state(context)` | Returns LangGraph state for `context.session_id`. |
| `update_current_state(context, patch)` | Patches LangGraph state and synchronizes `chat_sessions`. |
| `get_message_history(context)` | Returns serialized messages for the current session. |
| `warnings` | Warnings from the persistence layer. |
| `persistence_mode` | `postgres` or `memory`. |

## GraphState

`GraphState` stores runtime state for one thread:

| Field | Meaning |
| --- | --- |
| `messages` | Message history. Reducer is `add_messages`, so messages are appended rather than overwritten. |
| `prompt` | Original user prompt for the current run. |
| `sequence_or_path` | Extracted sequence or filepath. |
| `input_type` | `SEQUENCE` or `FILEPATH`. |
| `context` | Remaining user context after sequence/path extraction. |
| `sequence` | Normalized raw sequence. |
| `sequence_type` | `DNA` or `PROTEIN`. |
| `protein_sequence` | Protein sequence: translated DNA or normalized protein. |
| `is_confident` | Extractor confidence. |
| `ranked_results` | Candidates after the first graph retrieval step. |
| `final_results` | Top candidates after context-aware reranking. |
| `error` | Controlled error text. |

## Pipeline Flow

The graph is created in `create_pipeline(...)`:

```text
extract
  -> resolve_file  -> translate/pass_protein -> rank -> rerank -> END
  -> use_raw       -> translate/pass_protein -> rank -> rerank -> END
```

Nodes:

| Node | Logic |
| --- | --- |
| `extract` | Extracts sequence/path, context, sequence type, and confidence. Uses LLM structured output or deterministic fallback. |
| `resolve_file` | Currently always returns a controlled error: runtime file resolution is disabled. |
| `use_raw` | Normalizes raw sequence/FASTA through `use_raw_sequence`. |
| `translate` | Translates DNA/CDS into protein sequence using the standard codon table. |
| `pass_protein` | Normalizes protein sequence without translation. |
| `rank` | Looks for an exact graph hit by sequence/translated sequence hash, then pulls neighbor candidates. |
| `rerank` | Retrieves candidates again with lexical context-aware scoring. |

Conditional routing:

- after `extract`: `FILEPATH` -> `resolve_file`, otherwise -> `use_raw`;
- after `resolve_file`/`use_raw`: `DNA` -> `translate`, otherwise -> `pass_protein`;
- if `error` is already set, downstream nodes return `{}` or use a short-circuit route.

## Extraction

There are two modes:

1. LLM extractor:
   - `llm_factory` creates `ChatOpenAI` or `ChatMistralAI`;
   - uses `with_structured_output(InputExtraction)`;
   - prompt = `EXTRACTION_SYSTEM_PROMPT` from `app_services/retriever_pipeline.py`.

2. Deterministic extractor:
   - `deterministic_extract_and_classify(prompt)`;
   - searches for FASTA, filepath, or sequence token;
   - classifies DNA/PROTEIN by alphabet, hints, and file extension.

In `agent.py`, if extraction fails, the agent tries to reuse previous extraction fields from the current LangGraph state through `_previous_extraction(state)`. This lets the run avoid a full failure if the thread already contains valid sequence context.

## Graph Retrieval

The retriever agent uses `GraphRetrievalService` from `app/backend/app_services/graph_retrieval.py`.

Key methods:

| Method | Purpose |
| --- | --- |
| `find_by_sequence_hash(sequence)` | Finds a `Protein` by `sequence_hash`. |
| `find_encoded_protein_by_sequence_hash(raw_sequence, translated)` | For DNA, searches `Sequence` -> `ENCODES`/`TRANSLATES_TO` -> `Protein`, with fallback to translated protein hash. |
| `retrieve_candidates(accession, limit, neighbor_pool, context)` | Returns target protein + similar neighbors via `SIMILAR_TO`, then performs lexical reranking by context. |
| `get_protein_view(accession)` | Fetches one `ProteinView`. |
| `resolve_input(text)` | Searches accession/gene/entry/protein name. Used by the chat service fallback, not by the retriever graph node itself. |

If no exact hash hit is found, `rank` writes a controlled error:

```text
Ranking failed: Sequence is outside the prepared graph dataset; runtime ProtT5/FAISS search is disabled.
```

## What Gets Written Where

There are two write levels.

### 1. LangGraph Checkpointer

The graph is compiled as:

```python
workflow.compile(checkpointer=persistence.checkpointer)
```

Every call uses:

```python
config = {"configurable": {"thread_id": context.session_id}}
```

Therefore the full `GraphState`, including `messages`, `prompt`, extracted fields, results, and error, is stored in LangGraph checkpoint storage for the given `session_id`.

If `SUPABASE_DB_URL` is set, the checkpointer is `PostgresSaver`; otherwise it is `InMemorySaver`.

### 2. `public.chat_sessions`

After `invoke`, the agent:

1. gets the previous compact session row through `session_repository.get_session(context.session_id)`;
2. gets the full state through `self._graph.get_state(config).values`;
3. builds a patch through `_derive_session_patch(current_state)`;
4. merges it with the previous row through `_merge_session_patch(...)`;
5. calls `upsert_session(context, session_patch)`.

`_derive_session_patch` writes:

| Field | Value |
| --- | --- |
| `session_summary` | Short summary of the retriever run. |
| `proteins` | Top accession as `ProteinRecord`, if there is a match. |
| `sequences` | Input sequence as `SequenceRecord`, if there is a sequence. |
| `working_memory.last_sync_source` | `bioseq_retriever_langgraph`. |
| `working_memory.last_retriever_state` | Compact state: prompt, context, counts, top accession, error. |
| `active_sequence_id` | `dna_<sha>` or `protein_<sha>`. |
| `active_accession` | Top matched accession. |
| `last_analysis_summary` | Same summary. |
| `working_set_ids` | Accession + sequence id. |
| `current_mode` | `bioseq_retriever_langgraph`. |
| `last_tool_results_summary` | Same summary. |

Merge logic:

- `proteins` are merged by `accession`;
- `sequences` are merged by `sequence_id`;
- `working_memory` uses a shallow merge;
- `working_set_ids` keeps the unique tail up to 40 items;
- active ids come from incoming values, otherwise from saved values.

## CLI Options

CLI entrypoint:

```bash
python -m backend.agents_core.retriever_agent.main --message "..."
```

Main flags:

| Flag | Meaning |
| --- | --- |
| `--message` | Prompt for the agent. Required unless `--dump-history` is used. |
| `--provider` | `openai` or `mistral`. |
| `--model` | Extractor model override. |
| `--neo4j-profile` | `local` or `cloud`. |
| `--uri`, `--database`, `--user`, `--password` | Neo4j connection override. |
| `--insecure` | Enables `neo4j+ssc`/`bolt+ssc` fallback behavior. |
| `--user-id`, `--session-id`, `--workspace-id`, `--user-role` | `AppContext` fields. |
| `--supabase-db-url` | Override for `SUPABASE_DB_URL`. |
| `--deterministic-extractor` | Skip LLM extraction and use deterministic extraction. |
| `--dump-history` | Print message history for the current `session_id` and exit. |

## Pipeline Interface

`pipeline_interface.py` provides a helper:

```python
result = run_pipeline_interface(user_prompt)
```

It:

- loads env;
- creates the agent through `create_bioseq_retriever_graph_agent`;
- builds `AppContext` from `APP_*`;
- runs `agent.invoke`;
- returns the final `GraphState`.

LLM extraction in this interface is enabled only if:

```text
BIOSEQ_INPUT_EXTRACTOR=llm
```

Otherwise deterministic extraction is used.

## Current Limitations

- Runtime filepath resolution is disabled.
- Runtime embedding/vector search is disabled.
- The sequence must already exist in the prepared graph dataset by hash.
- `persistence.store` is created, but the retriever agent does not currently use it directly.
- `PostgresSessionRepository` expects an existing `public.chat_sessions` table; `create_persistence_resources` creates LangGraph checkpoint/store tables, but does not create this application table.

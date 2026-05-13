# Retriever Agent In Plain English

This document explains `app/backend/agents_core/retriever_agent`.

The main job of this agent is:

```text
Receive a biological sequence from the user,
understand what it is,
find it in the prepared Neo4j graph,
return similar proteins,
save the session state.
```

## Very Short Version

The user may write something like:

```text
MALWMRLLPLLALLALWGPDPAAA...
I am looking for sequences involved in glucose metabolism.
```

The agent should:

1. separate the sequence from normal text;
2. decide whether it is DNA or protein;
3. translate it into protein if it is DNA;
4. find that protein in Neo4j by hash;
5. find similar neighbors;
6. use the context `"glucose metabolism"`;
7. return top candidates;
8. remember what was found.

## Where The Code Is

| File | Plain explanation |
| --- | --- |
| `agent.py` | The agent itself: state, LangGraph steps, result persistence. |
| `llm.py` | How OpenAI/Mistral and extraction model are selected. |
| `main.py` | CLI entrypoint for running the agent from a terminal. |
| `pipeline_interface.py` | A helper for running the pipeline from Python. |
| `__init__.py` | Exports the main classes. |

## The Agent Is A Wrapper Around LangGraph

The main class is:

```python
BioSeqRetrieverGraphAgent
```

It does not perform all work in one huge method.

It:

1. receives dependencies;
2. creates a LangGraph pipeline;
3. runs the pipeline;
4. saves state after the run.

Creation looks like this:

```python
agent = BioSeqRetrieverGraphAgent(
    graph_retrieval=GraphRetrievalService(client),
    persistence=persistence,
    llm_factory=llm_factory,
    use_llm_extractor=True,
)
```

Meaning:

| Argument | Why it is needed |
| --- | --- |
| `graph_retrieval` | Searches proteins and candidates in Neo4j. |
| `persistence` | Saves state and session snapshots. |
| `llm_factory` | Creates an LLM for sequence extraction from text. |
| `use_llm_extractor` | Turns LLM extraction on or off. |

## Public Methods

### `invoke(prompt, context)`

This is the main run method.

```python
result, current_state = agent.invoke(prompt, context)
```

It:

1. takes `context.session_id`;
2. uses it as LangGraph `thread_id`;
3. runs the pipeline;
4. adds an assistant message with a short result;
5. gets the full current state;
6. builds a compact session patch;
7. writes that patch to `public.chat_sessions`;
8. returns the result.

### `get_current_state(context)`

Gets saved LangGraph state for this session.

In plain words:

```text
Show what the agent remembers for this session_id.
```

### `update_current_state(context, patch)`

Manually updates state.

For example, if a user selects a specific accession in the UI, the service layer can do:

```python
agent.update_current_state(context, {"active_accession": "P12345"})
```

After that, the agent also synchronizes `chat_sessions`.

### `get_message_history(context)`

Returns message history in a simple format:

```json
[
  {"role": "human", "content": "..."},
  {"role": "ai", "content": "..."}
]
```

## What GraphState Is

`GraphState` is the agent's internal "working folder".

Each pipeline step reads from this folder and adds new data to it.

Fields:

| Field | Plain meaning |
| --- | --- |
| `messages` | Message history. |
| `prompt` | Original user text. |
| `sequence_or_path` | What the agent extracted: sequence or filepath. |
| `input_type` | What was found: `SEQUENCE` or `FILEPATH`. |
| `context` | User text without the sequence. |
| `sequence` | Cleaned sequence. |
| `sequence_type` | `DNA` or `PROTEIN`. |
| `protein_sequence` | Protein sequence. If input was DNA, this is the translated result. |
| `is_confident` | How confident the extractor is. |
| `ranked_results` | First graph results. |
| `final_results` | Final top candidates after reranking. |
| `error` | Error, if something went wrong. |

Important detail:

```python
messages: Annotated[list[Any], add_messages]
```

This means messages are appended, not overwritten.

## How The Pipeline Runs

The pipeline looks like this:

```text
extract
  -> resolve_file  -> translate/pass_protein -> rank -> rerank -> END
  -> use_raw       -> translate/pass_protein -> rank -> rerank -> END
```

Now each step.

## Step 1: `extract`

Goal:

```text
Understand what the user wrote.
```

For example:

```text
MALWMRLLPLLALLALWGPDPAAA...
Find insulin-like proteins.
```

`extract` should produce:

| What | Example |
| --- | --- |
| sequence | `MALWMRLLPLLALLALWGPDPAAA...` |
| context | `Find insulin-like proteins.` |
| input type | `SEQUENCE` |
| sequence type | `PROTEIN` |
| confidence | `true` or `false` |

There are two extraction modes.

### LLM Extraction

If LLM extraction is enabled, the agent uses OpenAI or Mistral.

The LLM receives a system prompt and must return structured output:

```python
InputExtraction
```

This is useful because an LLM can understand messy text better.

### Deterministic Extraction

If LLM is disabled, normal regex/rule logic is used:

- find FASTA;
- find filepath;
- find a long sequence-like token;
- inspect the alphabet;
- decide DNA vs protein;
- use hints like `protein`, `DNA`, `gene`, `peptide`.

This logic lives in:

```text
app/backend/app_services/retriever_pipeline.py
```

## Step 2: Choose A Branch

After `extract`, the agent checks:

```python
input_type == "FILEPATH" ?
```

If yes:

```text
extract -> resolve_file
```

If no:

```text
extract -> use_raw
```

## Step 3A: `resolve_file`

Runtime file resolution is currently disabled.

That means:

```text
The user cannot simply provide a filepath and expect the agent
to read that file during the request.
```

Why:

- current mode is DB-only;
- data must be loaded into Neo4j beforehand;
- runtime file search is not implemented.

So the node returns a controlled error:

```text
File resolution failed: Runtime file path resolution is disabled in DB-only graph mode.
```

## Step 3B: `use_raw`

If the input is a normal sequence, it must be cleaned.

For example:

- remove FASTA header;
- remove line breaks;
- uppercase letters;
- remove spaces;
- remove `-` and `*`.

After this, state contains:

```python
sequence
```

## Step 4: DNA Or Protein

After `resolve_file` or `use_raw`, the agent checks:

```python
sequence_type == "DNA" ?
```

If DNA:

```text
translate
```

If protein:

```text
pass_protein
```

## Step 5A: `translate`

If input was DNA, the agent translates DNA into protein sequence.

Example:

```text
ATGGCC...
```

becomes something like:

```text
MA...
```

If DNA length is not divisible by 3, there is an error:

```text
Translation failed: The coding sequence (CDS) length must be divisible by 3
```

## Step 5B: `pass_protein`

If input is already protein, no translation is needed.

The agent only normalizes the protein sequence and stores it in:

```python
protein_sequence
```

## Step 6: `rank`

This is the main Neo4j lookup.

The agent does not search for a similar new sequence at runtime.
It searches for an exact match by hash.

Roughly:

```text
protein_sequence
  -> normalize
  -> sha256 hash
  -> find Protein with that sequence_hash in Neo4j
```

For DNA, the path is a bit more complex:

```text
raw DNA sequence hash
  -> find Sequence node
  -> find Protein through ENCODES or TRANSLATES_TO
```

If a protein is found:

```text
find similar neighbors via SIMILAR_TO
```

If no protein is found:

```text
Ranking failed: Sequence is outside the prepared graph dataset; runtime ProtT5/FAISS search is disabled.
```

This is not a crash. It is an expected controlled miss.

## Step 7: `rerank`

At this step, the agent uses the user's text context.

Example:

```text
Find proteins related to glucose metabolism.
```

`GraphRetrievalService` gets candidates and calculates a simple lexical context score:

- which words are in the query;
- which words are in the protein description;
- how many words overlap.

This is not LLM reranking.
It is simple lexical sorting.

The target protein stays first.
Neighbors are sorted by:

- context score;
- similarity score;
- rank.

## Where Neo4j Is Used

The agent does not write Cypher directly.

It calls:

```python
GraphRetrievalService
```

This service is in:

```text
app/backend/app_services/graph_retrieval.py
```

Important methods:

| Method | What it does |
| --- | --- |
| `find_by_sequence_hash` | Finds protein by protein sequence hash. |
| `find_encoded_protein_by_sequence_hash` | For DNA, finds the protein encoded by the sequence. |
| `retrieve_candidates` | Gets target protein and similar neighbors. |
| `get_protein_view` | Gets one protein card. |
| `resolve_input` | Searches protein by accession/gene/name. Mostly used by the chat service. |

## What The Result Looks Like

After a successful run, `final_results` contains candidates.

A candidate usually looks like:

```json
{
  "protein": {
    "accession": "...",
    "name": "...",
    "gene": "...",
    "organism_scientific": "...",
    "function_text": "..."
  },
  "match_score": 1.0,
  "rank": 0,
  "similarity_score": 1.0,
  "context_score": 0.5,
  "evidence": []
}
```

These are UI-friendly data.

## What Gets Written To Memory

After `invoke`, the agent writes two kinds of data.

### Full LangGraph State

Saved automatically through:

```python
workflow.compile(checkpointer=persistence.checkpointer)
```

and:

```python
config = {"configurable": {"thread_id": context.session_id}}
```

So:

```text
user session_id = LangGraph thread_id
```

If Supabase/Postgres is enabled, state is saved in LangGraph Postgres tables.
Otherwise, state lives only in process memory.

### Compact Session Patch

The agent separately builds a short application record.

It is written to:

```text
public.chat_sessions
```

It contains:

| Field | Plain explanation |
| --- | --- |
| `session_summary` | Short summary of what was found. |
| `proteins` | Found top protein. |
| `sequences` | Input sequence. |
| `working_memory.last_retriever_state` | Short technical summary of the last run. |
| `active_sequence_id` | Id of the current sequence. |
| `active_accession` | Current accession. |
| `working_set_ids` | Object ids the user is currently working with. |
| `current_mode` | `bioseq_retriever_langgraph`. |
| `last_tool_results_summary` | Short result summary. |

## Running From CLI

Example:

```bash
python -m backend.agents_core.retriever_agent.main --message "MALWMRLLPLLALLALWGPDPAAA..."
```

Useful flags:

| Flag | What it does |
| --- | --- |
| `--message` | User message. |
| `--deterministic-extractor` | Use rules instead of LLM. |
| `--dump-history` | Show session message history. |
| `--session-id` | Explicitly set session id. |
| `--user-id` | Explicitly set user id. |
| `--supabase-db-url` | Explicitly pass Postgres/Supabase URL. |
| `--provider` | Select `openai` or `mistral`. |
| `--model` | Select extractor model. |

## What pipeline_interface.py Does

`pipeline_interface.py` is a simple helper for Python runs.

Main function:

```python
run_pipeline_interface(user_prompt)
```

It:

1. creates an agent through the factory;
2. creates `AppContext` from env variables;
3. runs `agent.invoke`;
4. prints a short summary;
5. returns the result.

LLM extraction is enabled only if:

```text
BIOSEQ_INPUT_EXTRACTOR=llm
```

## Main Limitations

| Limitation | Meaning |
| --- | --- |
| Runtime filepath is disabled | The agent cannot read a file path during the request. |
| Runtime FAISS/ProtT5 are disabled | The agent does not search for a new similar sequence at runtime. |
| Prepared graph is required | The sequence must already exist in Neo4j. |
| Store is barely used | `persistence.store` is created, but retriever does not directly read/write it. |
| `chat_sessions` must be created separately | LangGraph creates its own tables, but `public.chat_sessions` must already exist. |

## Simple Mental Model

Think of the agent as a conveyor belt:

```text
raw text
  -> extract sequence
  -> decide DNA/protein
  -> get protein sequence
  -> find exact protein in graph
  -> get similar neighbors
  -> sort by context
  -> save state
```

If at any step it becomes clear that the process cannot continue, the agent writes an error into `error` and finishes gracefully.


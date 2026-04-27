# Simple Graph Agent

This folder contains a minimal LangChain agent that talks to the project's Neo4j graph.

## What it does

The agent can:

- explain the graph schema
- find proteins by accession, gene, or name
- fetch nearest neighbors for a protein
- summarize diseases found among neighboring proteins
- run custom read-only Cypher queries

## Why tools matter

For this use case, tools are more important than separate "skills".

The agent learns how to query the database from:

- a system prompt that explains the graph schema
- tool descriptions that define safe operations
- a read-only Cypher tool with query validation

That is what helps the model choose the right query pattern.

## Install

Add the required packages from the project root:

```bash
./.venv/bin/pip install -r requirements.txt
```

## Run

Interactive mode:

```bash
./.venv/bin/python agents_core/simple_agent/main.py
```

Single-shot mode:

```bash
./.venv/bin/python agents_core/simple_agent/main.py \
  --message "Find neighbors for accession P13439 and summarize what functional class they suggest."
```

## Example prompts

```text
Find protein P13439 and show its 5 closest neighbors.
```

```text
For accession A0A668KLC8, look at the neighbors and tell me what functional class it may belong to.
```

```text
For accession P13439, are there any diseases shared across its neighbor set?
```

## Notes

- The agent uses the Neo4j credentials and `OPENAI_API_KEY` from the project `.env`.
- Disease results may be empty if the graph was loaded without disease annotations.
- The custom Cypher tool is read-only on purpose.

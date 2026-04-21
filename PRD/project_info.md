**BioSeq Investigator**

> **Status:** Draft
> 
> 
> **Last Updated:** April 14, 2026
> 

---

## **Table of Contents**

1. [Vision](https://file+.vscode-resource.vscode-cdn.net/Users/ilia_kustov/Documents/bioseq/PRD/main.md#1-vision)
2. [Goals](https://file+.vscode-resource.vscode-cdn.net/Users/ilia_kustov/Documents/bioseq/PRD/main.md#2-goals)
3. [Target Users](https://file+.vscode-resource.vscode-cdn.net/Users/ilia_kustov/Documents/bioseq/PRD/main.md#3-target-users)
4. [Recommended Development Path](https://file+.vscode-resource.vscode-cdn.net/Users/ilia_kustov/Documents/bioseq/PRD/main.md#4-recommended-development-path)
5. [Detailed Specifications](https://file+.vscode-resource.vscode-cdn.net/Users/ilia_kustov/Documents/bioseq/PRD/main.md#5-detailed-specifications)
    - [Part A — Search Assistant](https://file+.vscode-resource.vscode-cdn.net/Users/ilia_kustov/Documents/bioseq/PRD/main.md#part-a--search-assistant-first-step)
    - [Part B — Deeper Search Copilot (MVP)](https://file+.vscode-resource.vscode-cdn.net/Users/ilia_kustov/Documents/bioseq/PRD/main.md#part-b--deeper-search-copilot-our-mvp-agent)
    - [Part C — Evidence Checker & Aggregator](https://file+.vscode-resource.vscode-cdn.net/Users/ilia_kustov/Documents/bioseq/PRD/main.md#part-c--evidence-checker-and-several-points-of-view-aggregator-advanced-if-we-have-time-left)
6. [Out of Scope](https://file+.vscode-resource.vscode-cdn.net/Users/ilia_kustov/Documents/bioseq/PRD/main.md#6-out-of-scope)
7. [Why This Project Makes Sense](https://file+.vscode-resource.vscode-cdn.net/Users/ilia_kustov/Documents/bioseq/PRD/main.md#7-why-this-project-makes-sense)

---

## **1. Vision**

**Simple idea:** Paste a DNA or protein sequence, ask what it is, and get an evidence-based answer.

BioSeq Investigator is a tool that takes biological sequences as input and returns scientifically grounded insights about their identity, function, and context.

---

## **2. Goals**

- Choose a realistic scope that is **interesting**, **understandable**, and **achievable in 3 weeks**.
- Deliver a working prototype that demonstrates clear user value.
- Build a foundation that can be iterated on in future sprints.

---

## **3. Target Users**

| User Segment | Needs |
| --- | --- |
| **Students** learning bioinformatics or molecular biology | Quick, accessible way to understand unknown sequences |
| **Educators** who need teaching/demo tools | Reliable, explainable results for classroom use |
| **Non-expert researchers** or technical users working near biology | Low-barrier entry to sequence analysis without deep bioinformatics expertise |

---

## **4. Recommended Development Path**

| Stage | Scope | Objective |
| --- | --- | --- |
| **Stage 1** | Part A | Build the minimal working version; make the pipeline work end-to-end |
| **Stage 2** | Part B | Grow into the recommended final version: evidence grounding, follow-up Q&A, uncertainty handling |
| **Stage 3** | Part C | Stretch goals: literature support, session memory, evidence aggregation |

> **Recommendation:** Follow the path **A → B → C**, treating Part B as the target MVP.
> 

---

## **5. Detailed Specifications**

### **Part A — Search Assistant (First Step)**

**Concept:** A tool that tells the user what the sequence probably is.

### **Input**

```
DNA sequence:
AGCTTTTCACTTCT
```

### **Pipeline**

1. Validate FASTA input
2. Detect DNA vs. protein
3. Run BLAST and show top matches with data from open-source databases
4. Fetch basic metadata from NCBI and UniProt
5. Return a short summary with likely species and function

### **Output**

- Best *k* matches with comments
- Confidence indicators
- Basic metadata (species, function)

### **UX Feel**

> More like a helpful classroom demo than a search assistant.
> 

**Best for:** The safest and easiest version.

---

### **Part B — Deeper Search Copilot (Our MVP Agent)**

**Concept:** A tool that not only identifies the sequence, but also chats with the user and answers questions based on retrieved data.

### **What It Adds on Top of Part A**

| Feature | Description |
| --- | --- |
| **Source attribution** | Shows which claim comes from which source |
| **Uncertainty highlighting** | Calls out weak hits, missing data, or low-confidence results |
| **Follow-up Q&A** | Supports conversational questions over retrieved evidence |
| **Session continuity** | Reuses previous results instead of restarting from zero |

### **Example Interaction**

> **User:** *"What does low-confidence match mean here?"*
> 
> 
> **System:** LLM answers based on context from Part A research and produces a final answer.
> 

### **UX Feel**

> More like a small research copilot than a simple identifier.
> 

**Best for:** The best balance between ambition and realism.

---

### **Part C — Evidence Checker and Several Points of View Aggregator *(Advanced, if we have time left)***

**Concept:** A sequence-centered notebook that gathers evidence from databases and maybe a few papers.

### **What It Adds on Top of Part B**

| Feature | Description |
| --- | --- |
| **Evidence session pack** | Stores evidence as a reusable session knowledge pack |
| **PubMed integration** | May include relevant PubMed abstracts |
| **Cross-source analysis** | Summarizes agreement, disagreement, and uncertainty across sources |
| **Deeper iterative exploration** | Supports deeper research workflows |

### **UX Feel**

> More like a lightweight bioinformatics NotebookLM.
> 

**Best for:** A stretch version if the MVP is already solid.

---

## **6. Out of Scope**

The following are **explicitly excluded** from this project:

- ❌ Mutation pathogenicity claims
- ❌ Gene editing advice
- ❌ Medical or breeding recommendations

---

## **7. Why This Project Makes Sense**

| Criteria | Rationale |
| --- | --- |
| **Clear user value** | Solves a real, frequent need for students and researchers |
| **Strong AI agent / workflow story** | Combines database search, LLM reasoning, and evidence grounding |
| **Realistic scope for limited bioinformatics background** | Relies on established databases (NCBI, UniProt) rather than custom algorithms |
| **Easy to demo and evaluate** | Clear input → output flow that's intuitive to show and assess |
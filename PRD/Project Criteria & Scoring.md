# Final Project — Criteria & Scoring Overview

The final project is a collaborative team assignment where students design, implement, and present an LLM-based application. The project should demonstrate practical application of the core course topics.

The application should be:
- LLM-based: an LLM under the hood is a core component of the system.
- Thoughtfully designed: a clean architecture that consists of several components with clearly defined functionality (as opposed to a single script with “spaghetti code”).
- Testable: the team should be able to demonstrate that it works correctly and evaluate its accuracy.
- User-friendly: a person unfamiliar with the code should be able to interact with the app through a user interface.
- Meaningful: The project should solve a real, meaningful task — something that would be genuinely useful to at least one person (e.g. you).

It does not have to be a cutting-edge or commercially viable product, but there should be a clear answer to “who would use this and why.”

Each team will be assigned a dedicated mentor who will support the project throughout the course.

There is one mandatory checkpoint midway through the project timeline (see Checkpoint section).

- May 14, 23:59 — Report submission deadline (Checkpoint)
- May 20, 23:59 — Code submission deadline
- May 23, 10:00 — Project presentations

## Score Summary

| Criteria | Max Score |
|---|---|
| Technical Implementation | 20 |
| Interface | 10 |
| Presentation | 20 |
| **TOTAL** | **50** |

## 1. Technical Implementation — 20 points

This is the primary signal of project quality. Evaluators look at whether the system is thoughtfully designed and whether it actually works as intended. A project with a polished interface but a weak core should score low here.

Evaluated based on the checkpoint report, mentor feedback, and the defense discussion.

### Criteria

- The solution approach is well-chosen and justified for the task: the system makes meaningful use of RAG, an agent, workflow, etc. The complexity of the implementation matches the complexity of the problem. — 0–5
- Architecture is well-designed: components are clearly separated, their interactions make sense, and there are no obvious structural flaws. — 0–4
- The system works end-to-end and produces meaningful outputs for the intended task. — 0–4
- Quality is measured: a validation set exists, metrics are reported and interpreted. — 0–4
- Unit and integration tests: meaningful coverage of the codebase. — 0–2
- Logging: inputs and outputs of LLM-based components. — 0–1
- **Bonus:** Deployment: The system is deployed to the cloud. — 0–2*

## 2. Interface — 10 points

A functional interface is required, but it is not graded strictly on aesthetics or polish. What matters is that the interface works correctly, handles realistic inputs, and is easy enough for a new user to understand without guidance.

Evaluated based on a live demo during the defense or screenshots / screen recording.

### Criteria

- An interface exists and is connected to the full pipeline (web app, Telegram bot, or equivalent). — 0–4
- The interface handles requests correctly and includes basic error handling. — 0–3
- The interface is reasonably easy to use — a new user can understand what to do without guidance. — 0–3

> Note: Aesthetics and visual design are not evaluated. A minimal but fully functional interface is sufficient for full marks.

## 3. Presentation — 20 points

The project will be evaluated during the final presentation (8–10 minutes for the pitch + questions). It rewards clarity, honesty, and the ability to explain what was built and why decisions were made.

A team that clearly explains trade-offs and limitations should score higher than one that oversells a weaker system.

The presentation should cover:
- the problem and motivation,
- the system architecture,
- a live demo or recording,
- evaluation results,
- individual team contributions.

### Criteria

- The problem and motivation are clearly stated — it is obvious what the system does and why. — 0–4
- Architecture and key design decisions are explained clearly. — 0–4
- A live demo or recording / screenshots is shown and the system visibly works. — 0–4
- Evaluation results are presented: metrics, examples, and an honest assessment of limitations. — 0–4
- The presentation is delivered clearly and confidently. Slides are readable and well-structured. Questions from the audience and mentor are answered thoughtfully. — 0–4

> Note: The presentation can be delivered by one person or the full team. What matters is that all members can speak to their own part of the project if asked.

## Checkpoint (mandatory)

There is one checkpoint roughly halfway through the project timeline. Its purpose is to confirm that the project is on track — not to formally grade intermediate work.

The checkpoint is not a call or presentation; it is a written report submitted to the course team.

### Report submission deadline

- May 14, 23:59

### What should be ready by this point

- Data sources are identified and accessible; data is collected and stored.
- Basic preprocessing or chunking is completed if needed.
- The main pipeline (RAG / agent / workflow / …) is implemented or substantially in progress.
- A validation set is prepared or described, and the team has a clear plan for measuring quality.
- Preliminary metrics or test results are reported if available.

### What the report should contain

- Brief description of the project and its current status.
- Description of the data: sources, volume, storage format, preprocessing steps.
- Description of the pipeline architecture and how the components interact.
- Evaluation approach: what metrics will be used and on what data.
- Preliminary results if available, or a description of what has been tested so far.
- Any open questions or blockers.

Submit the report as a document via the form (will be shared). It should be concise — the main goal is to clearly address all aforementioned items.

## Code submission

The code should be uploaded to a GitHub repository (private or public) and submitted for review prior to the presentation.

Please don’t forget to include `README.md` explaining principal application components, how to run it (for a developer), and how to use it (for a user).

### Code submission deadline

- May 20, 23:59

You can modify the code after the submission — however, it might not affect the score of technical implementation and interface.

Also, leave enough time to test the code — and avoid the typical mistake of introducing last-minute changes that break the code.

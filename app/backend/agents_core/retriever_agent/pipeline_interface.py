from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.agents_core.session_agent.models import AppContext
from backend.app_services.service_factory import create_bioseq_retriever_graph_agent


DEFAULT_DB_ACCESSION = "A0A075B5I0"
DEFAULT_USER_PROMPT = "SLTIIVSSVLRCQASELHVGCVGGCVYSKCGLAFELLIVGNTTIFRMNSSNPFQSLLQTLLHPLNFIVIKIVARSLAGQLHLSPRYYQLWSRLLKLDL"


def run_pipeline_interface(user_prompt: str = DEFAULT_USER_PROMPT):
    """
    Interface to run the DB-only LangGraph retriever agent with a custom prompt.
    Returns the final GraphState dictionary.
    """
    use_llm_extractor = os.getenv("BIOSEQ_INPUT_EXTRACTOR", "deterministic").lower() == "llm"
    agent = create_bioseq_retriever_graph_agent(use_llm_extractor=use_llm_extractor)
    context = AppContext(
        user_id=os.getenv("APP_USER_ID", "local-user"),
        session_id=os.getenv("APP_SESSION_ID", f"retriever_{uuid.uuid4().hex[:8]}"),
        workspace_id=os.getenv("APP_WORKSPACE_ID"),
        user_role=os.getenv("APP_USER_ROLE"),
    )

    print(f"Executing DB-only retriever graph for prompt: {user_prompt[:80]}...")
    result, _session_state = agent.invoke(user_prompt, context)

    if result.get("error"):
        print(f"Pipeline Error: {result['error']}")

    return result


def main() -> None:
    print("--- BioSeq Investigator: DB-only LangGraph Retriever ---")
    print(f"DB smoke target accession: {DEFAULT_DB_ACCESSION}")
    print(f"User Prompt: {DEFAULT_USER_PROMPT}\n")

    try:
        result = run_pipeline_interface(DEFAULT_USER_PROMPT)

        if result.get("error"):
            return

        print("\n--- Pipeline Summary ---")
        print(f"Detected Type: {result.get('sequence_type')}")
        print(f"Classification Confidence: {result.get('is_confident')}")
        print(f"Protein Sequence Length: {len(result.get('protein_sequence') or '')}")

        print("\n--- Top 5 Context-Aware Matches (Graph CandidateView JSON) ---")
        output = {
            "classification_confident": result.get("is_confident"),
            "top_matches": result.get("final_results") or [],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))

        print("\n--- Quick View ---")
        for index, record in enumerate(result.get("final_results") or [], 1):
            protein = record.get("protein") or {}
            accession = protein.get("accession")
            name = protein.get("name", "N/A")
            gene = protein.get("gene", "")
            print(f"{index}. [{accession}] {name} {f'({gene})' if gene else ''}")

    except Exception as exc:
        print(f"Critical Failure: {exc}")


if __name__ == "__main__":
    main()

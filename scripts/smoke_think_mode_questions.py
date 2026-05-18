"""Smoke test for Think Mode suggested questions over a demo chat.

The script does three things:

1. Sends the built-in UNC5C demo sequence/question through the retriever turn.
2. Simulates several follow-up chat questions in the same session.
3. Verifies every Think Mode-enabled successful turn returns exactly three
   suggested questions.

By default the first turn attempts the real ``BioSeqRetrieverPipeline``. If the
local retriever/search-service/artifacts are unavailable, it falls back to a
small fixture retriever so the Think Mode wiring can still be checked offline.
Use ``--strict-real`` to fail instead of falling back.

Usage:
    python3 scripts/smoke_think_mode_questions.py
    python3 scripts/smoke_think_mode_questions.py --strict-real
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "app"
FRONTEND_ROOT = APP_ROOT / "frontend"
for path in (APP_ROOT, FRONTEND_ROOT, PROJECT_ROOT):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.store.memory import InMemoryStore  # noqa: E402

from backend.agents_core.retriever_agent.runtime_agent import BioSeqRuntimeSessionAgent  # noqa: E402
from backend.agents_core.shared.config import DEFAULT_ENV_PATH, load_env_file  # noqa: E402
from backend.agents_core.shared.models import AppContext, PersistenceResources  # noqa: E402
from backend.app_contracts import BioSeqPipelineSnapshot, CandidateView, ChatTurnRequest  # noqa: E402
from backend.app_contracts.protein_view import DomainFeature, ProteinView  # noqa: E402
from backend.app_services.bioseq_chat import BioSeqChatService  # noqa: E402
from backend.app_services.chat_llm import ChatLLMResponse  # noqa: E402
from backend.app_services.retriever_pipeline import BioSeqRetrieverPipeline  # noqa: E402
from backend.app_services.suggested_questions import SuggestedQuestionsResponse  # noqa: E402
from mock.conversation import example_first_message  # noqa: E402


FOLLOW_UP_PROMPTS = [
    "Explain the most important domains in simple language.",
    "What disease evidence is most relevant here?",
    "Which pathway or interaction should I inspect next?",
]


class MemoryRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.rows.get(session_id)
        return dict(row) if row else None

    def upsert_session(self, context: AppContext, state: dict[str, Any]) -> None:
        self.rows[context.session_id] = dict(state)

    def list_sessions(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return [
            {"session_id": session_id, **row}
            for session_id, row in list(self.rows.items())[:limit]
            if row.get("user_id") == user_id or user_id
        ]

    def close(self) -> None:
        return None


class StubChatLLMService:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def generate(self, request) -> ChatLLMResponse:  # noqa: ANN001
        self.calls.append(request)
        accession = ((request.selected_candidate or {}).get("protein") or {}).get("accession", "the protein")
        return ChatLLMResponse(
            reply=f"Stub follow-up answer grounded in {accession}.",
            provider="stub_chat_llm",
            model="stub-chat-model",
            raw={"mode": "chat_llm", "provider": "stub_chat_llm"},
        )


class StubSuggestedQuestionsService:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def generate(self, request) -> SuggestedQuestionsResponse:  # noqa: ANN001
        self.calls.append(request)
        protein = (request.selected_candidate or {}).get("protein") or {}
        accession = protein.get("accession") or "this protein"
        gene = protein.get("gene") or protein.get("gene_primary") or accession
        return SuggestedQuestionsResponse(
            questions=[
                f"What does {gene} do at the domain level?",
                f"How strong is the evidence for {accession}?",
                f"Which pathway or interaction should we inspect next for {gene}?",
            ],
            provider="stub_think",
            model="stub-think-model",
            raw={"mode": "think_mode", "provider": "stub_think"},
        )


class FixtureRetriever:
    def run(self, message: str, *, limit: int = 5, search_algorithm: str | None = None):
        if "Sequence:" not in message and "MRKGLRATAA" not in message:
            return BioSeqPipelineSnapshot(
                prompt=message,
                input_type="TEXT",
                context=message,
            ), []

        candidate = fixture_candidate()
        snapshot = BioSeqPipelineSnapshot(
            prompt=message,
            input_type="SEQUENCE",
            sequence_type="PROTEIN",
            sequence=candidate.protein.sequence,
            protein_sequence=candidate.protein.sequence,
            active_accession=candidate.protein.accession,
            context="Fixture fallback for local Think Mode smoke test.",
            warnings=["Fixture retriever fallback used; real runtime retriever was unavailable."],
        )
        return snapshot, [candidate]


def fixture_candidate() -> CandidateView:
    protein = ProteinView(
        accession="O95185",
        name="Netrin receptor UNC5C",
        gene="UNC5C",
        organism_scientific="Homo sapiens",
        organism_common="human",
        reviewed=True,
        length=931,
        mol_weight=103146,
        function_text=(
            "Receptor for netrin required for axon guidance. Mediates axon "
            "repulsion and can act as a dependence receptor involved in apoptosis."
        ),
        domains=[
            DomainFeature(type="Domain", name="Ig-like", start=62, end=159),
            DomainFeature(type="Domain", name="TSP type-1", start=256, end=313),
            DomainFeature(type="Domain", name="Death", start=842, end=926),
        ],
        keywords=["Receptor", "Axon guidance", "Apoptosis"],
        go_terms=["GO:0005042", "GO:0007411", "GO:0006915"],
        pubmed_ids=["25419706", "27068745", "28483977"],
        alphafold_accession="O95185",
        sequence="MRKGLRATAARCGLGLGYLLQMLVLPALALLSASGTGSAAQDDDFFHELPETFPSDPPEPLPHFLIEPEEAYIVKNKPVNLY",
    )
    return CandidateView(protein=protein, match_score=98.7, rank=0, similarity_score=0.987)


def build_agent() -> tuple[BioSeqRuntimeSessionAgent, MemoryRepo]:
    repo = MemoryRepo()
    resources = PersistenceResources(
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        session_repository=repo,
        mode="memory",
        warnings=[],
    )
    return BioSeqRuntimeSessionAgent(resources), repo


def build_service(agent: BioSeqRuntimeSessionAgent, retriever_pipeline) -> tuple[BioSeqChatService, StubChatLLMService, StubSuggestedQuestionsService]:
    chat_llm = StubChatLLMService()
    suggested = StubSuggestedQuestionsService()
    return (
        BioSeqChatService(
            agent=agent,
            retriever_pipeline=retriever_pipeline,
            chat_llm_service=chat_llm,
            suggested_questions_service=suggested,
        ),
        chat_llm,
        suggested,
    )


def main() -> int:
    args = parse_args()
    load_env_file(DEFAULT_ENV_PATH)

    session_id = f"smoke_think_{uuid.uuid4().hex[:8]}"
    user_id = f"smoke_user_{uuid.uuid4().hex[:8]}"
    context = AppContext(user_id=user_id, session_id=session_id)
    agent, repo = build_agent()

    print("--- Think Mode smoke ---")
    print(f"session_id: {session_id}")
    print(f"user_id:    {user_id}")
    print(f"strict_real: {args.strict_real}")

    service, chat_llm, suggested = build_service(
        agent,
        BioSeqRetrieverPipeline(enable_runtime_retriever=True),
    )

    demo_message = example_first_message()
    history: list[dict[str, str]] = [{"role": "user", "content": demo_message}]
    print("\n[1] Retriever turn with demo UNC5C prompt")
    first = service.submit_turn(
        ChatTurnRequest(
            message=demo_message,
            session_id=session_id,
            user_id=user_id,
            think_mode=True,
            ui_context={"turn_count": 0, "messages": history},
        )
    )

    if first.candidates:
        print(f"    [ok] real retriever returned {len(first.candidates)} candidate(s)")
    else:
        print("    [warn] real retriever did not return candidates")
        print(f"           reply: {first.assistant_message}")
        print(f"           warnings: {first.warnings}")
        if args.strict_real:
            print("FAIL: strict real retriever mode requested.")
            return 1
        print("    [fallback] switching to fixture retriever for Think Mode chat simulation")
        agent, repo = build_agent()
        context = AppContext(user_id=user_id, session_id=session_id)
        service, chat_llm, suggested = build_service(agent, FixtureRetriever())
        first = service.submit_turn(
            ChatTurnRequest(
                message=demo_message,
                session_id=session_id,
                user_id=user_id,
                think_mode=True,
                ui_context={"turn_count": 0, "messages": history},
            )
        )

    assert first.update_card is True
    assert first.candidates, "retriever/fixture turn must return candidates"
    assert_three_questions(first.suggested_questions, label="retriever turn")
    top_candidate = first.candidates[0].model_dump()
    print(f"    top accession: {first.candidates[0].protein.accession}")
    print_questions(first.suggested_questions)

    history.append({"role": "assistant", "content": first.assistant_message})

    for index, prompt in enumerate(FOLLOW_UP_PROMPTS, start=2):
        print(f"\n[{index}] Follow-up chat turn: {prompt}")
        history.append({"role": "user", "content": prompt})
        result = service.submit_turn(
            ChatTurnRequest(
                message=prompt,
                session_id=session_id,
                user_id=user_id,
                think_mode=True,
                ui_context={
                    "turn_count": index - 1,
                    "messages": list(history),
                    "selected_candidate": top_candidate,
                    "selected_candidate_index": 0,
                },
            )
        )
        assert result.update_card is False, "follow-up should preserve the protein card"
        assert result.current_mode == "chat_llm", result.current_mode
        assert_three_questions(result.suggested_questions, label=f"follow-up {index - 1}")
        print(f"    reply: {result.assistant_message}")
        print_questions(result.suggested_questions)
        history.append({"role": "assistant", "content": result.assistant_message})

    state = agent.get_current_state(context)
    row = repo.get_session(session_id) or {}
    print("\n[done] Checks")
    print(f"    chat LLM calls:        {len(chat_llm.calls)}")
    print(f"    think agent calls:     {len(suggested.calls)}")
    print(f"    agent active_accession:{state.get('active_accession')}")
    print(f"    repo row current_mode: {row.get('current_mode')}")
    assert len(chat_llm.calls) == len(FOLLOW_UP_PROMPTS)
    assert len(suggested.calls) == len(FOLLOW_UP_PROMPTS) + 1
    assert state.get("active_accession") == top_candidate["protein"]["accession"]
    print("\nALL CHECKS PASSED")
    return 0


def assert_three_questions(questions: list[str], *, label: str) -> None:
    assert len(questions) == 3, f"{label}: expected 3 questions, got {questions!r}"
    assert all(q.strip() for q in questions), f"{label}: questions must be non-empty"
    assert len({q.lower() for q in questions}) == 3, f"{label}: questions must be unique"


def print_questions(questions: list[str]) -> None:
    for idx, question in enumerate(questions, start=1):
        print(f"    q{idx}: {question}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict-real",
        action="store_true",
        help="Fail if the real retriever does not return candidates instead of using fixture fallback.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())

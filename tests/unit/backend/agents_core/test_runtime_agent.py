from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from backend.agents_core.retriever_agent.runtime_agent import BioSeqRuntimeSessionAgent
from backend.agents_core.shared.models import AppContext, PersistenceResources


class MemoryRepo:
    def __init__(self) -> None:
        self.rows = {}

    def get_session(self, session_id):
        return self.rows.get(session_id)

    def upsert_session(self, context, state):
        self.rows[context.session_id] = dict(state)


def agent_with_repo() -> tuple[BioSeqRuntimeSessionAgent, MemoryRepo]:
    repo = MemoryRepo()
    resources = PersistenceResources(
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        session_repository=repo,
        mode="memory",
        warnings=[],
    )
    return BioSeqRuntimeSessionAgent(resources), repo


def test_runtime_agent_keeps_thread_state_separate() -> None:
    agent, repo = agent_with_repo()
    c1 = AppContext(user_id="u1", session_id="s1")
    c2 = AppContext(user_id="u1", session_id="s2")

    agent.update_current_state(c1, {"active_accession": "O95185"})
    agent.update_current_state(c2, {"active_accession": "Q761X5"})

    assert agent.get_current_state(c1)["active_accession"] == "O95185"
    assert agent.get_current_state(c2)["active_accession"] == "Q761X5"
    assert repo.rows["s1"]["active_accession"] == "O95185"
    assert repo.rows["s2"]["active_accession"] == "Q761X5"

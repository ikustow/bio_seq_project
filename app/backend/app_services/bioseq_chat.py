from __future__ import annotations

import uuid
from typing import Any, Protocol

from backend.agents_core.shared.models import AppContext
from backend.agents_core.shared.services.session_state import get_message_text, serialize_message
from backend.app_contracts import BioSeqPipelineSnapshot, CandidateView, ChatTurnRequest, ChatTurnResult, ProteinView, SessionSnapshot

from .graph_retrieval import GraphRetrievalService
from .retriever_pipeline import BioSeqRetrieverPipeline

class SessionGraphAgent(Protocol):
    @property
    def warnings(self) -> list[str]: ...

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def get_current_state(self, context: AppContext) -> dict[str, Any]: ...

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]: ...


class BioSeqChatService:
    def __init__(
        self,
        agent: "SessionGraphAgent",
        graph_retrieval: GraphRetrievalService,
        retriever_pipeline: BioSeqRetrieverPipeline | None = None,
    ) -> None:
        self._agent = agent
        self._graph_retrieval = graph_retrieval
        self._retriever_pipeline = retriever_pipeline or BioSeqRetrieverPipeline(graph_retrieval)

    def submit_turn(self, request: ChatTurnRequest) -> ChatTurnResult:
        context = _context_from_request(request)
        warnings = list(self._agent.warnings)
        selected_index = request.selected_candidate_index or 0

        if request.selected_accession:
            state = self._agent.update_current_state(context, {"active_accession": request.selected_accession})
            candidates, retrieval_warnings = self._safe_candidates(request.selected_accession)
            warnings.extend(retrieval_warnings)
            return ChatTurnResult(
                session_id=request.session_id,
                assistant_message=f"Selected {request.selected_accession}.",
                candidates=candidates,
                selected_candidate_index=selected_index,
                revealed_sections=_revealed_sections(candidates),
                session=self._snapshot(context, state),
                warnings=warnings,
            )

        pipeline, pipeline_candidates = self._retriever_pipeline.run(request.message, limit=5)
        warnings.extend(pipeline.warnings)
        if pipeline.input_type in {"SEQUENCE", "FILEPATH"}:
            state_patch: dict[str, Any] = {
                "current_mode": "graph_retriever_pipeline",
                "working_memory": {
                    "last_pipeline": pipeline.model_dump(),
                    "last_sync_source": "bioseq_retriever_compat",
                },
            }
            if pipeline.sequence:
                state_patch["active_sequence_id"] = f"seq_{uuid.uuid5(uuid.NAMESPACE_OID, pipeline.sequence).hex[:12]}"
            if pipeline.active_accession:
                state_patch["active_accession"] = pipeline.active_accession
            state = self._agent.update_current_state(context, state_patch)

            if pipeline.error or pipeline.controlled_miss:
                assistant_message = _pipeline_miss_message(pipeline)
                return ChatTurnResult(
                    session_id=request.session_id,
                    assistant_message=assistant_message,
                    candidates=[],
                    selected_candidate_index=selected_index,
                    revealed_sections=set(),
                    session=self._snapshot(context, state),
                    pipeline=pipeline,
                    warnings=warnings,
                )

            if pipeline_candidates:
                return ChatTurnResult(
                    session_id=request.session_id,
                    assistant_message=_pipeline_hit_message(pipeline),
                    candidates=pipeline_candidates,
                    selected_candidate_index=selected_index,
                    revealed_sections=_revealed_sections(pipeline_candidates),
                    session=self._snapshot(context, state),
                    pipeline=pipeline,
                    warnings=warnings,
                )

        result, state = self._agent.invoke(request.message, context)
        assistant_message = _assistant_message(result)

        active_accession = request.selected_accession or state.get("active_accession")
        if not active_accession:
            hits = self._graph_retrieval.resolve_input(request.message, limit=1)
            active_accession = hits[0].accession if hits else None
            if active_accession:
                state = self._agent.update_current_state(context, {"active_accession": active_accession})

        candidates: list[CandidateView] = []
        if active_accession:
            candidates, retrieval_warnings = self._safe_candidates(active_accession)
            warnings.extend(retrieval_warnings)

        return ChatTurnResult(
            session_id=request.session_id,
            assistant_message=assistant_message,
            candidates=candidates,
            selected_candidate_index=selected_index,
            revealed_sections=_revealed_sections(candidates),
            session=self._snapshot(context, state),
            pipeline=pipeline,
            warnings=warnings,
        )

    def get_session(self, session_id: str, user_id: str = "anonymous") -> SessionSnapshot:
        context = AppContext(user_id=user_id, session_id=session_id)
        return self._snapshot(context, self._agent.get_current_state(context))

    def _safe_candidates(self, accession: str) -> tuple[list[CandidateView], list[str]]:
        try:
            return self._graph_retrieval.retrieve_candidates(accession, limit=5), []
        except Exception as exc:
            return [], [f"Could not retrieve graph candidates for {accession}: {exc}"]

    def _snapshot(self, context: AppContext, state: dict[str, Any]) -> SessionSnapshot:
        return SessionSnapshot(
            session_id=context.session_id,
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            user_role=context.user_role,
            active_accession=state.get("active_accession"),
            active_sequence_id=state.get("active_sequence_id"),
            current_mode=state.get("current_mode"),
            proteins=_model_dump_list(state.get("proteins", [])),
            sequences=_model_dump_list(state.get("sequences", [])),
            working_memory=dict(state.get("working_memory") or {}),
            message_history=[serialize_message(message) for message in state.get("messages", [])],
        )


class MockBioSeqChatService:
    def submit_turn(self, request: ChatTurnRequest) -> ChatTurnResult:
        protein = ProteinView(
            accession="P69905",
            name="Hemoglobin subunit alpha",
            gene="HBA1",
            organism_scientific="Homo sapiens",
            annotation_score=5.0,
            reviewed=True,
            length=142,
            function_text="Mock protein card for UI development without Neo4j.",
            keywords=["oxygen transport", "mock"],
            go_terms=["GO:0015671"],
            pubmed_ids=["6726807"],
            alphafold_accession="AF-P69905-F1",
            sequence="MVLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHF",
        )
        candidate = CandidateView(protein=protein, match_score=1.0, rank=0, similarity_score=1.0)
        snapshot = SessionSnapshot(
            session_id=request.session_id,
            user_id=request.user_id,
            workspace_id=request.workspace_id,
            user_role=request.user_role,
            active_accession=protein.accession,
            current_mode="mock",
            message_history=[
                {"role": "user", "content": request.message},
                {"role": "assistant", "content": "Mock mode is active; this response does not query Neo4j."},
            ],
        )
        return ChatTurnResult(
            session_id=request.session_id,
            assistant_message="Mock mode is active; showing a scripted protein card while the graph backend is offline.",
            candidates=[candidate],
            revealed_sections={"overview", "function", "evidence"},
            session=snapshot,
            pipeline=BioSeqPipelineSnapshot(prompt=request.message, input_type="TEXT", context=request.message),
            warnings=["BIOSEQ_BACKEND=mock: no graph queries were executed."],
        )

    def get_session(self, session_id: str, user_id: str = "anonymous") -> SessionSnapshot:
        return SessionSnapshot(session_id=session_id, user_id=user_id, current_mode="mock")


def _context_from_request(request: ChatTurnRequest) -> AppContext:
    return AppContext(
        user_id=request.user_id,
        session_id=request.session_id,
        workspace_id=request.workspace_id,
        user_role=request.user_role,
    )


def _assistant_message(result: dict[str, Any]) -> str:
    messages = result.get("messages") or []
    if not messages:
        return ""
    return get_message_text(messages[-1])


def _model_dump_list(items: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in items or []:
        if hasattr(item, "model_dump"):
            output.append(item.model_dump())
        elif isinstance(item, dict):
            output.append(dict(item))
    return output


def _revealed_sections(candidates: list[CandidateView]) -> set[str]:
    """Return the set of protein-card sections the UI should unlock.

    Section keys must match the frontend's ``protein_card._ALL_SECTIONS``:
    ``header / keyfacts / function / domains / structure / keywords /
    disease / references``. Header / keyfacts / structure are revealed
    whenever there is *any* candidate to show; the rest gate on actual
    data being present so the card doesn't pretend a section is filled.
    """
    if not candidates:
        return set()
    sections = {"header", "keyfacts", "structure"}
    protein = candidates[0].protein
    if protein.function_text:
        sections.add("function")
    if protein.domains:
        sections.add("domains")
    if protein.keywords or protein.go_terms:
        sections.add("keywords")
    if protein.disease:
        sections.add("disease")
    if protein.pubmed_ids or protein.xrefs:
        sections.add("references")
    return sections


def _pipeline_hit_message(pipeline: BioSeqPipelineSnapshot) -> str:
    source = "DNA sequence" if pipeline.sequence_type == "DNA" else "protein sequence"
    if pipeline.sequence_type == "DNA":
        return (
            f"I classified the input as DNA, translated it to a protein sequence, "
            f"and found prepared graph accession {pipeline.active_accession}."
        )
    return f"I classified the input as {source} and found prepared graph accession {pipeline.active_accession}."


def _pipeline_miss_message(pipeline: BioSeqPipelineSnapshot) -> str:
    if pipeline.input_type == "FILEPATH":
        return (
            "I detected a file path, but graph runtime does not read server-side paths. "
            "Add that sequence through the offline ingestion pipeline first."
        )
    if pipeline.error:
        return f"I could not process this sequence in graph-first runtime: {pipeline.error}"
    return (
        "I classified the input sequence, but it is outside the prepared graph dataset. "
        "Runtime ProtT5/FAISS search is disabled here; add the sequence to offline ingestion first."
    )

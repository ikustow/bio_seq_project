from __future__ import annotations

import uuid
from typing import Any, Protocol

from backend.agents_core.shared.models import AppContext
from backend.agents_core.shared.services.session_state import get_message_text, serialize_message
from backend.app_contracts import BioSeqPipelineSnapshot, CandidateView, ChatTurnRequest, ChatTurnResult, ProteinView, SessionSnapshot

from .chat_llm import ChatLLMRequest, ChatLLMService
from .retriever_pipeline import BioSeqRetrieverPipeline

class SessionAgent(Protocol):
    @property
    def warnings(self) -> list[str]: ...

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def get_current_state(self, context: AppContext) -> dict[str, Any]: ...

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]: ...


class BioSeqChatService:
    def __init__(
        self,
        agent: "SessionAgent",
        retriever_pipeline: BioSeqRetrieverPipeline | None = None,
        chat_llm_service: ChatLLMService | None = None,
    ) -> None:
        self._agent = agent
        self._retriever_pipeline = retriever_pipeline or BioSeqRetrieverPipeline()
        # Optional: when None the service falls back to the agent stub for
        # text turns (legacy behaviour). Frontend signals follow-up turns via
        # ``ui_context.turn_count`` so the backend can route to chat-LLM.
        self._chat_llm_service = chat_llm_service

    def submit_turn(self, request: ChatTurnRequest) -> ChatTurnResult:
        context = _context_from_request(request)
        warnings = list(self._agent.warnings)
        selected_index = request.selected_candidate_index or 0

        if request.selected_accession:
            state = self._agent.update_current_state(context, {"active_accession": request.selected_accession})
            candidates = _candidates_from_state(state)
            if not candidates:
                warnings.append(f"Candidate reload for {request.selected_accession} requires a fresh bioseq_retriever search.")
            return ChatTurnResult(
                session_id=request.session_id,
                assistant_message=f"Selected {request.selected_accession}.",
                candidates=candidates,
                selected_candidate_index=selected_index,
                revealed_sections=_revealed_sections(candidates),
                session=self._snapshot(context, state),
                warnings=warnings,
            )

        pipeline, pipeline_candidates = self._retriever_pipeline.run(
            request.message, limit=5, search_algorithm=request.search_algorithm
        )
        warnings.extend(pipeline.warnings)
        if pipeline.input_type in {"SEQUENCE", "FILEPATH"}:
            active_sequence_id = _sequence_id(pipeline.sequence) if pipeline.sequence else None
            state_patch: dict[str, Any] = {
                "current_mode": "bioseq_retriever_pipeline",
                "working_memory": {
                    "last_pipeline": pipeline.model_dump(),
                    "last_sync_source": "bioseq_retriever_compat",
                },
            }
            if active_sequence_id:
                state_patch["active_sequence_id"] = active_sequence_id
            if pipeline.active_accession:
                state_patch["active_accession"] = pipeline.active_accession
            if pipeline_candidates:
                state_patch["proteins"] = _protein_records(pipeline_candidates)
                state_patch["sequences"] = _sequence_records(pipeline, active_sequence_id)
                state_patch["working_memory"]["last_candidates"] = [
                    candidate.model_dump() for candidate in pipeline_candidates
                ]
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

        # Follow-up turn: TEXT input within an existing session. Frontend
        # advertises turn_count via ui_context; when there is already a
        # retriever turn on the books we route to Chat-LLM instead of the
        # agent stub so the right-hand protein card stays intact.
        if self._chat_llm_service is not None and _is_follow_up_turn(request):
            return self._handle_follow_up(request, context, pipeline, selected_index, warnings)

        result, state = self._agent.invoke(request.message, context)
        assistant_message = _assistant_message(result)

        active_accession = request.selected_accession or state.get("active_accession")

        candidates: list[CandidateView] = []
        if active_accession:
            candidates = _candidates_from_state(state)
            if not candidates:
                warnings.append(f"Candidate reload for {active_accession} requires a fresh bioseq_retriever search.")

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

    def _handle_follow_up(
        self,
        request: ChatTurnRequest,
        context: AppContext,
        pipeline: BioSeqPipelineSnapshot,
        selected_index: int,
        warnings: list[str],
    ) -> ChatTurnResult:
        ui_context = request.ui_context or {}
        history = _history_from_ui_context(ui_context)
        selected_candidate = _selected_candidate_from_ui_context(ui_context, selected_index)

        provider: str | None = None
        provider_model: str | None = None
        metadata: dict[str, Any] = {}
        current_mode = "chat_llm"
        try:
            response = self._chat_llm_service.generate(  # type: ignore[union-attr]
                ChatLLMRequest(
                    prompt=request.message,
                    history=history,
                    selected_candidate=selected_candidate,
                )
            )
            assistant_message = response.reply
            provider = response.provider
            provider_model = response.model
            metadata = dict(response.raw)
            current_mode = str(metadata.get("mode") or "chat_llm")
        except Exception as exc:
            assistant_message = f"**Chat LLM error:** {exc}"
            current_mode = "chat_llm_error"
            metadata = {"error": str(exc)}
            warnings.append(str(exc))

        # Reuse the existing candidate cards from agent state so the snapshot
        # we return reflects what the user is still looking at. Frontend will
        # ignore ``candidates`` because ``update_card=False``.
        state = self._agent.get_current_state(context)
        candidates = _candidates_from_state(state)

        return ChatTurnResult(
            session_id=request.session_id,
            assistant_message=assistant_message,
            candidates=candidates,
            selected_candidate_index=selected_index,
            revealed_sections=_revealed_sections(candidates),
            session=self._snapshot(context, state),
            pipeline=pipeline,
            warnings=warnings,
            update_card=False,
            current_mode=current_mode,
            provider=provider,
            provider_model=provider_model,
            metadata=metadata,
        )

    def get_session(self, session_id: str, user_id: str = "anonymous") -> SessionSnapshot:
        context = AppContext(user_id=user_id, session_id=session_id)
        return self._snapshot(context, self._agent.get_current_state(context))

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
            function_text="Mock protein card for UI development without runtime retriever calls.",
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
                {"role": "assistant", "content": "Mock mode is active; this response does not query the runtime retriever."},
            ],
        )
        return ChatTurnResult(
            session_id=request.session_id,
            assistant_message="Mock mode is active; showing a scripted protein card while the runtime backend is offline.",
            candidates=[candidate],
            revealed_sections={"overview", "function", "evidence"},
            session=snapshot,
            pipeline=BioSeqPipelineSnapshot(prompt=request.message, input_type="TEXT", context=request.message),
            warnings=["BIOSEQ_BACKEND=mock: no runtime retriever queries were executed."],
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


def _candidates_from_state(state: dict[str, Any]) -> list[CandidateView]:
    working_memory = state.get("working_memory") or {}
    raw_candidates = working_memory.get("last_candidates") if isinstance(working_memory, dict) else []
    candidates: list[CandidateView] = []
    for item in raw_candidates or []:
        try:
            candidates.append(CandidateView.model_validate(item))
        except Exception:
            continue
    return candidates


def _sequence_id(sequence: str) -> str:
    return f"seq_{uuid.uuid5(uuid.NAMESPACE_OID, sequence).hex[:12]}"


def _protein_records(candidates: list[CandidateView]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, candidate in enumerate(candidates):
        protein = candidate.protein
        if not protein.accession or protein.accession in seen:
            continue
        seen.add(protein.accession)
        records.append(
            {
                "accession": protein.accession,
                "gene_name": protein.gene,
                "protein_name": protein.name,
                "source": "bioseq_retriever",
                "status": "active" if index == 0 else "candidate",
                "notes": protein.organism_scientific,
            }
        )
    return records


def _sequence_records(pipeline: BioSeqPipelineSnapshot, active_sequence_id: str | None) -> list[dict[str, Any]]:
    if not pipeline.sequence or not active_sequence_id:
        return []
    return [
        {
            "sequence_id": active_sequence_id,
            "sequence_type": pipeline.sequence_type.lower(),
            "raw_sequence": pipeline.sequence,
            "label": "retriever_input",
            "source": "bioseq_retriever",
            "linked_accession": pipeline.active_accession,
        }
    ]


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
            f"and found bioseq_retriever accession {pipeline.active_accession}."
        )
    return f"I classified the input as {source} and found bioseq_retriever accession {pipeline.active_accession}."


def _is_follow_up_turn(request: ChatTurnRequest) -> bool:
    """Return True if frontend signals an existing chat session.

    Frontend reads ``working_memory.turn_count`` from ``public.chat_sessions``
    and forwards it via ``ui_context``. Any positive count means the retriever
    has already produced a protein card; subsequent TEXT prompts should be
    answered by Chat-LLM. When the field is missing we conservatively treat
    the turn as the first one so legacy callers get unchanged behaviour.
    """
    ui_context = request.ui_context or {}
    raw = ui_context.get("turn_count")
    if raw is None:
        return False
    try:
        return int(raw) > 0
    except (TypeError, ValueError):
        return False


def _history_from_ui_context(ui_context: dict[str, Any]) -> list[dict[str, Any]]:
    history = ui_context.get("messages")
    if not isinstance(history, list):
        return []
    return [message for message in history if isinstance(message, dict)]


def _selected_candidate_from_ui_context(
    ui_context: dict[str, Any], selected_index: int
) -> dict[str, Any] | None:
    candidate = ui_context.get("selected_candidate")
    if isinstance(candidate, dict):
        return candidate
    candidates = ui_context.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    if selected_index < 0 or selected_index >= len(candidates):
        return None
    picked = candidates[selected_index]
    return picked if isinstance(picked, dict) else None


def _pipeline_miss_message(pipeline: BioSeqPipelineSnapshot) -> str:
    if pipeline.input_type == "FILEPATH":
        return (
            "I detected a file path, but this runtime did not resolve it. "
            "Put the sequence in the allowed data directory or send the raw FASTA/sequence."
        )
    if pipeline.error:
        return f"I could not process this sequence via bioseq_retriever: {pipeline.error}"
    return "I classified the input sequence, but bioseq_retriever did not return final matches."

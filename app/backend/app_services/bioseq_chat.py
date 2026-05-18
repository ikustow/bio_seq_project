from __future__ import annotations

import re
import uuid
from typing import Any, Protocol

from backend.agents_core.shared.models import AppContext
from backend.agents_core.shared.services.session_state import get_message_text, serialize_message
from backend.app_contracts import (
    BioSeqPipelineSnapshot,
    CandidateView,
    ChatTurnRequest,
    ChatTurnResult,
    ObjectsPatch,
    ProteinView,
    SessionSnapshot,
)

from .chat_llm import ChatLLMRequest, ChatLLMService
from .retriever_pipeline import BioSeqRetrieverPipeline
from .suggested_questions import SuggestedQuestionsRequest, SuggestedQuestionsService
from .uniprot_lookup import lookup_protein_view

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
        suggested_questions_service: SuggestedQuestionsService | None = None,
    ) -> None:
        self._agent = agent
        self._retriever_pipeline = retriever_pipeline or BioSeqRetrieverPipeline()
        # Optional: when None the service falls back to the agent stub for
        # text turns (legacy behaviour). Frontend signals follow-up turns via
        # ``ui_context.turn_count`` so the backend can route to chat-LLM.
        self._chat_llm_service = chat_llm_service
        self._suggested_questions_service = suggested_questions_service

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

        # ---- Direct UniProt accession / mnemonic lookup ----
        direct_identifier = _direct_uniprot_identifier(request)
        if direct_identifier:
            return self._handle_direct_uniprot(request, context, direct_identifier, warnings)

        # ---- Pure follow-up about objects that are already ``ready`` ----
        # If no Sequence is awaiting retrieval AND the message just
        # references one or more known @labels, the user is asking about
        # objects we've already classified. Running the retriever on the
        # chat text itself would re-classify natural language as a fresh
        # sequence (the bug that produced
        # "I classified the input as protein sequence and found accession
        # P05787" for "Что общего у этих двух белков? @Seq_A и @Seq_B").
        # Hand off straight to chat-LLM; it already knows how to render
        # the workspace and per-mention UniProt context.
        if (
            self._chat_llm_service is not None
            and not _has_pending_sequence(request)
            and _references_existing_objects(request)
        ):
            return self._handle_follow_up(
                request,
                context,
                BioSeqPipelineSnapshot(
                    prompt=request.message,
                    input_type="TEXT",
                    context=request.message,
                ),
                selected_index,
                warnings,
            )

        # If the frontend already detected sequences and redacted them to
        # ``@Seq_A`` mentions, the retriever needs the actual sequence
        # body rather than the chat text. Look for the first pending
        # Sequence in the registry and feed its raw body to the pipeline.
        retriever_input, pending_sequence_id = _retriever_input_from_request(request)

        pipeline, pipeline_candidates = self._retriever_pipeline.run(
            retriever_input, limit=5, search_algorithm=request.search_algorithm
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
                failure_patch = _build_retriever_failure_patch(
                    request, pipeline, error_message=assistant_message
                )
                return ChatTurnResult(
                    session_id=request.session_id,
                    assistant_message=assistant_message,
                    candidates=[],
                    selected_candidate_index=selected_index,
                    revealed_sections=set(),
                    session=self._snapshot(context, state),
                    pipeline=pipeline,
                    warnings=warnings,
                    objects_patch=failure_patch,
                )

            if pipeline_candidates:
                patch = _build_retriever_patch(request, pipeline, pipeline_candidates)
                assistant_message = _pipeline_hit_message(pipeline)
                suggested_questions, suggested_metadata = self._maybe_generate_suggested_questions(
                    request=request,
                    assistant_message=assistant_message,
                    selected_candidate=pipeline_candidates[0].model_dump(),
                    history=_history_from_ui_context(request.ui_context or {}),
                    warnings=warnings,
                )
                return ChatTurnResult(
                    session_id=request.session_id,
                    assistant_message=assistant_message,
                    candidates=pipeline_candidates,
                    selected_candidate_index=selected_index,
                    revealed_sections=_revealed_sections(pipeline_candidates),
                    session=self._snapshot(context, state),
                    pipeline=pipeline,
                    warnings=warnings,
                    objects_patch=patch,
                    selected_object_id=patch.set_selected,
                    suggested_questions=suggested_questions,
                    metadata=suggested_metadata,
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

    def _maybe_generate_suggested_questions(
        self,
        *,
        request: ChatTurnRequest,
        assistant_message: str,
        selected_candidate: dict[str, Any] | None,
        history: list[dict[str, Any]],
        warnings: list[str],
    ) -> tuple[list[str], dict[str, Any]]:
        if not request.think_mode or self._suggested_questions_service is None:
            return [], {}
        response = self._suggested_questions_service.generate(
            SuggestedQuestionsRequest(
                user_message=request.message,
                assistant_message=assistant_message,
                history=history,
                selected_candidate=selected_candidate,
            )
        )
        warnings.extend(response.warnings)
        metadata = {
            "think_mode": True,
            "suggested_questions_provider": response.provider,
            "suggested_questions_model": response.model,
            "suggested_questions_raw": response.raw,
        }
        return response.questions, metadata

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
                    objects=request.objects or {},
                    selected_object_id=request.selected_object_id,
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

        suggested_questions: list[str] = []
        suggested_metadata: dict[str, Any] = {}
        if current_mode != "chat_llm_error":
            suggested_questions, suggested_metadata = self._maybe_generate_suggested_questions(
                request=request,
                assistant_message=assistant_message,
                selected_candidate=selected_candidate,
                history=history,
                warnings=warnings,
            )
            metadata = {**metadata, **suggested_metadata}

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
            suggested_questions=suggested_questions,
            metadata=metadata,
        )

    def get_session(self, session_id: str, user_id: str = "anonymous") -> SessionSnapshot:
        context = AppContext(user_id=user_id, session_id=session_id)
        return self._snapshot(context, self._agent.get_current_state(context))

    def _handle_direct_uniprot(
        self,
        request: ChatTurnRequest,
        context: AppContext,
        identifier: str,
        warnings: list[str],
    ) -> ChatTurnResult:
        """Resolve a UniProt accession / mnemonic ID without running retriever."""
        protein = lookup_protein_view(identifier)
        if protein is None:
            warnings.append(
                f"Could not load UniProt record for `{identifier}` from local cache or API."
            )
            state = self._agent.get_current_state(context)
            return ChatTurnResult(
                session_id=request.session_id,
                assistant_message=(
                    f"I couldn't find a UniProt record for `{identifier}`. "
                    "Double-check the identifier or try a different one."
                ),
                candidates=[],
                selected_candidate_index=0,
                revealed_sections=set(),
                session=self._snapshot(context, state),
                warnings=warnings,
                update_card=False,
                current_mode="uniprot_direct_lookup",
            )

        object_id = f"protein_{protein.accession.upper()}"
        protein_payload = {
            "id": object_id,
            "kind": "protein",
            "label": protein.accession,
            "display_name": protein.name or protein.accession,
            "accession": protein.accession,
            "uniprot_id": "",
            "gene": protein.gene or "",
            "organism": protein.organism_scientific or "",
            "linked_sequence_ids": [],
            "card": protein.model_dump(),
            "match_score": 1.0,
            "last_origin_sequence_id": None,
        }
        patch = ObjectsPatch(
            upsert={object_id: protein_payload},
            set_selected=object_id,
        )

        state = self._agent.update_current_state(
            context,
            {
                "active_accession": protein.accession,
                "current_mode": "uniprot_direct_lookup",
            },
        )
        assistant_message = (
            f"I loaded **{protein.accession}** ({protein.name or 'UniProt entry'}) "
            "directly without running a similarity search."
        )
        return ChatTurnResult(
            session_id=request.session_id,
            assistant_message=assistant_message,
            candidates=[CandidateView(protein=protein, match_score=1.0, rank=0)],
            selected_candidate_index=0,
            revealed_sections=_revealed_sections(
                [CandidateView(protein=protein, match_score=1.0, rank=0)]
            ),
            session=self._snapshot(context, state),
            warnings=warnings,
            update_card=False,
            current_mode="uniprot_direct_lookup",
            objects_patch=patch,
            selected_object_id=object_id,
        )

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

    Section keys must match the frontend's ``protein_card._ALL_SECTIONS``.
    Header / keyfacts / structure are revealed
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
    if protein.keywords or protein.go_terms or protein.go_terms_by_category or protein.pathways:
        sections.add("pathways")
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


_MENTION_TOKEN_RE = re.compile(r"@([A-Za-z0-9_]+)")


def _has_pending_sequence(request: ChatTurnRequest) -> bool:
    """True if any Sequence object is still awaiting retriever processing."""
    interesting = {"queued", "searching", "classifying", "draft"}
    for obj in (request.objects or {}).values():
        if not isinstance(obj, dict):
            continue
        if obj.get("kind") != "sequence":
            continue
        if obj.get("status") in interesting:
            return True
    return False


def _references_existing_objects(request: ChatTurnRequest) -> bool:
    """True if ``request.message`` mentions at least one known @label.

    Frontend currently doesn't forward ``object_mentions``, so resolve
    ``@token`` against ``objects[].label`` / ``.accession`` ourselves.
    """
    message = request.message or ""
    objects = request.objects or {}
    if not message or not objects:
        return False
    tokens = {match.group(1) for match in _MENTION_TOKEN_RE.finditer(message)}
    if not tokens:
        return False
    for obj in objects.values():
        if not isinstance(obj, dict):
            continue
        label = str(obj.get("label") or "")
        accession = str(obj.get("accession") or "")
        if (label and label in tokens) or (accession and accession in tokens):
            return True
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


_UNIPROT_ACCESSION_RE = re.compile(
    r"^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]"
    r"|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})$"
)
_UNIPROT_MNEMONIC_RE = re.compile(r"^[A-Z0-9]{1,10}_[A-Z0-9]{1,5}$")


def _direct_uniprot_identifier(request: ChatTurnRequest) -> str | None:
    """Detect a bare UniProt accession or mnemonic in the user's message.

    Used to skip the embedding/rerank pipeline when the user has already
    named a specific UniProt entry. Internal Sequence labels (``Seq_A``,
    ``Seq_B``, ...) and any label that already exists in the workspace
    registry must NOT be treated as UniProt identifiers.
    """
    candidate = (request.message or "").strip().upper()
    if not candidate:
        return None
    if candidate.startswith("@"):
        candidate = candidate[1:]

    if _is_internal_sequence_label(candidate, request):
        return None
    if _UNIPROT_ACCESSION_RE.match(candidate) or _UNIPROT_MNEMONIC_RE.match(candidate):
        return candidate

    for mention in request.object_mentions or []:
        if getattr(mention, "kind", None) == "protein" and getattr(mention, "label", None):
            label = str(mention.label).upper()
            if _is_internal_sequence_label(label, request):
                continue
            if _UNIPROT_ACCESSION_RE.match(label) or _UNIPROT_MNEMONIC_RE.match(label):
                return label
    return None


def _is_internal_sequence_label(token: str, request: ChatTurnRequest) -> bool:
    """Return True if ``token`` is one of our own Sequence labels.

    Excludes both the literal ``SEQ_<letters>`` pattern we use for fresh
    labels and any label already registered in ``request.objects``.
    """
    if not token:
        return False
    upper = token.upper()
    if upper.startswith("SEQ_") and upper[4:].isalpha():
        return True
    for obj in (request.objects or {}).values():
        if not isinstance(obj, dict):
            continue
        if obj.get("kind") != "sequence":
            continue
        label = str(obj.get("label") or "").upper()
        if label and label == upper:
            return True
    return False


def _retriever_input_from_request(request: ChatTurnRequest) -> tuple[str, str | None]:
    """Pick the right text for the retriever pipeline.

    Preference order:

    1. A pending Sequence in ``request.objects`` (status queued/searching/
       classifying/draft) — feed its FASTA header + body or raw sequence.
    2. The user's chat message as-is.

    Returns ``(text, sequence_id)`` where ``sequence_id`` is the id of the
    Sequence that owned the body, or ``None`` if we fell back to the raw
    chat text.
    """
    interesting = {"queued", "searching", "classifying", "draft"}
    for object_id, obj in (request.objects or {}).items():
        if not isinstance(obj, dict):
            continue
        if obj.get("kind") != "sequence":
            continue
        if obj.get("status") not in interesting:
            continue
        body = (
            obj.get("protein_sequence")
            or obj.get("normalized_sequence")
            or obj.get("raw_sequence")
            or ""
        )
        if not body:
            continue
        header = obj.get("fasta_header") or ""
        text = f"{header}\n{body}" if header else body
        return text, object_id
    return request.message or "", None


def _build_retriever_patch(
    request: ChatTurnRequest,
    pipeline: BioSeqPipelineSnapshot,
    candidates: list[CandidateView],
) -> ObjectsPatch:
    """Construct an ``ObjectsPatch`` that updates a Sequence with its top-5.

    Strategy: figure out which Sequence id the matches belong to. The
    frontend sends the freshly-created Sequence (status ``queued`` /
    ``searching``) inside ``request.objects``; we update that exact id so
    the registry stays consistent. If no such Sequence is found, fall back
    to a synthetic id derived from the protein sequence so the patch is
    still valid.
    """
    target_id = _resolve_target_sequence_id(request, pipeline)
    matches_payload = [
        {
            "accession": cand.protein.accession,
            "rank": cand.rank,
            "match_score": cand.match_score,
            "protein": cand.protein.model_dump(),
        }
        for cand in candidates
    ]

    # Only the Sequence is upserted as a Session-object. The top-5 protein
    # candidates live INSIDE ``sequence.matches`` and are not surfaced as
    # standalone chips — those should appear only for proteins the user
    # explicitly opens (direct UniProt ID lookup).
    sequence_status: dict[str, Any] = {
        "status": "ready",
        "matches": matches_payload,
    }
    if pipeline.protein_sequence:
        sequence_status["protein_sequence"] = pipeline.protein_sequence
    upsert: dict[str, Any] = {target_id: sequence_status}

    return ObjectsPatch(upsert=upsert, set_selected=target_id)


def _build_retriever_failure_patch(
    request: ChatTurnRequest,
    pipeline: BioSeqPipelineSnapshot,
    *,
    error_message: str,
) -> ObjectsPatch | None:
    """Flip the target Sequence to ``search_failed`` so the UI stops
    showing an indefinite ``searching…`` chip when the retriever errors.

    Only emits a patch when we can resolve the Sequence that was pending —
    otherwise the frontend would target an arbitrary id. The new status
    keeps the body / source intact so a ``Search again`` retry can flip
    it back to ``queued`` and re-feed the pipeline.
    """
    target_id = _resolve_target_sequence_id(request, pipeline)
    if not target_id or not target_id.startswith("seq_"):
        return None
    # Refuse to patch a Sequence id the frontend doesn't actually have —
    # _resolve_target_sequence_id falls back to a synthesised hash when
    # the request has nothing pending, and patching that would create a
    # phantom object on the frontend.
    if target_id not in (request.objects or {}):
        return None
    # Only touch the fields we actually want to change — the frontend's
    # apply_objects_patch does a top-level merge, so unlisted fields
    # (label, source, normalized_sequence, warnings, ...) are preserved.
    del error_message  # not surfaced on the chip; the chat reply has it
    sequence_patch: dict[str, Any] = {
        "status": "search_failed",
        "matches": [],
    }
    return ObjectsPatch(upsert={target_id: sequence_patch})


def _resolve_target_sequence_id(
    request: ChatTurnRequest,
    pipeline: BioSeqPipelineSnapshot,
) -> str:
    """Find the Sequence id the retriever ran for.

    Preference order:

    1. The first sequence object in ``request.objects`` whose status is in
       ``{queued, searching, classifying, draft}``.
    2. The frontend's ``selected_object_id`` if it points at a sequence.
    3. A synthesised id based on the normalised protein sequence.
    """
    candidates: list[tuple[int, str]] = []
    objects = request.objects or {}
    interesting = {"queued", "searching", "classifying", "draft"}
    for index, (object_id, obj) in enumerate(objects.items()):
        if not isinstance(obj, dict):
            continue
        if obj.get("kind") != "sequence":
            continue
        if obj.get("status") in interesting:
            candidates.append((index, object_id))
    if candidates:
        return candidates[0][1]
    if request.selected_object_id:
        target = objects.get(request.selected_object_id)
        if isinstance(target, dict) and target.get("kind") == "sequence":
            return request.selected_object_id
    seq_body = pipeline.protein_sequence or pipeline.sequence or ""
    if seq_body:
        import hashlib

        digest = hashlib.sha1(seq_body.encode("utf-8")).hexdigest()[:12]
        return f"seq_{digest}"
    return "seq_pending"


def _pipeline_miss_message(pipeline: BioSeqPipelineSnapshot) -> str:
    if pipeline.input_type == "FILEPATH":
        return (
            "I detected a file path, but this runtime did not resolve it. "
            "Put the sequence in the allowed data directory or send the raw FASTA/sequence."
        )
    if pipeline.error:
        return f"I could not process this sequence via bioseq_retriever: {pipeline.error}"
    return "I classified the input sequence, but bioseq_retriever did not return final matches."

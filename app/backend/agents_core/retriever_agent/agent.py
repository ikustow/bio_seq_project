from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Callable, Literal, Optional, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from backend.agents_core.shared.models import AppContext, PersistenceResources
from backend.agents_core.shared.services.session_state import serialize_message
from backend.app_services.graph_retrieval import GraphRetrievalService, normalize_protein_sequence
from backend.app_services.retriever_pipeline import (
    EXTRACTION_SYSTEM_PROMPT,
    deterministic_extract_and_classify,
    translate_dna_to_protein,
    use_raw_sequence,
)


class InputExtraction(BaseModel):
    sequence_or_path: str = Field(description="The extracted raw biological sequence or the file path.")
    input_type: Literal["SEQUENCE", "FILEPATH"] = Field(description="Whether the input is a raw sequence or a file path.")
    context: str = Field(description="Any contextual information, questions, or hints provided by the user.")
    sequence_type: Literal["DNA", "PROTEIN"] = Field(description="The classified type of the biological sequence.")
    is_confident: bool = Field(description="True if the LLM is highly confident in the sequence type classification.")
    reasoning: str = Field(description="Brief chain-of-thought reasoning for the extraction and classification.")


class GraphState(TypedDict):
    messages: Annotated[list[Any], add_messages]
    prompt: str
    sequence_or_path: Optional[str]
    input_type: Optional[str]
    context: Optional[str]
    sequence: Optional[str]
    sequence_type: Optional[str]
    protein_sequence: Optional[str]
    is_confident: Optional[bool]
    ranked_results: Optional[list[dict[str, Any]]]
    final_results: Optional[list[dict[str, Any]]]
    error: Optional[str]


SYSTEM_PROMPT = EXTRACTION_SYSTEM_PROMPT


class BioSeqRetrieverGraphAgent:
    """LangGraph port of bioseq_retriever/src/pipeline.py with graph-first retrieval."""

    def __init__(
        self,
        graph_retrieval: GraphRetrievalService,
        persistence: PersistenceResources,
        llm_factory: Callable[[], object] | None = None,
        use_llm_extractor: bool = True,
    ) -> None:
        self._graph_retrieval = graph_retrieval
        self._persistence = persistence
        self._llm_factory = llm_factory
        self._use_llm_extractor = use_llm_extractor
        self._graph = create_pipeline(
            graph_retrieval=graph_retrieval,
            persistence=persistence,
            llm_factory=llm_factory,
            use_llm_extractor=use_llm_extractor,
        )

    @property
    def warnings(self) -> list[str]:
        return self._persistence.warnings

    @property
    def persistence_mode(self) -> str:
        return self._persistence.mode

    def invoke(self, prompt: str, context: AppContext) -> tuple[GraphState, dict[str, Any]]:
        config = {"configurable": {"thread_id": context.session_id}}
        saved_session = self._persistence.session_repository.get_session(context.session_id)
        result = self._graph.invoke(_initial_state(prompt), config=config)
        assistant_message = _assistant_message_from_state(result)
        self._graph.update_state(config, {"messages": [AIMessage(content=assistant_message)]})
        current_state = dict(self._graph.get_state(config).values)
        session_patch = _merge_session_patch(saved_session, _derive_session_patch(current_state))
        if session_patch:
            self._persistence.session_repository.upsert_session(context, session_patch)
        return result, current_state

    def get_current_state(self, context: AppContext) -> dict[str, Any]:
        config = {"configurable": {"thread_id": context.session_id}}
        return dict(self._graph.get_state(config).values)

    def update_current_state(self, context: AppContext, patch: dict[str, Any]) -> dict[str, Any]:
        config = {"configurable": {"thread_id": context.session_id}}
        self._graph.update_state(config, patch)
        current_state = dict(self._graph.get_state(config).values)
        saved_session = self._persistence.session_repository.get_session(context.session_id)
        self._persistence.session_repository.upsert_session(
            context,
            _merge_session_patch(saved_session, _derive_session_patch(current_state)),
        )
        return current_state

    def get_message_history(self, context: AppContext) -> list[dict[str, Any]]:
        return [serialize_message(message) for message in self.get_current_state(context).get("messages", [])]


def create_pipeline(
    graph_retrieval: GraphRetrievalService,
    persistence: PersistenceResources,
    llm_factory: Callable[[], object] | None = None,
    use_llm_extractor: bool = True,
):
    nodes = _RetrieverNodes(
        graph_retrieval=graph_retrieval,
        llm_factory=llm_factory,
        use_llm_extractor=use_llm_extractor,
    )
    workflow = StateGraph(GraphState)

    workflow.add_node("extract", nodes.extract_and_classify_node)
    workflow.add_node("resolve_file", nodes.resolve_filepath_node)
    workflow.add_node("use_raw", nodes.use_raw_sequence_node)
    workflow.add_node("translate", nodes.translate_dna_node)
    workflow.add_node("pass_protein", nodes.pass_protein_node)
    workflow.add_node("rank", nodes.rank_node)
    workflow.add_node("rerank", nodes.rerank_node)

    workflow.set_entry_point("extract")

    workflow.add_conditional_edges(
        "extract",
        should_resolve_filepath,
        {
            "resolve": "resolve_file",
            "raw": "use_raw",
        },
    )

    workflow.add_conditional_edges(
        "resolve_file",
        should_translate,
        {
            "translate": "translate",
            "skip": "pass_protein",
        },
    )
    workflow.add_conditional_edges(
        "use_raw",
        should_translate,
        {
            "translate": "translate",
            "skip": "pass_protein",
        },
    )

    workflow.add_edge("translate", "rank")
    workflow.add_edge("pass_protein", "rank")
    workflow.add_edge("rank", "rerank")
    workflow.add_edge("rerank", END)

    return workflow.compile(checkpointer=persistence.checkpointer)


class _RetrieverNodes:
    def __init__(
        self,
        graph_retrieval: GraphRetrievalService,
        llm_factory: Callable[[], object] | None,
        use_llm_extractor: bool,
    ) -> None:
        self._graph_retrieval = graph_retrieval
        self._llm_factory = llm_factory
        self._use_llm_extractor = use_llm_extractor

    def extract_and_classify_node(self, state: GraphState) -> dict[str, Any]:
        try:
            result = self._extract_with_llm(state["prompt"]) if self._use_llm_extractor and self._llm_factory else self._extract_deterministic(state["prompt"])
            return {
                "sequence_or_path": result.sequence_or_path,
                "input_type": result.input_type,
                "context": result.context,
                "sequence_type": result.sequence_type,
                "is_confident": result.is_confident,
            }
        except Exception as exc:
            previous = _previous_extraction(state)
            if previous:
                return previous
            return {"error": f"Extraction failed: {exc}"}

    def resolve_filepath_node(self, state: GraphState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        return {
            "error": (
                "File resolution failed: Runtime file path resolution is disabled in DB-only graph mode. "
                "Load sequences into the prepared graph database before querying them."
            )
        }

    def use_raw_sequence_node(self, state: GraphState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        seq = state.get("sequence_or_path") or ""
        return {"sequence": use_raw_sequence(seq)}

    def translate_dna_node(self, state: GraphState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        try:
            protein_seq = translate_dna_to_protein(state.get("sequence") or "")
            return {"protein_sequence": protein_seq}
        except Exception as exc:
            return {"error": f"Translation failed: {exc}"}

    def pass_protein_node(self, state: GraphState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        return {"protein_sequence": normalize_protein_sequence(state.get("sequence") or "")}

    def rank_node(self, state: GraphState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        try:
            hit = None
            if state.get("sequence_type") == "DNA":
                hit = self._graph_retrieval.find_encoded_protein_by_sequence_hash(
                    state.get("sequence") or "",
                    state.get("protein_sequence"),
                )
            if hit is None and state.get("protein_sequence"):
                hit = self._graph_retrieval.find_by_sequence_hash(state["protein_sequence"] or "")
            if hit is None:
                return {
                    "ranked_results": [],
                    "error": "Ranking failed: Sequence is outside the prepared graph dataset; runtime ProtT5/FAISS search is disabled.",
                }
            candidates = self._graph_retrieval.retrieve_candidates(hit.accession, limit=50, neighbor_pool=50)
            return {"ranked_results": [candidate.model_dump() for candidate in candidates]}
        except Exception as exc:
            return {"error": f"Ranking failed: {exc}"}

    def rerank_node(self, state: GraphState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        try:
            ranked_results = state.get("ranked_results") or []
            if not ranked_results:
                return {"final_results": []}
            target_accession = ranked_results[0].get("protein", {}).get("accession")
            if not target_accession:
                return {"final_results": ranked_results[:5]}
            candidates = self._graph_retrieval.retrieve_candidates(
                target_accession,
                limit=5,
                neighbor_pool=50,
                context=state.get("context") or None,
            )
            return {"final_results": [candidate.model_dump() for candidate in candidates]}
        except Exception as exc:
            return {"error": f"Reranking failed: {exc}"}

    def _extract_with_llm(self, prompt: str) -> InputExtraction:
        llm = self._llm_factory()
        structured_llm = llm.with_structured_output(InputExtraction)
        result = structured_llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
        return InputExtraction.model_validate(result)

    def _extract_deterministic(self, prompt: str) -> InputExtraction:
        fallback = deterministic_extract_and_classify(prompt)
        if fallback.input_type not in {"SEQUENCE", "FILEPATH"} or fallback.sequence_type not in {"DNA", "PROTEIN"}:
            raise ValueError(fallback.reasoning or "No raw sequence or file path was detected.")
        return InputExtraction(
            sequence_or_path=fallback.sequence_or_path or "",
            input_type=fallback.input_type,
            context=fallback.context,
            sequence_type=fallback.sequence_type,
            is_confident=fallback.is_confident,
            reasoning=fallback.reasoning,
        )


def should_resolve_filepath(state: GraphState) -> Literal["resolve", "raw"]:
    if state.get("error"):
        return "raw"
    return "resolve" if state["input_type"] == "FILEPATH" else "raw"


def should_translate(state: GraphState) -> Literal["translate", "skip"]:
    if state.get("error"):
        return "skip"
    return "translate" if state["sequence_type"] == "DNA" else "skip"


def _initial_state(prompt: str) -> GraphState:
    return {
        "messages": [HumanMessage(content=prompt)],
        "prompt": prompt,
        "error": None,
    }


def _previous_extraction(state: GraphState) -> dict[str, Any] | None:
    sequence_or_path = state.get("sequence_or_path")
    input_type = state.get("input_type")
    sequence_type = state.get("sequence_type")
    if not sequence_or_path or input_type not in {"SEQUENCE", "FILEPATH"} or sequence_type not in {"DNA", "PROTEIN"}:
        return None
    return {
        "sequence_or_path": sequence_or_path,
        "input_type": input_type,
        "context": state.get("prompt") or state.get("context") or "",
        "sequence_type": sequence_type,
        "is_confident": False,
    }


def _derive_session_patch(state: dict[str, Any]) -> dict[str, Any]:
    final_results = state.get("final_results") or []
    ranked_results = state.get("ranked_results") or []
    result_source = final_results or ranked_results
    target = result_source[0] if result_source else {}
    protein = target.get("protein") if isinstance(target, dict) else {}
    accession = protein.get("accession") if isinstance(protein, dict) else None
    sequence = state.get("sequence")
    sequence_type = state.get("sequence_type")
    sequence_id = _sequence_id(sequence, sequence_type) if sequence and sequence_type else None
    proteins = []
    if isinstance(protein, dict) and accession:
        proteins.append(
            {
                "accession": accession,
                "gene_name": protein.get("gene"),
                "protein_name": protein.get("name"),
                "source": "retriever_agent",
                "status": "active",
                "notes": protein.get("organism_scientific"),
            }
        )
    sequences = []
    if sequence and sequence_type and sequence_id:
        sequences.append(
            {
                "sequence_id": sequence_id,
                "sequence_type": str(sequence_type).lower(),
                "raw_sequence": sequence,
                "label": "retriever_input",
                "source": "retriever_agent",
                "linked_accession": accession,
            }
        )
    summary = _summarize_retriever_state(state, accession)
    return {
        "session_summary": summary,
        "proteins": proteins,
        "sequences": sequences,
        "working_memory": {
            "last_sync_source": "bioseq_retriever_langgraph",
            "last_retriever_state": _compact_state(state),
        },
        "active_sequence_id": sequence_id,
        "active_accession": accession,
        "last_analysis_summary": summary,
        "working_set_ids": [item for item in [accession, sequence_id] if item],
        "current_mode": "bioseq_retriever_langgraph",
        "last_tool_results_summary": summary,
    }


def _assistant_message_from_state(state: dict[str, Any]) -> str:
    if state.get("error"):
        return str(state["error"])
    final_results = state.get("final_results") or []
    if not final_results:
        return "Retriever graph pipeline completed without final matches."
    top = final_results[0].get("protein", {}) if isinstance(final_results[0], dict) else {}
    accession = top.get("accession")
    name = top.get("name")
    organism = top.get("organism_scientific")
    organism_suffix = f" Organism: {organism}." if organism else ""
    if accession and name:
        return f"Top graph match is {accession}: {name}.{organism_suffix}"
    if accession:
        return f"Top graph match is {accession}.{organism_suffix}"
    return f"Retriever graph pipeline returned {len(final_results)} final matches."


def _merge_session_patch(saved: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if not saved:
        return incoming
    proteins = _merge_by_key(saved.get("proteins") or [], incoming.get("proteins") or [], "accession")
    sequences = _merge_by_key(saved.get("sequences") or [], incoming.get("sequences") or [], "sequence_id")
    working_memory = {
        **(saved.get("working_memory") or {}),
        **(incoming.get("working_memory") or {}),
    }
    working_set_ids = _unique_tail(
        [
            *(saved.get("working_set_ids") or []),
            *(incoming.get("working_set_ids") or []),
        ],
        limit=40,
    )
    return {
        **incoming,
        "proteins": proteins,
        "sequences": sequences,
        "working_memory": working_memory,
        "active_sequence_id": incoming.get("active_sequence_id") or saved.get("active_sequence_id"),
        "active_accession": incoming.get("active_accession") or saved.get("active_accession"),
        "working_set_ids": working_set_ids,
    }


def _merge_by_key(existing: list[Any], incoming: list[Any], key: str) -> list[dict[str, Any]]:
    merged: dict[Any, dict[str, Any]] = {}
    for item in [*existing, *incoming]:
        payload = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        value = payload.get(key)
        if not value:
            continue
        previous = merged.get(value, {})
        merged[value] = {**previous, **{k: v for k, v in payload.items() if v is not None}}
    return list(merged.values())


def _unique_tail(items: list[Any], limit: int) -> list[Any]:
    unique: list[Any] = []
    for item in items:
        if item and item not in unique:
            unique.append(item)
    return unique[-limit:]


def _compact_state(state: dict[str, Any]) -> dict[str, Any]:
    final_results = state.get("final_results") or []
    ranked_results = state.get("ranked_results") or []
    return {
        "prompt": state.get("prompt"),
        "input_type": state.get("input_type"),
        "context": state.get("context"),
        "sequence_type": state.get("sequence_type"),
        "is_confident": state.get("is_confident"),
        "protein_sequence_length": len(state.get("protein_sequence") or ""),
        "ranked_count": len(ranked_results),
        "final_count": len(final_results),
        "top_accession": _top_accession(final_results or ranked_results),
        "error": state.get("error"),
    }


def _summarize_retriever_state(state: dict[str, Any], accession: str | None) -> str:
    if state.get("error"):
        return str(state["error"])
    final_results = state.get("final_results") or []
    if accession:
        return f"Retriever graph pipeline matched {accession}; final_results={len(final_results)}."
    return "Retriever graph pipeline completed without a graph match."


def _top_accession(results: list[dict[str, Any]]) -> str | None:
    if not results:
        return None
    protein = results[0].get("protein", {})
    return protein.get("accession") if isinstance(protein, dict) else None


def _sequence_id(sequence: str, sequence_type: str) -> str:
    digest = hashlib.sha256(normalize_protein_sequence(sequence).encode("utf-8")).hexdigest()[:12]
    return f"{str(sequence_type).lower()}_{digest}"


def dumps_state(state: GraphState) -> str:
    return json.dumps(state, ensure_ascii=False, indent=2)

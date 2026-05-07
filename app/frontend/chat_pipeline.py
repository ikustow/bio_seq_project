"""End-to-end turn pipeline for the Streamlit frontend.

Two-level dispatch on every user turn:

1. **Turn-position router.** First user turn in the session goes to a
   retriever backend (graph or embeddings — see below). Subsequent turns
   go to the chat-LLM module via ``chat_llm_pipeline``. "First" is decided
   by reading ``working_memory.turn_count`` from ``public.chat_sessions``
   so the rule survives page reloads, sidebar session-switches, and
   multi-tab use. While the chat-LLM module is being built by a teammate,
   ``chat_llm_pipeline`` is a stub that returns "module baking…" and keeps
   the protein card untouched.

2. **Retriever backend selector** (only for first turns). Picked from
   ``backend_choice.get_backend()``:
   - ``"graph"``     — Neo4j ``BioSeqRetrieverGraphAgent`` (this module).
   - ``"embeddings"``— Legacy ProtT5+FAISS via ``embeddings_pipeline``.

All paths return the same dict shape so the UI is backend-agnostic, and all
write the turn to ``public.chat_sessions`` so sidebar history is uniform.
The embeddings module is imported lazily so the heavy ML deps (~2 GB) don't
load when only the graph backend is in use.

For the graph backend, one turn is:

1. Pull the per-browser identity (``user_id``, ``session_id``) from
   ``st.session_state`` and build an ``AppContext``. The ``session_id`` is the
   LangGraph ``thread_id``, so multi-user concurrent chats stay isolated even
   though the agent itself is process-cached.
2. Call ``BioSeqRetrieverGraphAgent.invoke(prompt, context)``. The agent
   writes its compact patch to ``public.chat_sessions`` itself, then returns
   the LangGraph result.
3. Map ``final_results`` into UI ``Candidate`` shapes.
4. ``session_db_adapter.save_turn`` layers UI-owned fields on top — full 5
   candidate cards in ``working_memory.last_candidates``, the message
   transcript, turn counters.

Notes on multi-user safety:
- We never read ``APP_SESSION_ID`` from env here — it would alias all users
  onto one LangGraph thread.
- The agent factory is cached with ``@st.cache_resource``; per-user state
  lives in the LangGraph checkpointer keyed by ``thread_id=session_id``, so
  a single shared agent instance is correct.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import streamlit as st

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _PROJECT_ROOT / "app"
_FRONTEND_ROOT = Path(__file__).resolve().parent
for _path in (_FRONTEND_ROOT, _APP_ROOT, _PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import backend_choice  # noqa: E402
import session_db_adapter  # noqa: E402
from mock.protein_loader import Candidate, DiseaseInfo, DomainFeature, ProteinView  # noqa: E402


@st.cache_resource(show_spinner="Connecting to retriever…")
def _get_agent():
    # Lazy import so the graph-backend deps (Neo4j driver, langgraph postgres)
    # are only resolved when the user actually picks the graph backend.
    from backend.app_services.service_factory import create_bioseq_retriever_graph_agent
    return create_bioseq_retriever_graph_agent(use_llm_extractor=False)


def get_agent_warnings() -> list[str]:
    try:
        return list(_get_agent().warnings)
    except Exception as exc:
        return [f"Could not initialize retriever agent: {exc}"]


def run_turn(prompt: str) -> dict[str, Any]:
    """Dispatch one user turn.

    Returns a dict with:
        reply: str               — assistant text to stream
        candidates: list[Candidate] — UI-shaped cards
        candidates_raw: list[dict] — full backend candidate dicts (for DB)
        reveals: set[str]        — protein-card sections to unlock
        warnings: list[str]      — surfaced from agent + persistence
        result: dict             — raw backend state (for debug)
        persisted: bool          — True iff session was written to DB
        backend: str             — which backend produced the result
        update_card: bool        — caller should replace the protein card
                                   when True (False on follow-up turns to
                                   keep the existing selection stable).
    """
    if not _is_first_turn_in_session():
        # Follow-up turn → chat-LLM module. Lazy import keeps stub-only path
        # decoupled from retriever code.
        import chat_llm_pipeline  # noqa: WPS433

        return chat_llm_pipeline.run_turn_chat_llm(prompt)

    backend = backend_choice.get_backend()

    if backend == backend_choice.BACKEND_EMBEDDINGS:
        # Lazy import: don't load heavy deps if user is on the graph path.
        import embeddings_pipeline  # noqa: WPS433

        outcome = embeddings_pipeline.run_turn_embeddings(prompt)
        outcome["backend"] = backend
        outcome.setdefault("update_card", True)
        return outcome

    # default: graph backend
    outcome = _run_turn_graph(prompt)
    outcome["backend"] = backend_choice.BACKEND_GRAPH
    outcome.setdefault("update_card", True)
    return outcome


def _is_first_turn_in_session() -> bool:
    """Return True if this is the first user turn in the active session.

    Reads ``working_memory.turn_count`` from ``public.chat_sessions``. If the
    session row doesn't exist or the counter is 0, this is the first turn —
    route to a retriever. Any positive count means there's already history,
    so route to the chat-LLM module.

    Falls back to ``True`` (route to retriever) when DB persistence is off
    or the session id is missing — that way the UI degrades gracefully
    without history rather than dropping into the chat-LLM stub forever.
    """
    session_id = st.session_state.get("session_id")
    if not session_id:
        return True
    if not session_db_adapter.is_persistent():
        return True
    row = session_db_adapter.load_session(session_id)
    if not row:
        return True
    working_memory = row.get("working_memory") or {}
    if isinstance(working_memory, str):
        # Some psycopg / driver combinations may surface jsonb as text.
        try:
            import json
            working_memory = json.loads(working_memory)
        except Exception:
            working_memory = {}
    if not isinstance(working_memory, dict):
        return True
    try:
        turn_count = int(working_memory.get("turn_count") or 0)
    except (TypeError, ValueError):
        turn_count = 0
    return turn_count == 0


def _run_turn_graph(prompt: str) -> dict[str, Any]:
    user_id = st.session_state.get("user_id") or "anonymous"
    session_id = st.session_state.get("session_id")
    if not session_id:
        raise RuntimeError("session_id is missing from st.session_state; bootstrap_identity() must run first.")

    context = session_db_adapter.make_context(
        user_id=user_id,
        session_id=session_id,
        workspace_id=st.session_state.get("workspace_id"),
        user_role=st.session_state.get("user_role"),
    )

    warnings: list[str] = list(session_db_adapter.get_warnings())

    try:
        agent = _get_agent()
    except Exception as exc:
        reply = f"**Backend unavailable:** {exc}"
        warnings.append(str(exc))
        _safe_save_turn(context, prompt, reply, [], set(), warnings)
        return {
            "reply": reply,
            "candidates": [],
            "candidates_raw": [],
            "reveals": set(),
            "warnings": warnings,
            "result": {"error": str(exc)},
            "persisted": session_db_adapter.is_persistent(),
        }

    warnings.extend(agent.warnings)

    try:
        result, _state = agent.invoke(prompt, context)
    except Exception as exc:
        # Even on a hard agent failure we record the turn so the user's
        # question and the failure are visible in session history.
        reply = f"**Retriever error:** {exc}"
        warnings.append(str(exc))
        _safe_save_turn(context, prompt, reply, [], set(), warnings)
        return {
            "reply": reply,
            "candidates": [],
            "candidates_raw": [],
            "reveals": set(),
            "warnings": warnings,
            "result": {"error": str(exc)},
            "persisted": session_db_adapter.is_persistent(),
        }

    raw_candidates: list[dict[str, Any]] = list(result.get("final_results") or [])
    ui_candidates = [_candidate_from_backend(item) for item in raw_candidates]
    reply = _assistant_message(result)
    reveals = _revealed_sections(ui_candidates)

    _safe_save_turn(context, prompt, reply, raw_candidates, reveals, warnings)

    return {
        "reply": reply,
        "candidates": ui_candidates,
        "candidates_raw": raw_candidates,
        "reveals": reveals,
        "warnings": warnings,
        "result": result,
        "persisted": session_db_adapter.is_persistent(),
    }


def _safe_save_turn(
    context,
    prompt: str,
    reply: str,
    raw_candidates: list[dict[str, Any]],
    reveals: set[str],
    warnings: list[str],
) -> None:
    try:
        session_db_adapter.save_turn(
            context,
            user_message=prompt,
            assistant_message=reply,
            candidates=raw_candidates,
            revealed_sections=reveals,
        )
    except Exception as exc:
        warnings.append(f"Could not save session turn: {exc}")


def restore_session_state(session_id: str) -> dict[str, Any]:
    """Load a previous session into the UI state and return a status dict."""
    row = session_db_adapter.load_session(session_id)
    if not row:
        return {"loaded": False, "candidates": [], "messages": []}

    raw_candidates = session_db_adapter.extract_candidates(row)
    ui_candidates = [_candidate_from_backend(item) for item in raw_candidates]
    messages = session_db_adapter.extract_messages(row)
    reveals = _revealed_sections(ui_candidates)

    st.session_state.candidates = ui_candidates
    st.session_state.selected_candidate_idx = 0
    st.session_state.card_sections_revealed = set(reveals)
    if messages:
        st.session_state.messages = [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]

    # Restore the backend the session was last using. ``current_mode`` is what
    # the most recent turn wrote, which corresponds to the user's selection at
    # that moment. Falls back to whatever default was already in session_state.
    last_mode = (row.get("current_mode") or "").strip().lower()
    if last_mode == "embeddings_retriever":
        backend_choice.set_backend(backend_choice.BACKEND_EMBEDDINGS)
    elif last_mode in {"bioseq_retriever_langgraph", "streamlit_ui", "graph_retriever_pipeline"}:
        backend_choice.set_backend(backend_choice.BACKEND_GRAPH)

    return {
        "loaded": True,
        "candidates": ui_candidates,
        "messages": messages,
        "row": row,
    }


def auto_restore_if_fresh_load(session_id: str) -> bool:
    """Restore session state on Streamlit cold-start (e.g. browser reload).

    Only fires when the chat history still looks like the default welcome
    state — that way it doesn't clobber an in-progress conversation when the
    user clicks around the sidebar mid-session.
    """
    if not session_id:
        return False
    if not session_db_adapter.is_persistent():
        return False

    if st.session_state.get("_auto_restore_attempted") == session_id:
        return False
    st.session_state["_auto_restore_attempted"] = session_id

    messages = st.session_state.get("messages") or []
    looks_fresh = (
        len(messages) <= 1
        and (not messages or messages[0].get("role") == "assistant")
        and not st.session_state.get("candidates")
    )
    if not looks_fresh:
        return False

    outcome = restore_session_state(session_id)
    return bool(outcome.get("loaded"))


# ---------------------------------------------------------------------------
# Backend → UI mapping (kept compatible with vector_db_adapter so the protein
# card and chat column don't need schema changes for this task).
# ---------------------------------------------------------------------------


def _assistant_message(state: dict[str, Any]) -> str:
    if state.get("error"):
        return f"**Pipeline error:** {state['error']}"

    final_results = state.get("final_results") or []
    if not final_results:
        return "Retriever pipeline completed without final matches."

    lines = [
        "**Retriever result:**",
        "",
        f"- Detected type: `{state.get('sequence_type') or 'unknown'}`",
        f"- Classification confidence: `{state.get('is_confident')}`",
        f"- Final matches: `{len(final_results)}`",
        "",
        "**Top matches:**",
    ]
    for index, record in enumerate(final_results[:5], 1):
        protein = record.get("protein") or {}
        accession = protein.get("accession") or "unknown"
        name = protein.get("name") or protein.get("protein_name") or "Unknown protein"
        gene = protein.get("gene") or protein.get("gene_primary") or ""
        suffix = f" ({gene})" if gene else ""
        lines.append(f"{index}. `{accession}` — {name}{suffix}")
    return "\n".join(lines)


def _candidate_from_backend(record: dict[str, Any]) -> Candidate:
    protein = _protein_from_backend(record.get("protein") or {})
    return Candidate(
        protein=protein,
        match_score=_score_as_percent(record.get("match_score")),
    )


def _protein_from_backend(raw: dict[str, Any]) -> ProteinView:
    accession = str(_first(raw, "accession", default=""))
    return ProteinView(
        accession=accession,
        name=str(_first(raw, "name", "protein_name", default=accession or "Unknown protein")),
        alt_names=_as_str_list(raw.get("alt_names")),
        gene=str(_first(raw, "gene", "gene_primary", "gene_name", default="") or ""),
        organism_scientific=str(_first(raw, "organism_scientific", "organism_name", default="") or ""),
        organism_common=str(_first(raw, "organism_common", "organism_common_name", default="") or ""),
        taxon_id=_as_int(raw.get("taxon_id")),
        annotation_score=_as_float(raw.get("annotation_score")),
        reviewed=bool(raw.get("reviewed")),
        existence=str(_first(raw, "existence", "protein_existence", default="") or ""),
        length=_as_int(_first(raw, "length", "sequence_length", default=0)),
        mol_weight=_as_int(raw.get("mol_weight")),
        subcellular_locations=_as_str_list(raw.get("subcellular_locations")),
        function_text=str(raw.get("function_text") or ""),
        disease=_disease_from_backend(raw.get("disease")),
        domains=_domains_from_backend(raw.get("domains")),
        keywords=_as_str_list(raw.get("keywords")),
        go_terms=_as_str_list(raw.get("go_terms")),
        pubmed_ids=_as_str_list(raw.get("pubmed_ids")),
        xrefs={str(k): str(v) for k, v in (raw.get("xrefs") or {}).items() if v not in (None, "")},
        alphafold_accession=str(raw.get("alphafold_accession") or accession),
        sequence=str(_first(raw, "sequence", "protein_sequence", default="") or ""),
    )


def _disease_from_backend(value: Any) -> DiseaseInfo | None:
    if not isinstance(value, dict):
        return None
    names = _as_str_list(value.get("names"))
    xrefs = value.get("xrefs") if isinstance(value.get("xrefs"), dict) else {}
    name = value.get("name") or (names[0] if names else "")
    count = _as_int(value.get("count"), default=len(names))
    if not name and count == 0:
        return None
    return DiseaseInfo(
        name=str(name or "Disease association"),
        acronym=str(value.get("acronym") or ""),
        mim_id=str(value.get("mim_id") or xrefs.get("MIM") or ""),
        description=str(value.get("description") or ""),
        variants=_as_str_list(value.get("variants")),
    )


def _domains_from_backend(value: Any) -> list[DomainFeature]:
    if not isinstance(value, list):
        return []
    domains: list[DomainFeature] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        start = _as_int(item.get("start"), default=0)
        end = _as_int(item.get("end"), default=0)
        if start <= 0 or end <= 0:
            continue
        domains.append(
            DomainFeature(
                type=str(item.get("type") or "Domain"),
                name=str(item.get("name") or item.get("description") or "Domain"),
                start=start,
                end=end,
            )
        )
    return domains


def _revealed_sections(candidates: list[Candidate]) -> set[str]:
    if not candidates:
        return set()
    protein = candidates[0]["protein"]
    sections = {"header", "keyfacts", "structure"}
    if protein["function_text"]:
        sections.add("function")
    if protein["domains"]:
        sections.add("domains")
    if protein["keywords"] or protein["go_terms"]:
        sections.add("keywords")
    if protein["disease"]:
        sections.add("disease")
    if protein["pubmed_ids"] or protein["xrefs"]:
        sections.add("references")
    return sections


def _first(source: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None:
            return value
    return default


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _score_as_percent(value: Any) -> float:
    score = _as_float(value)
    if score <= 0:
        return 0.0
    if score <= 1:
        return score * 100
    return score

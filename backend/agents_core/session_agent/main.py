import argparse
import json
import os
import re
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentState
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_URI = "neo4j+s://dfb7807d.databases.neo4j.io"
DEFAULT_DATABASE = "dfb7807d"
DEFAULT_MODEL = "gpt-4.1-nano"
SESSION_STATE_KEYS = (
    "session_summary",
    "proteins",
    "sequences",
    "working_memory",
    "active_sequence_id",
    "active_accession",
    "last_analysis_summary",
    "working_set_ids",
    "current_mode",
    "last_tool_results_summary",
)
AMINO_ACID_SEQUENCE_RE = re.compile(r"\b[ACDEFGHIKLMNPQRSTVWY]{10,}\b", re.IGNORECASE)
MAX_TRACKED_PROTEINS = 20
MAX_TRACKED_SEQUENCES = 20
MAX_WORKING_SET_IDS = 40


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(DEFAULT_ENV_PATH)


def resolve_driver_uri(uri: str, insecure: bool) -> str:
    if not insecure:
        return uri
    if uri.startswith("neo4j+s://"):
        return "neo4j+ssc://" + uri[len("neo4j+s://") :]
    if uri.startswith("bolt+s://"):
        return "bolt+ssc://" + uri[len("bolt+s://") :]
    return uri


class Neo4jGraphClient:
    def __init__(self, uri: str, user: str, password: str, database: str) -> None:
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database

    def _execute(self, query: str, **params: Any) -> dict[str, Any]:
        try:
            return self._execute_once(self.uri, query, **params)
        except ServiceUnavailable as exc:
            fallback_uri = resolve_driver_uri(self.uri, insecure=True)
            if fallback_uri != self.uri:
                return self._execute_once(fallback_uri, query, **params)
            raise exc

    def _execute_once(self, uri: str, query: str, **params: Any) -> dict[str, Any]:
        with GraphDatabase.driver(uri, auth=(self.user, self.password)) as driver:
            records, summary, keys = driver.execute_query(
                query,
                database_=self.database,
                **params,
            )
        return {
            "keys": list(keys),
            "records": [record.data() for record in records],
            "query": summary.query,
        }


def ensure_read_only_cypher(query: str) -> str:
    normalized = " ".join(query.strip().split())
    lowered = normalized.lower()
    forbidden = [
        " create ",
        " merge ",
        " delete ",
        " detach ",
        " set ",
        " remove ",
        " drop ",
        " load csv ",
        " call dbms ",
        " call apoc.",
    ]
    wrapped = f" {lowered} "
    if any(token in wrapped for token in forbidden):
        raise ValueError("Only read-only Cypher queries are allowed.")
    if not lowered.startswith(("match", "optional match", "with", "call")):
        raise ValueError("Cypher query must start with MATCH, OPTIONAL MATCH, WITH, or CALL.")
    return normalized


@dataclass
class AppContext:
    user_id: str
    session_id: str
    workspace_id: str | None = None
    user_role: str | None = None


class SessionAgentState(AgentState[None], total=False):
    session_summary: str | None
    proteins: list[dict[str, Any]]
    sequences: list[dict[str, Any]]
    working_memory: dict[str, Any]
    active_sequence_id: str | None
    active_accession: str | None
    last_analysis_summary: str | None
    working_set_ids: list[str]
    current_mode: str | None
    last_tool_results_summary: str | None


class NullSessionRepository:
    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return None

    def upsert_session(self, context: AppContext, state: dict[str, Any]) -> None:
        return None

    def close(self) -> None:
        return None


class PostgresSessionRepository:
    def __init__(self, db_url: str) -> None:
        import psycopg

        self._conn = psycopg.connect(db_url, autocommit=True)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                select
                    session_id,
                    thread_id,
                    user_id,
                    workspace_id,
                    user_role,
                    session_summary,
                    proteins,
                    sequences,
                    working_memory,
                    active_sequence_id,
                    active_accession,
                    last_analysis_summary,
                    working_set_ids,
                    current_mode,
                    last_tool_results_summary,
                    created_at,
                    updated_at
                from public.chat_sessions
                where session_id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            columns = [desc.name for desc in cur.description]
        return dict(zip(columns, row, strict=False))

    def upsert_session(self, context: AppContext, state: dict[str, Any]) -> None:
        payload = build_session_row(context, state)
        with self._conn.cursor() as cur:
            cur.execute(
                """
                insert into public.chat_sessions (
                    session_id,
                    thread_id,
                    user_id,
                    workspace_id,
                    user_role,
                    session_summary,
                    proteins,
                    sequences,
                    working_memory,
                    active_sequence_id,
                    active_accession,
                    last_analysis_summary,
                    working_set_ids,
                    current_mode,
                    last_tool_results_summary
                ) values (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s::jsonb,
                    %s::jsonb,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s,
                    %s
                )
                on conflict (session_id) do update
                set
                    thread_id = excluded.thread_id,
                    user_id = excluded.user_id,
                    workspace_id = excluded.workspace_id,
                    user_role = excluded.user_role,
                    session_summary = excluded.session_summary,
                    proteins = excluded.proteins,
                    sequences = excluded.sequences,
                    working_memory = excluded.working_memory,
                    active_sequence_id = excluded.active_sequence_id,
                    active_accession = excluded.active_accession,
                    last_analysis_summary = excluded.last_analysis_summary,
                    working_set_ids = excluded.working_set_ids,
                    current_mode = excluded.current_mode,
                    last_tool_results_summary = excluded.last_tool_results_summary
                """,
                (
                    payload["session_id"],
                    payload["thread_id"],
                    payload["user_id"],
                    payload["workspace_id"],
                    payload["user_role"],
                    payload["session_summary"],
                    json.dumps(payload["proteins"], ensure_ascii=False),
                    json.dumps(payload["sequences"], ensure_ascii=False),
                    json.dumps(payload["working_memory"], ensure_ascii=False),
                    payload["active_sequence_id"],
                    payload["active_accession"],
                    payload["last_analysis_summary"],
                    json.dumps(payload["working_set_ids"], ensure_ascii=False),
                    payload["current_mode"],
                    payload["last_tool_results_summary"],
                ),
            )

    def close(self) -> None:
        self._conn.close()


@dataclass
class PersistenceResources:
    checkpointer: Any
    store: Any
    session_repository: NullSessionRepository | PostgresSessionRepository
    mode: str
    warnings: list[str]


def build_session_row(context: AppContext, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": context.session_id,
        "thread_id": context.session_id,
        "user_id": context.user_id,
        "workspace_id": context.workspace_id,
        "user_role": context.user_role,
        "session_summary": state.get("session_summary"),
        "proteins": list(state.get("proteins", [])),
        "sequences": list(state.get("sequences", [])),
        "working_memory": dict(state.get("working_memory", {})),
        "active_sequence_id": state.get("active_sequence_id"),
        "active_accession": state.get("active_accession"),
        "last_analysis_summary": state.get("last_analysis_summary"),
        "working_set_ids": list(state.get("working_set_ids", [])),
        "current_mode": state.get("current_mode"),
        "last_tool_results_summary": state.get("last_tool_results_summary"),
    }


def create_persistence_resources(db_url: str | None, exit_stack: ExitStack) -> PersistenceResources:
    warnings: list[str] = []

    if db_url:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore[import-not-found]
            from langgraph.store.postgres import PostgresStore  # type: ignore[import-not-found]

            checkpointer = exit_stack.enter_context(PostgresSaver.from_conn_string(db_url))
            checkpointer.setup()
            store = exit_stack.enter_context(PostgresStore.from_conn_string(db_url))
            store.setup()
            session_repository: NullSessionRepository | PostgresSessionRepository = PostgresSessionRepository(db_url)
            return PersistenceResources(
                checkpointer=checkpointer,
                store=store,
                session_repository=session_repository,
                mode="postgres",
                warnings=warnings,
            )
        except ImportError as exc:
            warnings.append(
                "Postgres persistence packages are missing; falling back to in-memory persistence. "
                f"Install `langgraph-checkpoint-postgres` and `psycopg[binary]` to enable Supabase-backed memory. ({exc})"
            )
        except Exception as exc:
            warnings.append(
                "Could not initialize Supabase/Postgres persistence; falling back to in-memory persistence. "
                f"Reason: {exc}"
            )
    else:
        warnings.append("SUPABASE_DB_URL is not set; using in-memory persistence.")

    return PersistenceResources(
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        session_repository=NullSessionRepository(),
        mode="memory",
        warnings=warnings,
    )


def get_message_text(message: BaseMessage | dict[str, Any] | str) -> str:
    if isinstance(message, str):
        return message

    if isinstance(message, dict):
        return str(message.get("content", ""))

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if text:
                    parts.append(str(text))
        return "\n".join(parts)
    return str(content)


def get_message_role(message: BaseMessage | dict[str, Any] | str) -> str:
    if isinstance(message, dict):
        return str(message.get("role", "unknown"))
    if isinstance(message, str):
        return "text"
    return str(getattr(message, "type", "unknown"))


def serialize_message(message: BaseMessage | dict[str, Any] | str) -> dict[str, Any]:
    return {
        "role": get_message_role(message),
        "content": get_message_text(message),
    }


def maybe_parse_json_records(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text or text[0] not in "[{":
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []

    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def merge_unique_records(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    key_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}

    for item in existing + incoming:
        key = tuple(item.get(field) for field in key_fields)
        if not any(value is not None for value in key):
            continue
        current = by_key.get(key, {})
        merged = dict(current)
        merged.update({k: v for k, v in item.items() if v is not None})
        by_key[key] = merged

    return list(by_key.values())


def trim_tail(items: list[Any], limit: int) -> list[Any]:
    if len(items) <= limit:
        return items
    return items[-limit:]


def extract_proteins(messages: list[Any], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []

    for message in messages:
        for record in maybe_parse_json_records(get_message_text(message)):
            if record.get("accession"):
                extracted.append(
                    {
                        "accession": record.get("accession"),
                        "gene_name": record.get("gene_primary"),
                        "protein_name": record.get("protein_name"),
                        "source": "tool_output",
                        "status": "active",
                        "notes": record.get("organism_name"),
                    }
                )
            if record.get("neighbor_accession"):
                extracted.append(
                    {
                        "accession": record.get("neighbor_accession"),
                        "gene_name": record.get("neighbor_gene"),
                        "protein_name": record.get("neighbor_protein_name"),
                        "source": "tool_output",
                        "status": "candidate_neighbor",
                        "notes": record.get("neighbor_organism"),
                    }
                )
            if record.get("target_accession"):
                extracted.append(
                    {
                        "accession": record.get("target_accession"),
                        "gene_name": None,
                        "protein_name": None,
                        "source": "tool_output",
                        "status": "active",
                        "notes": "Target accession from disease-context summary",
                    }
                )

    return merge_unique_records(existing, extracted, ("accession",))


def extract_sequences(messages: list[Any], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []

    for message in messages:
        text = get_message_text(message)
        role = get_message_role(message)
        for raw_sequence in AMINO_ACID_SEQUENCE_RE.findall(text):
            normalized = raw_sequence.upper()
            extracted.append(
                {
                    "sequence_id": f"seq_{uuid.uuid5(uuid.NAMESPACE_OID, normalized).hex[:12]}",
                    "sequence_type": "protein",
                    "raw_sequence": normalized,
                    "label": f"{role}_sequence",
                    "source": role,
                    "linked_accession": None,
                }
            )

    return merge_unique_records(existing, extracted, ("sequence_id",))


def summarize_text(text: str, limit: int = 800) -> str | None:
    compact = " ".join(text.strip().split())
    if not compact:
        return None
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def derive_session_patch(state: dict[str, Any]) -> dict[str, Any]:
    messages = list(state.get("messages", []))
    existing_proteins = list(state.get("proteins", []))
    existing_sequences = list(state.get("sequences", []))
    proteins = trim_tail(extract_proteins(messages, existing_proteins), MAX_TRACKED_PROTEINS)
    sequences = trim_tail(extract_sequences(messages, existing_sequences), MAX_TRACKED_SEQUENCES)

    ai_messages = [message for message in messages if get_message_role(message) == "ai"]
    tool_messages = [message for message in messages if get_message_role(message) == "tool"]
    last_ai_text = get_message_text(ai_messages[-1]) if ai_messages else ""
    last_tool_text = get_message_text(tool_messages[-1]) if tool_messages else ""

    patch = {
        "session_summary": summarize_text(last_ai_text),
        "proteins": proteins,
        "sequences": sequences,
        "working_memory": {
            **dict(state.get("working_memory", {})),
            "message_count": len(messages),
            "last_sync_source": "session_agent",
        },
        "active_sequence_id": sequences[-1]["sequence_id"] if sequences else state.get("active_sequence_id"),
        "active_accession": proteins[-1]["accession"] if proteins else state.get("active_accession"),
        "last_analysis_summary": summarize_text(last_ai_text, limit=400),
        "working_set_ids": trim_tail(
            [
                *[protein["accession"] for protein in proteins if protein.get("accession")],
                *[sequence["sequence_id"] for sequence in sequences if sequence.get("sequence_id")],
            ],
            MAX_WORKING_SET_IDS,
        ),
        "current_mode": state.get("current_mode") or "graph_analysis",
        "last_tool_results_summary": summarize_text(last_tool_text, limit=400),
    }
    return patch


def build_tools(client: Neo4jGraphClient):
    def upsert_store_value(namespace: tuple[str, ...], key: str, patch: dict[str, Any], runtime: ToolRuntime[AppContext]) -> dict[str, Any]:
        assert runtime.store is not None
        current = runtime.store.get(namespace, key)
        merged = dict(current.value) if current else {}
        merged.update(patch)
        runtime.store.put(namespace, key, merged)
        return merged

    @tool
    def graph_schema_guide() -> str:
        """Return the graph schema and query-writing guidance for this Neo4j database."""
        return (
            "Graph schema:\n"
            "- Nodes:\n"
            "  Protein {row_id, accession, dataset, entry_name, protein_name, gene_primary, "
            "organism_name, sequence_length, reviewed, annotation_score, protein_existence, "
            "ensembl_ids, disease_count?, disease_names?}\n"
            "  Disease {disease_accession, disease_id, disease_acronym, disease_description, "
            "disease_xref_db, disease_xref_id, association_source}\n"
            "- Relationships:\n"
            "  (:Protein)-[:SIMILAR_TO {cosine_sim}]->(:Protein)\n"
            "  (:Protein)-[:ASSOCIATED_WITH {association_note, association_source}]->(:Disease)\n"
            "- Query guidance:\n"
            "  Use accession or gene_primary to find proteins.\n"
            "  Use cosine_sim DESC for strongest neighbors.\n"
            "  Disease nodes may be absent if no disease annotations were loaded.\n"
            "  Always prefer read-only MATCH/OPTIONAL MATCH/RETURN queries."
        )

    @tool
    def find_proteins(search_text: str, limit: int = 10) -> str:
        """Find proteins by accession, gene name, entry name, or protein name."""
        result = client._execute(
            """
            MATCH (p:Protein)
            WHERE toLower(p.accession) CONTAINS toLower($search_text)
               OR toLower(coalesce(p.gene_primary, "")) CONTAINS toLower($search_text)
               OR toLower(coalesce(p.entry_name, "")) CONTAINS toLower($search_text)
               OR toLower(coalesce(p.protein_name, "")) CONTAINS toLower($search_text)
            RETURN p.row_id AS row_id,
                   p.accession AS accession,
                   p.gene_primary AS gene_primary,
                   p.entry_name AS entry_name,
                   p.protein_name AS protein_name,
                   p.organism_name AS organism_name
            ORDER BY p.reviewed DESC, p.annotation_score DESC, p.accession ASC
            LIMIT $limit
            """,
            search_text=search_text,
            limit=limit,
        )
        return json.dumps(result["records"], ensure_ascii=False, indent=2)

    @tool
    def get_protein_neighbors(accession: str, limit: int = 10) -> str:
        """Get the most similar neighboring proteins for a given accession."""
        result = client._execute(
            """
            MATCH (p:Protein {accession: $accession})-[r:SIMILAR_TO]->(n:Protein)
            RETURN p.accession AS accession,
                   n.accession AS neighbor_accession,
                   n.gene_primary AS neighbor_gene,
                   n.entry_name AS neighbor_entry_name,
                   n.protein_name AS neighbor_protein_name,
                   n.organism_name AS neighbor_organism,
                   r.cosine_sim AS cosine_sim
            ORDER BY r.cosine_sim DESC
            LIMIT $limit
            """,
            accession=accession,
            limit=limit,
        )
        return json.dumps(result["records"], ensure_ascii=False, indent=2)

    @tool
    def get_neighbor_diseases(accession: str, neighbor_limit: int = 15, disease_limit: int = 20) -> str:
        """Aggregate diseases observed among the nearest neighbors of a protein."""
        result = client._execute(
            """
            MATCH (:Protein {accession: $accession})-[:SIMILAR_TO]->(n:Protein)
            WITH n
            ORDER BY n.annotation_score DESC
            LIMIT $neighbor_limit
            OPTIONAL MATCH (n)-[rel:ASSOCIATED_WITH]->(d:Disease)
            WITH n, d, rel
            WHERE d IS NOT NULL
            RETURN d.disease_id AS disease_id,
                   d.disease_accession AS disease_accession,
                   count(DISTINCT n) AS neighbor_hits,
                   collect(DISTINCT n.accession)[0..5] AS example_neighbors,
                   collect(DISTINCT rel.association_source)[0..3] AS sources
            ORDER BY neighbor_hits DESC, disease_id ASC
            LIMIT $disease_limit
            """,
            accession=accession,
            neighbor_limit=neighbor_limit,
            disease_limit=disease_limit,
        )
        return json.dumps(result["records"], ensure_ascii=False, indent=2)

    @tool
    def summarize_neighbor_disease_context(accession: str, neighbor_limit: int = 15, disease_limit: int = 10) -> str:
        """Summarize diseases shared across the nearest neighbors of a protein, with example proteins."""
        result = client._execute(
            """
            MATCH (p:Protein {accession: $accession})-[:SIMILAR_TO]->(n:Protein)
            WITH p, n
            ORDER BY n.annotation_score DESC
            LIMIT $neighbor_limit
            OPTIONAL MATCH (n)-[rel:ASSOCIATED_WITH]->(d:Disease)
            WITH p, n, rel, d
            WHERE d IS NOT NULL
            RETURN p.accession AS target_accession,
                   d.disease_id AS disease_id,
                   d.disease_accession AS disease_accession,
                   d.disease_description AS disease_description,
                   count(DISTINCT n) AS neighbor_hits,
                   collect(DISTINCT {
                       accession: n.accession,
                       gene_primary: n.gene_primary,
                       protein_name: n.protein_name
                   })[0..5] AS example_neighbors,
                   collect(DISTINCT rel.association_note)[0..3] AS example_notes
            ORDER BY neighbor_hits DESC, disease_id ASC
            LIMIT $disease_limit
            """,
            accession=accession,
            neighbor_limit=neighbor_limit,
            disease_limit=disease_limit,
        )
        return json.dumps(result["records"], ensure_ascii=False, indent=2)

    @tool
    def run_read_cypher(query: str) -> str:
        """Run a custom read-only Cypher query against the graph."""
        safe_query = ensure_read_only_cypher(query)
        result = client._execute(safe_query)
        return json.dumps(result["records"], ensure_ascii=False, indent=2)

    @tool
    def save_user_profile(name: str | None = None, language: str | None = None, answer_style: str | None = None, runtime: ToolRuntime[AppContext] = None) -> str:
        """Save durable user profile data that should persist across sessions."""
        patch = {k: v for k, v in {"name": name, "language": language, "answer_style": answer_style}.items() if v}
        merged = upsert_store_value(("users",), runtime.context.user_id, patch, runtime)
        return json.dumps(merged, ensure_ascii=False, indent=2)

    @tool
    def get_user_profile(runtime: ToolRuntime[AppContext]) -> str:
        """Read the saved user profile for the current user."""
        assert runtime.store is not None
        item = runtime.store.get(("users",), runtime.context.user_id)
        return json.dumps(item.value if item else {}, ensure_ascii=False, indent=2)

    @tool
    def save_user_preference(preference_key: str, preference_value: str, runtime: ToolRuntime[AppContext]) -> str:
        """Save a durable user preference such as language or answer style."""
        merged = upsert_store_value(("preferences",), runtime.context.user_id, {preference_key: preference_value}, runtime)
        return json.dumps(merged, ensure_ascii=False, indent=2)

    @tool
    def save_user_fact(fact: str, runtime: ToolRuntime[AppContext]) -> str:
        """Save a reusable user fact that may matter in future sessions."""
        assert runtime.store is not None
        fact_id = f"fact_{uuid.uuid5(uuid.NAMESPACE_URL, fact).hex[:12]}"
        runtime.store.put(("saved_facts", runtime.context.user_id), fact_id, {"fact": fact})
        return f"Saved fact {fact_id}"

    @tool
    def save_investigation_default(setting_key: str, setting_value: str, runtime: ToolRuntime[AppContext]) -> str:
        """Save durable default investigation settings for this user."""
        merged = upsert_store_value(("investigation_defaults",), runtime.context.user_id, {setting_key: setting_value}, runtime)
        return json.dumps(merged, ensure_ascii=False, indent=2)

    @tool
    def get_investigation_defaults(runtime: ToolRuntime[AppContext]) -> str:
        """Read saved default investigation settings for the current user."""
        assert runtime.store is not None
        item = runtime.store.get(("investigation_defaults",), runtime.context.user_id)
        return json.dumps(item.value if item else {}, ensure_ascii=False, indent=2)

    @tool
    def get_session_context(runtime: ToolRuntime[AppContext]) -> str:
        """Return the current session context and compact session state."""
        state = runtime.state
        payload = {
            "context": {
                "user_id": runtime.context.user_id,
                "session_id": runtime.context.session_id,
                "workspace_id": runtime.context.workspace_id,
                "user_role": runtime.context.user_role,
            },
            "state": {key: state.get(key) for key in SESSION_STATE_KEYS},
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    return [
        graph_schema_guide,
        find_proteins,
        get_protein_neighbors,
        get_neighbor_diseases,
        summarize_neighbor_disease_context,
        run_read_cypher,
        save_user_profile,
        get_user_profile,
        save_user_preference,
        save_user_fact,
        save_investigation_default,
        get_investigation_defaults,
        get_session_context,
    ]


SYSTEM_PROMPT = """You are a bioinformatics graph assistant for this project.

Your job is to help the user explore a Neo4j graph of proteins, similarity edges, and optional disease links.

Rules:
- Prefer the domain tools before writing custom Cypher.
- Use graph_schema_guide first when the graph shape matters.
- Be explicit about uncertainty.
- Treat similarity as hypothesis-generating evidence, not proof of function or disease causality.
- If disease results are empty, say that the graph may not have disease annotations loaded yet.
- When asked about common diseases around a protein, prefer summarize_neighbor_disease_context.
- When answering biological questions, ground your summary in the returned graph data.
- Use save_user_profile, save_user_preference, save_user_fact, and save_investigation_default when the user shares durable information that should persist across sessions.
- Use get_user_profile or get_session_context when the answer depends on remembered user context or the current session state.
"""


class SessionGraphAgent:
    def __init__(self, model_name: str, client: Neo4jGraphClient, persistence: PersistenceResources) -> None:
        self._persistence = persistence
        model = ChatOpenAI(model=model_name, temperature=0)
        self._agent = create_agent(
            model=model,
            tools=build_tools(client),
            system_prompt=SYSTEM_PROMPT,
            state_schema=SessionAgentState,
            context_schema=AppContext,
            checkpointer=persistence.checkpointer,
            store=persistence.store,
        )

    @property
    def warnings(self) -> list[str]:
        return self._persistence.warnings

    @property
    def persistence_mode(self) -> str:
        return self._persistence.mode

    def invoke(self, message: str, context: AppContext) -> tuple[dict[str, Any], dict[str, Any]]:
        config = {"configurable": {"thread_id": context.session_id}}
        seed_input = self._build_input_payload(message, context, config)

        self._persistence.session_repository.upsert_session(context, {})
        result = self._agent.invoke(seed_input, config=config, context=context)

        state_snapshot = self._agent.get_state(config)
        current_state = dict(state_snapshot.values)
        patch = derive_session_patch(current_state)
        if patch:
            self._agent.update_state(config, patch)
            state_snapshot = self._agent.get_state(config)
            current_state = dict(state_snapshot.values)

        self._persistence.session_repository.upsert_session(context, current_state)
        return result, current_state

    def get_current_state(self, context: AppContext) -> dict[str, Any]:
        config = {"configurable": {"thread_id": context.session_id}}
        snapshot = self._agent.get_state(config)
        return dict(snapshot.values)

    def get_message_history(self, context: AppContext) -> list[dict[str, Any]]:
        state = self.get_current_state(context)
        messages = state.get("messages", [])
        return [serialize_message(message) for message in messages]

    def _build_input_payload(self, message: str, context: AppContext, config: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"messages": [{"role": "user", "content": message}]}
        snapshot = self._agent.get_state(config)
        current_state = dict(snapshot.values)
        if current_state.get("messages"):
            return payload

        saved = self._persistence.session_repository.get_session(context.session_id)
        if not saved:
            return payload

        for key in SESSION_STATE_KEYS:
            value = saved.get(key)
            if value is not None:
                payload[key] = value
        return payload


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", help="Single message to send to the agent.")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--uri", default=os.getenv("NEO4J_URI", DEFAULT_URI))
    parser.add_argument("--database", default=os.getenv("NEO4J_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--user", default=os.getenv("NEO4J_USERNAME", os.getenv("USERNAME")))
    parser.add_argument("--password", default=os.getenv("NEO4J_PASSWORD", os.getenv("PASSWORD")))
    parser.add_argument("--user-id", default=os.getenv("APP_USER_ID", "local-user"))
    parser.add_argument("--session-id", default=os.getenv("APP_SESSION_ID", f"session_{uuid.uuid4().hex[:8]}"))
    parser.add_argument("--workspace-id", default=os.getenv("APP_WORKSPACE_ID"))
    parser.add_argument("--user-role", default=os.getenv("APP_USER_ROLE"))
    parser.add_argument("--supabase-db-url", default=os.getenv("SUPABASE_DB_URL"))
    parser.add_argument("--show-session-state", action="store_true", help="Print compact session state after each invocation.")
    parser.add_argument("--dump-history", action="store_true", help="Print the stored message history for the current session and exit.")
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=os.getenv("NEO4J_INSECURE", "1").lower() not in {"0", "false", "no"},
        help="Use neo4j+ssc:// or bolt+ssc:// for environments with self-signed TLS chains.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is missing in the environment or .env file.")
    if not args.user or not args.password:
        raise ValueError("Neo4j credentials are missing in the environment or .env file.")

    context = AppContext(
        user_id=args.user_id,
        session_id=args.session_id,
        workspace_id=args.workspace_id,
        user_role=args.user_role,
    )
    client = Neo4jGraphClient(
        uri=resolve_driver_uri(args.uri, insecure=args.insecure),
        user=args.user,
        password=args.password,
        database=args.database,
    )

    with ExitStack() as exit_stack:
        persistence = create_persistence_resources(args.supabase_db_url, exit_stack)
        exit_stack.callback(persistence.session_repository.close)
        agent = SessionGraphAgent(args.model, client, persistence)

        for warning in agent.warnings:
            print(f"[persistence] {warning}")
        print(f"[persistence] mode={agent.persistence_mode} thread_id={context.session_id} user_id={context.user_id}")

        if args.dump_history:
            history = agent.get_message_history(context)
            print(json.dumps(history, ensure_ascii=False, indent=2))
            return

        if args.message:
            result, session_state = agent.invoke(args.message, context)
            print(result["messages"][-1].content)
            if args.show_session_state:
                print("\n[session_state]")
                print(json.dumps({key: session_state.get(key) for key in SESSION_STATE_KEYS}, ensure_ascii=False, indent=2))
            return

        print("Session-aware graph agent is ready. Type 'exit' to stop.")
        while True:
            try:
                user_input = input("\nYou> ").strip()
            except EOFError:
                print()
                break
            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit"}:
                break

            result, session_state = agent.invoke(user_input, context)
            print(f"\nAgent> {result['messages'][-1].content}")
            if args.show_session_state:
                print("\n[session_state]")
                print(json.dumps({key: session_state.get(key) for key in SESSION_STATE_KEYS}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

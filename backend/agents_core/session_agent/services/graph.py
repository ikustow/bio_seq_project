from __future__ import annotations

from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable


def resolve_driver_uri(uri: str, insecure: bool) -> str:
    if not insecure:
        return uri
    if uri.startswith("neo4j+s://"):
        return "neo4j+ssc://" + uri[len("neo4j+s://") :]
    if uri.startswith("bolt+s://"):
        return "bolt+ssc://" + uri[len("bolt+s://") :]
    return uri


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
    if any(token in f" {lowered} " for token in forbidden):
        raise ValueError("Only read-only Cypher queries are allowed.")
    if not lowered.startswith(("match", "optional match", "with", "call")):
        raise ValueError("Cypher query must start with MATCH, OPTIONAL MATCH, WITH, or CALL.")
    return normalized


class Neo4jGraphClient:
    def __init__(self, uri: str, user: str, password: str, database: str) -> None:
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database

    def execute(self, query: str, **params: Any) -> dict[str, Any]:
        try:
            return self._execute_once(self.uri, query, **params)
        except ServiceUnavailable as exc:
            fallback_uri = resolve_driver_uri(self.uri, insecure=True)
            if fallback_uri == self.uri:
                raise exc
            return self._execute_once(fallback_uri, query, **params)

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

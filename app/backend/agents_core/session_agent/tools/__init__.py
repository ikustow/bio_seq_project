from __future__ import annotations

from ..services.graph import Neo4jGraphClient
from .graph import build_graph_tools
from .memory import build_memory_tools
from .session import build_session_tools


def build_tools(client: Neo4jGraphClient) -> list:
    return [
        *build_graph_tools(client),
        *build_memory_tools(),
        *build_session_tools(),
    ]

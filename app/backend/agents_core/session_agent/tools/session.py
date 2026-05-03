from __future__ import annotations

from langchain.tools import ToolRuntime, tool

from ..config import SESSION_STATE_KEYS
from ..models import AppContext
from .base import dump_json


def build_session_tools() -> list:
    @tool
    def get_session_context(runtime: ToolRuntime[AppContext]) -> str:
        """Return the current session context and compact session state."""
        payload = {
            "context": runtime.context.model_dump(),
            "state": {key: runtime.state.get(key) for key in SESSION_STATE_KEYS},
        }
        return dump_json(payload)

    return [get_session_context]

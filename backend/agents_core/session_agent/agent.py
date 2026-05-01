from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from backend.agents_core.session_agent.config import SESSION_STATE_KEYS, SYSTEM_PROMPT
from backend.agents_core.session_agent.models import AppContext, PersistenceResources, SessionAgentState
from backend.agents_core.session_agent.services.graph import Neo4jGraphClient
from backend.agents_core.session_agent.services.session_state import derive_session_patch, serialize_message
from backend.agents_core.session_agent.tools import build_tools


class SessionGraphAgent:
    def __init__(self, model_name: str, client: Neo4jGraphClient, persistence: PersistenceResources) -> None:
        self._persistence = persistence
        self._agent = create_agent(
            model=ChatOpenAI(model=model_name, temperature=0),
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

        current_state = dict(self._agent.get_state(config).values)
        patch = derive_session_patch(current_state)
        if patch:
            self._agent.update_state(config, patch)
            current_state = dict(self._agent.get_state(config).values)

        self._persistence.session_repository.upsert_session(context, current_state)
        return result, current_state

    def get_current_state(self, context: AppContext) -> dict[str, Any]:
        config = {"configurable": {"thread_id": context.session_id}}
        return dict(self._agent.get_state(config).values)

    def get_message_history(self, context: AppContext) -> list[dict[str, Any]]:
        return [serialize_message(message) for message in self.get_current_state(context).get("messages", [])]

    def _build_input_payload(self, message: str, context: AppContext, config: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"messages": [{"role": "user", "content": message}]}
        current_state = dict(self._agent.get_state(config).values)
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

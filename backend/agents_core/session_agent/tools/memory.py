from __future__ import annotations

import uuid

from langchain.tools import ToolRuntime, tool

from ..models import AppContext
from .base import dump_json, upsert_store_value


def build_memory_tools() -> list:
    @tool
    def save_user_profile(
        name: str | None = None,
        language: str | None = None,
        answer_style: str | None = None,
        runtime: ToolRuntime[AppContext] = None,
    ) -> str:
        """Save durable user profile data that should persist across sessions."""
        patch = {key: value for key, value in {"name": name, "language": language, "answer_style": answer_style}.items() if value}
        return dump_json(upsert_store_value(("users",), runtime.context.user_id, patch, runtime))

    @tool
    def get_user_profile(runtime: ToolRuntime[AppContext]) -> str:
        """Read the saved user profile for the current user."""
        assert runtime.store is not None
        item = runtime.store.get(("users",), runtime.context.user_id)
        return dump_json(item.value if item else {})

    @tool
    def save_user_preference(preference_key: str, preference_value: str, runtime: ToolRuntime[AppContext]) -> str:
        """Save a durable user preference such as language or answer style."""
        return dump_json(
            upsert_store_value(
                ("preferences",),
                runtime.context.user_id,
                {preference_key: preference_value},
                runtime,
            )
        )

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
        return dump_json(
            upsert_store_value(
                ("investigation_defaults",),
                runtime.context.user_id,
                {setting_key: setting_value},
                runtime,
            )
        )

    @tool
    def get_investigation_defaults(runtime: ToolRuntime[AppContext]) -> str:
        """Read saved default investigation settings for the current user."""
        assert runtime.store is not None
        item = runtime.store.get(("investigation_defaults",), runtime.context.user_id)
        return dump_json(item.value if item else {})

    return [
        save_user_profile,
        get_user_profile,
        save_user_preference,
        save_user_fact,
        save_investigation_default,
        get_investigation_defaults,
    ]

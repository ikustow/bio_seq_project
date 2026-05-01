from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime

from ..models import AppContext


def dump_json(payload: Any) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


def upsert_store_value(
    namespace: tuple[str, ...],
    key: str,
    patch: dict[str, Any],
    runtime: ToolRuntime[AppContext],
) -> dict[str, Any]:
    assert runtime.store is not None
    current = runtime.store.get(namespace, key)
    merged = dict(current.value) if current else {}
    merged.update(patch)
    runtime.store.put(namespace, key, merged)
    return merged

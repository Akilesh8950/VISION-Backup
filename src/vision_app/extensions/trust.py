from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


def _normalise(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalise(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalise(item) for item in value]
    return value


def descriptor_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")  # type: ignore[no-any-return]
    if isinstance(item, dict):
        return item
    result: dict[str, Any] = {}
    for key in ("name", "description", "inputSchema", "input_schema", "annotations", "uri", "arguments"):
        if hasattr(item, key):
            result[key] = getattr(item, key)
    return result


def toolset_fingerprint(items: Iterable[Any]) -> str:
    descriptors = [_normalise(descriptor_dict(item)) for item in items]
    descriptors.sort(key=lambda item: str(item.get("name") or item.get("uri") or ""))
    payload = json.dumps(descriptors, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

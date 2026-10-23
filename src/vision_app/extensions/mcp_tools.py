from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

import jsonschema

from ..tools.context import ToolContext
from ..tools.registry import ToolDefinition
from .mcp_config import McpServerConfig

SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass
class McpToolDescriptor:
    name: str
    description: str = ""
    input_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None

    @classmethod
    def from_any(cls, item: Any) -> McpToolDescriptor:
        if isinstance(item, dict):
            return cls(
                name=str(item.get("name") or ""),
                description=str(item.get("description") or ""),
                input_schema=item.get("inputSchema") or item.get("input_schema") or {"type": "object", "properties": {}},
                annotations=item.get("annotations") if isinstance(item.get("annotations"), dict) else None,
            )
        return cls(
            name=str(getattr(item, "name", "") or ""),
            description=str(getattr(item, "description", "") or ""),
            input_schema=getattr(item, "inputSchema", None) or getattr(item, "input_schema", None) or {"type": "object", "properties": {}},
            annotations=getattr(item, "annotations", None) if isinstance(getattr(item, "annotations", None), dict) else None,
        )


def namespaced_name(server_id: str, tool_name: str) -> str:
    left = SAFE_RE.sub("_", server_id).strip("_") or "server"
    right = SAFE_RE.sub("_", tool_name).strip("_") or "tool"
    result = f"mcp__{left}__{right}"
    if len(result) <= 64:
        return result
    suffix = hashlib.sha256(result.encode()).hexdigest()[:8]
    return result[:55] + "_" + suffix


def _normalise_result(result: Any) -> Any:
    if hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")
    if isinstance(result, dict):
        content = result.get("content")
        structured = result.get("structuredContent") or result.get("structured_content")
        if structured is not None:
            return structured
        if isinstance(content, list):
            parts: list[Any] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif item.get("type") in {"image", "audio"}:
                        parts.append({"type": item.get("type"), "mimeType": item.get("mimeType") or item.get("mime_type"), "note": "Media was rendered to the user; binary data was not sent to the text model."})
                    elif item.get("type") == "resource":
                        resource = item.get("resource") or {}
                        parts.append(resource.get("text") or {"uri": resource.get("uri"), "mimeType": resource.get("mimeType")})
                    else:
                        parts.append(item)
                else:
                    parts.append(str(item))
            if len(parts) == 1 and isinstance(parts[0], str):
                return parts[0]
            return parts
    return result


def make_mcp_tool_definition(
    config: McpServerConfig,
    descriptor: McpToolDescriptor,
    call_tool,
) -> ToolDefinition:
    name = namespaced_name(config.id, descriptor.name)
    permission = config.permissions.get(descriptor.name, "ask")
    registry_permission = "policy" if permission == "allow" else permission

    async def handler(args: dict[str, Any], _context: ToolContext) -> Any:
        if descriptor.input_schema:
            jsonschema.validate(instance=args, schema=descriptor.input_schema)
        result = await call_tool(config.id, descriptor.name, args)
        is_error = False
        if hasattr(result, "isError"):
            is_error = bool(result.isError)
        elif isinstance(result, dict):
            is_error = bool(result.get("isError") or result.get("is_error"))
        normalised = _normalise_result(result)
        if is_error:
            raise RuntimeError(str(normalised)[:2000] or "MCP tool returned an error")
        return normalised

    return ToolDefinition(
        name=name,
        description=(descriptor.description or f"MCP tool {descriptor.name} from {config.display_name}")[:1024],
        handler=handler,
        parameters=descriptor.input_schema or {"type": "object", "properties": {}},
        source=f"mcp:{config.id}",
        side_effect="external",
        permission=registry_permission,
    )

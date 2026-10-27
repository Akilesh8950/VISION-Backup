from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

from ..tools.context import ToolContext
from ..tools.registry import ToolDefinition
from .mcp_config import McpServerConfig

SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")
DEFAULT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def _mapping_from_any(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else None
    if isinstance(value, Mapping):
        return {str(key): child for key, child in value.items()}
    return None


def _schema_from_any(value: Any) -> dict[str, Any]:
    if value is None:
        return dict(DEFAULT_SCHEMA)
    schema = _mapping_from_any(value)
    if schema is None:
        raise ValueError("MCP tool inputSchema must be a JSON Schema object")
    return schema


@dataclass
class McpToolDescriptor:
    name: str
    description: str = ""
    input_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None

    @classmethod
    def from_any(cls, item: Any) -> McpToolDescriptor:
        data = _mapping_from_any(item)
        if data is not None:
            name = str(data.get("name") or "").strip()
            description = str(data.get("description") or "")
            raw_schema = data.get("inputSchema", data.get("input_schema"))
            raw_annotations = data.get("annotations")
        else:
            name = str(getattr(item, "name", "") or "").strip()
            description = str(getattr(item, "description", "") or "")
            raw_schema = getattr(item, "inputSchema", None)
            if raw_schema is None:
                raw_schema = getattr(item, "input_schema", None)
            raw_annotations = getattr(item, "annotations", None)
        if not name:
            raise ValueError("MCP tool name is empty")
        return cls(
            name=name,
            description=description,
            input_schema=_schema_from_any(raw_schema),
            annotations=_mapping_from_any(raw_annotations),
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



# Extension tools whose *names* indicate deletion, process termination,
# registry writes or unrestricted shell access are hard-blocked: they are
# listed for transparency but can never be called, whatever the stored
# per-tool permission says. Matching is on name tokens so ordinary tools
# like "remove_from_list" in a todo server still work only if the token
# itself is not destructive... deletion verbs are blocked deliberately.
_DESTRUCTIVE_NAME_TOKENS = {
    "delete", "deletes", "deleting", "erase", "destroy", "wipe", "purge",
    "rmdir", "unlink", "truncate", "format_disk", "kill", "taskkill",
    "terminate_process", "kill_process", "stop_process",
    "regedit", "regwrite", "shell", "bash", "powershell", "cmd", "exec_shell",
    "run_shell", "shell_exec", "run_command", "execute_command",
}
_DESTRUCTIVE_SUBSTRINGS = (
    "delete", "registry_write", "write_registry", "set_registry",
    "registry_set", "taskkill", "format_disk",
)
_DELETION_VERBS = {"remove", "removes", "removing"}
_FILE_TOKENS = {"file", "files", "dir", "dirs", "directory", "folder", "path"}


def is_destructive_tool_name(name: str) -> bool:
    lowered = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower())
    tokens = {t for t in lowered.split("_") if t}
    if tokens & _DESTRUCTIVE_NAME_TOKENS:
        return True
    # "remove_item_from_cart" is commerce, not deletion; "remove_file" is.
    if tokens & _DELETION_VERBS and tokens & _FILE_TOKENS:
        return True
    return any(fragment in lowered for fragment in _DESTRUCTIVE_SUBSTRINGS)


_PATHISH_KEY_RE = re.compile(r"(^|_)(path|file|dir|directory|folder|filename|filepath)($|_)", re.IGNORECASE)
_ABSOLUTE_PATH_RE = re.compile(r"^([a-zA-Z]:[\\/]|\\\\|/|~[\\/])")


def _iter_path_strings(value: Any, key: str = ""):
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _iter_path_strings(child, str(child_key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_path_strings(child, key)
    elif (
        isinstance(value, str)
        and value
        and (_PATHISH_KEY_RE.search(key) or _ABSOLUTE_PATH_RE.match(value.strip()))
    ):
        yield value


def enforce_allow_paths(args: dict[str, Any], allow_paths: list[str]) -> None:
    """Reject absolute path arguments outside the server's allowed roots."""
    roots = []
    for raw in allow_paths or []:
        try:
            roots.append(Path(str(raw)).expanduser().resolve())
        except Exception:
            continue
    if not roots:
        return
    for candidate in _iter_path_strings(args):
        text = candidate.strip().strip('"')
        if not _ABSOLUTE_PATH_RE.match(text):
            continue
        try:
            target = Path(text).expanduser().resolve()
        except Exception:
            continue
        if not any(target == root or root in target.parents for root in roots):
            raise PermissionError(
                f"Blocked by VISION safety policy: {target} is outside this server's allowed paths"
            )

def make_mcp_tool_definition(
    config: McpServerConfig,
    descriptor: McpToolDescriptor,
    call_tool,
) -> ToolDefinition:
    descriptor_name = str(descriptor.name or "").strip()
    if not descriptor_name:
        raise ValueError("MCP tool name is empty")
    input_schema = _schema_from_any(descriptor.input_schema)
    try:
        jsonschema.Draft202012Validator.check_schema(input_schema)
    except jsonschema.SchemaError as exc:
        raise ValueError(f"Invalid JSON Schema for MCP tool {descriptor_name}: {exc.message}") from exc

    name = namespaced_name(config.id, descriptor_name)
    destructive = is_destructive_tool_name(descriptor_name)
    default_permission = "allow" if getattr(config, "auto_approve", False) else "ask"
    permission = config.permissions.get(descriptor_name, default_permission)
    if destructive:
        permission = "blocked"
    registry_permission = "policy" if permission == "allow" else permission

    async def handler(args: dict[str, Any], _context: ToolContext) -> Any:
        jsonschema.validate(instance=args, schema=input_schema)
        enforce_allow_paths(args, list(getattr(config, "allow_paths", []) or []))
        result = await call_tool(config.id, descriptor_name, args)
        is_error = False
        if hasattr(result, "isError"):
            is_error = bool(result.isError)
        elif isinstance(result, dict):
            is_error = bool(result.get("isError") or result.get("is_error"))
        normalised = _normalise_result(result)
        if is_error:
            raise RuntimeError(str(normalised)[:2000] or "MCP tool returned an error")
        return normalised

    description = (descriptor.description or f"MCP tool {descriptor_name} from {config.display_name}")[:1000]
    if destructive:
        description = ("Blocked by VISION safety policy (destructive tool name). " + description)[:1024]
    return ToolDefinition(
        name=name,
        description=description,
        handler=handler,
        parameters=input_schema,
        source=f"mcp:{config.id}",
        side_effect="external",
        permission=registry_permission,
    )

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-_")
    return (slug or "server")[:48]


class McpServerConfig(BaseModel):
    id: str
    display_name: str
    transport: Literal["stdio", "streamable_http", "sse"] = "stdio"
    enabled: bool = False
    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str = ""
    env_refs: dict[str, str] = Field(default_factory=dict)  # env name -> credential key
    env_plain: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    header_refs: dict[str, str] = Field(default_factory=dict)  # header -> credential key
    headers_plain: dict[str, str] = Field(default_factory=dict)
    startup: Literal["lazy", "app_start"] = "lazy"
    toolset_sha256: str = ""
    approved_at: str = ""
    permissions: dict[str, Literal["ask", "allow", "blocked"]] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        value = slugify(value)
        if not value:
            raise ValueError("server id is required")
        return value


class McpConfigFile(BaseModel):
    schema_version: int = 1
    servers: list[McpServerConfig] = Field(default_factory=list)


def load_mcp_config(path: Path) -> McpConfigFile:
    if not path.exists():
        return McpConfigFile()
    return McpConfigFile.model_validate_json(path.read_text(encoding="utf-8"))


def save_mcp_config(path: Path, config: McpConfigFile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix="mcp-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def import_mcp_servers(data: dict[str, Any]) -> tuple[list[McpServerConfig], dict[str, str]]:
    """Convert common mcpServers JSON and extract secret env/header values.

    Returned secrets map generated credential keys to values; config stores only keys.
    Plain environment values are treated as secret refs conservatively.
    """
    raw_servers = data.get("mcpServers") or data.get("servers") or {}
    if isinstance(raw_servers, list):
        iterable = [(str(item.get("name") or item.get("id") or index), item) for index, item in enumerate(raw_servers)]
    elif isinstance(raw_servers, dict):
        iterable = list(raw_servers.items())
    else:
        raise ValueError("Expected an mcpServers object")
    configs: list[McpServerConfig] = []
    secrets: dict[str, str] = {}
    for raw_name, raw in iterable:
        if not isinstance(raw, dict):
            continue
        server_id = slugify(str(raw.get("id") or raw_name))
        url = str(raw.get("url") or raw.get("href") or "")
        transport = "streamable_http" if url else "stdio"
        if str(raw.get("transport", "")).lower() == "sse":
            transport = "sse"
        env_refs: dict[str, str] = {}
        for key, value in (raw.get("env") or {}).items():
            credential_key = f"MCP/{server_id}/env/{key}"
            env_refs[str(key)] = credential_key
            if value:
                secrets[credential_key] = str(value)
        header_refs: dict[str, str] = {}
        for key, value in (raw.get("headers") or {}).items():
            credential_key = f"MCP/{server_id}/header/{key}"
            header_refs[str(key)] = credential_key
            if value:
                secrets[credential_key] = str(value)
        configs.append(McpServerConfig(
            id=server_id,
            display_name=str(raw.get("display_name") or raw_name),
            transport=transport,  # type: ignore[arg-type]
            enabled=False,
            command=str(raw.get("command") or ""),
            args=[str(arg) for arg in raw.get("args") or []],
            cwd=str(raw.get("cwd") or ""),
            env_refs=env_refs,
            url=url,
            header_refs=header_refs,
        ))
    return configs, secrets


def export_mcp_servers(configs: list[McpServerConfig]) -> dict[str, Any]:
    exported: dict[str, Any] = {}
    for config in configs:
        item: dict[str, Any] = {}
        if config.transport == "stdio":
            item.update({"command": config.command, "args": config.args})
            if config.cwd:
                item["cwd"] = config.cwd
            # Secret values are intentionally not exported.
            item["env"] = {key: "" for key in config.env_refs} | config.env_plain
        else:
            item.update({"url": config.url, "transport": config.transport})
            item["headers"] = {key: "" for key in config.header_refs} | config.headers_plain
        exported[config.id] = item
    return {"mcpServers": exported}

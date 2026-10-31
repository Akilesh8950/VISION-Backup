from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    allow_sampling: bool = False
    allow_elicitation: bool = True
    timeout_seconds: int = 60
    auto_approve: bool = False
    allow_paths: list[str] = Field(default_factory=list, max_length=32)
    approved_at: str = ""
    permissions: dict[str, Literal["ask", "allow", "blocked"]] = Field(default_factory=dict)

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _clamp_timeout(cls, value: Any) -> int:
        try:
            return max(5, min(600, int(value)))
        except Exception:
            return 60

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        value = slugify(value)
        if not value:
            raise ValueError("server id is required")
        return value

    @model_validator(mode="after")
    def _enabled_record_is_connectable(self) -> McpServerConfig:
        if not self.enabled:
            return self
        if self.transport == "stdio":
            if not self.command.strip():
                raise ValueError("enabled stdio MCP server requires a command")
            return self
        from urllib.parse import urlparse

        parsed = urlparse(self.url.strip())
        try:
            _port = parsed.port
        except ValueError as exc:
            raise ValueError("enabled remote MCP server URL port is invalid") from exc
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("enabled remote MCP server requires a valid HTTP/HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("remote MCP URL must not contain embedded credentials")
        host = parsed.hostname.lower().rstrip(".")
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if parsed.scheme.lower() == "http" and not loopback:
            raise ValueError("remote MCP URL must use HTTPS outside loopback")
        return self


class McpConfigFile(BaseModel):
    schema_version: int = 1
    servers: list[McpServerConfig] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def _unique_ids(self) -> McpConfigFile:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for server in self.servers:
            if server.id in seen:
                duplicates.add(server.id)
            seen.add(server.id)
        if duplicates:
            raise ValueError(f"duplicate MCP server id(s): {', '.join(sorted(duplicates))}")
        return self


def load_mcp_config(path: Path) -> McpConfigFile:
    if not path.exists():
        return McpConfigFile()
    with path.open("rb") as stream:
        raw = stream.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("MCP configuration exceeds 5 MB")
    return McpConfigFile.model_validate_json(raw)


def load_mcp_config_resilient(path: Path) -> tuple[McpConfigFile, dict[str, str]]:
    """Load valid records while reporting malformed ones without their values.

    Extension configuration is user-editable JSON. One stale or hand-edited
    record should not make every other trusted server disappear. The strict
    loader remains available for save/trust paths, where silently dropping a
    record would be destructive.
    """
    if not path.exists():
        return McpConfigFile(), {}
    try:
        with path.open("rb") as stream:
            raw = stream.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            return McpConfigFile(), {"_file": "MCP configuration exceeds 5 MB"}
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return McpConfigFile(), {"_file": f"Could not read MCP configuration ({exc.__class__.__name__})"}
    if not isinstance(payload, dict) or not isinstance(payload.get("servers", []), list):
        return McpConfigFile(), {"_file": "MCP configuration must contain a servers list"}

    errors: dict[str, str] = {}
    servers: list[McpServerConfig] = []
    seen: set[str] = set()
    for index, item in enumerate(payload.get("servers", [])[:256]):
        key = str(item.get("id") or f"index-{index}") if isinstance(item, dict) else f"index-{index}"
        try:
            server = McpServerConfig.model_validate(item)
        except Exception:
            errors[key] = "Invalid or incomplete MCP server record"
            continue
        if server.id in seen:
            errors[server.id] = "Duplicate MCP server ID ignored"
            continue
        seen.add(server.id)
        servers.append(server)
    if len(payload.get("servers", [])) > 256:
        errors["_file"] = "Only the first 256 MCP server records were loaded"
    schema_version = payload.get("schema_version", 1)
    if not isinstance(schema_version, int):
        schema_version = 1
        errors.setdefault("_file", "Invalid schema version ignored")
    return McpConfigFile(schema_version=schema_version, servers=servers), errors


def save_mcp_config(path: Path, config: McpConfigFile) -> None:
    # Pydantic does not validate ordinary attribute assignment. Re-validate
    # at the persistence boundary so an in-memory toggle can never write a
    # file that the strict loader will reject on the next launch.
    validated = McpConfigFile.model_validate(config.model_dump(mode="python"))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(validated.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix="mcp-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
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

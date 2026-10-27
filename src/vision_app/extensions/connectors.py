from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .mcp_config import McpServerConfig


class ConnectorConfig(McpServerConfig):
    service_alias: str = ""
    auth_type: Literal["none", "api_key", "bearer", "oauth"] = "none"
    scopes: list[str] = Field(default_factory=list)
    last_used_at: str = ""


class ConnectorConfigFile(BaseModel):
    schema_version: int = 1
    connectors: list[ConnectorConfig] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def _unique_ids(self) -> ConnectorConfigFile:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for connector in self.connectors:
            if connector.id in seen:
                duplicates.add(connector.id)
            seen.add(connector.id)
        if duplicates:
            raise ValueError(f"duplicate connector id(s): {', '.join(sorted(duplicates))}")
        return self


def load_connectors(path: Path) -> ConnectorConfigFile:
    if not path.exists():
        return ConnectorConfigFile()
    with path.open("rb") as stream:
        raw = stream.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("Connector configuration exceeds 5 MB")
    return ConnectorConfigFile.model_validate_json(raw)


def load_connectors_resilient(path: Path) -> tuple[ConnectorConfigFile, dict[str, str]]:
    """Load valid connector records and report malformed ones by ID/index."""
    if not path.exists():
        return ConnectorConfigFile(), {}
    try:
        with path.open("rb") as stream:
            raw = stream.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            return ConnectorConfigFile(), {"_file": "Connector configuration exceeds 5 MB"}
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return ConnectorConfigFile(), {"_file": f"Could not read connector configuration ({exc.__class__.__name__})"}
    if not isinstance(payload, dict) or not isinstance(payload.get("connectors", []), list):
        return ConnectorConfigFile(), {"_file": "Connector configuration must contain a connectors list"}

    errors: dict[str, str] = {}
    connectors: list[ConnectorConfig] = []
    seen: set[str] = set()
    for index, item in enumerate(payload.get("connectors", [])[:256]):
        key = str(item.get("id") or f"index-{index}") if isinstance(item, dict) else f"index-{index}"
        try:
            connector = ConnectorConfig.model_validate(item)
        except Exception:
            errors[key] = "Invalid or incomplete connector record"
            continue
        if connector.id in seen:
            errors[connector.id] = "Duplicate connector ID ignored"
            continue
        seen.add(connector.id)
        connectors.append(connector)
    if len(payload.get("connectors", [])) > 256:
        errors["_file"] = "Only the first 256 connector records were loaded"
    schema_version = payload.get("schema_version", 1)
    if not isinstance(schema_version, int):
        schema_version = 1
        errors.setdefault("_file", "Invalid schema version ignored")
    return ConnectorConfigFile(schema_version=schema_version, connectors=connectors), errors


def save_connectors(path: Path, data: ConnectorConfigFile) -> None:
    # Validate direct in-memory edits before the atomic replace; see
    # save_mcp_config for why this persistence-boundary check matters.
    validated = ConnectorConfigFile.model_validate(data.model_dump(mode="python"))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(validated.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix="connectors-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def connector_runtime_id(connector_id: str) -> str:
    """Return the host/session identity for a connector.

    MCP servers and connectors live in different files, so the same stored
    ID may legally exist in both. The shared MCP session manager keys live
    sessions by one string ID; giving connectors a distinct runtime identity
    prevents one record from replacing/routing to the other's session.
    Stored connector IDs and trust records remain unchanged.
    """
    runtime_id = f"connector-{connector_id}"
    if len(runtime_id) <= 48:
        return runtime_id
    digest = hashlib.sha256(connector_id.encode("utf-8")).hexdigest()[:8]
    return f"connector-{connector_id[:29]}-{digest}"


def connector_as_server(connector: ConnectorConfig) -> McpServerConfig:
    data = connector.model_dump()
    data["id"] = connector_runtime_id(connector.id)
    data.pop("service_alias", None)
    data.pop("auth_type", None)
    data.pop("scopes", None)
    data.pop("last_used_at", None)
    return McpServerConfig.model_validate(data)

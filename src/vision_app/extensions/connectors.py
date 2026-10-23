from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .mcp_config import McpServerConfig


class ConnectorConfig(McpServerConfig):
    service_alias: str = ""
    auth_type: Literal["none", "api_key", "bearer", "oauth"] = "none"
    scopes: list[str] = Field(default_factory=list)
    last_used_at: str = ""


class ConnectorConfigFile(BaseModel):
    schema_version: int = 1
    connectors: list[ConnectorConfig] = Field(default_factory=list)


def load_connectors(path: Path) -> ConnectorConfigFile:
    if not path.exists():
        return ConnectorConfigFile()
    return ConnectorConfigFile.model_validate_json(path.read_text(encoding="utf-8"))


def save_connectors(path: Path, data: ConnectorConfigFile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix="connectors-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def connector_as_server(connector: ConnectorConfig) -> McpServerConfig:
    data = connector.model_dump()
    data.pop("service_alias", None)
    data.pop("auth_type", None)
    data.pop("scopes", None)
    data.pop("last_used_at", None)
    return McpServerConfig.model_validate(data)

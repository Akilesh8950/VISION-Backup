from __future__ import annotations

import json
import os
import tempfile
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from .constants import DEFAULT_CONTEXT_SIZE, DEFAULT_GPU_LAYERS, DEFAULT_LOCAL_PORT
from .paths import AppPaths


class EndpointMode(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"
    AUTO = "auto"


class AppConfig(BaseModel):
    schema_version: int = 1
    endpoint_mode: EndpointMode = EndpointMode.AUTO
    local_port: int = DEFAULT_LOCAL_PORT
    remote_base_url: str = ""
    remote_model_override: str = ""
    local_model_override: str = ""
    context_size: int = DEFAULT_CONTEXT_SIZE
    gpu_layers: int = DEFAULT_GPU_LAYERS
    model_path: str = ""
    llama_server_path: str = ""
    installed_model_sha: str = ""
    pending_model_path: str = ""
    pending_model_sha: str = ""
    pending_restart: bool = False
    theme: str = "dark"
    telemetry_opt_in: bool = False
    first_run_completed: bool = False
    max_extension_tools: int = 32
    auto_start_local_server: bool = True
    enabled_skills: list[str] = Field(default_factory=list)
    enabled_mcp_servers: list[str] = Field(default_factory=list)
    enabled_connectors: list[str] = Field(default_factory=list)
    active_project_id: str = ""

    @field_validator("local_port")
    @classmethod
    def _port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return value

    @field_validator("context_size")
    @classmethod
    def _context(cls, value: int) -> int:
        if not 1024 <= value <= 262144:
            raise ValueError("context_size must be between 1024 and 262144")
        return value

    @field_validator("gpu_layers")
    @classmethod
    def _gpu_layers(cls, value: int) -> int:
        if not 0 <= value <= 999:
            raise ValueError("gpu_layers must be between 0 and 999")
        return value

    @classmethod
    def defaults(cls, paths: AppPaths) -> AppConfig:
        return cls(model_path=str(paths.model_file))

    @classmethod
    def load(cls, paths: AppPaths) -> AppConfig:
        if not paths.config_file.exists():
            config = cls.defaults(paths)
            config.save(paths)
            return config
        try:
            config = cls.model_validate_json(paths.config_file.read_text(encoding="utf-8"))
        except Exception:
            backup = paths.config_file.with_suffix(".invalid.json")
            try:
                paths.config_file.replace(backup)
            except OSError:
                pass
            config = cls.defaults(paths)
            config.save(paths)
            return config
        if not config.model_path:
            config.model_path = str(paths.model_file)
        return config

    def save(self, paths: AppPaths) -> None:
        paths.ensure()
        payload = json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        fd, tmp_name = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=str(paths.app_data))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, paths.config_file)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    @property
    def local_base_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}/v1"

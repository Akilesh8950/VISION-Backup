from __future__ import annotations

import ipaddress
import json
import os
import tempfile
from enum import Enum
from urllib.parse import urlparse

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
    agent_max_steps: int = 30
    gpu_layers: int = DEFAULT_GPU_LAYERS
    # Server performance tuning: "auto" (detect hardware), "balanced"
    # (default engine settings), "performance" (strong PCs), or
    # "low_end" (CPU-only PCs such as an Intel i3 with 8 GB RAM).
    # Resolved in vision_app.server.hardware; applies at server start.
    performance_profile: str = "auto"
    model_path: str = ""
    llama_server_path: str = ""
    installed_model_sha: str = ""
    pending_model_path: str = ""
    pending_model_sha: str = ""
    pending_restart: bool = False
    theme: str = "dark"
    # Read-aloud (text-to-speech) preferences. Voices themselves live in
    # Windows (Web Speech API via WebView2); only the chosen voice id/name
    # and calm style sliders are persisted. Empty voice id = automatic
    # soothing pick. See app/frontend/src/voice.js (kept in sync).
    tts_voice_uri: str = ""
    tts_voice_name: str = ""
    tts_style: str = "soothing"
    tts_rate: float = 0.92
    tts_pitch: float = 0.95
    tts_volume: float = 1.0
    tts_auto_read: bool = False
    # Vision Live (optional, local-first). Speech-to-text uses a
    # user-downloaded whisper.cpp engine; screen understanding additionally
    # needs a future vision-capable model build with a matching mmproj
    # projector, which today's text-only GGUF does not ship.
    stt_binary_path: str = ""
    stt_model_size: str = "base"
    stt_language: str = "auto"
    mmproj_path: str = ""
    # Manual "Reduce animations" toggle (the UI also honours the OS-level
    # prefers-reduced-motion setting automatically).
    reduce_motion: bool = False
    telemetry_opt_in: bool = False
    first_run_completed: bool = False
    max_extension_tools: int = 32
    extensions_enabled: bool = True
    tool_routing_enabled: bool = True
    tool_router_max_tools: int = 40
    tool_router_max_tokens: int = 4000
    tool_router_min_score: float = 0.08
    skill_auto_inject: bool = True
    skill_relevance_threshold: float = 0.12
    auto_start_local_server: bool = True
    enabled_skills: list[str] = Field(default_factory=list)
    enabled_mcp_servers: list[str] = Field(default_factory=list)
    enabled_connectors: list[str] = Field(default_factory=list)
    active_project_id: str = ""
    updates_auto_check: bool = True
    update_channel: str = "stable"

    @field_validator("local_port")
    @classmethod
    def _port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return value

    @field_validator("remote_base_url", mode="before")
    @classmethod
    def _remote_base_url(cls, value: object) -> str:
        # Clear an unsafe stale value instead of making the whole config
        # unloadable.  The settings bridge separately reports a newly
        # entered invalid address and refuses that save.
        text = str(value or "").strip()
        if not text or len(text) > 2000:
            return ""
        parsed = urlparse(text)
        try:
            _port = parsed.port
        except ValueError:
            return ""
        scheme = parsed.scheme.lower()
        if (
            scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or not text.lower().startswith(f"{scheme}://")
        ):
            return ""
        host = parsed.hostname.lower().rstrip(".")
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if scheme == "http" and not loopback:
            return ""
        return text

    @field_validator("agent_max_steps", mode="before")
    @classmethod
    def _agent_steps(cls, value: object) -> int:
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            return 30
        return max(1, min(100, parsed))

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

    @field_validator("performance_profile", mode="before")
    @classmethod
    def _performance_profile(cls, value: object) -> str:
        normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {"low": "low_end", "lowend": "low_end", "cpu": "low_end", "fast": "performance"}
        normalized = aliases.get(normalized, normalized)
        # Clamp instead of raising: one stale/foreign value must not make
        # the whole config file fail validation and reset every setting.
        return normalized if normalized in {"auto", "balanced", "performance", "low_end"} else "auto"

    @field_validator("endpoint_mode", mode="before")
    @classmethod
    def _endpoint_mode(cls, value: object) -> EndpointMode:
        if isinstance(value, EndpointMode):
            return value
        normalized = str(value or "").strip().lower()
        return EndpointMode(normalized) if normalized in {mode.value for mode in EndpointMode} else EndpointMode.AUTO

    @field_validator("tool_router_max_tools", mode="before")
    @classmethod
    def _router_max_tools(cls, value: object) -> int:
        try:
            return max(30, min(80, int(str(value))))
        except Exception:
            return 40

    @field_validator("tool_router_max_tokens", mode="before")
    @classmethod
    def _router_max_tokens(cls, value: object) -> int:
        try:
            return max(1000, min(12_000, int(str(value))))
        except Exception:
            return 4000

    @field_validator("tool_router_min_score", "skill_relevance_threshold", mode="before")
    @classmethod
    def _router_scores(cls, value: object) -> float:
        try:
            return max(0.0, min(1.0, float(str(value))))
        except Exception:
            return 0.1

    @field_validator("max_extension_tools", mode="before")
    @classmethod
    def _max_extension_tools(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return 32
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            return 32
        return max(0, min(parsed, 256))

    @field_validator("theme", mode="before")
    @classmethod
    def _theme(cls, value: object) -> str:
        return "light" if str(value or "").strip().lower() == "light" else "dark"

    @field_validator("tts_style", mode="before")
    @classmethod
    def _tts_style(cls, value: object) -> str:
        # Clamp instead of raising, like performance_profile: an unknown
        # style from a newer/older build must not reset the whole config.
        normalized = str(value or "").strip().lower()
        allowed = {"soothing", "calm_deep", "natural", "bright", "slow_clear", "custom"}
        return normalized if normalized in allowed else "soothing"

    @field_validator("tts_rate", mode="before")
    @classmethod
    def _tts_rate(cls, value: object) -> float:
        if isinstance(value, bool):
            return 0.92
        try:
            return max(0.5, min(1.5, float(str(value))))
        except (TypeError, ValueError):
            return 0.92

    @field_validator("tts_pitch", mode="before")
    @classmethod
    def _tts_pitch(cls, value: object) -> float:
        if isinstance(value, bool):
            return 0.95
        try:
            return max(0.5, min(1.5, float(str(value))))
        except (TypeError, ValueError):
            return 0.95

    @field_validator("tts_volume", mode="before")
    @classmethod
    def _tts_volume(cls, value: object) -> float:
        if isinstance(value, bool):
            return 1.0
        try:
            return max(0.0, min(1.0, float(str(value))))
        except (TypeError, ValueError):
            return 1.0

    @field_validator("tts_voice_uri", "tts_voice_name", mode="before")
    @classmethod
    def _tts_voice_ids(cls, value: object) -> str:
        return str(value or "").strip()[:300]

    @field_validator("stt_model_size", mode="before")
    @classmethod
    def _stt_model_size(cls, value: object) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"tiny", "base", "small"} else "base"

    @field_validator("stt_language", mode="before")
    @classmethod
    def _stt_language(cls, value: object) -> str:
        normalized = str(value or "").strip().lower().replace("_", "-")
        if normalized == "auto":
            return normalized
        # Whisper accepts a two/three-letter language code. Anything else
        # falls back to automatic detection instead of failing validation.
        head = normalized.split("-", 1)[0]
        return head if head.isalpha() and 2 <= len(head) <= 3 else "auto"

    @field_validator("update_channel", mode="before")
    @classmethod
    def _update_channel(cls, value: object) -> str:
        return "dev" if str(value or "").strip().lower() == "dev" else "stable"

    @field_validator("enabled_skills", "enabled_mcp_servers", "enabled_connectors", mode="before")
    @classmethod
    def _enabled_lists(cls, value: object) -> list[str]:
        if isinstance(value, str):
            raw_values = [value]
        elif isinstance(value, (list, tuple, set)):
            raw_values = list(value)
        else:
            return []
        result: list[str] = []
        for item in raw_values:
            clean = str(item).strip()
            if clean and clean not in result:
                result.append(clean)
        return result

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
            with paths.config_file.open("rb") as stream:
                raw = stream.read(5 * 1024 * 1024 + 1)
            if len(raw) > 5 * 1024 * 1024:
                raise ValueError("Configuration file exceeds 5 MB")
            config = cls.model_validate_json(raw)
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
        # Direct attribute assignment is used throughout the desktop UI and
        # Pydantic does not validate assignments by default. Validate (and
        # adopt normalized values) at the persistence boundary so an invalid
        # in-memory edit can never make the next launch discard the config.
        validated = type(self).model_validate(self.model_dump(mode="python"))
        self.__dict__.update(validated.__dict__)
        payload = json.dumps(validated.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
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

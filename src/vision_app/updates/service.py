from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import total_ordering
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

import httpx
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from ..config import AppConfig
from ..connectivity import ConnectivityMonitor, ReconnectWaiter, default_monitor
from ..constants import EXPECTED_GGUF_SIZE, GGUF_FILENAME, MODEL_REPO_ID, SYSTEM_PROMPT
from ..paths import AppPaths
from ..server.download_llama import safe_extract_zip
from ..tools import schemas as tool_schemas
from ..updater.downloads import activate_pending_model, download_model

logger = logging.getLogger(__name__)

APP_VERSION = "1.0.0"
LAUNCHER_VERSION = "1.0.0"
# Channel authorities are deliberately split by host:
# - The GitHub repository (Akilesh8950/VISION) serves APP updates only:
#   the release manifests below (pointing at GitHub Release ZIPs) and the
#   live remote config that ships with them. It never hosts model files
#   or model-update metadata.
# - The MODEL channel talks exclusively to Hugging Face
#   (SpiritKing/VISION-Ornith-1.5-9B-GGUF) for both the update check
#   and the GGUF download. Do not repoint it at GitHub.
MANIFEST_URL = "https://raw.githubusercontent.com/Akilesh8950/VISION/main/latest.json"
MANIFEST_URL_DEV = "https://raw.githubusercontent.com/Akilesh8950/VISION/main/latest-dev.json"
CONFIG_URL = "https://raw.githubusercontent.com/Akilesh8950/VISION/main/updates/config.json"
CONFIG_URL_DEV = "https://raw.githubusercontent.com/Akilesh8950/VISION/main/updates/config-dev.json"
MODEL_REPO = MODEL_REPO_ID
MODEL_FILE = GGUF_FILENAME
MODEL_API_URL = f"https://huggingface.co/api/models/{MODEL_REPO}"
UPDATE_CHANNEL = "stable"
# VISION ships one build flavour; the manifest must be for this exact build.
EXPECTED_PLATFORM = "windows-x64"

# The service and UI must render and be fully testable when an instance is
# explicitly disabled, but a disabled service performs no network or download
# work. This global was enabled only after the local HTTP-server proof staged
# and launcher-applied a fake 9.9.9 release end to end.
UPDATES_ENABLED = True

NETWORK_TIMEOUT_SECONDS = 3.0
DOWNLOAD_READ_TIMEOUT_SECONDS = 120.0
MIN_MODEL_BYTES = 5_000_000_000
MAX_MANIFEST_BYTES = 1_000_000
MAX_REMOTE_CONFIG_BYTES = 2_000_000
MAX_MODEL_INFO_BYTES = 2_000_000
MAX_APP_UPDATE_BYTES = 4 * 1024**3
APP_EXE_NAME = "VISION.exe"

ChannelName = Literal["app", "model", "config"]
ChannelStatusName = Literal[
    "up_to_date",
    "update_available",
    "downloading",
    "verifying",
    "restart_to_apply",
    "failed",
]

_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SAFE_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]*$")


class UpdateError(RuntimeError):
    """A user-safe update failure."""


class UpdateNetworkError(UpdateError):
    """The update service could not be reached; callers should stay quiet."""


class UpdateValidationError(UpdateError):
    """Remote update data was present but malformed or unsafe."""




class UpdateMismatchError(UpdateValidationError):
    """The manifest targets a different channel/platform build.

    Carried separately so the UI shows the one clear reason line
    instead of a generic validation error.
    """

@total_ordering
@dataclass(frozen=True)
class SemVer:
    """A small strict SemVer 2.0.0 implementation.

    This is deliberately used instead of a string comparison so that, for
    example, ``1.10.0`` is correctly newer than ``1.9.0``. Build metadata is
    preserved for display but ignored for precedence, as SemVer requires.
    """

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: str = ""

    @classmethod
    def parse(cls, value: str) -> SemVer:
        text = str(value or "").strip()
        if text.startswith(("v", "V")) and len(text) > 1 and text[1].isdigit():
            text = text[1:]
        match = _SEMVER_RE.fullmatch(text)
        if not match:
            raise ValueError(f"Invalid semantic version: {value!r}")
        prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
        return cls(
            major=int(match.group(1)),
            minor=int(match.group(2)),
            patch=int(match.group(3)),
            prerelease=prerelease,
            build=match.group(5) or "",
        )

    @classmethod
    def try_parse(cls, value: str) -> SemVer | None:
        try:
            return cls.parse(value)
        except (TypeError, ValueError):
            return None

    def _core(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._core() == other._core() and self.prerelease == other.prerelease

    def __hash__(self) -> int:
        return hash((self._core(), self.prerelease))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        if self._core() != other._core():
            return self._core() < other._core()
        if not self.prerelease and not other.prerelease:
            return False
        if not self.prerelease:
            return False  # A release outranks every prerelease of it.
        if not other.prerelease:
            return True
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            left_numeric = left.isdigit()
            right_numeric = right.isdigit()
            if left_numeric and right_numeric:
                return int(left) < int(right)
            if left_numeric:
                return True  # Numeric identifiers rank below alphanumeric.
            if right_numeric:
                return False
            return left < right
        return len(self.prerelease) < len(other.prerelease)

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += "-" + ".".join(self.prerelease)
        if self.build:
            value += "+" + self.build
        return value

    @property
    def directory_name(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += "-" + ".".join(self.prerelease)
        return value


def compare_semver(left: str, right: str) -> int:
    a = SemVer.parse(left)
    b = SemVer.parse(right)
    if a == b:
        return 0
    return -1 if a < b else 1


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _read_json_file(path: Path, *, max_bytes: int = 2_000_000) -> dict[str, Any] | None:
    try:
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return None
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _nearest_existing_parent(path: Path) -> Path:
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return probe


def _parse_content_range(value: str | None) -> tuple[int, int, int | None] | None:
    if not value:
        return None
    match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", value.strip(), flags=re.IGNORECASE)
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2))
    total = None if match.group(3) == "*" else int(match.group(3))
    if end < start or (total is not None and (total <= 0 or end >= total)):
        return None
    return start, end, total


class AppManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    channel: str = UPDATE_CHANNEL
    platform: str = EXPECTED_PLATFORM
    url: str = ""
    size_bytes: int = Field(
        validation_alias=AliasChoices("size_bytes", "size"),
        gt=0,
        le=MAX_APP_UPDATE_BYTES,
    )
    sha256: str
    min_launcher: str = "0.0.0"
    notes: str = ""
    released_at: str = ""

    @property
    def expected_size(self) -> int:
        return int(self.size_bytes or 0)

    # Backwards-compatible alias for older callers/tests.
    @property
    def size(self) -> int:  # type: ignore[override]
        return self.expected_size

    @field_validator("version", "min_launcher")
    @classmethod
    def _semver(cls, value: str) -> str:
        return str(SemVer.parse(str(value)))

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        text = str(value or "").strip()
        parsed = urlparse(text)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("Update URL must be an HTTP(S) URL")
        # Plain HTTP is accepted only for a loopback proof/test server.
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Update URL must use HTTPS outside loopback tests")
        return text

    @field_validator("sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not _SHA256_RE.fullmatch(text):
            raise ValueError("Update SHA-256 must be 64 hexadecimal characters")
        return text

    @field_validator("size_bytes", mode="before")
    @classmethod
    def _size(cls, value: object) -> int:
        if value is None:
            return 0
        try:
            parsed = int(cast(Any, value))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Update size must be an integer") from exc
        if parsed <= 0:
            raise ValueError("Update size must be positive")
        if parsed > MAX_APP_UPDATE_BYTES:
            raise ValueError("Update size exceeds the 4 GiB safety limit")
        return parsed

    @field_validator("notes", mode="before")
    @classmethod
    def _notes(cls, value: object) -> str:
        return str(value or "")[:20_000]

    @field_validator("channel", mode="before")
    @classmethod
    def _channel(cls, value: object) -> str:
        text = str(value or UPDATE_CHANNEL).strip().lower()
        if text not in {"stable", "dev"}:
            raise ValueError("Update channel must be stable or dev")
        return text

    @field_validator("platform", mode="before")
    @classmethod
    def _platform(cls, value: object) -> str:
        text = str(value or EXPECTED_PLATFORM).strip().lower()
        if not text or len(text) > 64:
            raise ValueError("Update platform is invalid")
        return text

    @field_validator("released_at", mode="before")
    @classmethod
    def _released_at(cls, value: object) -> str:
        return str(value or "")[:64]


class UpdateState(BaseModel):
    """Persisted local update state (kept separate from launcher selection)."""

    model_config = ConfigDict(extra="ignore")

    version: str = APP_VERSION
    previous: str = ""
    model_sha: str = ""
    # UTC ISO time the installed GGUF was last replaced on this PC
    # (weekly rebuilds; shown in Settings -> Model).  None until the
    # first tracked install/activation happens through the updater.
    model_updated_at: str | None = None
    last_checked: str | None = None

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        return str(SemVer.parse(str(value or APP_VERSION)))

    @field_validator("previous", mode="before")
    @classmethod
    def _previous(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return str(SemVer.parse(text))

    @field_validator("model_sha", mode="before")
    @classmethod
    def _model_sha(cls, value: object) -> str:
        return str(value or "").strip()[:128]


class PendingUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: str
    previous: str = ""
    sha256: str = ""
    created_at: str = ""
    rollback: bool = False

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        return str(SemVer.parse(str(value)))

    @field_validator("previous", mode="before")
    @classmethod
    def _previous(cls, value: object) -> str:
        text = str(value or "").strip()
        return str(SemVer.parse(text)) if text else ""


class ToolSchemaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        text = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", text):
            raise ValueError("Invalid remote tool name")
        return text

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: object) -> str:
        return str(value or "")[:2_000]

    @field_validator("parameters")
    @classmethod
    def _parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict) or value.get("type") not in (None, "object"):
            raise ValueError("Remote tool parameters must describe an object")
        if len(json.dumps(value, separators=(",", ":"))) > 50_000:
            raise ValueError("Remote tool parameters are too large")
        return value



class ConnectorOAuthConfig(BaseModel):
    """Optional OAuth endpoints for a directory connector.

    Generic OAuth only: an entry must carry its own authorize/token URLs
    and (public) client id. VISION never ships invented per-service
    endpoints; entries without these fields cannot be installed via
    OAuth and the UI says exactly that.
    """

    model_config = ConfigDict(extra="forbid")

    authorize_url: str = ""
    token_url: str = ""
    client_id: str = ""

    @field_validator("authorize_url", "token_url", mode="before")
    @classmethod
    def _urls(cls, value: object) -> str:
        text = str(value or "").strip()
        if len(text) > 2000:
            raise ValueError("OAuth URL too long")
        return text


class ConnectorDirectoryEntry(BaseModel):
    """One installable connector from the config channel directory.

    A connector is a product-level bundle on top of MCP: an MCP server
    definition plus described auth plus preset permissions. Installing
    one writes an ordinary connector (remote MCP) record, so connectors
    reuse the single MCP client implementation.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    icon: str = ""
    description: str = ""
    transport: Literal["stdio", "streamable_http", "sse"] = "streamable_http"
    url: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list, max_length=32)
    auth_type: Literal["none", "api_key", "oauth"] = "none"
    scopes: list[str] = Field(default_factory=list, max_length=32)
    token_field: str = ""  # header name for api_key auth
    oauth: ConnectorOAuthConfig | None = None
    preset_tools: list[str] = Field(default_factory=list, max_length=128)
    auto_approve: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        from ..extensions.mcp_config import slugify

        text = slugify(str(value or ""))
        if not text:
            raise ValueError("connector id is required")
        return text

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text or len(text) > 120:
            raise ValueError("connector name is required")
        return text

    @field_validator("url", mode="before")
    @classmethod
    def _url(cls, value: object) -> str:
        text = str(value or "").strip()
        if text:
            parsed = urlparse(text)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                raise ValueError("connector URL must be http(s)")
        return text[:2000]

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: object) -> str:
        return str(value or "").strip()[:1000]

class RemoteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "bundled"
    system_prompt: str = SYSTEM_PROMPT
    tool_schemas: list[ToolSchemaConfig] = Field(default_factory=list)
    ui_strings: dict[str, str] = Field(default_factory=dict)
    connectors: list[ConnectorDirectoryEntry] = Field(default_factory=list, max_length=128)

    @field_validator("version", mode="before")
    @classmethod
    def _version(cls, value: object) -> str:
        text = str(value or "bundled").strip()
        if not _SAFE_VERSION_RE.fullmatch(text) or len(text) > 40:
            raise ValueError("Invalid remote config version")
        return text

    @field_validator("system_prompt")
    @classmethod
    def _prompt(cls, value: str) -> str:
        text = str(value or "").strip()
        if len(text) < 20 or len(text) > 20_000 or "VISION" not in text:
            raise ValueError("Remote system prompt is missing or unsafe")
        return text

    @field_validator("ui_strings", mode="before")
    @classmethod
    def _ui_strings(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("Remote UI strings must be an object")
        cleaned: dict[str, str] = {}
        for key, item in value.items():
            clean_key = str(key).strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", clean_key):
                raise ValueError("Invalid remote UI string key")
            cleaned[clean_key] = str(item or "")[:500]
        return cleaned

    @model_validator(mode="after")
    def _tool_set(self) -> RemoteConfig:
        names = [tool.name for tool in self.tool_schemas]
        if len(names) != len(set(names)):
            raise ValueError("Remote tool schemas contain duplicates")
        expected = set(tool_schemas.ORIGINAL_27_TOOLS)
        if set(names) != expected:
            raise ValueError("Remote config must contain the complete 27-tool schema set")
        return self


def bundled_remote_config() -> RemoteConfig:
    tools: list[ToolSchemaConfig] = []
    for name in tool_schemas.ORIGINAL_27_TOOLS:
        function = tool_schemas.openai_tool(name)["function"]
        tools.append(
            ToolSchemaConfig(
                name=function["name"],
                description=function["description"],
                parameters=function["parameters"],
            )
        )
    return RemoteConfig(
        version=f"bundled-{APP_VERSION}",
        system_prompt=SYSTEM_PROMPT,
        tool_schemas=tools,
        ui_strings={
            "hero_title": "What can VISION do for you?",
            "hero_subtitle": "Ask anything, connect your work, or attach a file to begin.",
            "composer_placeholder": "Message VISION…",
        },
    )


class ChannelStatus(BaseModel):
    channel: ChannelName
    installed_version: str = ""
    available_version: str = ""
    status: ChannelStatusName = "up_to_date"
    last_checked: str | None = None
    progress: float | None = None
    notes: str = ""
    error: str = ""
    previous_version: str = ""
    available_size: int = 0

    @property
    def status_label(self) -> str:
        if self.status == "downloading":
            pct = 0 if self.progress is None else max(0, min(100, round(self.progress)))
            return f"Downloading {pct}%"
        return {
            "up_to_date": "Up to date",
            "update_available": "Update available",
            "verifying": "Verifying",
            "restart_to_apply": "Restart to apply",
            "failed": "Failed",
        }[self.status]

    def ui_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["status_label"] = self.status_label
        return data


class UpdateCheckResult(BaseModel):
    app: ChannelStatus
    model: ChannelStatus
    config: ChannelStatus
    enabled: bool = UPDATES_ENABLED
    offline: bool = False
    channel: str = UPDATE_CHANNEL
    last_checked: str | None = None
    launcher_managed: bool = False

    def ui_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "offline": self.offline,
            "channel": self.channel,
            "last_checked": self.last_checked,
            "launcher_managed": self.launcher_managed,
            "channels": {
                "app": self.app.ui_dict(),
                "model": self.model.ui_dict(),
                "config": self.config.ui_dict(),
            },
        }


@dataclass
class _ModelRemoteInfo:
    sha: str = ""
    size: int = 0


from .model_verify import ModelSmokeResult, run_model_smoke_test, validate_gguf

ProgressCallback = Callable[[dict[str, Any]], None]
ModelSmokeRunner = Callable[[Path], "ModelSmokeResult | bool | None"]
ConfigAppliedCallback = Callable[[RemoteConfig], None]


class UpdateService:
    """UI-facing update facade.

    Methods are synchronous and must be called from a worker thread.  The web
    bridge does exactly that.  The service itself never starts a hidden app
    download and never rewrites the launcher's ``current.json`` selection
    while the desktop app is running.
    """

    def __init__(
        self,
        *,
        root: Path,
        paths: AppPaths | None = None,
        config: AppConfig | None = None,
        dev_channel: bool = False,
        offline: bool = False,
        enabled: bool = UPDATES_ENABLED,
        manifest_url: str | None = None,
        config_url: str | None = None,
        model_api_url: str | None = None,
        token_provider: Callable[[], str] | None = None,
        on_config_applied: ConfigAppliedCallback | None = None,
        progress_callback: ProgressCallback | None = None,
        current_version: str = APP_VERSION,
        launcher_version: str = LAUNCHER_VERSION,
        platform_id: str = EXPECTED_PLATFORM,
        min_model_bytes: int = MIN_MODEL_BYTES,
        http_timeout: float = NETWORK_TIMEOUT_SECONDS,
        model_smoke_runner: ModelSmokeRunner | None = None,
        connectivity: ConnectivityMonitor | None = None,
        network_retry_delay_seconds: float = 2.0,
        max_offline_wait_seconds: float = 2 * 3600.0,
    ):
        self.root = Path(root)
        self.paths = paths
        self.config = config
        self.dev_channel = bool(dev_channel)
        self.offline = bool(offline)
        self.enabled = bool(enabled)
        self.manifest_url = manifest_url or (MANIFEST_URL_DEV if self.dev_channel else MANIFEST_URL)
        self.config_url = config_url or (CONFIG_URL_DEV if self.dev_channel else CONFIG_URL)
        self.model_api_url = model_api_url or MODEL_API_URL
        self.token_provider = token_provider
        self.on_config_applied = on_config_applied
        self.progress_callback = progress_callback
        self.current_version = str(SemVer.parse(current_version))
        self.launcher_version = str(SemVer.parse(launcher_version))
        self.platform_id = str(platform_id or EXPECTED_PLATFORM)
        self.min_model_bytes = max(1, int(min_model_bytes))
        self.model_smoke_runner = model_smoke_runner
        self.http_timeout = max(0.1, float(http_timeout))
        self.connectivity = connectivity or default_monitor()
        self.network_retry_delay_seconds = max(0.0, float(network_retry_delay_seconds))
        self.max_offline_wait_seconds = max(0.0, float(max_offline_wait_seconds))

        self.downloads_dir = self.root / "downloads"
        self.versions_dir = self.root / "versions"
        self.current_file = self.root / "current.json"
        self.pending_file = self.root / "pending.json"
        self.state_file = paths.update_state_file if paths is not None else self.root / "update-state.json"
        self.remote_config_file = (
            paths.remote_config_file if paths is not None else self.root / "remote-config.json"
        )

        self._lock = threading.RLock()
        self._download_lock = threading.Lock()
        self._manifest: AppManifest | None = None
        self._available_config: RemoteConfig | None = None
        self._model_remote: _ModelRemoteInfo | None = None
        self._last_result: UpdateCheckResult | None = None
        self._phases: dict[str, str] = {}
        self._progress: dict[str, float | None] = {"app": None, "model": None, "config": None}
        self._status_errors: dict[str, str] = {}

        self.state = self._load_state()
        self.active_config = self._load_cached_config()

    # ------------------------------------------------------------------
    # Properties and state
    # ------------------------------------------------------------------
    @property
    def channel_name(self) -> str:
        return "dev" if self.dev_channel else UPDATE_CHANNEL

    @property
    def launcher_managed(self) -> bool:
        return self.current_file.is_file() or (self.versions_dir / self.state.version / APP_EXE_NAME).is_file()

    @property
    def system_prompt(self) -> str:
        return self.active_config.system_prompt or SYSTEM_PROMPT

    @property
    def connector_directory(self) -> list[ConnectorDirectoryEntry]:
        """Installable connectors from the config channel (bundled fallback: none)."""
        try:
            return list(self.active_config.connectors or [])
        except Exception:
            return []

    def set_channel(self, *, dev_channel: bool | None = None, offline: bool | None = None) -> None:
        if dev_channel is not None and bool(dev_channel) != self.dev_channel:
            self.dev_channel = bool(dev_channel)
            # Explicit constructor/proof URLs keep their host/path unless they
            # are one of the two built-in endpoints being switched.  In the
            # normal app this toggles stable/dev manifests immediately.
            if self.manifest_url in {MANIFEST_URL, MANIFEST_URL_DEV}:
                self.manifest_url = MANIFEST_URL_DEV if self.dev_channel else MANIFEST_URL
            if self.config_url in {CONFIG_URL, CONFIG_URL_DEV}:
                self.config_url = CONFIG_URL_DEV if self.dev_channel else CONFIG_URL
            self._manifest = None
            self._available_config = None
        if offline is not None:
            self.offline = bool(offline)

    def _load_state(self) -> UpdateState:
        raw_state = _read_json_file(self.state_file)
        try:
            state = UpdateState.model_validate(raw_state) if raw_state else UpdateState(version=self.current_version)
        except (ValidationError, ValueError):
            state = UpdateState(version=self.current_version)

        raw_current = _read_json_file(self.current_file)
        if raw_current:
            try:
                current = UpdateState.model_validate(raw_current)
                state.version = current.version
                state.previous = current.previous
                if current.model_sha:
                    state.model_sha = current.model_sha
            except (ValidationError, ValueError):
                pass
        elif self.current_version != state.version:
            # First launch of a newly installed binary whose state file was
            # written by the prior version.  The running code is authoritative
            # when no launcher selection file exists.
            if SemVer.parse(self.current_version) > SemVer.parse(state.version):
                state.previous = state.version
            state.version = self.current_version

        if self.config is not None:
            if self.config.installed_model_sha:
                state.model_sha = self.config.installed_model_sha
            elif state.model_sha:
                self.config.installed_model_sha = state.model_sha
        return state

    def _save_state(self) -> None:
        if self.config is not None and self.config.installed_model_sha:
            self.state.model_sha = self.config.installed_model_sha
        try:
            _atomic_write_json(self.state_file, self.state.model_dump(mode="json"))
        except OSError:
            logger.warning("Could not persist update state", exc_info=True)

    def _load_cached_config(self) -> RemoteConfig:
        raw = _read_json_file(self.remote_config_file, max_bytes=MAX_REMOTE_CONFIG_BYTES)
        if raw:
            try:
                return RemoteConfig.model_validate(raw)
            except (ValidationError, ValueError):
                logger.warning("Ignoring malformed cached remote configuration")
        return bundled_remote_config()

    def apply_active_config(self) -> None:
        if self.on_config_applied is not None:
            self.on_config_applied(self.active_config)

    def _pending(self) -> PendingUpdate | None:
        raw = _read_json_file(self.pending_file)
        if not raw:
            return None
        try:
            return PendingUpdate.model_validate(raw)
        except (ValidationError, ValueError):
            return None

    def _version_dir(self, version: str) -> Path:
        return self.versions_dir / SemVer.parse(version).directory_name

    def _is_version_installed(self, version: str) -> bool:
        try:
            return (self._version_dir(version) / APP_EXE_NAME).is_file()
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # HTTP helpers (worker-thread only)
    # ------------------------------------------------------------------
    def _headers(self, *, model: bool = False) -> dict[str, str]:
        headers = {"User-Agent": f"VISION/{APP_VERSION} update-check"}
        if model and self.token_provider is not None:
            try:
                token = str(self.token_provider() or "").strip()
            except Exception:
                token = ""
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get_json(self, url: str, *, max_bytes: int, model: bool = False) -> dict[str, Any]:
        if self.offline:
            raise UpdateNetworkError("Offline")
        timeout = httpx.Timeout(
            connect=self.http_timeout,
            read=self.http_timeout,
            write=self.http_timeout,
            pool=self.http_timeout,
        )
        try:
            with httpx.stream(
                "GET",
                url,
                headers=self._headers(model=model),
                follow_redirects=True,
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    raise UpdateNetworkError("Update information is unavailable")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise UpdateValidationError("Update information is too large")
                    chunks.append(chunk)
        except UpdateError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise UpdateNetworkError("Could not reach the update service") from exc
        try:
            parsed = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateValidationError("Update information was malformed") from exc
        if not isinstance(parsed, dict):
            raise UpdateValidationError("Update information was malformed")
        return parsed

    def fetch_manifest(self) -> AppManifest:
        payload = self._get_json(self.manifest_url, max_bytes=MAX_MANIFEST_BYTES)
        try:
            manifest = AppManifest.model_validate(payload)
        except (ValidationError, ValueError) as exc:
            raise UpdateValidationError("The app update manifest was rejected") from exc
        channel = "dev" if self.dev_channel else UPDATE_CHANNEL
        if manifest.channel != channel:
            raise UpdateMismatchError(
                f"Update skipped: version {manifest.version} is for the {manifest.channel} channel."
            )
        if manifest.platform != self.platform_id:
            raise UpdateMismatchError(
                f"Update skipped: version {manifest.version} is for {manifest.platform}, not this PC."
            )
        self._manifest = manifest
        return manifest

    def _fetch_model_info(self) -> _ModelRemoteInfo:
        separator = "&" if "?" in self.model_api_url else "?"
        model_info_url = f"{self.model_api_url}{separator}blobs=true"
        payload = self._get_json(model_info_url, max_bytes=MAX_MODEL_INFO_BYTES, model=True)
        sha = str(payload.get("sha") or "").strip()
        size = 0
        siblings = payload.get("siblings")
        if isinstance(siblings, list):
            for sibling in siblings:
                if not isinstance(sibling, dict):
                    continue
                filename = str(sibling.get("rfilename") or sibling.get("filename") or "").replace("\\", "/")
                if filename != MODEL_FILE and filename.rsplit("/", 1)[-1] != MODEL_FILE:
                    continue
                raw_size = sibling.get("size")
                lfs = sibling.get("lfs")
                if raw_size is None and isinstance(lfs, dict):
                    raw_size = lfs.get("size")
                try:
                    size = max(0, int(raw_size)) if raw_size is not None else 0
                except (TypeError, ValueError, OverflowError):
                    size = 0
                break
        if not sha:
            raise UpdateValidationError("The model update information was malformed")
        result = _ModelRemoteInfo(sha=sha, size=size)
        self._model_remote = result
        return result

    def _fetch_remote_config(self) -> RemoteConfig:
        payload = self._get_json(self.config_url, max_bytes=MAX_REMOTE_CONFIG_BYTES)
        try:
            return RemoteConfig.model_validate(payload)
        except (ValidationError, ValueError) as exc:
            raise UpdateValidationError("The remote configuration was rejected") from exc

    # ------------------------------------------------------------------
    # Local status construction
    # ------------------------------------------------------------------
    def _short_sha(self, value: str) -> str:
        text = str(value or "").strip()
        return text[:12] if text else ""

    def _installed_model_sha(self) -> str:
        if self.config is not None and self.config.installed_model_sha:
            return self.config.installed_model_sha
        return self.state.model_sha

    def _local_statuses(self) -> tuple[ChannelStatus, ChannelStatus, ChannelStatus]:
        pending = self._pending()
        app_status: ChannelStatusName = "up_to_date"
        app_available = ""
        if pending is not None and self._is_version_installed(pending.version):
            app_status = "restart_to_apply"
            app_available = pending.version
        elif self._manifest is not None and SemVer.parse(self._manifest.version) > SemVer.parse(self.state.version):
            app_status = "update_available"
            app_available = self._manifest.version
        if self._status_errors.get("app") and app_status != "restart_to_apply":
            app_status = "failed"

        model_pending = bool(self.config is not None and self.config.pending_restart)
        model_status: ChannelStatusName = "restart_to_apply" if model_pending else "up_to_date"
        model_available = ""
        if self._model_remote is not None and self._model_remote.sha != self._installed_model_sha():
            model_available = self._short_sha(self._model_remote.sha)
            if not model_pending:
                model_status = "update_available"
        if self._status_errors.get("model") and model_status != "restart_to_apply":
            model_status = "failed"

        config_status: ChannelStatusName = "up_to_date"
        config_available = ""
        if self._available_config is not None and self._available_config.version != self.active_config.version:
            config_status = "update_available"
            config_available = self._available_config.version
        if self._status_errors.get("config"):
            config_status = "failed"

        app = ChannelStatus(
            channel="app",
            installed_version=self.state.version,
            available_version=app_available,
            status=("verifying" if self._phases.get("app") == "verifying" else "downloading") if self._progress.get("app") is not None else app_status,
            last_checked=self.state.last_checked,
            progress=self._progress.get("app"),
            notes=self._manifest.notes if self._manifest is not None else "",
            error=self._status_errors.get("app", ""),
            previous_version=self.state.previous,
            available_size=self._manifest.expected_size if self._manifest is not None else 0,
        )
        model = ChannelStatus(
            channel="model",
            installed_version=self._short_sha(self._installed_model_sha()) or "Not installed",
            available_version=model_available,
            status=("verifying" if self._phases.get("model") == "verifying" else "downloading") if self._progress.get("model") is not None else model_status,
            last_checked=self.state.last_checked,
            progress=self._progress.get("model"),
            error=self._status_errors.get("model", ""),
            available_size=self._model_remote.size if self._model_remote is not None else 0,
        )
        config = ChannelStatus(
            channel="config",
            installed_version=self.active_config.version,
            available_version=config_available,
            status="downloading" if self._progress.get("config") is not None else config_status,
            last_checked=self.state.last_checked,
            progress=self._progress.get("config"),
            error=self._status_errors.get("config", ""),
        )
        return app, model, config

    def _make_result(self, *, offline: bool = False) -> UpdateCheckResult:
        app, model, config = self._local_statuses()
        result = UpdateCheckResult(
            app=app,
            model=model,
            config=config,
            enabled=self.enabled,
            offline=offline or self.offline,
            channel=self.channel_name,
            last_checked=self.state.last_checked,
            launcher_managed=self.launcher_managed,
        )
        self._last_result = result
        return result

    def ui_state(self) -> dict[str, Any]:
        """Return last-known status without network or waiting on a download.

        Long downloads hold ``_lock`` in their worker thread.  The bridge may
        call this method from the WebView thread, so it must not block behind
        that worker: if the lock is owned elsewhere, build a best-effort
        snapshot from the current in-memory fields instead.  Re-entrant calls
        from the download thread itself still acquire the RLock normally.
        """
        acquired = self._lock.acquire(blocking=False)
        try:
            result = self._make_result()
            payload = result.ui_dict()
            payload["ui_strings"] = dict(self.active_config.ui_strings)
            # Honest-model metadata for Settings -> Model: the full
            # installed GGUF repo sha and when it last changed here.
            payload["installed_model_sha"] = self._installed_model_sha()
            payload["model_updated_at"] = self.state.model_updated_at
            return payload
        finally:
            if acquired:
                self._lock.release()

    # ------------------------------------------------------------------
    # Checking
    # ------------------------------------------------------------------
    def _check_app(self) -> bool:
        """Return True when the failure was a quiet network failure."""
        try:
            manifest = self.fetch_manifest()
            self._status_errors.pop("app", None)
            if SemVer.parse(manifest.min_launcher) > SemVer.parse(self.launcher_version):
                self._status_errors["app"] = (
                    "This update needs a newer VISION launcher. Install the latest VISION setup manually."
                )
            return False
        except UpdateNetworkError:
            return True
        except UpdateMismatchError as exc:
            self._status_errors["app"] = str(exc)
            return False
        except UpdateValidationError:
            self._status_errors["app"] = "The app update information was invalid and was rejected."
            return False
        except Exception:
            logger.debug("App update check failed unexpectedly", exc_info=True)
            self._status_errors["app"] = "The app update check failed."
            return False

    def _check_model(self) -> bool:
        try:
            self._fetch_model_info()
            self._status_errors.pop("model", None)
            return False
        except UpdateNetworkError:
            return True
        except UpdateValidationError:
            self._status_errors["model"] = "The model update information was invalid and was rejected."
            return False
        except Exception:
            logger.debug("Model update check failed unexpectedly", exc_info=True)
            self._status_errors["model"] = "The model update check failed."
            return False

    def _check_config(self, *, apply: bool) -> bool:
        try:
            remote = self._fetch_remote_config()
            self._status_errors.pop("config", None)
            if remote.version == self.active_config.version:
                self._available_config = None
            elif apply:
                self._apply_remote_config(remote)
            else:
                self._available_config = remote
            return False
        except UpdateNetworkError:
            return True
        except UpdateValidationError:
            self._status_errors["config"] = "The remote configuration was invalid and was rejected."
            return False
        except Exception:
            logger.debug("Config update check failed unexpectedly", exc_info=True)
            self._status_errors["config"] = "The configuration update check failed."
            return False

    def check_all(self, *, apply_config: bool = False) -> UpdateCheckResult:
        """Check all channels.  Offline and disabled checks never raise."""
        with self._lock:
            if not self.enabled or self.offline:
                return self._make_result(offline=self.offline)
            offline = False
            offline = self._check_app() or offline
            offline = self._check_model() or offline
            offline = self._check_config(apply=apply_config) or offline
            self.state.last_checked = _utc_now_iso()
            self._save_state()
            return self._make_result(offline=offline)

    def check_channel(self, channel: ChannelName) -> UpdateCheckResult:
        with self._lock:
            if not self.enabled or self.offline:
                return self._make_result(offline=self.offline)
            if channel == "app":
                offline = self._check_app()
            elif channel == "model":
                offline = self._check_model()
            elif channel == "config":
                offline = self._check_config(apply=False)
            else:  # Defensive for dynamic bridge values normalized upstream.
                raise UpdateValidationError("Unknown update channel")
            self.state.last_checked = _utc_now_iso()
            self._save_state()
            return self._make_result(offline=offline)

    # ------------------------------------------------------------------
    # Remote config application
    # ------------------------------------------------------------------
    def _apply_remote_config(self, remote: RemoteConfig) -> ChannelStatus:
        self.active_config = remote
        self._available_config = None
        try:
            _atomic_write_json(self.remote_config_file, remote.model_dump(mode="json"))
        except OSError:
            logger.warning("Could not cache the applied remote configuration", exc_info=True)
        if self.on_config_applied is not None:
            try:
                self.on_config_applied(remote)
            except Exception:
                logger.exception("Remote configuration was validated but could not be applied")
        _app, _model, config = self._local_statuses()
        return config

    def refresh_config(self, *, apply: bool = True) -> ChannelStatus:
        with self._lock:
            if not self.enabled or self.offline:
                return self._local_statuses()[2]
            try:
                remote = self._fetch_remote_config()
            except UpdateNetworkError:
                return self._local_statuses()[2]
            except UpdateValidationError as exc:
                self._status_errors["config"] = "The remote configuration was invalid and was rejected."
                raise UpdateError("The remote configuration was invalid and was rejected.") from exc
            self._status_errors.pop("config", None)
            if apply:
                return self._apply_remote_config(remote)
            self._available_config = remote if remote.version != self.active_config.version else None
            return self._local_statuses()[2]

    # ------------------------------------------------------------------
    # App download and staging
    # ------------------------------------------------------------------
    def _emit_progress(
        self,
        channel: ChannelName,
        *,
        downloaded: int | None = None,
        total: int | None = None,
        message: str = "",
        phase: str = "downloading",
    ) -> None:
        percent = None
        if downloaded is not None and total:
            percent = max(0.0, min(100.0, downloaded / total * 100.0))
        self._progress[channel] = percent if percent is not None else 0.0
        self._phases[channel] = phase
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(
                {
                    "channel": channel,
                    "downloaded": downloaded,
                    "total": total,
                    "percent": percent,
                    "message": message,
                    "phase": phase,
                }
            )
        except Exception:
            logger.debug("Update progress callback failed", exc_info=True)

    def _ensure_download_space(self, total: int) -> None:
        if total <= 0:
            return
        probe = _nearest_existing_parent(self.root)
        free = shutil.disk_usage(probe).free
        required = int(total * 2.2)
        if free < required:
            raise UpdateError(
                "Not enough free disk space for this update. "
                f"VISION needs about {required / 1024**3:.1f} GiB free while downloading and unpacking."
            )

    def _fetch_app_zip_once(self, manifest: AppManifest, zip_path: Path) -> Path:
        part_path = zip_path.with_suffix(zip_path.suffix + ".part")
        part_path.parent.mkdir(parents=True, exist_ok=True)
        if manifest.expected_size > 0:
            self._ensure_download_space(manifest.expected_size)

        for _attempt in range(2):
            downloaded = part_path.stat().st_size if part_path.exists() else 0
            if manifest.expected_size and downloaded > manifest.expected_size:
                part_path.unlink(missing_ok=True)
                downloaded = 0
            headers = self._headers()
            if downloaded:
                headers["Range"] = f"bytes={downloaded}-"
            timeout = httpx.Timeout(
                connect=self.http_timeout,
                read=DOWNLOAD_READ_TIMEOUT_SECONDS,
                write=self.http_timeout,
                pool=self.http_timeout,
            )
            restart = False
            try:
                with httpx.stream(
                    "GET",
                    manifest.url,
                    headers=headers,
                    follow_redirects=True,
                    timeout=timeout,
                ) as response:
                    total = manifest.expected_size or 0
                    mode = "wb"
                    if downloaded and response.status_code == 416:
                        restart = True
                    elif downloaded and response.status_code == 206:
                        parsed = _parse_content_range(response.headers.get("content-range"))
                        if parsed is None or parsed[0] != downloaded:
                            restart = True
                        else:
                            mode = "ab"
                            total = parsed[2] or total or downloaded + int(response.headers.get("content-length") or 0)
                    elif response.status_code == 200:
                        downloaded = 0
                        mode = "wb"
                        total = total or int(response.headers.get("content-length") or 0)
                    elif response.status_code >= 500 or response.status_code in (408, 429):
                        # Transient server trouble: resumable later.
                        raise UpdateNetworkError("The app update download is unavailable")
                    else:
                        # A 4xx (e.g. the release asset is gone) will not
                        # fix itself by waiting for a reconnect.
                        raise UpdateError(
                            f"The app update download is unavailable (HTTP {response.status_code})"
                        )

                    if restart:
                        part_path.unlink(missing_ok=True)
                        continue
                    if total:
                        self._ensure_download_space(total)
                    self._emit_progress("app", downloaded=downloaded, total=total or None, message="Downloading VISION update")
                    with part_path.open(mode) as stream:
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            stream.write(chunk)
                            downloaded += len(chunk)
                            if manifest.expected_size and downloaded > manifest.expected_size:
                                raise UpdateValidationError("The app update download was larger than expected")
                            self._emit_progress(
                                "app",
                                downloaded=downloaded,
                                total=total or None,
                                message="Downloading VISION update",
                            )
            except UpdateError:
                raise
            except (httpx.HTTPError, OSError) as exc:
                raise UpdateNetworkError("The app update download was interrupted") from exc

            if total and downloaded != total:
                raise UpdateNetworkError("The app update download was incomplete")
            part_path.replace(zip_path)
            return zip_path
        raise UpdateNetworkError("The app update download could not be resumed")

    def _fetch_with_reconnect(self, manifest: AppManifest, zip_path: Path) -> Path:
        """Fetch the zip, surviving a mid-download connection loss.

        ``_fetch_app_zip_once`` already resumes from the ``.part`` file
        via HTTP Range; this loop supplies the patience: when the
        network drops, wait for connectivity and resume instead of
        failing the whole update.  The partial file is kept even when
        giving up, so a later attempt continues where this stopped.
        """

        waiter = ReconnectWaiter(
            self.connectivity,
            max_offline_wait_seconds=self.max_offline_wait_seconds,
            base_delay_seconds=self.network_retry_delay_seconds,
        )

        def _waiting() -> None:
            self._emit_progress(
                "app",
                message="Internet connection lost — download paused; it will resume automatically",
            )

        def _resumed() -> None:
            self._emit_progress("app", message="Back online — resuming the update download")

        while True:
            try:
                result = self._fetch_app_zip_once(manifest, zip_path)
                self.connectivity.note_success()
                return result
            except UpdateNetworkError:
                self.connectivity.note_failure()
                if not waiter.wait_after_failure(on_waiting=_waiting, on_resumed=_resumed):
                    raise

    def _fetch_verified_app_zip(self, manifest: AppManifest) -> Path:
        zip_path = self.downloads_dir / f"{SemVer.parse(manifest.version).directory_name}.zip"
        if (
            zip_path.is_file()
            and (not manifest.expected_size or zip_path.stat().st_size == manifest.expected_size)
            and _sha256_file(zip_path) == manifest.sha256
        ):
            return zip_path
        if zip_path.exists():
            zip_path.unlink(missing_ok=True)

        # Exactly one complete retry is allowed after a hash mismatch.  A
        # mismatched file is never extracted or staged as pending.
        for attempt in range(2):
            downloaded_zip = self._fetch_with_reconnect(manifest, zip_path)
            self._emit_progress("app", message="Verifying VISION update", phase="verifying")
            size_ok = not manifest.expected_size or downloaded_zip.stat().st_size == manifest.expected_size
            if size_ok and _sha256_file(downloaded_zip) == manifest.sha256:
                return downloaded_zip
            downloaded_zip.unlink(missing_ok=True)
            downloaded_zip.with_suffix(downloaded_zip.suffix + ".part").unlink(missing_ok=True)
            if attempt == 1:
                raise UpdateValidationError("The downloaded update failed its size/SHA-256 verification")
        raise AssertionError("unreachable")


    @staticmethod
    def _validate_app_zip_contents(archive: Path, version: str) -> None:
        """Gate 3: inspect (never extract) the archive before staging it.

        The zip must carry ``VISION.exe`` and ``version.json`` at its root,
        the version inside must equal the manifest version, and every entry
        must be a safe relative path (the extract step re-checks this too).
        """
        try:
            with zipfile.ZipFile(archive) as zf:
                names: list[str] = []
                for info in zf.infolist():
                    raw_name = str(info.filename or "")
                    normalized = raw_name.replace("\\", "/")
                    if (
                        not normalized
                        or normalized.startswith("/")
                        or ":" in normalized.split("/", 1)[0]
                        or any(part == ".." for part in normalized.split("/"))
                    ):
                        raise UpdateValidationError("The downloaded update has an unsafe file path")
                    names.append(normalized)
                if APP_EXE_NAME not in names:
                    raise UpdateValidationError("The downloaded update does not contain VISION.exe")
                if "version.json" not in names:
                    raise UpdateValidationError("The downloaded update is missing version.json")
                try:
                    payload = json.loads(zf.read("version.json").decode("utf-8"))
                except Exception as exc:
                    raise UpdateValidationError("The downloaded update has an invalid version.json") from exc
        except UpdateError:
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            raise UpdateValidationError("The downloaded update could not be read safely") from exc
        inner_version = str(payload.get("version") or "") if isinstance(payload, dict) else ""
        if inner_version != str(SemVer.parse(version)):
            raise UpdateValidationError("The downloaded update version does not match its manifest")

    def _extract_app_zip(self, archive: Path, version: str) -> Path:
        version_key = SemVer.parse(version).directory_name
        final_dir = self.versions_dir / version_key
        if (final_dir / APP_EXE_NAME).is_file():
            return final_dir

        self._validate_app_zip_contents(archive, version)
        self._emit_progress("app", message="Verifying VISION update", phase="verifying")
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = self.versions_dir / f"{version_key}.tmp"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        try:
            safe_extract_zip(archive, tmp_dir)
            candidates = [tmp_dir]
            candidates.extend(child for child in tmp_dir.iterdir() if child.is_dir())
            source_dir = next((candidate for candidate in candidates if (candidate / APP_EXE_NAME).is_file()), None)
            if source_dir is None:
                raise UpdateValidationError("The downloaded update does not contain VISION.exe")

            if final_dir.exists():
                stale = self.versions_dir / f"{version_key}.stale-{uuid.uuid4().hex}"
                os.replace(final_dir, stale)
                try:
                    os.replace(source_dir, final_dir)
                except Exception:
                    os.replace(stale, final_dir)
                    raise
                shutil.rmtree(stale, ignore_errors=True)
            else:
                os.replace(source_dir, final_dir)
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            if not (final_dir / APP_EXE_NAME).is_file():
                raise UpdateValidationError("The staged update is missing VISION.exe")
            return final_dir
        except UpdateError:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise UpdateValidationError("The downloaded update could not be unpacked safely") from exc

    def download(self, version: str = "") -> ChannelStatus:
        """Download and stage an app update; never apply it while running."""
        with self._lock:
            if not self.enabled:
                return self._local_statuses()[0]
            if self.offline:
                raise UpdateNetworkError("VISION is offline")
            if not self._download_lock.acquire(blocking=False):
                raise UpdateError("An update download is already running")
            try:
                manifest = self._manifest or self.fetch_manifest()
                requested = str(SemVer.parse(version)) if version else manifest.version
                if requested != manifest.version:
                    raise UpdateValidationError("That app version is not the version offered by the update manifest")
                if SemVer.parse(manifest.min_launcher) > SemVer.parse(self.launcher_version):
                    self._status_errors["app"] = (
                        "This update needs a newer VISION launcher. Install the latest VISION setup manually."
                    )
                    raise UpdateError(self._status_errors["app"])
                if SemVer.parse(manifest.version) <= SemVer.parse(self.state.version):
                    self._status_errors.pop("app", None)
                    return self._local_statuses()[0]

                self._status_errors.pop("app", None)
                self._progress["app"] = 0.0
                archive = self._fetch_verified_app_zip(manifest)
                self._extract_app_zip(archive, manifest.version)

                pending = PendingUpdate(
                    version=manifest.version,
                    previous=self.state.version,
                    sha256=manifest.sha256,
                    created_at=_utc_now_iso(),
                )
                # pending.json is the launcher handoff and is written only
                # after hash verification, safe extraction, and VISION.exe
                # confirmation.  current.json is not touched here.
                _atomic_write_json(self.pending_file, pending.model_dump(mode="json"))
                self._progress["app"] = None
                return self._local_statuses()[0]
            except UpdateError as exc:
                self._status_errors["app"] = str(exc)
                raise
            except ValueError as exc:
                self._status_errors["app"] = str(exc)
                raise UpdateValidationError(str(exc)) from exc
            except OSError as exc:
                self._status_errors["app"] = (
                    "VISION could not write to its update folder. Use a current-user or portable "
                    "installation for in-app updates, or run the latest setup manually."
                )
                raise UpdateError(self._status_errors["app"]) from exc
            finally:
                self._progress["app"] = None
                self._download_lock.release()

    # ------------------------------------------------------------------
    # Model download and staging
    # ------------------------------------------------------------------
    def download_model_update(self) -> ChannelStatus:
        with self._lock:
            if not self.enabled:
                return self._local_statuses()[1]
            if self.offline:
                raise UpdateNetworkError("VISION is offline")
            if self.paths is None or self.config is None:
                raise UpdateError("Model update storage is not configured")
            if not self._download_lock.acquire(blocking=False):
                raise UpdateError("An update download is already running")
            token = ""
            if self.token_provider is not None:
                try:
                    token = str(self.token_provider() or "")
                except Exception:
                    token = ""
            try:
                remote = self._model_remote or self._fetch_model_info()
                installed_sha = self._installed_model_sha()
                if remote.sha == installed_sha:
                    self._status_errors.pop("model", None)
                    return self._local_statuses()[1]
                existing = Path(self.config.model_path or self.paths.model_file).exists()
                expected = remote.size or EXPECTED_GGUF_SIZE
                self._progress["model"] = 0.0
                downloaded = download_model(
                    self.paths,
                    token=token,
                    revision=remote.sha or None,
                    pending=existing,
                    expected_size=expected,
                    progress=lambda message: self._emit_progress("model", message=message),
                )
                actual_size = downloaded.stat().st_size
                if actual_size <= self.min_model_bytes:
                    # Existing download_model() already enforces the remote
                    # file size when known. This is the independent floor
                    # requested for the model channel.
                    downloaded.unlink(missing_ok=True)
                    raise UpdateValidationError("The downloaded model was unexpectedly small")
                self._verify_downloaded_model(downloaded)
                if existing:
                    self.config.pending_model_path = str(downloaded)
                    self.config.pending_model_sha = remote.sha
                    self.config.pending_restart = True
                else:
                    self.config.model_path = str(downloaded)
                    self.config.installed_model_sha = remote.sha
                    self.state.model_sha = remote.sha
                    self.state.model_updated_at = _utc_now_iso()
                self.config.save(self.paths)
                self._save_state()
                self._status_errors.pop("model", None)
                self._progress["model"] = None
                return self._local_statuses()[1]
            except UpdateError as exc:
                self._status_errors["model"] = str(exc)
                raise
            except Exception as exc:
                logger.exception("Model update download failed")
                self._status_errors["model"] = "The model update download failed."
                raise UpdateError("The model update download failed.") from exc
            finally:
                self._progress["model"] = None
                self._download_lock.release()


    # ------------------------------------------------------------------
    # Model verification (structural gate + behavioural smoke test)
    # ------------------------------------------------------------------
    def _default_model_smoke(self, downloaded: Path) -> ModelSmokeResult | None:
        if self.paths is None:
            return None
        try:
            from ..server.discovery import find_llama_server

            binary = find_llama_server(
                self.paths,
                self.config.llama_server_path if self.config is not None else "",
            )
        except Exception:
            logger.debug("Model smoke-test binary lookup failed", exc_info=True)
            return None
        if binary is None:
            return None  # Nothing to run it with yet; verification is deferred, not failed.
        return run_model_smoke_test(Path(binary), downloaded)

    def _verify_downloaded_model(self, downloaded: Path) -> None:
        """Prove a freshly downloaded GGUF before it can become pending.

        Any failure deletes the staged file and leaves the installed model
        (and the running server) completely untouched.
        """
        self._emit_progress("model", message="Verifying new model", phase="verifying")
        if not validate_gguf(downloaded, min_bytes=self.min_model_bytes):
            downloaded.unlink(missing_ok=True)
            raise UpdateValidationError(
                "The new model failed verification (size/GGUF header). Your current model was kept."
            )
        runner = self.model_smoke_runner or self._default_model_smoke
        try:
            outcome = runner(downloaded)
        except Exception as exc:
            downloaded.unlink(missing_ok=True)
            raise UpdateValidationError(
                "The new model failed verification (smoke test error). Your current model was kept."
            ) from exc
        if outcome is None:
            return  # Smoke test deferred (no server binary available yet).
        ok = outcome.ok if isinstance(outcome, ModelSmokeResult) else bool(outcome)
        if not ok:
            reason = outcome.reason if isinstance(outcome, ModelSmokeResult) and outcome.reason else "smoke test"
            downloaded.unlink(missing_ok=True)
            raise UpdateValidationError(
                f"The new model failed verification ({reason}). Your current model was kept."
            )

    def download_channel(self, channel: ChannelName, version: str = "") -> ChannelStatus:
        if channel == "app":
            return self.download(version)
        if channel == "model":
            return self.download_model_update()
        if channel == "config":
            with self._lock:
                if not self.enabled:
                    return self._local_statuses()[2]
                if self._available_config is not None:
                    return self._apply_remote_config(self._available_config)
                return self.refresh_config(apply=True)
        raise UpdateValidationError("Unknown update channel")

    # ------------------------------------------------------------------
    # Apply and rollback handoffs
    # ------------------------------------------------------------------
    def apply_pending(self, *, allow_model_swap: bool = False) -> UpdateCheckResult:
        """Validate pending work without switching a running app binary.

        The outer launcher consumes ``pending.json`` after this process has
        exited.  Therefore the normal UI action is a validated restart
        request, not an in-place binary swap.  At process startup,
        ``allow_model_swap=True`` may activate a staged model before the
        local server starts.
        """
        with self._lock:
            if not self.enabled:
                return self._make_result()
            pending = self._pending()
            if pending is not None and not self._is_version_installed(pending.version):
                self.pending_file.unlink(missing_ok=True)
                self._status_errors["app"] = "The staged app update was incomplete and was discarded."
            if allow_model_swap and self.paths is not None and self.config is not None:
                try:
                    changed = activate_pending_model(self.paths, self.config)
                    if changed:
                        self.state.model_sha = self.config.installed_model_sha
                        self.state.model_updated_at = _utc_now_iso()
                        self._save_state()
                except Exception:
                    logger.exception("Pending model activation failed; previous model remains available")
                    self._status_errors["model"] = "The new model could not be activated. The previous model was kept."
            return self._make_result()

    def rollback(self, *, immediate: bool = False) -> UpdateCheckResult:
        """Roll back to the previous app version.

        In a running app this writes a verified previous version to
        ``pending.json`` for the launcher.  ``immediate=True`` is reserved
        for an external/launcher context where the app is not running; only
        then may ``current.json`` be rewritten directly.
        """
        with self._lock:
            if not self.enabled:
                return self._make_result()
            previous = self.state.previous
            pending = self._pending()
            if pending is not None and not previous:
                # Cancelling a not-yet-applied update is also a safe rollback.
                self.pending_file.unlink(missing_ok=True)
                return self._make_result()
            if not previous or not self._is_version_installed(previous):
                raise UpdateError("No previous VISION version is available to restore")
            if immediate:
                current_payload = {
                    "version": previous,
                    "previous": self.state.version,
                    "model_sha": self._installed_model_sha(),
                    "last_checked": self.state.last_checked,
                }
                _atomic_write_json(self.current_file, current_payload)
                old_current = self.state.version
                self.state.version = previous
                self.state.previous = old_current
                self.pending_file.unlink(missing_ok=True)
            else:
                rollback_pending = PendingUpdate(
                    version=previous,
                    previous=self.state.version,
                    created_at=_utc_now_iso(),
                    rollback=True,
                )
                _atomic_write_json(self.pending_file, rollback_pending.model_dump(mode="json"))
            self._save_state()
            return self._make_result()

    def prune_versions(self) -> list[str]:
        """Keep only current + previous extracted app versions.

        The launcher calls equivalent logic after a successful child launch;
        this service method is useful for maintenance/tests and is not run
        automatically during a download.
        """
        keep = {self.state.version, self.state.previous}
        pending = self._pending()
        if pending is not None:
            keep.add(pending.version)
        removed: list[str] = []
        if not self.versions_dir.exists():
            return removed
        for child in self.versions_dir.iterdir():
            if not child.is_dir() or child.name in keep or child.name.endswith(".tmp"):
                continue
            if SemVer.try_parse(child.name) is None:
                continue
            shutil.rmtree(child)
            removed.append(child.name)
        return removed


__all__ = [
    "APP_VERSION",
    "CONFIG_URL",
    "CONFIG_URL_DEV",
    "EXPECTED_PLATFORM",
    "LAUNCHER_VERSION",
    "MANIFEST_URL",
    "MANIFEST_URL_DEV",
    "MAX_APP_UPDATE_BYTES",
    "MIN_MODEL_BYTES",
    "MODEL_API_URL",
    "MODEL_FILE",
    "MODEL_REPO",
    "UPDATES_ENABLED",
    "UPDATE_CHANNEL",
    "AppManifest",
    "ChannelStatus",
    "PendingUpdate",
    "RemoteConfig",
    "SemVer",
    "ToolSchemaConfig",
    "UpdateCheckResult",
    "UpdateError",
    "UpdateMismatchError",
    "UpdateNetworkError",
    "UpdateService",
    "UpdateState",
    "UpdateValidationError",
    "bundled_remote_config",
    "compare_semver",
]

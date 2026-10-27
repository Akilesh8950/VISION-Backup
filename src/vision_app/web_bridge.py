from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
import re
import shutil
import sys
import threading
import time
import uuid
import webbrowser
from collections import deque
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib import parse as urllib_parse

from pydantic import BaseModel

from . import BUILD_COMMIT, __version__
from .chat.controller import ChatCallbacks, TurnResult
from .config import AppConfig
from .connectivity import default_monitor
from .constants import CREDITS_LINE, EXPECTED_GGUF_SIZE, GGUF_FILENAME, MIN_LLAMA_BUILD, PRODUCT_NAME
from .extensions.connectors import (
    connector_as_server,
    connector_runtime_id,
    load_connectors,
    load_connectors_resilient,
    save_connectors,
)
from .extensions.mcp_config import (
    McpServerConfig,
    load_mcp_config,
    load_mcp_config_resilient,
    save_mcp_config,
)
from .extensions.mcp_tools import make_mcp_tool_definition
from .history import generate_conversation_title
from .knowledge.parsers import parse_file as parse_document_file
from .knowledge.retrieval import build_project_context
from .live.screen_capture import capture_screen_frame, decode_image_data_url
from .live.speech_to_text import (
    MAX_WAV_BYTES,
    ensure_speech_to_text,
    speech_to_text_status,
    transcribe_wav_bytes,
)
from .redaction import redact_value
from .server.discovery import build_is_supported, detect_build, find_llama_server
from .server.download_llama import download_llama
from .server.hardware import detect_hardware, effective_profile, recommended_profile
from .server.health import STARTUP_GRACE_SECONDS, HealthState, check_health_sync
from .server.vision import screen_vision_status
from .sync_service import SyncService
from .telemetry.uploader import upload_needs_log
from .tools.registry import ToolDefinition, ToolExecutionResult
from .tools.router import select_tool_schemas
from .updater.downloads import download_model
from .updater.model_updater import check_for_update
from .updates.service import UpdateError, UpdateService

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 256 * 1024
MAX_ATTACHMENTS = 10
MAX_ATTACHMENTS_TOTAL_BYTES = 1_000_000
MAX_EXPORT_BYTES = 20 * 1024 * 1024
LIVE_STATUSES = {
    "idle",
    "listening",
    "transcribing",
    "thinking",
    "speaking",
    "capturing",
    "preview",
    "error",
    "setup",
}
LIVE_MINI_ACTIONS = {"ptt_start", "ptt_stop", "share_frame", "stop", "open_live"}


def _payload_dict(value: object) -> dict[str, Any]:
    """Return a copy of a bridge payload, treating non-objects as empty."""
    return dict(value) if isinstance(value, dict) else {}


def _json_safe(value: Any) -> Any:
    redacted = redact_value(value)

    def convert(item: Any, seen: set[int], depth: int = 0) -> Any:
        if depth > 40:
            return "<truncated>"
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            return item if math.isfinite(item) else None
        if isinstance(item, Path):
            return str(item)
        if isinstance(item, Enum):
            return convert(item.value, seen, depth + 1)
        if isinstance(item, (datetime, date)):
            return item.isoformat()
        if isinstance(item, BaseModel):
            marker = id(item)
            if marker in seen:
                return "<cycle>"
            seen.add(marker)
            try:
                return convert(item.model_dump(mode="json"), seen, depth + 1)
            except Exception:
                return str(item)
            finally:
                seen.discard(marker)
        if is_dataclass(item) and not isinstance(item, type):
            marker = id(item)
            if marker in seen:
                return "<cycle>"
            seen.add(marker)
            try:
                return convert(asdict(item), seen, depth + 1)
            except Exception:
                return str(item)
            finally:
                seen.discard(marker)
        if isinstance(item, dict):
            marker = id(item)
            if marker in seen:
                return "<cycle>"
            seen.add(marker)
            try:
                return {str(key): convert(child, seen, depth + 1) for key, child in item.items()}
            finally:
                seen.discard(marker)
        if isinstance(item, (list, tuple, set)):
            marker = id(item)
            if marker in seen:
                return "<cycle>"
            seen.add(marker)
            try:
                return [convert(child, seen, depth + 1) for child in item]
            finally:
                seen.discard(marker)
        if isinstance(item, bytes):
            return {"base64": base64.b64encode(item).decode("ascii")}
        return str(item)

    return convert(redacted, set())


def _read_tail_lines(path: Path, limit: int) -> list[str]:
    if not path.exists():
        return []
    lines: deque[str] = deque(maxlen=max(1, limit))
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            lines.append(line.rstrip("\n"))
    return list(lines)


def find_web_dir(explicit: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get("VISION_WEB_DIR")
    if env:
        candidates.append(Path(env).expanduser())
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass) / "app" / "web")
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                exe_dir / "app" / "web",
                exe_dir / "_internal" / "app" / "web",
            ]
        )
    module_file = Path(__file__).resolve()
    candidates.extend(
        [
            module_file.parents[2] / "app" / "web",
            Path.cwd() / "app" / "web",
            module_file.parent / "web",
        ]
    )
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate.resolve()
    checked = ", ".join(str(item) for item in candidates)
    raise FileNotFoundError(f"VISION web assets were not found. Checked: {checked}")


class VisionWebApi:
    """Thin pywebview bridge.

    It adapts existing Python services to the web UI. Chat, tool execution,
    persistence, downloads, telemetry and extension trust rules remain in
    their existing backend modules.
    """

    def __init__(self, services: SimpleNamespace):
        self.services = services
        self.paths = services.paths
        self.config = services.config
        self.credentials = services.credentials
        self.history = services.history
        self.projects = services.projects
        self.knowledge = services.knowledge
        self.library = getattr(services, "library", None)
        self.checkpoints = getattr(services, "checkpoints", None)
        self.registry = services.registry
        self.controller = services.controller
        self.tool_context = services.tool_context
        self.skill_manager = services.skill_manager
        self.server_manager = services.server_manager
        self.extension_host = services.extension_host
        try:
            self.extension_host.set_user_callbacks(
                confirm=self._confirm,
                elicit=self._elicit_user,
                event_sink=self._extension_event,
            )
        except Exception:
            logger.debug("MCP user callbacks not wired", exc_info=True)
        self.needs_log = services.needs_log
        self.update_service: Any = getattr(services, "update_service", None)
        if self.update_service is None or not hasattr(self.update_service, "ui_state"):
            # Older service bundles/tests still get a functioning Updates
            # page. It is explicitly disabled so merely constructing the UI
            # cannot trigger network activity.
            self.update_service = UpdateService(
                root=self.paths.updates_dir,
                paths=self.paths,
                config=self.config,
                enabled=False,
            )
        try:
            self.update_service.progress_callback = self._update_progress
        except Exception:
            logger.debug("Could not attach the update progress callback", exc_info=True)

        self.sync_service: Any = getattr(services, "sync_service", None)
        if self.sync_service is None or not hasattr(self.sync_service, "start"):
            self.sync_service = SyncService(
                config=self.config,
                needs_log=self.needs_log,
                update_service=self.update_service,
                token_provider=lambda: self.credentials.get("HF_TOKEN"),
                on_updates=self._handle_sync_updates,
            )
        else:
            try:
                self.sync_service.on_updates = self._handle_sync_updates
            except Exception:
                logger.debug("Could not attach the sync update callback", exc_info=True)

        self.window: Any | None = None
        self._frontend_ready = False
        self._event_lock = threading.RLock()
        self._event_queue: list[dict[str, Any]] = []

        self._stream_lock = threading.Lock()
        self._pending_content = ""
        self._pending_reasoning = ""
        self._stream_timer: threading.Timer | None = None

        self._confirm_lock = threading.Lock()
        self._confirmations: dict[str, tuple[threading.Event, dict[str, Any]]] = {}

        self._attachments: dict[str, dict[str, Any]] = {}
        self._attachments_lock = threading.RLock()
        self._chat_thread: threading.Thread | None = None
        self._chat_loop: asyncio.AbstractEventLoop | None = None
        self._chat_task: asyncio.Task | None = None
        self._chat_busy = False
        self._chat_lock = threading.RLock()
        self._active_chat_conversation_id = ""

        self._closed = threading.Event()
        self._background_started = False
        self._background_lock = threading.Lock()
        self._tasks: dict[str, threading.Thread] = {}
        self._tasks_lock = threading.RLock()
        self._task_progress_at: dict[str, float] = {}
        self._extensions_lock = threading.RLock()
        self._shutdown_lock = threading.Lock()

        self._health_state = HealthState()
        self._server_healthy: bool | None = None
        self._server_state = "Not checked"
        self._server_binary: Path | None = None
        self._server_build: int | None = None
        self._server_supported: bool | None = None
        self._server_binary_checked = False
        self._server_intentionally_stopped = True
        self._server_desired_running = False
        self._server_started_monotonic: float | None = None
        self._server_lock = threading.RLock()
        self._last_failover_notice = 0.0

        self._dynamic_names: set[str] = set()
        self._last_routing: dict[str, Any] = {}
        self._router_vector_cache: dict[str, list[float]] = {}
        self._embeddings_unavailable_until: float = 0.0
        self._extension_notice_sent: set[str] = set()
        self._imported_server_ids: set[str] = set()
        self._extension_messages: list[str] = []
        self._extension_config_messages: list[str] = []
        self._latest_remote_sha = ""
        self._latest_remote_size = 0
        self._window_maximized = False
        self._shutdown_called = False

        # Vision Live ephemeral state. Captions/status only — microphone
        # audio and screen-frame bytes are never stored here.
        self._live_lock = threading.RLock()
        self._live_state: dict[str, Any] = {
            "active": False,
            "status": "idle",
            "caption": "",
            "user_text": "",
            "assistant_text": "",
            "level": 0.0,
            "screen_mode": False,
            "frame_approved": False,
            "speech_output": True,
        }
        self._live_command: dict[str, Any] = {"seq": 0, "action": ""}
        self._live_mini_window: Any | None = None
        self._live_mini_url = ""

        self.controller.callbacks = ChatCallbacks(
            on_content=lambda text: self._buffer_stream("content", text),
            on_reasoning=lambda text: self._buffer_stream("reasoning", text),
            on_tool_start=self._on_tool_start,
            on_tool_result=self._on_tool_result,
            on_endpoint=self._on_endpoint,
            on_sources=lambda sources: self._emit("sources", {"sources": _json_safe(sources)}),
            on_error=lambda text: self._emit("error", {"message": str(text)}),
        )
        self.tool_context.confirm = self._confirm
        self.tool_context.timer_callback = self._schedule_timer
        self.server_manager.on_log(lambda line: self._emit("server_log", {"line": line}))

    # ------------------------------------------------------------------
    # Window and event plumbing
    # ------------------------------------------------------------------
    def attach_window(self, window: Any) -> None:
        self.window = window

    def start_background_services(self) -> None:
        with self._background_lock:
            if self._background_started:
                return
            self._background_started = True
            workers = [
                threading.Thread(target=self._health_loop, daemon=True, name="vision-health"),
                threading.Thread(target=self._refresh_binary, daemon=True, name="vision-binary-check"),
                threading.Thread(target=self._refresh_extensions, daemon=True, name="vision-extensions"),
            ]
            if self.config.auto_start_local_server:
                workers.append(threading.Thread(target=self._start_server_internal, daemon=True, name="vision-server-start"))
            try:
                for worker in workers:
                    worker.start()
                # One quiet scheduler owns launch update checks and the
                # six-hour/close telemetry sync.  Downloads stay user-clicked
                # and offline failures are silent.
                self.sync_service.start()
            except Exception:
                self._background_started = False
                raise

    def frontend_ready(self) -> dict[str, Any]:
        self._frontend_ready = True
        self._flush_event_queue()
        self.start_background_services()
        return {"ok": True}

    def _emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        event = {"type": event_type, "payload": _json_safe(payload or {})}
        with self._event_lock:
            if not self._frontend_ready or self.window is None:
                self._event_queue.append(event)
                if len(self._event_queue) > 1000:
                    self._event_queue = self._event_queue[-1000:]
                return
            self._send_event(event)

    def _send_event(self, event: dict[str, Any]) -> None:
        if self.window is None:
            return
        try:
            script = "window.VisionBridge && window.VisionBridge.receive(" + json.dumps(event, ensure_ascii=False) + ");"
            self.window.evaluate_js(script)
        except Exception:
            # The renderer may be between page loads. Queue a small tail for
            # the next frontend_ready call instead of crashing a backend turn.
            with self._event_lock:
                self._event_queue.append(event)
                self._event_queue = self._event_queue[-1000:]

    def _flush_event_queue(self) -> None:
        with self._event_lock:
            queued_count = len(self._event_queue)
            for _ in range(queued_count):
                event = self._event_queue.pop(0)
                self._send_event(event)

    def _buffer_stream(self, kind: str, text: str) -> None:
        if not text:
            return
        with self._stream_lock:
            if kind == "content":
                self._pending_content += text
            else:
                self._pending_reasoning += text
            if self._stream_timer is None:
                self._stream_timer = threading.Timer(0.03, self._flush_stream)
                self._stream_timer.daemon = True
                self._stream_timer.start()

    def _flush_stream(self) -> None:
        with self._stream_lock:
            content, self._pending_content = self._pending_content, ""
            reasoning, self._pending_reasoning = self._pending_reasoning, ""
            if self._stream_timer is not None:
                self._stream_timer.cancel()
                self._stream_timer = None
        if reasoning:
            self._emit("reasoning_delta", {"text": reasoning})
        if content:
            self._emit("content_delta", {"text": content})

    # ------------------------------------------------------------------
    # Serializers and state
    # ------------------------------------------------------------------
    def _conversation_row(self, conversation_id: str) -> dict[str, Any] | None:
        return next(
            (row for row in self.history.list_conversations(include_archived=True) if row["id"] == conversation_id),
            None,
        )

    def _hardware_payload(self) -> dict[str, Any]:
        hardware = detect_hardware()
        return {
            **hardware.as_dict(),
            "recommended_profile": recommended_profile(hardware),
            "effective_profile": effective_profile(self.config.performance_profile, hardware),
        }

    def _config_payload(self) -> dict[str, Any]:
        # Do not add derived internal URLs to the UI payload, and do not
        # echo the saved backup-service address back to the page. The UI
        # receives only whether one exists; a blank replacement field means
        # "keep it" in save_settings(), while an explicit clear flag removes
        # it. Endpoint construction stays in the backend.
        payload = self.config.model_dump(mode="json")
        payload["remote_base_url_configured"] = bool(self.config.remote_base_url)
        payload["remote_base_url"] = ""
        return _json_safe(payload)

    def _updates_payload(self) -> dict[str, Any]:
        payload: dict[str, Any]
        try:
            payload = dict(self.update_service.ui_state())
        except Exception:
            logger.exception("Could not build the update status payload")
            payload = {"enabled": False, "offline": False, "channels": {}, "ui_strings": {}}
        raw_strings: Any = payload.get("ui_strings")
        strings: dict[str, Any] = raw_strings if isinstance(raw_strings, dict) else {}
        payload["ui_strings"] = {
            key: str(strings[key])
            for key in ("hero_title", "hero_subtitle", "composer_placeholder")
            if isinstance(strings.get(key), str) and strings[key].strip()
        }
        payload["auto_check"] = bool(self.config.updates_auto_check)
        payload["channel"] = "dev" if getattr(self.update_service, "dev_channel", False) else str(self.config.update_channel or "stable")
        payload["offline"] = bool(getattr(self.update_service, "offline", False) or payload.get("offline"))
        return _json_safe(payload)

    def _update_progress(self, payload: dict[str, Any]) -> None:
        data = _payload_dict(payload)
        downloaded = data.get("downloaded") if isinstance(data.get("downloaded"), int) else None
        total = data.get("total") if isinstance(data.get("total"), int) else None
        percent = data.get("percent") if isinstance(data.get("percent"), (int, float)) else None
        message = str(data.get("message") or "Downloading update")
        event: dict[str, Any] = {
            "channel": str(data.get("channel") or "app"),
            "downloaded": downloaded,
            "total": total,
            "percent": percent,
            "message": message,
            "updates": self._updates_payload(),
        }
        self._emit("update_progress", event)
        self._task_progress("download_update", message, downloaded, total)

    def _telemetry_payload(self) -> dict[str, Any]:
        try:
            status = self.needs_log.upload_status()
            report = self.needs_log.report(flush_first=False)
            pending = self.needs_log.pending_count()
        except Exception:
            logger.exception("Could not build the telemetry status payload")
            status = {
                "state": "never",
                "at": None,
                "message": "Telemetry status is unavailable.",
                "uploaded_events": 0,
                "pending_events": 0,
            }
            report = None
            pending = 0
        return _json_safe(
            {
                "opt_in": bool(self.config.telemetry_opt_in),
                "pending_count": pending,
                "status": status,
                "report": report,
            }
        )

    def get_telemetry_status(self) -> dict[str, Any]:
        return {"telemetry": self._telemetry_payload()}

    def preview_telemetry(self) -> dict[str, Any]:
        preview = self.needs_log.preview_payload()
        return {
            "ok": True,
            "needs_jsonl": preview.get("needs_jsonl", ""),
            "report": _json_safe(preview.get("report")),
            "event_count": int(preview.get("event_count") or 0),
            "telemetry": self._telemetry_payload(),
            "available_models": self._available_models(),
        }

    def _skills_payload(self) -> list[dict[str, Any]]:
        enabled = set(self.config.enabled_skills)
        rows: list[dict[str, Any]] = []
        for package in self.skill_manager.discover():
            rows.append(
                {
                    "name": package.name,
                    "description": package.metadata.description,
                    "enabled": package.name in enabled,
                    # The Settings UI only needs counts. Do not send the
                    # package file inventory or fingerprint to the page.
                    "file_count": len(package.files),
                    "script_count": len(package.scripts),
                }
            )
        return rows

    def _projects_payload(self) -> list[dict[str, Any]]:
        # Project selection needs only identity and display name. Root paths
        # and private project instructions remain backend-only.
        return [
            {"id": str(row.get("id") or ""), "name": str(row.get("name") or "Project")}
            for row in self.projects.list_projects()
        ]

    def _validated_project_id(self, value: object) -> str:
        selected = str(value or "")
        if selected and self.projects.get_project(selected):
            return selected
        return ""

    def _load_extension_configs(self) -> tuple[Any, Any, list[str]]:
        servers, server_errors = load_mcp_config_resilient(self.paths.mcp_config)
        connectors, connector_errors = load_connectors_resilient(self.paths.connectors_config)
        messages = [f"MCP record skipped: {message}" for message in server_errors.values()]
        messages.extend(f"Connector record skipped: {message}" for message in connector_errors.values())
        self._import_server_files(servers, messages)
        self._extension_config_messages = messages
        return servers, connectors, messages

    def _import_server_files(self, servers: Any, messages: list[str]) -> None:
        """Merge drop-in `servers/*.json` MCP definitions (read-only).

        File shape: {name, transport, command|url, args, env, enabled,
        auto_approve, allow_paths}. Records from the main config win on
        id conflicts; imported records are never written back.
        """
        from .extensions.mcp_config import McpServerConfig as _ServerCfg

        self._imported_server_ids = set()
        folder = self.paths.extensions_dir / "servers"
        if not folder.is_dir():
            return
        known = {str(item.id) for item in servers.servers}
        try:
            files = sorted(folder.glob("*.json"))[:64]
        except OSError:
            return
        for file in files:
            try:
                if file.stat().st_size > 64 * 1024:
                    raise ValueError("file exceeds 64 KB")
                raw = json.loads(file.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("expected a JSON object")
                name = str(raw.get("name") or file.stem).strip()
                raw_env = raw.get("env")
                env: dict[Any, Any] = raw_env if isinstance(raw_env, dict) else {}
                record = _ServerCfg.model_validate(
                    {
                        "id": name,
                        "display_name": name,
                        "transport": str(raw.get("transport") or ("stdio" if raw.get("command") else "streamable_http")),
                        "command": str(raw.get("command") or ""),
                        "url": str(raw.get("url") or ""),
                        "args": [str(arg) for arg in (raw.get("args") or [])][:32],
                        "cwd": str(raw.get("cwd") or ""),
                        "env_plain": {str(k)[:80]: str(v)[:4096] for k, v in list(env.items())[:32]},
                        "enabled": bool(raw.get("enabled", False)),
                        "auto_approve": bool(raw.get("auto_approve", False)),
                        "allow_paths": [str(item)[:1000] for item in (raw.get("allow_paths") or [])][:32],
                        "timeout_seconds": raw.get("timeout_seconds", 30),
                    }
                )
                if record.id in known:
                    continue
                known.add(record.id)
                servers.servers.append(record)
                self._imported_server_ids.add(record.id)
            except Exception as exc:
                messages.append(f"servers/{file.name} skipped: {exc}")

    def _extension_records(self) -> list[tuple[str, McpServerConfig]]:
        servers, connectors, _messages = self._load_extension_configs()
        records: list[tuple[str, McpServerConfig]] = []
        for server in servers.servers:
            if server.enabled:
                records.append(("mcp", server))
        for connector in connectors.connectors:
            if connector.enabled:
                records.append(("connector", connector_as_server(connector)))
        return records

    @staticmethod
    def _extension_summary(item: Any, kind: str) -> dict[str, Any]:
        config = connector_as_server(item) if kind == "connector" else item
        destination = "Configured privately"
        if getattr(config, "url", ""):
            destination = urllib_parse.urlparse(str(config.url)).hostname or destination
        elif getattr(config, "command", ""):
            destination = Path(str(config.command)).name or destination
        # Never serialize the whole stored record to the WebView: it can
        # contain environment values, headers, full URLs/commands, tool
        # permissions, and other connection details the summary UI does not
        # need. Discovery/trust use IDs and reload the record in Python.
        return {
            "id": str(item.id),
            "display_name": str(getattr(item, "display_name", "") or item.id),
            "enabled": bool(getattr(item, "enabled", False)),
            "transport": str(getattr(config, "transport", "remote") or "remote"),
            "trusted": bool(getattr(item, "toolset_sha256", "")),
            "destination_label": destination,
        }

    def _extensions_payload(self) -> dict[str, Any]:
        servers, connectors, config_messages = self._load_extension_configs()
        messages = list(self._extension_messages) or list(config_messages)
        counts: dict[str, int] = {}
        definitions = list(self.registry.definitions.values())
        for definition in definitions:
            if definition.source.startswith("mcp:"):
                server_id = definition.source[4:]
                counts[server_id] = counts.get(server_id, 0) + 1

        manager = getattr(self.extension_host, "manager", None)

        def resource_summary(item: Any) -> dict[str, Any]:
            data = item.model_dump(mode="json") if hasattr(item, "model_dump") else (item if isinstance(item, dict) else {})
            uri = str(data.get("uri") or data.get("uriTemplate") or getattr(item, "uri", "") or "")
            return {
                "uri": uri[:512],
                "name": str(data.get("name") or uri)[:160],
                "description": str(data.get("description") or "")[:300],
                "mime_type": str(data.get("mimeType") or data.get("mime_type") or ""),
                "template": bool(data.get("uriTemplate")),
            }

        def prompt_summary(item: Any) -> dict[str, Any]:
            data = item.model_dump(mode="json") if hasattr(item, "model_dump") else (item if isinstance(item, dict) else {})
            args = []
            for arg in data.get("arguments") or []:
                if isinstance(arg, dict):
                    args.append({"name": str(arg.get("name") or ""), "required": bool(arg.get("required")), "description": str(arg.get("description") or "")[:200]})
            return {
                "name": str(data.get("name") or "")[:160],
                "description": str(data.get("description") or "")[:300],
                "arguments": args[:16],
            }

        def tool_summary(item: Any) -> dict[str, Any]:
            annotations = getattr(item, "annotations", None)
            return {
                "name": str(getattr(item, "name", "") or "")[:160],
                "description": str(getattr(item, "description", "") or "")[:300],
                "read_only_hint": (annotations or {}).get("readOnlyHint") if isinstance(annotations, dict) else None,
                "destructive_hint": (annotations or {}).get("destructiveHint") if isinstance(annotations, dict) else None,
            }

        def with_counts(items: list[Any], kind: str) -> list[dict[str, Any]]:
            rows = []
            for item in items:
                row = self._extension_summary(item, kind)
                count_key = connector_runtime_id(str(item.id)) if kind == "connector" else str(item.id)
                row["active_tool_count"] = counts.get(count_key, 0)
                row["runtime_id"] = count_key
                row["allow_sampling"] = bool(getattr(item, "allow_sampling", False))
                row["allow_elicitation"] = bool(getattr(item, "allow_elicitation", True))
                row["auto_approve"] = bool(getattr(item, "auto_approve", False))
                row["allow_paths"] = list(getattr(item, "allow_paths", []) or [])
                row["imported"] = kind == "mcp" and str(item.id) in self._imported_server_ids
                row["timeout_seconds"] = int(getattr(item, "timeout_seconds", 60) or 60)
                row["permissions"] = dict(getattr(item, "permissions", {}) or {})
                row["has_secret"] = bool(getattr(item, "env_refs", {}) or getattr(item, "header_refs", {}))
                if kind == "connector":
                    row["auth_type"] = str(getattr(item, "auth_type", "none") or "none")
                    row["last_used_at"] = str(getattr(item, "last_used_at", "") or "")
                if manager is not None:
                    row["discovered_tools"] = [tool_summary(tool) for tool in manager.tools.get(count_key, [])][:64]
                    row["resources"] = [resource_summary(entry) for entry in manager.resources.get(count_key, [])][:64]
                    row["prompts"] = [prompt_summary(entry) for entry in manager.prompts.get(count_key, [])][:64]
                    row["connected"] = count_key in manager.sessions
                    row["unavailable"] = count_key in manager.unavailable
                    row["status"] = (
                        "running" if row["connected"]
                        else "error" if (row["unavailable"] or manager.last_errors.get(count_key))
                        else "stopped"
                    )
                    row["stale"] = count_key in manager.stale
                    row["last_error"] = str(manager.last_errors.get(count_key, "") or "")[:300]
                    row["logs"] = list(manager.server_logs.get(count_key, []))[-10:]
                    row["progress"] = manager.last_progress.get(count_key)
                rows.append(row)
            return rows

        return {
            "servers": with_counts(servers.servers, "mcp"),
            "connectors": with_counts(connectors.connectors, "connector"),
            # Names are not needed by Settings and can disclose private
            # server/tool naming; counts carry the useful UI information.
            "dynamic_tool_count": len(self._dynamic_names),
            "messages": _json_safe(messages),
            "extensions_enabled": bool(getattr(self.config, "extensions_enabled", True)),
            "routing": _json_safe(self._last_routing),
        }

    @staticmethod
    def _internet_state() -> str:
        monitor = default_monitor()
        monitor.refresh_async()  # no-op while fresh; never blocks
        state = monitor.cached_online()
        if state is None:
            return "unknown"
        return "online" if state else "offline"

    def _server_payload(self, *, include_logs: bool = False) -> dict[str, Any]:
        model = Path(self.config.model_path or self.paths.model_file)
        binary = self._server_binary
        if binary is None and not self._server_binary_checked:
            binary = find_llama_server(self.paths, self.config.llama_server_path)
        try:
            model_stat = model.stat()
            model_exists = True
            model_size = model_stat.st_size
        except OSError:
            model_exists = False
            model_size = 0
        payload: dict[str, Any] = {
            "running": self.server_manager.is_running,
            "healthy": self._server_healthy,
            "state": self._server_state,
            "failures": self._health_state.failures,
            "intentionally_stopped": self._server_intentionally_stopped,
            "desired_running": self._server_desired_running,
            # The UI needs filenames/status, not full filesystem paths, the
            # loopback service address, or the process launch command.
            "model_path": model.name,
            "model_exists": model_exists,
            "model_size": model_size,
            "expected_model_size": EXPECTED_GGUF_SIZE,
            "model_filename": GGUF_FILENAME,
            "binary": binary.name if binary else "",
            "binary_checked": self._server_binary_checked,
            "build": self._server_build,
            "build_supported": self._server_supported,
            "minimum_build": MIN_LLAMA_BUILD,
            # Last known internet state for the UI ("online"/"offline"/
            # "unknown"); refreshed in the background, never probed
            # synchronously on this payload path.
            "internet": self._internet_state(),
            "exit_code": self.server_manager.exit_code,
        }
        if include_logs:
            payload["logs"] = self.server_manager.logs(300)
        return payload

    # ------------------------------------------------------------------
    # Vision Live: local speech-to-text, one-frame screen sharing, mini UI
    # ------------------------------------------------------------------
    def _live_capabilities(self) -> dict[str, Any]:
        model = Path(self.config.model_path or self.paths.model_file)
        stt = speech_to_text_status(self.paths, self.config)
        vision = screen_vision_status(
            self.config,
            model,
            server_running=self.server_manager.is_running,
            server_command=list(getattr(self.server_manager, "command", []) or []),
        )
        return {
            "voice_chat_available": bool(stt["available"]),
            "speech_to_text": stt,
            "screen_capture_available": sys.platform == "win32",
            "screen_vision": vision,
            "mini_open": self._live_mini_window is not None,
            "privacy": {
                "push_to_talk_only": True,
                "audio_saved": False,
                "screen_saved": False,
                "screen_remote_allowed": False,
                "computer_control": False,
            },
        }

    def get_live_capabilities(self) -> dict[str, Any]:
        return {"ok": True, "live": _json_safe(self._live_capabilities())}

    def download_speech_to_text(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = _payload_dict(payload)
        requested = str(data.get("model_size") or self.config.stt_model_size)

        def work() -> dict[str, Any]:
            result = ensure_speech_to_text(
                self.paths,
                self.config,
                progress=lambda stage, downloaded, total: self._task_progress(
                    "download_stt", f"Downloading {stage}", downloaded, total
                ),
                model_size=requested,
            )
            self.config.stt_binary_path = str(result["binary_path"])
            self.config.stt_model_size = str(result["model_size"])
            self.config.save(self.paths)
            live = self._live_capabilities()
            self._emit("live_updated", {"live": live})
            return {"ok": True, "message": str(result["message"]), "live": live}

        return self._run_task("download_stt", work)

    def transcribe_live_audio(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = _payload_dict(payload)
        encoded = data.get("audio_base64")
        if not isinstance(encoded, str) or not encoded:
            return {"ok": False, "message": "No voice clip was received."}
        if len(encoded) > ((MAX_WAV_BYTES + 2) * 4 // 3) + 8:
            return {"ok": False, "message": "That voice clip is too long. Please use a shorter message."}
        try:
            wav_bytes = base64.b64decode(encoded, validate=True)
        except Exception:
            return {"ok": False, "message": "That voice clip could not be read."}
        try:
            # The clip bytes are never logged and the transcription helper
            # deletes its one temporary WAV on every path.
            text = transcribe_wav_bytes(self.paths, self.config, wav_bytes)
        except (RuntimeError, ValueError) as exc:
            return {"ok": False, "message": str(exc), "live": self._live_capabilities()}
        except Exception:
            logger.exception("Vision Live transcription failed")
            return {"ok": False, "message": "Local speech-to-text failed. Please try again."}
        return {"ok": True, "text": text}

    def capture_screen_frame(self) -> dict[str, Any]:
        mini = self._live_mini_window
        hidden = False
        if mini is not None:
            try:
                mini.hide()
                hidden = True
                time.sleep(0.18)
            except Exception:
                hidden = False
        try:
            frame = capture_screen_frame()
            return frame.payload()
        except RuntimeError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception:
            logger.exception("Vision Live screen capture failed")
            return {"ok": False, "message": "VISION could not capture the screen."}
        finally:
            if hidden and mini is not None:
                try:
                    mini.show()
                except Exception:
                    pass

    def _validated_live_frame(self, payload: dict[str, Any]) -> tuple[str, str]:
        data_url = str(payload.get("live_image_data_url") or "")
        if not data_url:
            return "", ""
        if payload.get("live_screen_approved") is not True:
            return "", "Preview and approve the screen frame before sharing it."
        vision = self._live_capabilities()["screen_vision"]
        if not vision.get("available"):
            return "", str(vision.get("reason") or "Screen vision is not available with this model.")
        try:
            decode_image_data_url(data_url)
        except ValueError as exc:
            return "", str(exc)
        return data_url, ""

    def set_live_state(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = _payload_dict(payload)
        with self._live_lock:
            state = dict(self._live_state)
            if "active" in data:
                state["active"] = bool(data.get("active"))
            status = str(data.get("status") or state.get("status") or "idle")
            state["status"] = status if status in LIVE_STATUSES else "idle"
            for key, limit in (("caption", 1200), ("user_text", 4000), ("assistant_text", 4000)):
                if key in data:
                    state[key] = str(data.get(key) or "")[:limit]
            if "level" in data:
                try:
                    state["level"] = max(0.0, min(1.0, float(data.get("level") or 0.0)))
                except (TypeError, ValueError):
                    state["level"] = 0.0
            for key in ("screen_mode", "frame_approved", "speech_output"):
                if key in data:
                    state[key] = bool(data.get(key))
            self._live_state = state
            return {"ok": True, "live_state": dict(state)}

    def get_live_state(self) -> dict[str, Any]:
        with self._live_lock:
            return {
                "ok": True,
                "live_state": dict(self._live_state),
                "command": dict(self._live_command),
            }

    def live_mini_action(self, action: str = "") -> dict[str, Any]:
        clean = str(action or "").strip().lower()
        if clean not in LIVE_MINI_ACTIONS:
            return {"ok": False, "message": "Unknown Vision Live action."}
        with self._live_lock:
            seq = int(self._live_command.get("seq") or 0) + 1
            self._live_command = {"seq": seq, "action": clean}
        if clean == "stop":
            self.stop_generation()
        return {"ok": True, "command": dict(self._live_command)}

    def open_live_mini(self) -> dict[str, Any]:
        if self._live_mini_window is not None:
            try:
                self._live_mini_window.show()
            except Exception:
                self._live_mini_window = None
            else:
                return {"ok": True, "message": "Vision Live mini is already open."}
        if not self._live_mini_url:
            return {"ok": False, "message": "The floating Vision Live window is available in the desktop app."}
        try:
            import webview

            mini = webview.create_window(
                "VISION Live",
                url=self._live_mini_url,
                js_api=self,
                width=320,
                height=400,
                min_size=(240, 260),
                frameless=True,
                easy_drag=True,
                on_top=True,
                resizable=True,
                background_color="#0B0D10",
                text_select=False,
            )
            if mini is None:
                return {"ok": False, "message": "VISION could not open the floating Live window."}
            self._live_mini_window = mini
            try:
                mini.events.closed += lambda: setattr(self, "_live_mini_window", None)
            except Exception:
                pass
        except Exception:
            logger.exception("Could not open the Vision Live mini window")
            return {"ok": False, "message": "VISION could not open the floating Live window."}
        return {"ok": True, "message": "Vision Live mini opened."}

    def close_live_mini(self) -> dict[str, Any]:
        mini = self._live_mini_window
        self._live_mini_window = None
        if mini is not None:
            def destroy() -> None:
                try:
                    mini.destroy()
                except Exception:
                    pass

            timer = threading.Timer(0.05, destroy)
            timer.daemon = True
            timer.start()
        return {"ok": True}

    def _paths_payload(self, *, detailed: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "app_data": str(self.paths.app_data),
            "workspace": str(self.paths.workspace),
            "installation_mode": self.paths.installation_mode,
            "installation_root": (
                str(self.paths.installation_root) if self.paths.installation_root else ""
            ),
        }
        if detailed:
            payload.update(
                {
                    "model_file": str(self.paths.model_file),
                    "audit_log": str(self.paths.audit_log),
                    "needs_log": str(self.paths.needs_log),
                }
            )
        return payload

    def get_state(self) -> dict[str, Any]:
        conversations = self.history.list_conversations()
        if not conversations:
            conversation_id = self.history.create_conversation(project_id=self.config.active_project_id or None)
            conversations = self.history.list_conversations()
        else:
            conversation_id = str(conversations[0]["id"])
        return {
            "app": {
                "name": PRODUCT_NAME,
                "version": __version__,
                "build": BUILD_COMMIT,
                "credits": CREDITS_LINE,
                "first_run_completed": self.config.first_run_completed,
            },
            "config": self._config_payload(),
            "credentials": {
                "remote_api_key_stored": bool(self.credentials.get("REMOTE_API_KEY")),
                "hf_token_stored": bool(self.credentials.get("HF_TOKEN")),
            },
            "paths": self._paths_payload(),
            "conversations": _json_safe(conversations),
            "current_conversation_id": conversation_id,
            "projects": self._projects_payload(),
            "active_project_id": self.config.active_project_id,
            "skills": self._skills_payload(),
            "extensions": self._extensions_payload(),
            "server": self._server_payload(),
            "attachments": self._attachments_payload(),
            "hardware": self._hardware_payload(),
            "library_count": self.library.count() if self.library is not None else 0,
            "updates": self._updates_payload(),
            "live": self._live_capabilities(),
            "chat_busy": self._chat_busy,
        }

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------
    def list_conversations(self, query: str = "") -> dict[str, Any]:
        query_text = query.strip() if isinstance(query, str) else ""
        rows = self.history.search_conversations(query_text) if query_text else self.history.list_conversations()
        return {"conversations": _json_safe(rows)}

    def create_conversation(self, project_id: str = "") -> dict[str, Any]:
        selected = self._validated_project_id(project_id or self.config.active_project_id)
        conversation_id = self.history.create_conversation(project_id=selected or None)
        return {
            "conversation": _json_safe(self._conversation_row(conversation_id)),
            "messages": [],
            "conversations": _json_safe(self.history.list_conversations()),
        }

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        return {
            "conversation": _json_safe(self._conversation_row(conversation_id)),
            "messages": _json_safe(self.history.list_messages(conversation_id)[-100:]),
        }

    def rename_conversation(self, conversation_id: str, title: str) -> dict[str, Any]:
        clean = str(title).strip()
        if clean:
            self.history.rename_conversation(conversation_id, clean)
        return {"conversation": _json_safe(self._conversation_row(conversation_id)), "conversations": _json_safe(self.history.list_conversations())}

    def set_conversation_pinned(self, conversation_id: str, pinned: bool) -> dict[str, Any]:
        self.history.set_conversation_flags(conversation_id, pinned=bool(pinned))
        return {"conversations": _json_safe(self.history.list_conversations(include_archived=True))}

    def set_conversation_archived(self, conversation_id: str, archived: bool) -> dict[str, Any]:
        self.history.set_conversation_flags(conversation_id, archived=bool(archived))
        return {"conversations": _json_safe(self.history.list_conversations(include_archived=True))}

    def delete_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._chat_lock:
            if self._chat_busy and self._active_chat_conversation_id == str(conversation_id):
                return {
                    "ok": False,
                    "message": "Stop the current response before deleting its conversation.",
                }
        self.history.delete_conversation(conversation_id)
        if self.library is not None:
            try:
                self.library.forget_conversation(str(conversation_id))
            except Exception:
                logger.exception("Could not remove library usage for the deleted conversation")
        conversations = self.history.list_conversations()
        if not conversations:
            self.history.create_conversation(
                project_id=self._validated_project_id(self.config.active_project_id) or None
            )
            conversations = self.history.list_conversations()
        return {"conversations": _json_safe(conversations), "current_conversation_id": conversations[0]["id"] if conversations else ""}

    def set_active_project(self, project_id: str = "", conversation_id: str = "") -> dict[str, Any]:
        self.config.active_project_id = self._validated_project_id(project_id)
        self.config.save(self.paths)
        if conversation_id:
            self.projects.assign_conversation(conversation_id, self.config.active_project_id or None)
        return {"active_project_id": self.config.active_project_id, "projects": self._projects_payload()}

    def create_project(self, name: str) -> dict[str, Any]:
        clean = " ".join(str(name or "").split())[:120].strip()
        if not clean:
            return {"ok": False, "message": "Project name cannot be empty"}
        try:
            project_id = self.projects.create_project(
                clean,
                self.projects.default_root(self.paths.workspace, clean),
            )
        except Exception as exc:
            return {"ok": False, "message": f"Could not create the project: {exc}"}
        project = next(
            (row for row in self._projects_payload() if str(row.get("id")) == project_id),
            {"id": project_id, "name": clean},
        )
        return {"ok": True, "project": _json_safe(project), "projects": self._projects_payload()}

    def assign_conversation_project(
        self, conversation_id: str, project_id: str = ""
    ) -> dict[str, Any]:
        conversation_key = str(conversation_id or "")
        if not conversation_key or self._conversation_row(conversation_key) is None:
            return {"ok": False, "message": "That chat no longer exists"}
        requested = str(project_id or "")
        validated = self._validated_project_id(requested) if requested else ""
        if requested and not validated:
            return {"ok": False, "message": "That project no longer exists"}
        self.projects.assign_conversation(conversation_key, validated or None)
        return {
            "ok": True,
            "conversation": _json_safe(self._conversation_row(conversation_key)),
            "conversations": _json_safe(self.history.list_conversations(include_archived=True)),
            "projects": self._projects_payload(),
        }

    def export_conversation(self, conversation_id: str) -> dict[str, Any]:
        if self.window is None:
            return {"ok": False, "message": "Window is not ready"}
        import webview

        result = self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename="vision-conversation.md",
            file_types=("Markdown (*.md)",),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        filename = result if isinstance(result, str) else result[0]
        lines = ["# VISION conversation", ""]
        for row in self.history.list_messages(conversation_id):
            lines.extend([f"## {row['role'].title()}", "", row["content"], ""])
        Path(filename).write_text("\n".join(lines), encoding="utf-8")
        return {"ok": True, "path": str(filename)}

    # ------------------------------------------------------------------
    # Attachments, Item Library and confirmations
    # ------------------------------------------------------------------
    def _attachments_payload(self) -> list[dict[str, Any]]:
        with self._attachments_lock:
            return [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "size": item["size"],
                    "library_item_id": str(item.get("library_item_id") or ""),
                    "kind": str(item.get("kind") or "text"),
                    "extension": str(item.get("extension") or ""),
                }
                for item in self._attachments.values()
            ]

    def _library_payload(self) -> dict[str, Any]:
        if self.library is None:
            return {"ok": True, "items": [], "library_count": 0}
        items = self.library.list_items()
        return {"ok": True, "items": _json_safe(items), "library_count": len(items)}

    def _add_attachment(
        self,
        *,
        name: str,
        path: Path,
        size: int,
        library_item_id: str = "",
        kind: str = "text",
        extension: str = "",
    ) -> bool:
        with self._attachments_lock:
            if library_item_id and any(
                str(item.get("library_item_id") or "") == library_item_id
                for item in self._attachments.values()
            ):
                return True
            if len(self._attachments) >= MAX_ATTACHMENTS:
                return False
            attachment_id = uuid.uuid4().hex
            self._attachments[attachment_id] = {
                "id": attachment_id,
                "name": name,
                "path": str(path),
                "size": int(size),
                "library_item_id": str(library_item_id or ""),
                "kind": str(kind or "text"),
                "extension": str(extension or path.suffix.lower()),
            }
            return True


    def _available_models(self) -> list[dict[str, Any]]:
        """GGUF files the user can switch between from the models folder."""
        rows: list[dict[str, Any]] = []
        try:
            candidates = sorted(self.paths.models_dir.glob("*.gguf"))
        except OSError:
            candidates = []
        active = str(self.config.model_path or "")
        for candidate in candidates:
            if candidate.name.endswith(".previous.gguf"):
                continue
            try:
                size = candidate.stat().st_size
            except OSError:
                continue
            rows.append(
                {
                    "name": candidate.name,
                    "path": str(candidate),
                    "size": size,
                    "active": str(candidate) == active,
                }
            )
        return rows

    def get_available_models(self) -> dict[str, Any]:
        return {"available_models": self._available_models()}

    def list_checkpoints(self) -> dict[str, Any]:
        if self.checkpoints is None:
            return {"ok": False, "message": "Checkpoints are unavailable", "checkpoints": []}
        rows = []
        for item in self.checkpoints.list()[:100]:
            original = str(item.get("original_path") or "")
            rows.append(
                {
                    "id": str(item.get("id") or ""),
                    "file_name": Path(original).name if original else "",
                    "original_path": original,
                    "created_at": str(item.get("created_at") or ""),
                    "existed": bool(item.get("existed")),
                }
            )
        return {"ok": True, "checkpoints": rows}

    def restore_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        if self.checkpoints is None:
            return {"ok": False, "message": "Checkpoints are unavailable"}
        manager = self.checkpoints

        def work() -> dict[str, Any]:
            target = manager.restore(str(checkpoint_id))
            return {"ok": True, "message": f"Restored {target.name} from its checkpoint."}

        return self._run_task("restore_checkpoint", work)

    def get_library(self) -> dict[str, Any]:
        try:
            return self._library_payload()
        except Exception as exc:
            logger.exception("Could not load the Item Library")
            return {"ok": False, "message": f"Could not load the Item Library: {exc}", "items": []}

    def import_library_files(self) -> dict[str, Any]:
        if self.library is None:
            return {"ok": False, "message": "Item Library is unavailable", "items": []}
        if self.window is None:
            return {**self._library_payload(), "message": "Window is not ready"}
        import webview

        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=(
                "Library files (*.txt;*.md;*.py;*.json;*.csv;*.log;*.yaml;*.yml;*.toml;*.pdf;*.docx;*.png;*.jpg;*.jpeg;*.gif;*.webp)",
                "All files (*.*)",
            ),
        )
        imported: list[str] = []
        skipped = 0
        if result:
            paths = [result] if isinstance(result, str) else list(result)
            for raw in paths:
                try:
                    item = self.library.import_file(Path(raw))
                    imported.append(str(item["id"]))
                except Exception:
                    skipped += 1
                    logger.exception("Could not import a selected file into the Item Library")
        payload = self._library_payload()
        payload.update({"imported_ids": imported})
        if skipped:
            payload["message"] = f"Skipped {skipped} file(s) that were unavailable or over the 25 MB library limit."
        return payload

    def attach_library_item(self, item_id: str) -> dict[str, Any]:
        if self.library is None:
            return {"ok": False, "message": "Item Library is unavailable", "attachments": self._attachments_payload()}
        item = self.library.get_item(str(item_id))
        if item is None:
            return {"ok": False, "message": "That library item no longer exists", "attachments": self._attachments_payload()}
        if not item.get("attachable"):
            return {
                "ok": False,
                "message": "Only text files (256 KB) and PDF/DOCX documents (10 MB, text is extracted) can be attached.",
                "attachments": self._attachments_payload(),
            }
        path = self.library.item_path(str(item_id))
        if path is None:
            return {"ok": False, "message": "That library file is missing", "attachments": self._attachments_payload()}
        try:
            size = path.stat().st_size
        except OSError:
            return {"ok": False, "message": "That library file is missing", "attachments": self._attachments_payload()}
        added = self._add_attachment(
            name=str(item.get("name") or path.name),
            path=path,
            size=size,
            library_item_id=str(item["id"]),
            kind=str(item.get("kind") or "text"),
            extension=str(item.get("extension") or path.suffix.lower()),
        )
        if not added:
            return {
                "ok": False,
                "message": f"Only {MAX_ATTACHMENTS} files can be attached at once.",
                "attachments": self._attachments_payload(),
            }
        return {"ok": True, "attachments": self._attachments_payload()}

    def rename_library_item(self, item_id: str, name: str) -> dict[str, Any]:
        if self.library is None:
            return {"ok": False, "message": "Item Library is unavailable", "items": []}
        try:
            item = self.library.rename_item(str(item_id), str(name or ""))
        except (KeyError, ValueError) as exc:
            return {"ok": False, "message": str(exc), **self._library_payload()}
        with self._attachments_lock:
            for attachment in self._attachments.values():
                if str(attachment.get("library_item_id") or "") == str(item_id):
                    attachment["name"] = str(item["name"])
        return {"ok": True, "item": _json_safe(item), "attachments": self._attachments_payload(), **self._library_payload()}

    def delete_library_item(self, item_id: str) -> dict[str, Any]:
        if self.library is None:
            return {"ok": False, "message": "Item Library is unavailable", "items": []}
        deleted = self.library.delete_item(str(item_id))
        with self._attachments_lock:
            stale = [
                attachment_id
                for attachment_id, item in self._attachments.items()
                if str(item.get("library_item_id") or "") == str(item_id)
            ]
            for attachment_id in stale:
                self._attachments.pop(attachment_id, None)
        return {
            "ok": bool(deleted),
            "message": "" if deleted else "That library item no longer exists",
            "attachments": self._attachments_payload(),
            **self._library_payload(),
        }

    def choose_attachments(self) -> dict[str, Any]:
        if self.window is None:
            return {"attachments": self._attachments_payload(), "message": "Window is not ready"}
        import webview

        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=("Text files (*.txt;*.md;*.py;*.json;*.csv;*.log;*.yaml;*.yml;*.toml)", "All files (*.*)"),
        )
        skipped = 0
        saved_only = 0
        if result:
            paths = [result] if isinstance(result, str) else list(result)
            for raw in paths:
                path = Path(raw)
                try:
                    size = path.stat().st_size if path.is_file() else None
                except OSError:
                    size = None
                if size is None:
                    skipped += 1
                    continue

                # Every upload becomes a persistent, deduplicated Item
                # Library copy. The ephemeral attachment points at that
                # managed copy, so the file remains usable if the original
                # moves before the message is sent.
                library_item: dict[str, Any] | None = None
                attach_path = path
                if self.library is not None:
                    try:
                        library_item = self.library.import_file(path)
                        managed_path = self.library.item_path(str(library_item["id"]))
                        if managed_path is not None:
                            attach_path = managed_path
                    except Exception:
                        logger.exception("Could not save an upload to the Item Library")
                        skipped += 1
                        continue
                if size > MAX_ATTACHMENT_BYTES or (
                    library_item is not None and not library_item.get("attachable")
                ):
                    saved_only += 1
                    continue
                added = self._add_attachment(
                    name=str((library_item or {}).get("name") or path.name),
                    path=attach_path,
                    size=size,
                    library_item_id=str((library_item or {}).get("id") or ""),
                    kind=str((library_item or {}).get("kind") or "text"),
                    extension=str((library_item or {}).get("extension") or path.suffix.lower()),
                )
                if not added:
                    skipped += 1
        payload: dict[str, Any] = {"attachments": self._attachments_payload()}
        if self.library is not None:
            payload.update(self._library_payload())
        messages: list[str] = []
        if skipped:
            messages.append(f"Skipped {skipped} unavailable or excess attachment(s); the limit is {MAX_ATTACHMENTS}.")
        if saved_only:
            messages.append(f"Saved {saved_only} file(s) to the Item Library; only supported UTF-8 text up to 256 KB can be attached to a message.")
        if messages:
            payload["message"] = " ".join(messages)
        return payload

    def remove_attachment(self, attachment_id: str) -> dict[str, Any]:
        with self._attachments_lock:
            self._attachments.pop(str(attachment_id), None)
        return {"attachments": self._attachments_payload()}

    def _confirm(self, title: str, message: str, details: dict[str, Any]) -> bool:
        if self.window is None or not self._frontend_ready or self._closed.is_set():
            return False
        request_id = uuid.uuid4().hex
        event = threading.Event()
        state: dict[str, Any] = {"approved": False}
        with self._confirm_lock:
            self._confirmations[request_id] = (event, state)
        self._flush_stream()
        self._emit(
            "confirmation_requested",
            {"id": request_id, "title": title, "message": message, "details": _json_safe(details)},
        )
        event.wait(timeout=300)
        with self._confirm_lock:
            self._confirmations.pop(request_id, None)
            return bool(state["approved"])

    def resolve_confirmation(self, request_id: str, approved: bool, values: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._confirm_lock:
            stored = self._confirmations.pop(str(request_id), None)
            if not stored:
                return {"ok": False}
            event, state = stored
            state["approved"] = bool(approved)
            if isinstance(values, dict):
                state["values"] = values
            event.set()
            return {"ok": True}

    def _elicit_user(self, title: str, message: str, fields: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Blocking MCP elicitation prompt (runs on a worker thread)."""
        if self.window is None or not self._frontend_ready or self._closed.is_set():
            return None
        request_id = uuid.uuid4().hex
        event = threading.Event()
        state: dict[str, Any] = {"approved": False, "values": {}}
        with self._confirm_lock:
            self._confirmations[request_id] = (event, state)
        self._flush_stream()
        self._emit(
            "confirmation_requested",
            {
                "id": request_id,
                "title": title,
                "message": message,
                "details": {"elicitation_fields": _json_safe(fields)},
            },
        )
        event.wait(timeout=300)
        with self._confirm_lock:
            self._confirmations.pop(request_id, None)
        if not state.get("approved"):
            return None
        values = state.get("values")
        return values if isinstance(values, dict) else {}

    def _extension_event(self, kind: str, server_id: str, data: dict[str, Any]) -> None:
        self._emit("extension_event", {"kind": kind, "server_id": server_id, "data": _json_safe(data)})
        if kind == "list_changed":
            # The server says its tools/resources/prompts changed. Refresh
            # re-runs discovery; the trust fingerprint check decides whether
            # tools stay live or need re-approval.
            self.refresh_extensions()

    def _schedule_timer(self, minutes: float, label: str) -> None:
        seconds = max(0.1, float(minutes) * 60)

        def _fire() -> None:
            try:
                self.tool_context.timers.remove(timer)
            except ValueError:
                pass
            self._emit("timer_fired", {"label": label, "minutes": minutes})

        timer = threading.Timer(seconds, _fire)
        timer.daemon = True
        self.tool_context.timers.append(timer)
        timer.start()
        self._emit("timer_scheduled", {"label": label, "minutes": minutes})

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    def _active_project(self, conversation_id: str, explicit_project_id: str = "") -> dict[str, Any] | None:
        project_id = explicit_project_id
        if not project_id and conversation_id:
            row = self._conversation_row(conversation_id)
            project_id = str((row or {}).get("project_id") or "")
        if not project_id:
            project_id = self.config.active_project_id
        return self.projects.get_project(project_id) if project_id else None

    def _enabled_skill_names(self, project_id: str) -> set[str]:
        if project_id:
            project_enabled = self.projects.enabled_extensions(project_id, "skill")
            if project_enabled:
                return project_enabled
        return set(self.config.enabled_skills)

    def _recent_turn_text(self, conversation_id: str) -> str:
        """Last two turns as plain text for tool/skill relevance ranking."""
        try:
            messages = self.history.list_messages(conversation_id)
        except Exception:
            return ""
        parts: list[str] = []
        for item in messages[-4:]:
            content = str(item.get("content") or "").strip()
            if content:
                parts.append(content[:600])
        return "\n".join(parts)[:2000]

    def _embed_texts(self, texts: list[str]) -> list[list[float]] | None:
        """Optional embeddings from the local endpoint; None when unavailable.

        Failures are negative-cached for two minutes so a missing
        embeddings endpoint never stalls every turn; the Tool Router
        then ranks lexically.
        """
        now = time.monotonic()
        if now < self._embeddings_unavailable_until or not texts:
            return None
        try:
            import httpx as _httpx

            response = _httpx.post(
                f"http://127.0.0.1:{self.config.local_port}/v1/embeddings",
                json={"input": texts},
                timeout=3.0,
            )
            response.raise_for_status()
            data = response.json().get("data") or []
            vectors = [list(item.get("embedding") or []) for item in data]
            if len(vectors) != len(texts) or not all(vectors):
                raise ValueError("unexpected embeddings shape")
            return vectors
        except Exception:
            self._embeddings_unavailable_until = now + 120
            return None

    def _prepare_turn(self, payload: dict[str, Any], *, regenerate: bool) -> dict[str, Any]:
        conversation_id = str(payload.get("conversation_id") or "")
        if not conversation_id:
            conversation_id = self.history.create_conversation(
                project_id=self._validated_project_id(self.config.active_project_id) or None
            )
        text = "" if regenerate else str(payload.get("text") or "")
        retrieval_text = text
        if regenerate:
            retrieval_text, _answer = self.history.last_exchange(conversation_id)

        final_text = text
        attachment_ids = list(dict.fromkeys(str(item) for item in payload.get("attachment_ids") or []))
        if len(attachment_ids) > MAX_ATTACHMENTS:
            raise RuntimeError(f"Too many attachments; the limit is {MAX_ATTACHMENTS}")
        consumed: list[tuple[str, str, str]] = []
        total_attachment_bytes = 0
        for attachment_id in attachment_ids:
            with self._attachments_lock:
                stored = self._attachments.get(attachment_id)
                record = dict(stored) if stored else None
            if not record:
                raise RuntimeError("An attachment is no longer available. Remove it and try again.")
            path = Path(str(record.get("path") or ""))
            display_name = str(record.get("name") or path.name)
            if path.suffix.lower() in {".pdf", ".docx"}:
                try:
                    sections = parse_document_file(path)
                except FileNotFoundError as exc:
                    with self._attachments_lock:
                        self._attachments.pop(attachment_id, None)
                    raise RuntimeError(f"Attachment is no longer available: {display_name}") from exc
                except Exception as exc:
                    raise RuntimeError(f"Could not extract text from {display_name}: {exc}") from exc
                content = "\n\n".join(section.text for section in sections)[:MAX_ATTACHMENT_BYTES]
                total_attachment_bytes += len(content.encode("utf-8"))
                if total_attachment_bytes > MAX_ATTACHMENTS_TOTAL_BYTES:
                    raise RuntimeError("Attachments exceed the 1 MB total limit for one message")
                final_text += f"\n\nAttachment: {display_name}\n```\n{content}\n```"
                consumed.append(
                    (
                        attachment_id,
                        str(record.get("path") or ""),
                        str(record.get("library_item_id") or ""),
                    )
                )
                continue
            try:
                with path.open("rb") as stream:
                    data = stream.read(MAX_ATTACHMENT_BYTES + 1)
            except FileNotFoundError as exc:
                with self._attachments_lock:
                    self._attachments.pop(attachment_id, None)
                raise RuntimeError(f"Attachment is no longer available: {display_name}") from exc
            except OSError as exc:
                raise RuntimeError(f"Could not read attachment {display_name}: {exc}") from exc
            if len(data) > MAX_ATTACHMENT_BYTES:
                raise RuntimeError(f"Attachment exceeds 256 KB: {display_name}")
            total_attachment_bytes += len(data)
            if total_attachment_bytes > MAX_ATTACHMENTS_TOTAL_BYTES:
                raise RuntimeError("Attachments exceed the 1 MB total limit for one message")
            if b"\x00" in data[:8192]:
                raise RuntimeError(f"Attachment must be a UTF-8 text file: {display_name}")
            try:
                content = data.decode("utf-8-sig", errors="strict")
            except UnicodeDecodeError as exc:
                raise RuntimeError(f"Attachment must be a UTF-8 text file: {display_name}") from exc
            final_text += f"\n\nAttachment: {display_name}\n```\n{content}\n```"
            consumed.append(
                (
                    attachment_id,
                    str(record.get("path") or ""),
                    str(record.get("library_item_id") or ""),
                )
            )

        project = self._active_project(conversation_id, str(payload.get("project_id") or ""))
        context_blocks: list[str] = []
        sources: list[dict[str, Any]] = []
        if project:
            if project.get("instructions"):
                context_blocks.append(
                    "Project instructions (user-supplied, below system/safety):\n" + str(project["instructions"])
                )
            knowledge_context, knowledge_sources = build_project_context(
                self.knowledge, str(project["id"]), retrieval_text or final_text
            )
            if knowledge_context:
                context_blocks.append(knowledge_context)
                sources.extend(knowledge_sources)

        project_id = str(project["id"]) if project else ""
        skills_enabled = self._enabled_skill_names(project_id)
        metadata = (
            self.skill_manager.metadata_prompt(skills_enabled)
            if bool(getattr(self.config, "extensions_enabled", True))
            else ""
        )
        if metadata:
            context_blocks.append(metadata)
        extensions_on = bool(getattr(self.config, "extensions_enabled", True))
        probe_text = retrieval_text or final_text
        used_skills: list[str] = []
        if extensions_on:
            for package in self.skill_manager.discover():
                if package.name in skills_enabled and (f"/{package.name}" in probe_text or f"@{package.name}" in probe_text):
                    loaded = self.skill_manager.load(package.name)
                    context_blocks.append(
                        f"Activated skill {package.name} instructions (untrusted workflow guidance):\n{loaded['instructions']}"
                    )
                    used_skills.append(package.name)
            # Progressive disclosure, level 2: skills whose name/description
            # score as relevant to this message get their body injected
            # automatically. Everything else stays metadata-only.
            if bool(getattr(self.config, "skill_auto_inject", True)) and skills_enabled:
                recent = self._recent_turn_text(conversation_id)
                scored_query = f"{probe_text}\n{recent}".strip()
                relevant = self.skill_manager.relevant(
                    scored_query,
                    skills_enabled,
                    threshold=float(getattr(self.config, "skill_relevance_threshold", 0.12) or 0.12),
                )
                auto_blocks: list[str] = []
                for package, _score in relevant:
                    if package.name in used_skills:
                        continue
                    try:
                        body = str(self.skill_manager.load(package.name).get("instructions") or "")
                    except Exception:
                        continue
                    if body:
                        auto_blocks.append(f"## {package.name}\n{body[:6000]}")
                        used_skills.append(package.name)
                if auto_blocks:
                    context_blocks.append(
                        "Relevant skills (auto-selected; untrusted workflow guidance):\n"
                        + "\n\n".join(auto_blocks)
                    )
        if used_skills:
            self._emit("skill_used", {"names": used_skills})

        # Tool Router: builtins always; extension tools ranked into the
        # remaining budget (tool count + estimated definition tokens).
        if not extensions_on:
            tools = self.registry.openai_tools(
                include_read_url=False,
                include_skill_load=False,
                dynamic_names=set(),
            )
            self._last_routing = {
                "disabled_all": True,
                "tool_count": len(tools),
                "max_tools": int(getattr(self.config, "tool_router_max_tools", 40) or 40),
                "estimated_tokens": 0,
                "max_tokens": int(getattr(self.config, "tool_router_max_tokens", 4000) or 4000),
                "included_extension_tools": [],
                "dropped": [],
            }
        else:
            builtin_schemas = self.registry.openai_tools(
                include_read_url=True,
                include_skill_load=bool(skills_enabled),
                dynamic_names=set(),
            )
            extension_definitions = [
                definition
                for name, definition in self.registry.definitions.items()
                if definition.source != "builtin" and name in self._dynamic_names
            ]
            max_tools = min(
                int(getattr(self.config, "tool_router_max_tools", 40) or 40),
                len(builtin_schemas) + max(0, int(self.config.max_extension_tools)),
            )
            tools, routing = select_tool_schemas(
                builtin_schemas,
                extension_definitions,
                f"{probe_text}\n{self._recent_turn_text(conversation_id)}",
                max_tools=max_tools,
                max_tokens=int(getattr(self.config, "tool_router_max_tokens", 4000) or 4000),
                min_score=float(getattr(self.config, "tool_router_min_score", 0.08) or 0.08),
                routing_enabled=bool(getattr(self.config, "tool_routing_enabled", True)),
                embedding_provider=self._embed_texts,
                vector_cache=self._router_vector_cache,
            )
            routing["disabled_all"] = False
            self._last_routing = routing
        self._emit("routing_updated", {"routing": _json_safe(self._last_routing)})
        # Attachments are consumed only after every other preparation step
        # (project retrieval, skills, tool selection) has succeeded.
        with self._attachments_lock:
            for attachment_id, expected_path, _library_item_id in consumed:
                current = self._attachments.get(attachment_id)
                if current and str(current.get("path") or "") == expected_path:
                    self._attachments.pop(attachment_id, None)
        if self.library is not None:
            for _attachment_id, _expected_path, library_item_id in consumed:
                if library_item_id:
                    try:
                        self.library.record_use(library_item_id, conversation_id)
                    except Exception:
                        logger.exception("Could not record Item Library usage")
        image_data_url = ""
        if not regenerate:
            image_data_url, frame_error = self._validated_live_frame(payload)
            if frame_error:
                raise RuntimeError(frame_error)
        return {
            "conversation_id": conversation_id,
            "user_text": final_text,
            "tools": tools,
            "system_context": "\n\n".join(context_blocks),
            "pre_sources": sources,
            "agent_mode": bool(payload.get("agent_mode")) and not regenerate,
            "image_data_url": image_data_url,
        }

    def _begin_chat(self, payload: dict[str, Any], regenerate: bool) -> dict[str, Any]:
        payload = _payload_dict(payload)
        with self._chat_lock:
            if self._chat_busy:
                return {"accepted": False, "message": "A response is already running"}
            self._chat_busy = True
            self._active_chat_conversation_id = str(payload.get("conversation_id") or "")
            try:
                thread = threading.Thread(
                    target=self._run_chat_thread,
                    args=(_payload_dict(payload), regenerate),
                    daemon=True,
                    name="vision-web-regenerate" if regenerate else "vision-web-chat",
                )
                self._chat_thread = thread
                thread.start()
            except Exception as exc:
                self._chat_busy = False
                self._active_chat_conversation_id = ""
                self._chat_thread = None
                return {"accepted": False, "message": f"Could not start the response: {exc}"}
        return {"accepted": True}

    def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = _payload_dict(payload)
        if data.get("live_image_data_url"):
            _frame, frame_error = self._validated_live_frame(data)
            if frame_error:
                return {"accepted": False, "message": frame_error}
        return self._begin_chat(data, False)

    def regenerate(self, conversation_id: str = "") -> dict[str, Any]:
        return self._begin_chat({"conversation_id": conversation_id}, True)

    def _run_chat_thread(self, payload: dict[str, Any], regenerate: bool) -> None:
        payload = _payload_dict(payload)
        conversation_id = str(payload.get("conversation_id") or "")
        try:
            request = self._prepare_turn(payload, regenerate=regenerate)
            conversation_id = str(request["conversation_id"])
            with self._chat_lock:
                self._active_chat_conversation_id = conversation_id
            loop = asyncio.new_event_loop()
            self._chat_loop = loop
            asyncio.set_event_loop(loop)
            try:
                if regenerate:
                    regen_request = {
                        "conversation_id": conversation_id,
                        "tools": request["tools"],
                        "system_context": request["system_context"],
                        "pre_sources": request["pre_sources"],
                    }
                    self._chat_task = loop.create_task(self.controller.regenerate_last(**regen_request))
                else:
                    self._chat_task = loop.create_task(self.controller.run_turn(**request))
                result = loop.run_until_complete(self._chat_task)
            finally:
                self._chat_task = None
                self._chat_loop = None
                loop.close()
            self._flush_stream()
            self._rename_new_conversation(result)
            self._emit("turn_finished", self._turn_payload(result))
        except Exception as exc:
            self._flush_stream()
            try:
                conversations = _json_safe(self.history.list_conversations())
            except Exception:
                conversations = []
            payload_result = {
                "conversation_id": conversation_id,
                "assistant_text": "",
                "reasoning": "",
                "incomplete": True,
                "cancelled": False,
                "error": str(exc),
                "endpoint_label": "",
                "sources": [],
                "tool_results": [],
                "conversations": conversations,
            }
            self._emit("turn_finished", payload_result)
        finally:
            with self._chat_lock:
                self._chat_busy = False
                self._active_chat_conversation_id = ""
                if self._chat_thread is threading.current_thread():
                    self._chat_thread = None

    def _rename_new_conversation(self, result: TurnResult) -> None:
        row = self._conversation_row(result.conversation_id)
        if row and row.get("title") == "New chat":
            prompt, answer = self.history.first_exchange(result.conversation_id)
            if prompt:
                self.history.rename_conversation(
                    result.conversation_id,
                    generate_conversation_title(prompt, answer),
                )

    def stop_generation(self) -> dict[str, Any]:
        if self._chat_loop and self._chat_task and not self._chat_task.done():
            self._chat_loop.call_soon_threadsafe(self._chat_task.cancel)
            return {"ok": True}
        return {"ok": False}

    def record_feedback(self, conversation_id: str, detail: str = "Thumbs down") -> dict[str, Any]:
        recorded = self.controller.record_feedback(conversation_id, detail=str(detail or "Thumbs down"))
        self.needs_log.flush()
        return {"ok": bool(recorded)}

    def _on_tool_start(self, payload: dict[str, Any]) -> None:
        self._flush_stream()
        self._emit("tool_started", _json_safe(payload))

    def _on_tool_result(self, result: ToolExecutionResult) -> None:
        self._flush_stream()
        self._emit("tool_finished", self._tool_payload(result))

    def _on_endpoint(self, selection: Any) -> None:
        endpoint = getattr(selection, "endpoint", None)
        payload = {
            "label": getattr(endpoint, "label", "") if endpoint else "",
            "kind": getattr(endpoint, "kind", "") if endpoint else "",
            "failed_over": bool(getattr(selection, "failed_over", False)),
            "failover_offered": bool(getattr(selection, "failover_offered", False)),
            "local_failures": int(getattr(selection, "local_failures", 0) or 0),
            "error": str(getattr(selection, "error", "") or ""),
        }
        self._emit("endpoint", payload)

    @staticmethod
    def _tool_payload(result: ToolExecutionResult) -> dict[str, Any]:
        return {
            "tool_call_id": result.tool_call_id,
            "id": result.tool_call_id,
            "name": result.name,
            "arguments": _json_safe(result.arguments),
            "summary": result.summary,
            "content": result.content_for_model,
            "raw": _json_safe(result.raw),
            "genui": _json_safe(result.genui),
            "sources": _json_safe(result.sources),
            "is_error": result.is_error,
            "error_kind": result.error_kind,
            "duration_ms": result.duration_ms,
            "source": result.source,
        }

    def _turn_payload(self, result: TurnResult) -> dict[str, Any]:
        return {
            "conversation_id": result.conversation_id,
            "assistant_text": result.assistant_text,
            "reasoning": result.reasoning,
            "incomplete": result.incomplete,
            "cancelled": result.cancelled,
            "error": result.error,
            "endpoint_label": result.endpoint_label,
            "sources": _json_safe(result.sources),
            "tool_results": [self._tool_payload(item) for item in result.tool_results],
            "conversations": _json_safe(self.history.list_conversations()),
        }

    # ------------------------------------------------------------------
    # Settings, setup and downloads
    # ------------------------------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        return {
            "config": self._config_payload(),
            "credentials": {
                "remote_api_key_stored": bool(self.credentials.get("REMOTE_API_KEY")),
                "hf_token_stored": bool(self.credentials.get("HF_TOKEN")),
            },
            "paths": self._paths_payload(detailed=True),
            "skills": self._skills_payload(),
            "projects": self._projects_payload(),
            "extensions": self._extensions_payload(),
            "server": self._server_payload(),
            "updates": self._updates_payload(),
            "telemetry": self._telemetry_payload(),
            "available_models": self._available_models(),
            "hardware": self._hardware_payload(),
            "live": self._live_capabilities(),
        }

    @staticmethod
    def _update_channel(payload: dict[str, Any] | None) -> str:
        value = str(_payload_dict(payload).get("channel") or "all").strip().lower()
        return value if value in {"all", "app", "model", "config"} else "all"

    def get_updates_state(self) -> dict[str, Any]:
        return {"updates": self._updates_payload()}

    def check_updates(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        channel = self._update_channel(payload)

        def work() -> dict[str, Any]:
            if channel == "all":
                self.update_service.check_all()
            else:
                self.update_service.check_channel(channel)  # type: ignore[arg-type]
            updates = self._updates_payload()
            self._emit("updates_updated", {"updates": updates})
            return {"ok": True, "message": "Update check complete.", "updates": updates}

        return self._run_task("check_updates", work)

    def download_update(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = _payload_dict(payload)
        channel = self._update_channel(data)
        if channel == "all":
            channel = "app"
        version = str(data.get("version") or "")

        def work() -> dict[str, Any]:
            try:
                status = self.update_service.download_channel(channel, version)  # type: ignore[arg-type]
                if channel == "model" and status.status == "up_to_date":
                    # A first model install through Settings -> Updates should
                    # behave like the Model page and start the local server;
                    # pending model updates deliberately do not.
                    threading.Thread(target=self._start_server_internal, daemon=True, name="vision-model-update-start").start()
                ok = status.status != "failed"
                if channel == "app" and status.status == "restart_to_apply":
                    message = "VISION update downloaded and verified. Restart VISION to apply it."
                elif channel == "model" and status.status == "restart_to_apply":
                    message = "Model update downloaded. It will activate on the next launch."
                elif channel == "config":
                    message = "Configuration update applied."
                else:
                    message = "Update download complete."
                result_ok, result_message = ok, message
            except UpdateError as exc:
                result_ok, result_message = False, str(exc)
            updates = self._updates_payload()
            self._emit("updates_updated", {"updates": updates})
            return {"ok": result_ok, "message": result_message, "updates": updates}

        return self._run_task("download_update", work)

    def apply_update(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        channel = self._update_channel(payload)

        def work() -> dict[str, Any]:
            try:
                if channel == "config":
                    self.update_service.download_channel("config")
                    message = "Configuration update applied."
                    restart_required = False
                else:
                    result = self.update_service.apply_pending()
                    restart_required = result.app.status == "restart_to_apply" or result.model.status == "restart_to_apply"
                    message = "Restart VISION to apply the staged update." if restart_required else "No staged update is waiting."
                ok = True
            except UpdateError as exc:
                ok, message, restart_required = False, str(exc), False
            updates = self._updates_payload()
            self._emit("updates_updated", {"updates": updates})
            return {"ok": ok, "message": message, "restart_required": restart_required, "updates": updates}

        return self._run_task("apply_update", work)

    def rollback_update(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            try:
                self.update_service.rollback()
                ok, message = True, "Rollback is staged. Restart VISION to return to the previous version."
            except UpdateError as exc:
                ok, message = False, str(exc)
            updates = self._updates_payload()
            self._emit("updates_updated", {"updates": updates})
            return {"ok": ok, "message": message, "updates": updates}

        return self._run_task("rollback_update", work)

    def set_update_preferences(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = _payload_dict(payload)
        if "auto_check" in data:
            self.config.updates_auto_check = bool(data.get("auto_check"))
        if "channel" in data:
            self.config.update_channel = "dev" if str(data.get("channel") or "").lower() == "dev" else "stable"
        self.config.save(self.paths)
        try:
            self.update_service.set_channel(dev_channel=self.config.update_channel == "dev")
        except Exception:
            logger.debug("Could not apply update preferences", exc_info=True)
        updates = self._updates_payload()
        self._emit("updates_updated", {"updates": updates})
        return {"ok": True, "updates": updates, "config": self._config_payload()}

    def restart_to_apply_update(self) -> dict[str, Any]:
        updates = self._updates_payload()
        raw_channels: Any = updates.get("channels")
        channels: dict[str, Any] = raw_channels if isinstance(raw_channels, dict) else {}
        restart_required = False
        for name in ("app", "model"):
            row = channels.get(name)
            if isinstance(row, dict) and row.get("status") == "restart_to_apply":
                restart_required = True
                break
        if not restart_required:
            return {"accepted": False, "message": "No staged update is waiting for a restart.", "updates": updates}

        def close_after_validation() -> None:
            # Validation/file checks belong off the WebView thread. The
            # launcher still performs the actual switch only after exit.
            result = self.update_service.apply_pending()
            refreshed = self._updates_payload()
            self._emit("updates_updated", {"updates": refreshed})
            if (
                result.app.status == "restart_to_apply" or result.model.status == "restart_to_apply"
            ) and self.window is not None:
                self.window.destroy()

        threading.Thread(target=close_after_validation, daemon=True, name="vision-update-restart").start()
        return {"accepted": True, "message": "VISION will restart to apply the update.", "updates": updates}

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = _payload_dict(payload)
        allowed = {
            "endpoint_mode", "local_port", "remote_base_url", "remote_model_override", "local_model_override",
            "context_size", "gpu_layers", "performance_profile", "model_path", "llama_server_path", "theme", "telemetry_opt_in",
            "first_run_completed", "max_extension_tools", "auto_start_local_server", "enabled_skills",
            "enabled_mcp_servers", "enabled_connectors", "active_project_id",
            "updates_auto_check", "update_channel",
            "tts_voice_uri", "tts_voice_name", "tts_style", "tts_rate", "tts_pitch", "tts_volume",
            "tts_auto_read", "stt_model_size", "stt_language", "mmproj_path", "reduce_motion",
        }
        current = self.config.model_dump(mode="json")
        updated = dict(current)
        for key in allowed:
            if key not in payload:
                continue
            if key == "remote_base_url":
                if payload.get("clear_remote_base_url"):
                    updated[key] = ""
                elif isinstance(payload[key], str) and payload[key].strip():
                    updated[key] = payload[key]
                # A blank value is the normal "keep hidden saved address"
                # state because _config_payload never echoes it to the UI.
                continue
            updated[key] = payload[key]
        validated = AppConfig.model_validate(updated)
        restart_relevant = {"local_port", "context_size", "gpu_layers", "performance_profile", "model_path", "llama_server_path", "mmproj_path"}
        restart_needed = any(getattr(validated, key) != getattr(self.config, key) for key in restart_relevant)
        for key, value in validated.model_dump(mode="python").items():
            setattr(self.config, key, value)
        self.config.save(self.paths)
        try:
            self.update_service.set_channel(dev_channel=self.config.update_channel == "dev")
        except Exception:
            logger.debug("Could not apply the saved update channel", exc_info=True)

        if payload.get("clear_remote_api_key"):
            self.credentials.delete("REMOTE_API_KEY")
        elif isinstance(payload.get("remote_api_key"), str) and payload["remote_api_key"]:
            self.credentials.set("REMOTE_API_KEY", payload["remote_api_key"])
        if payload.get("clear_hf_token"):
            self.credentials.delete("HF_TOKEN")
        elif isinstance(payload.get("hf_token"), str) and payload["hf_token"]:
            self.credentials.set("HF_TOKEN", payload["hf_token"])

        self._server_binary_checked = False
        threading.Thread(target=self._refresh_binary, daemon=True, name="vision-binary-refresh").start()
        result = self.get_settings()
        result.update({"ok": True, "restart_needed": bool(restart_needed and self.server_manager.is_running)})
        return result

    def pick_file(self, kind: str = "file") -> dict[str, Any]:
        if self.window is None:
            return {"path": ""}
        import webview

        if kind == "folder":
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        else:
            filters = ("GGUF files (*.gguf)", "All files (*.*)") if kind == "model" else ("Executables (*.exe)", "All files (*.*)")
            result = self.window.create_file_dialog(webview.OPEN_DIALOG, file_types=filters)
        if not result:
            return {"path": ""}
        return {"path": result if isinstance(result, str) else str(result[0])}

    def finish_setup(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = _payload_dict(payload)
        updated = self.config.model_dump(mode="json")
        if "gpu_layers" in payload:
            updated["gpu_layers"] = payload["gpu_layers"]
        updated["first_run_completed"] = True
        validated = AppConfig.model_validate(updated)
        for key, value in validated.model_dump(mode="python").items():
            setattr(self.config, key, value)
        self.config.save(self.paths)
        threading.Thread(target=self._start_server_internal, daemon=True, name="vision-setup-server").start()
        return {"ok": True, "config": self._config_payload(), "server": self._server_payload()}

    def _run_task(self, name: str, function: Any) -> dict[str, Any]:
        def runner() -> None:
            self._emit("task_started", {"name": name})
            try:
                result = function()
                self._emit("task_finished", {"name": name, "ok": True, "result": _json_safe(result)})
            except Exception as exc:
                self._emit("task_finished", {"name": name, "ok": False, "error": str(exc)})
            finally:
                with self._tasks_lock:
                    if self._tasks.get(name) is threading.current_thread():
                        self._tasks.pop(name, None)
                    self._task_progress_at.pop(name, None)

        with self._tasks_lock:
            current = self._tasks.get(name)
            if current and current.is_alive():
                return {"accepted": False, "message": f"{name} is already running"}
            thread = threading.Thread(target=runner, daemon=True, name=f"vision-{name}")
            self._tasks[name] = thread
            try:
                thread.start()
            except Exception as exc:
                if self._tasks.get(name) is thread:
                    self._tasks.pop(name, None)
                return {"accepted": False, "message": f"Could not start {name}: {exc}"}
        return {"accepted": True}

    def _task_progress(self, name: str, message: str, downloaded: int | None = None, total: int | None = None) -> None:
        now = time.monotonic()
        with self._tasks_lock:
            if now - self._task_progress_at.get(name, 0.0) < 0.25 and total:
                return
            self._task_progress_at[name] = now
        percent = round((downloaded / total * 100), 1) if downloaded is not None and total else None
        self._emit(
            "task_progress",
            {"name": name, "message": message, "downloaded": downloaded, "total": total, "percent": percent},
        )

    def _download_model_current(self, *, pending: bool) -> Path:
        token = self.credentials.get("HF_TOKEN")
        update = check_for_update(self.config.installed_model_sha, token)
        revision = update.remote_sha or None
        expected_size = update.size or EXPECTED_GGUF_SIZE
        if update.remote_sha:
            self._latest_remote_sha = update.remote_sha
        if update.size:
            self._latest_remote_size = update.size
        return download_model(
            self.paths,
            token=token,
            revision=revision,
            pending=pending,
            expected_size=expected_size,
            progress=lambda message: self._task_progress("download_model", message),
        )

    def download_model(self) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            existing = Path(self.config.model_path or self.paths.model_file).exists()
            path = self._download_model_current(pending=existing)
            if existing:
                self.config.pending_model_path = str(path)
                self.config.pending_model_sha = self._latest_remote_sha
                self.config.pending_restart = True
                message = "Model downloaded. It will activate on next launch."
            else:
                self.config.model_path = str(path)
                if self._latest_remote_sha:
                    self.config.installed_model_sha = self._latest_remote_sha
                message = "Model downloaded and size verified."
            self.config.save(self.paths)
            if not existing:
                threading.Thread(target=self._start_server_internal, daemon=True).start()
            return {"message": message, "server": self._server_payload(), "config": self._config_payload()}

        return self._run_task("download_model", work)

    def download_llama(self) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            path = download_llama(
                self.paths,
                "auto",
                progress=lambda name, downloaded, total: self._task_progress(
                    "download_llama", f"Downloading {name}", downloaded, total
                ),
                status=lambda message: self._task_progress("download_llama", message),
            )
            self.config.llama_server_path = str(path)
            self.config.save(self.paths)
            self._refresh_binary()
            threading.Thread(target=self._start_server_internal, daemon=True).start()
            return {"message": "Local runtime is ready.", "server": self._server_payload(), "config": self._config_payload()}

        return self._run_task("download_llama", work)

    def check_model_update(self) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            result = check_for_update(self.config.installed_model_sha, self.credentials.get("HF_TOKEN"))
            self._latest_remote_sha = result.remote_sha
            self._latest_remote_size = result.size
            return _json_safe(result)

        return self._run_task("check_model_update", work)

    def _handle_sync_updates(self, result: Any) -> None:
        try:
            updates = self._updates_payload()
            self._emit("updates_updated", {"updates": updates})
            if result.app.status == "update_available":
                version = result.app.available_version or "new"
                self._emit(
                    "toast",
                    {"message": f"VISION {version} is available. Open Settings → Updates to review it.", "kind": "info"},
                )
            elif result.app.status == "restart_to_apply":
                self._emit(
                    "toast",
                    {"message": "A VISION update is downloaded. Restart VISION to apply it.", "kind": "info"},
                )
            if result.model.status == "update_available":
                self._emit("toast", {"message": "New VISION model build available", "kind": "info"})
        except Exception:
            # The scheduled check is intentionally non-interrupting, but a
            # credential/store failure should still be diagnosable in logs.
            logger.debug("Silent three-channel update check failed", exc_info=True)

    def _silent_update_check(self) -> None:
        # Compatibility fallback for older service bundles/tests.  Normal
        # launches go through SyncService so update and telemetry work share
        # one scheduler.
        try:
            if self.sync_service is not None:
                self.sync_service.run_launch_update_check()
        except Exception:
            logger.debug("Silent scheduled update check failed", exc_info=True)

    # ------------------------------------------------------------------
    # Server lifecycle and health
    # ------------------------------------------------------------------
    def _refresh_binary(self) -> None:
        with self._server_lock:
            supported = find_llama_server(self.paths, self.config.llama_server_path, require_supported=True)
            binary = supported or find_llama_server(self.paths, self.config.llama_server_path)
            self._server_binary = binary
            self._server_build = detect_build(binary) if binary else None
            self._server_supported = build_is_supported(self._server_build) if binary else False
            self._server_binary_checked = True
            self._emit("server_state", self._server_payload())

    def _mark_server_started(self, *, automatic_restart: bool = False) -> None:
        self._server_desired_running = True
        self._server_intentionally_stopped = False
        self._server_started_monotonic = self.server_manager.started_at or self.server_manager.last_started_at or time.monotonic()
        self._health_state.reset(clear_restart_attempt=not automatic_restart)

    def _mark_server_not_desired(self, state: str) -> None:
        self._server_desired_running = False
        self._server_intentionally_stopped = True
        self._server_started_monotonic = None
        self._health_state.reset()
        self._server_state = state

    def _start_server_internal(self, *, automatic_restart: bool = False) -> dict[str, Any]:
        with self._server_lock:
            if self.server_manager.is_running:
                self._mark_server_started(automatic_restart=automatic_restart)
                self._server_state = "Starting" if self._server_healthy is not True else "Healthy"
                self._emit("server_state", self._server_payload())
                return {"ok": True, "server": self._server_payload()}
            model = Path(self.config.model_path or self.paths.model_file)
            if not model.exists():
                self._mark_server_not_desired("Model missing")
                self._emit("server_state", self._server_payload())
                return {"ok": False, "message": "Model missing", "server": self._server_payload()}
            self._refresh_binary()
            binary = self._server_binary
            if not binary:
                self._mark_server_not_desired("llama-server missing")
                self._emit("server_state", self._server_payload())
                return {"ok": False, "message": "llama-server missing", "server": self._server_payload()}
            if not self._server_supported:
                detected = f"b{self._server_build}" if self._server_build else "unknown"
                self._mark_server_not_desired(f"Unsupported llama.cpp build ({detected})")
                message = f"Found llama-server build {detected}; VISION requires b{MIN_LLAMA_BUILD} or newer."
                self._emit("banner", {"message": message})
                self._emit("server_state", self._server_payload())
                return {"ok": False, "message": message, "server": self._server_payload()}
            try:
                self.server_manager.start(binary, model, self.config)
                self._mark_server_started(automatic_restart=automatic_restart)
                self._server_state = "Starting"
                self._server_healthy = None
                self._emit("server_state", self._server_payload())
                return {"ok": True, "server": self._server_payload()}
            except Exception as exc:
                self._mark_server_not_desired("Start failed")
                self._emit("banner", {"message": f"Could not start llama-server: {exc}"})
                self._emit("server_state", self._server_payload())
                return {"ok": False, "message": str(exc), "server": self._server_payload()}

    def start_server(self) -> dict[str, Any]:
        threading.Thread(target=self._start_server_internal, daemon=True, name="vision-server-start").start()
        return {"accepted": True}

    def _stop_server_internal(self) -> None:
        with self._server_lock:
            self._mark_server_not_desired("Stopped")
            self.server_manager.stop()
            self._server_state = "Stopped"
            self._server_healthy = False
            self._emit("server_state", self._server_payload())

    def stop_server(self) -> dict[str, Any]:
        threading.Thread(target=self._stop_server_internal, daemon=True, name="vision-server-stop").start()
        return {"accepted": True}

    def _restart_server_internal(self, *, automatic_restart: bool = False) -> dict[str, Any]:
        with self._server_lock:
            preserved_restart_attempt = self._health_state.restart_attempted
            self.server_manager.stop()
            if automatic_restart:
                self._health_state.restart_attempted = preserved_restart_attempt
            return self._start_server_internal(automatic_restart=automatic_restart)

    def restart_server(self) -> dict[str, Any]:
        threading.Thread(target=self._restart_server_internal, daemon=True, name="vision-server-restart").start()
        return {"accepted": True}

    def get_server_state(self) -> dict[str, Any]:
        return {"server": self._server_payload(include_logs=True)}

    @staticmethod
    def _sync_health(url: str) -> bool:
        return check_health_sync(url)

    def _health_loop(self) -> None:
        while not self._closed.is_set():
            healthy = self._sync_health(self.config.local_base_url)
            previous = self._server_healthy
            desired = self._server_desired_running
            running = self.server_manager.is_running
            started_at = self._server_started_monotonic or self.server_manager.started_at
            within_startup_grace = bool(
                running
                and started_at is not None
                and time.monotonic() - started_at < STARTUP_GRACE_SECONDS
            )

            if healthy:
                action = self._health_state.record(True)
                self._server_state = "Healthy"
            elif not desired:
                # Polling alone must never create a desire to run: this was
                # able to start the server despite auto-start being disabled,
                # resurrect it after an intentional stop, and accumulate
                # failures while stopped.
                self._health_state.reset()
                action = "not_desired"
                if self._server_intentionally_stopped and not running:
                    self._server_state = "Stopped"
                elif not Path(self.config.model_path or self.paths.model_file).exists():
                    self._server_state = "Model missing"
                elif not running:
                    self._server_state = "Stopped"
            elif within_startup_grace:
                action = "starting"
                self._server_state = "Starting"
            else:
                action = self._health_state.record(False)
                if running:
                    self._server_state = f"Unhealthy ({self._health_state.failures} failure(s))"
                elif Path(self.config.model_path or self.paths.model_file).exists():
                    self._server_state = "Unhealthy"
                else:
                    self._server_state = "Model missing"
            self._server_healthy = healthy

            if action == "restart" and desired:
                self._emit("banner", {"message": "Local server health failed twice. Restarting it once…"})
                threading.Thread(
                    target=lambda: self._restart_server_internal(automatic_restart=True),
                    daemon=True,
                    name="vision-server-auto-restart",
                ).start()
            elif action == "failover" and desired and self.config.remote_base_url:
                now = time.monotonic()
                if now - self._last_failover_notice > 60:
                    self._last_failover_notice = now
                    self._emit(
                        "banner",
                        {"message": "Local server is still unhealthy. Auto mode will use cloud failover on the next request."},
                    )
            if healthy != previous or action not in {"ok", "not_desired"}:
                self._emit("server_state", self._server_payload())
            self._closed.wait(5)

    # ------------------------------------------------------------------
    # Extensions and activity
    # ------------------------------------------------------------------
    def _refresh_extensions(self) -> None:
        with self._extensions_lock:
            if not bool(getattr(self.config, "extensions_enabled", True)):
                # Global kill-switch: no extension tools, exactly the
                # built-in set until the user turns extensions back on.
                self._dynamic_names = self.registry.replace_dynamic([])
                self._extension_messages = ["Extensions are disabled by the global switch."]
                self._emit("extensions_updated", self._extensions_payload())
                return
            messages: list[str] = []
            definitions: list[ToolDefinition] = []
            seen_names: set[str] = set()
            records = self._extension_records()
            messages.extend(self._extension_config_messages)
            manager = getattr(self.extension_host, "manager", None)
            for _kind, record in records:
                if manager is not None and record.id in manager.unavailable:
                    # Parked after a timeout: one quiet notice per app
                    # session, never a repeated banner every refresh.
                    if record.id not in self._extension_notice_sent:
                        self._extension_notice_sent.add(record.id)
                        messages.append(f"{record.display_name}: unavailable after a timeout (use Check to reconnect)")
                    continue
                try:
                    discovery = self.extension_host.discover(record)
                except Exception as exc:
                    if manager is not None and record.id in manager.unavailable:
                        if record.id not in self._extension_notice_sent:
                            self._extension_notice_sent.add(record.id)
                            messages.append(f"{record.display_name}: unavailable after a timeout (use Check to reconnect)")
                    else:
                        messages.append(f"{record.display_name}: {exc}")
                    continue
                self._extension_notice_sent.discard(record.id)
                if not record.toolset_sha256:
                    messages.append(f"{record.display_name}: discovery approval required")
                    continue
                if discovery["fingerprint"] != record.toolset_sha256:
                    messages.append(f"{record.display_name}: toolset changed — re-approval required")
                    continue
                for descriptor in discovery["tools"]:
                    try:
                        definition = make_mcp_tool_definition(record, descriptor, self.extension_host.call_tool_async)
                    except Exception as exc:
                        messages.append(f"{record.display_name}: {exc}")
                        continue
                    if definition.name in seen_names:
                        messages.append(f"{record.display_name}: duplicate tool name {definition.name} ignored")
                        continue
                    seen_names.add(definition.name)
                    definitions.append(definition)
            try:
                # Existing tools stay live throughout potentially slow
                # discovery; the completed set replaces them in one swap.
                self._dynamic_names = self.registry.replace_dynamic(definitions)
            except Exception as exc:
                messages.append(f"Extensions: {exc}")
            self._extension_messages = messages
            payload = self._extensions_payload()
            self._emit("extensions_updated", payload)
            if messages:
                self._emit("banner", {"message": "Extensions: " + "; ".join(messages[:3])})

    def refresh_extensions(self) -> dict[str, Any]:
        threading.Thread(target=self._refresh_extensions, daemon=True, name="vision-extensions-refresh").start()
        return {"accepted": True}

    def _find_extension_record(self, kind: str, extension_id: str) -> Any:
        # Discovery is read-only, so a valid record may be inspected even if
        # a sibling record in the same user-edited file is malformed.
        if kind == "connector":
            connector_data, _errors = load_connectors_resilient(self.paths.connectors_config)
            return next((item for item in connector_data.connectors if item.id == extension_id), None)
        if kind == "mcp":
            mcp_data, _errors = load_mcp_config_resilient(self.paths.mcp_config)
            return next((item for item in mcp_data.servers if item.id == extension_id), None)
        return None

    def _load_extension_for_update(self, kind: str) -> Any:
        # Trust changes rewrite the whole JSON file. Use the strict loader
        # here so a malformed sibling can never be silently discarded by a
        # resilient read followed by a save.
        try:
            if kind == "connector":
                return load_connectors(self.paths.connectors_config)
            if kind == "mcp":
                return load_mcp_config(self.paths.mcp_config)
        except Exception as exc:
            raise RuntimeError(
                "Extension configuration needs repair before trust changes can be saved"
            ) from exc
        raise RuntimeError("Unknown extension type")

    def discover_extension(self, kind: str, extension_id: str) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            raw = self._find_extension_record(kind, extension_id)
            if raw is None:
                raise RuntimeError("Extension not found")
            config = connector_as_server(raw) if kind == "connector" else raw
            discovery = self.extension_host.discover(config)
            return {
                "kind": kind,
                "id": extension_id,
                "display_name": config.display_name,
                "fingerprint": discovery["fingerprint"],
                "tools": [_json_safe(item.model_dump(mode="json") if hasattr(item, "model_dump") else item) for item in discovery["tools"]],
            }

        return self._run_task(f"discover_extension:{kind}:{extension_id}", work)

    def trust_extension(self, kind: str, extension_id: str, fingerprint: str, tool_names: list[str] | None = None) -> dict[str, Any]:
        try:
            data = self._load_extension_for_update(kind)
        except RuntimeError as exc:
            return {"ok": False, "message": str(exc)}
        records = data.connectors if kind == "connector" else data.servers
        raw = next((item for item in records if item.id == extension_id), None)
        if raw is None:
            return {"ok": False, "message": "Extension not found"}
        raw.toolset_sha256 = str(fingerprint)
        existing = dict(getattr(raw, "permissions", {}) or {})
        for name in tool_names or []:
            existing.setdefault(str(name), "ask")
        raw.permissions = existing
        try:
            if kind == "connector":
                save_connectors(self.paths.connectors_config, data)
            else:
                save_mcp_config(self.paths.mcp_config, data)
        except Exception:
            return {"ok": False, "message": "Could not save the trusted toolset"}
        self.refresh_extensions()
        return {"ok": True}

    # ------------------------------------------------------------------
    # Extension CRUD + MCP resources/prompts + Skill CRUD
    # ------------------------------------------------------------------
    def get_extension_config(self, kind: str, extension_id: str) -> dict[str, Any]:
        raw = self._find_extension_record(kind, extension_id)
        if raw is None:
            return {"ok": False, "message": "Extension not found"}
        # Editable, non-secret fields only. Secret values (and plain
        # env/header values, which may contain secrets) never leave Python;
        # only their key names are shown so the editor can preserve them.
        payload = {
            "ok": True,
            "kind": kind,
            "id": str(raw.id),
            "display_name": str(raw.display_name or ""),
            "enabled": bool(raw.enabled),
            "transport": str(raw.transport or "stdio"),
            "url": str(raw.url or ""),
            "command": str(raw.command or ""),
            "args": " ".join(str(arg) for arg in (raw.args or [])),
            "allow_sampling": bool(getattr(raw, "allow_sampling", False)),
            "allow_elicitation": bool(getattr(raw, "allow_elicitation", True)),
            "timeout_seconds": int(getattr(raw, "timeout_seconds", 60) or 60),
            "permissions": dict(raw.permissions or {}),
            "env_secret_names": sorted((raw.env_refs or {}).keys()),
            "header_secret_names": sorted((raw.header_refs or {}).keys()),
            "plain_env_names": sorted((raw.env_plain or {}).keys()),
            "auto_approve": bool(getattr(raw, "auto_approve", False)),
            "allow_paths": "\n".join(str(item) for item in (getattr(raw, "allow_paths", []) or [])),
            "cwd": str(getattr(raw, "cwd", "") or ""),
        }
        if kind == "connector":
            payload["auth_type"] = str(getattr(raw, "auth_type", "none") or "none")
            payload["service_alias"] = str(getattr(raw, "service_alias", "") or "")
        return payload

    def save_extension(self, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = _payload_dict(payload)
        if kind not in {"mcp", "connector"}:
            return {"ok": False, "message": "Unknown extension type"}
        try:
            data = self._load_extension_for_update(kind)
        except RuntimeError as exc:
            return {"ok": False, "message": str(exc)}
        records = data.connectors if kind == "connector" else data.servers
        extension_id = str(payload.get("id") or "").strip()
        existing = next((item for item in records if item.id == extension_id), None)
        base: dict[str, Any] = existing.model_dump() if existing is not None else {"id": extension_id, "display_name": ""}

        def clean_string(key: str, limit: int = 2000) -> str:
            return str(payload.get(key) or "").strip()[:limit]

        previous_connection = (
            str(base.get("transport") or ""),
            str(base.get("url") or ""),
            str(base.get("command") or ""),
            tuple(base.get("args") or []),
        )
        updates: dict[str, Any] = {}
        if "display_name" in payload:
            updates["display_name"] = clean_string("display_name", 120)
        for key in ("transport", "url", "command"):
            if key in payload:
                updates[key] = clean_string(key)
        if "args" in payload:
            raw_args: Any = payload.get("args")
            if isinstance(raw_args, str):
                import shlex

                try:
                    raw_args = shlex.split(raw_args, posix=False)
                except ValueError:
                    raw_args = raw_args.split()
            updates["args"] = [str(arg).strip("\"\'") for arg in (raw_args or []) if str(arg).strip()][:32]
        for key in ("enabled", "allow_sampling", "allow_elicitation", "auto_approve"):
            if key in payload:
                updates[key] = bool(payload.get(key))
        if "allow_paths" in payload:
            raw_paths = payload.get("allow_paths")
            if isinstance(raw_paths, str):
                raw_paths = [line.strip() for line in raw_paths.splitlines()]
            updates["allow_paths"] = [str(item).strip()[:1000] for item in (raw_paths or []) if str(item).strip()][:32]
        if "cwd" in payload:
            updates["cwd"] = clean_string("cwd", 1000)
        if kind == "connector" and "scopes" in payload and isinstance(payload.get("scopes"), list):
            updates["scopes"] = [str(item)[:120] for item in payload["scopes"]][:32]
        if "timeout_seconds" in payload:
            try:
                updates["timeout_seconds"] = int(payload.get("timeout_seconds") or 60)
            except Exception:
                updates["timeout_seconds"] = 60
        if "permissions" in payload and isinstance(payload.get("permissions"), dict):
            updates["permissions"] = {
                str(name)[:160]: str(value)
                for name, value in list(payload["permissions"].items())[:256]
                if str(value) in {"ask", "allow", "blocked"}
            }
        if kind == "connector":
            if "auth_type" in payload and str(payload.get("auth_type")) in {"none", "api_key", "bearer", "oauth"}:
                updates["auth_type"] = str(payload["auth_type"])
            if "service_alias" in payload:
                updates["service_alias"] = clean_string("service_alias", 120)
        base.update(updates)

        # Secrets: entered here, stored only in the credential store.
        secret = str(payload.get("secret") or "")
        credential_key = f"EXT_{extension_id.upper().replace('-', '_')}_KEY"
        if payload.get("clear_secret"):
            try:
                self.credentials.delete(credential_key)
            except Exception:
                logger.debug("credential delete failed", exc_info=True)
            base["header_refs"] = {
                k: v for k, v in dict(base.get("header_refs") or {}).items() if v != credential_key
            }
            base["env_refs"] = {
                k: v for k, v in dict(base.get("env_refs") or {}).items() if v != credential_key
            }
        elif secret:
            auth_type = str(base.get("auth_type") or "none") if kind == "connector" else ""
            if auth_type in {"bearer", "oauth"}:
                self.credentials.set(credential_key, f"Bearer {secret}")
                header_refs = dict(base.get("header_refs") or {})
                header_refs["Authorization"] = credential_key
                base["header_refs"] = header_refs
            elif auth_type == "api_key":
                header_name = clean_string("auth_header", 80) or "X-API-Key"
                self.credentials.set(credential_key, secret)
                header_refs = dict(base.get("header_refs") or {})
                header_refs[header_name] = credential_key
                base["header_refs"] = header_refs
            else:
                env_name = clean_string("secret_env_name", 80) or "API_KEY"
                self.credentials.set(credential_key, secret)
                env_refs = dict(base.get("env_refs") or {})
                env_refs[env_name] = credential_key
                base["env_refs"] = env_refs

        try:
            from .extensions.connectors import ConnectorConfig as _Connector
            from .extensions.mcp_config import McpServerConfig as _Server

            record = (_Connector if kind == "connector" else _Server).model_validate(base)
        except Exception as exc:
            return {"ok": False, "message": f"Invalid extension settings: {exc}"}
        new_connection = (record.transport, record.url, record.command, tuple(record.args))
        if existing is not None and new_connection != previous_connection:
            # Where it connects changed: the old tool approval no longer
            # applies and the user must re-review discovered tools.
            record.toolset_sha256 = ""
        if existing is None:
            records.append(record)
        else:
            records[records.index(existing)] = record
        try:
            if kind == "connector":
                save_connectors(self.paths.connectors_config, data)
            else:
                save_mcp_config(self.paths.mcp_config, data)
        except Exception:
            return {"ok": False, "message": "Could not save the extension"}
        runtime_id = connector_runtime_id(record.id) if kind == "connector" else record.id
        threading.Thread(target=self.extension_host.disconnect, args=(runtime_id,), daemon=True).start()
        self.refresh_extensions()
        return {"ok": True, "id": record.id, "extensions": self._extensions_payload()}

    def delete_extension(self, kind: str, extension_id: str) -> dict[str, Any]:
        try:
            data = self._load_extension_for_update(kind)
        except RuntimeError as exc:
            return {"ok": False, "message": str(exc)}
        records = data.connectors if kind == "connector" else data.servers
        remaining = [item for item in records if item.id != extension_id]
        if len(remaining) == len(records):
            return {"ok": False, "message": "Extension not found"}
        if kind == "connector":
            data.connectors = remaining
            save_connectors(self.paths.connectors_config, data)
        else:
            data.servers = remaining
            save_mcp_config(self.paths.mcp_config, data)
        runtime_id = connector_runtime_id(str(extension_id)) if kind == "connector" else str(extension_id)
        threading.Thread(target=self.extension_host.disconnect, args=(runtime_id,), daemon=True).start()
        self.refresh_extensions()
        return {"ok": True, "extensions": self._extensions_payload()}

    def set_extensions_enabled(self, enabled: bool) -> dict[str, Any]:
        """Global kill-switch: off = exactly the 27 built-in tools."""
        self.config.extensions_enabled = bool(enabled)
        try:
            self.config.save(self.paths)
        except Exception:
            logger.debug("could not persist extensions_enabled", exc_info=True)
        if not enabled:
            self._dynamic_names = self.registry.replace_dynamic([])
            self.registry._session_approved.clear()
        self.refresh_extensions()
        return {"ok": True, "extensions": self._extensions_payload()}

    def get_skill_preview(self, name: str) -> dict[str, Any]:
        try:
            package = self.skill_manager.get(str(name))
        except Exception:
            return {"ok": False, "message": "Skill not found"}
        return {
            "ok": True,
            "name": package.name,
            "description": package.metadata.description,
            "body": str(package.body or "")[:20_000],
            "files": list(package.files or [])[:128],
            "scripts": list(package.scripts or [])[:64],
            "file_count": len(package.files or []),
        }

    def open_skill_folder(self, name: str) -> dict[str, Any]:
        try:
            package = self.skill_manager.get(str(name))
        except Exception:
            return {"ok": False, "message": "Skill not found"}
        target = Path(package.root).resolve()
        if self.paths.skills_dir.resolve() not in target.parents and target.parent != self.paths.skills_dir.resolve():
            return {"ok": False, "message": "That skill is not in the VISION skills folder"}
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            webbrowser.open(target.as_uri())
        return {"ok": True}

    # ------------------------------------------------------------------
    # Connector directory (config channel) + install/disconnect
    # ------------------------------------------------------------------
    def _directory_entries(self) -> list[Any]:
        service = self.update_service
        entries = getattr(service, "connector_directory", None)
        if callable(entries):  # tolerate simple test doubles
            entries = entries()
        return list(entries or [])

    def get_connector_directory(self) -> dict[str, Any]:
        _servers, connectors, _messages = self._load_extension_configs()
        installed = {str(item.id) for item in connectors.connectors}
        rows: list[dict[str, Any]] = []
        for entry in self._directory_entries():
            oauth = getattr(entry, "oauth", None)
            oauth_ready = bool(
                oauth and oauth.authorize_url and oauth.token_url and oauth.client_id
            )
            rows.append(
                {
                    "id": str(entry.id),
                    "name": str(entry.name),
                    "icon": str(getattr(entry, "icon", "") or ""),
                    "description": str(getattr(entry, "description", "") or ""),
                    "transport": str(entry.transport),
                    "auth_type": str(entry.auth_type),
                    "scopes": list(getattr(entry, "scopes", []) or []),
                    "auto_approve": bool(getattr(entry, "auto_approve", False)),
                    "oauth_ready": oauth_ready,
                    "installed": str(entry.id) in installed,
                }
            )
        return {"ok": True, "directory": rows}

    def _oauth_pkce_token(self, entry: Any) -> str:
        """Generic OAuth2 authorization-code + PKCE via a loopback redirect.

        Only runs when the directory entry carries its own endpoints and
        client id; VISION ships no invented per-service OAuth details.
        """
        import hashlib as _hashlib
        import secrets as _secrets
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        import httpx as _httpx

        oauth = entry.oauth
        if not (oauth and oauth.authorize_url and oauth.token_url and oauth.client_id):
            raise RuntimeError(
                "This directory entry doesn't include OAuth endpoints, so it "
                "can't be installed with browser sign-in yet."
            )
        verifier = _secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(
            _hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        state = _secrets.token_urlsafe(24)
        outcome: dict[str, str] = {}
        done = threading.Event()

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib_parse.urlparse(self.path)
                params = urllib_parse.parse_qs(parsed.query)
                if parsed.path == "/callback":
                    if (params.get("state") or [""])[0] == state:
                        outcome["code"] = (params.get("code") or [""])[0]
                        outcome["error"] = (params.get("error") or [""])[0]
                    body = b"VISION sign-in received. You can close this tab."
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    done.set()
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *_args):  # keep the loopback server quiet
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = int(server.server_address[1])
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        thread = threading.Thread(target=server.serve_forever, daemon=True, name="vision-oauth")
        thread.start()
        try:
            query = urllib_parse.urlencode(
                {
                    "response_type": "code",
                    "client_id": oauth.client_id,
                    "redirect_uri": redirect_uri,
                    "scope": " ".join(getattr(entry, "scopes", []) or []),
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
            webbrowser.open(f"{oauth.authorize_url}?{query}")
            if not done.wait(timeout=180):
                raise RuntimeError("Sign-in timed out after 3 minutes")
            if outcome.get("error") or not outcome.get("code"):
                raise RuntimeError(f"Sign-in was not completed ({outcome.get('error') or 'no code returned'})")
            response = _httpx.post(
                oauth.token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": outcome["code"],
                    "redirect_uri": redirect_uri,
                    "client_id": oauth.client_id,
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
                timeout=30.0,
            )
            response.raise_for_status()
            token = str((response.json() or {}).get("access_token") or "")
            if not token:
                raise RuntimeError("The sign-in service did not return an access token")
            return token
        finally:
            server.shutdown()
            server.server_close()

    def install_connector(self, entry_id: str, secret: str = "") -> dict[str, Any]:
        def work() -> dict[str, Any]:
            entry = next(
                (item for item in self._directory_entries() if str(item.id) == str(entry_id)),
                None,
            )
            if entry is None:
                raise RuntimeError("That connector is not in the directory (it may have been removed by a config update)")
            token = str(secret or "")
            if entry.auth_type == "oauth":
                token = self._oauth_pkce_token(entry)
            elif entry.auth_type == "api_key" and not token:
                raise RuntimeError("This connector needs an API key")
            payload: dict[str, Any] = {
                "id": str(entry.id),
                "display_name": str(entry.name),
                "transport": str(entry.transport),
                "url": str(getattr(entry, "url", "") or ""),
                "command": str(getattr(entry, "command", "") or ""),
                "args": list(getattr(entry, "args", []) or []),
                "enabled": True,
                "auth_type": str(entry.auth_type),
                "auth_header": str(getattr(entry, "token_field", "") or "") or "X-API-Key",
                "scopes": list(getattr(entry, "scopes", []) or []),
                "auto_approve": bool(getattr(entry, "auto_approve", False)),
                "permissions": {
                    str(tool): ("allow" if entry.auto_approve else "ask")
                    for tool in (getattr(entry, "preset_tools", []) or [])
                },
            }
            if token:
                payload["secret"] = token
            result = self.save_extension("connector", payload)
            if not result.get("ok"):
                raise RuntimeError(str(result.get("message") or "Could not install the connector"))
            return {"installed": str(entry.id), "message": f"{entry.name} connected. Use Check to review and trust its tools."}

        return self._run_task("install_connector", work)

    def disconnect_connector(self, extension_id: str) -> dict[str, Any]:
        result = self.delete_extension("connector", str(extension_id))
        if result.get("ok"):
            credential_key = f"EXT_{str(extension_id).upper().replace('-', '_')}_KEY"
            try:
                self.credentials.delete(credential_key)
            except Exception:
                logger.debug("credential delete on disconnect failed", exc_info=True)
        return result

    def attach_mcp_resource(self, kind: str, extension_id: str, uri: str) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            raw = self._find_extension_record(kind, extension_id)
            if raw is None:
                raise RuntimeError("Extension not found")
            config = connector_as_server(raw) if kind == "connector" else raw
            self.extension_host.discover(config)  # ensures a live session
            result = self.extension_host.read_resource(config.id, str(uri))
            texts: list[str] = []
            contents = getattr(result, "contents", None)
            if isinstance(result, dict):
                contents = result.get("contents", contents)
            for entry in contents or []:
                text = getattr(entry, "text", None)
                if isinstance(entry, dict):
                    text = entry.get("text", text)
                if isinstance(text, str) and text:
                    texts.append(text)
            if not texts:
                raise RuntimeError("That resource has no readable text content")
            content = "\n\n".join(texts)[:MAX_ATTACHMENT_BYTES]
            cache_dir = self.paths.extensions_dir / "resource_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            for stale in sorted(cache_dir.glob("*"), key=lambda p: p.stat().st_mtime if p.exists() else 0)[:-19]:
                try:
                    stale.unlink()
                except OSError:
                    pass
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(str(uri)).name or "resource")[:80] or "resource"
            if "." not in safe_name:
                safe_name += ".txt"
            target = cache_dir / f"{uuid.uuid4().hex[:8]}-{safe_name}"
            target.write_text(content, encoding="utf-8")
            added = self._add_attachment(name=safe_name, path=target, size=target.stat().st_size)
            if not added:
                raise RuntimeError(f"Attachment limit reached ({MAX_ATTACHMENTS})")
            return {"attachments": self._attachments_payload(), "name": safe_name}

        return self._run_task("attach_mcp_resource", work)

    def get_mcp_prompt(self, kind: str, extension_id: str, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            raw = self._find_extension_record(kind, extension_id)
            if raw is None:
                raise RuntimeError("Extension not found")
            config = connector_as_server(raw) if kind == "connector" else raw
            self.extension_host.discover(config)
            result = self.extension_host.get_prompt(config.id, str(name), arguments or {})
            messages = getattr(result, "messages", None)
            if isinstance(result, dict):
                messages = result.get("messages", messages)
            parts: list[str] = []
            for message in messages or []:
                content = getattr(message, "content", None)
                if isinstance(message, dict):
                    content = message.get("content", content)
                if isinstance(content, list):
                    for block in content:
                        text = getattr(block, "text", None)
                        if isinstance(block, dict):
                            text = block.get("text", text)
                        if isinstance(text, str):
                            parts.append(text)
                else:
                    text = getattr(content, "text", None)
                    if isinstance(content, dict):
                        text = content.get("text", text)
                    if isinstance(text, str):
                        parts.append(text)
            return {"text": "\n\n".join(parts)[:60_000]}

        return self._run_task("get_mcp_prompt", work)

    def create_skill(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = _payload_dict(payload)
        name = str(payload.get("name") or "").strip().lower()
        description = str(payload.get("description") or "").strip()
        instructions = str(payload.get("instructions") or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", name):
            return {"ok": False, "message": "Skill name must be 2-63 lowercase letters, numbers, or hyphens"}
        if len(description) < 8:
            return {"ok": False, "message": "Add a short description of when this skill should be used"}
        if len(instructions) < 12:
            return {"ok": False, "message": "Add the skill instructions"}
        skills_root = self.paths.skills_dir
        target = (skills_root / name).resolve()
        if skills_root.resolve() not in target.parents:
            return {"ok": False, "message": "Invalid skill location"}
        if target.exists():
            return {"ok": False, "message": "A skill with that name already exists"}
        target.mkdir(parents=True)
        body = (
            f"---\nname: {name}\ndescription: {description[:500]}\n---\n\n"
            f"# {name}\n\n{instructions[:60_000]}\n"
        )
        (target / "SKILL.md").write_text(body, encoding="utf-8")
        return {"ok": True, "name": name, "skills": self._skills_payload()}

    def delete_skill(self, name: str) -> dict[str, Any]:
        try:
            package = self.skill_manager.get(str(name))
        except Exception:
            return {"ok": False, "message": "Skill not found"}
        root = Path(package.root).resolve()
        if self.paths.skills_dir.resolve() not in root.parents and root.parent != self.paths.skills_dir.resolve():
            return {"ok": False, "message": "That skill is not in the VISION skills folder"}
        import shutil as _shutil

        _shutil.rmtree(root, ignore_errors=True)
        if root.exists():
            return {"ok": False, "message": "Could not delete the skill folder"}
        self.config.enabled_skills = [item for item in self.config.enabled_skills if item != package.name]
        try:
            self.config.save(self.paths)
        except Exception:
            logger.debug("could not persist enabled_skills after delete", exc_info=True)
        return {"ok": True, "skills": self._skills_payload()}

    def export_skill(self, name: str) -> dict[str, Any]:
        if self.window is None:
            return {"ok": False, "message": "Window is not ready"}
        try:
            package = self.skill_manager.get(str(name))
        except Exception:
            return {"ok": False, "message": "Skill not found"}
        import io
        import zipfile

        import webview

        root = Path(package.root)
        buffer = io.BytesIO()
        total = 0
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in sorted(package.files or []):
                source = (root / relative).resolve()
                if root.resolve() not in source.parents and source != root.resolve():
                    continue
                if not source.is_file():
                    continue
                total += source.stat().st_size
                if total > 25 * 1024 * 1024:
                    return {"ok": False, "message": "Skill is larger than 25 MB and cannot be exported"}
                archive.write(source, arcname=f"{package.name}/{relative}")
        result = self.window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=f"{package.name}.zip"
        )
        if not result:
            return {"ok": False, "cancelled": True}
        destination = Path(result if isinstance(result, str) else result[0])
        destination.write_bytes(buffer.getvalue())
        return {"ok": True, "path": destination.name}

    def import_skill(self, folder: bool = False) -> dict[str, Any]:
        if self.window is None:
            return {"ok": False, "message": "Window is not ready"}
        import webview

        from .extensions.skill_import import import_skill as import_skill_package

        if folder:
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        else:
            result = self.window.create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=("Skill packages (*.md;*.zip;*.skill)", "All files (*.*)"),
            )
        if not result:
            return {"ok": False, "cancelled": True}
        source = Path(result if isinstance(result, str) else result[0])

        def work() -> dict[str, Any]:
            installed = import_skill_package(source, self.paths.skills_dir, overwrite=True)
            return {"name": installed.name, "skills": self._skills_payload()}

        return self._run_task("import_skill", work)

    def get_activity(self) -> dict[str, Any]:
        audit_text = "\n".join(_read_tail_lines(self.paths.audit_log, 300))
        rows, feedback_count = self.needs_log.tail(100)
        return {
            "audit_text": audit_text,
            "feedback_rows": _json_safe(rows),
            "feedback_count": feedback_count,
            "telemetry": self._telemetry_payload(),
            "available_models": self._available_models(),
        }

    def upload_feedback(self) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            # A manual Send click is explicit consent for this attempt, even
            # if the automatic opt-in toggle is off.  The scheduler's routine
            # launch/interval/close attempts still require opt-in.
            if self.sync_service is not None:
                uploaded = self.sync_service.flush_and_upload(reason="manual", force=True)
            else:
                uploaded = upload_needs_log(self.needs_log, self.credentials.get("HF_TOKEN"))
            telemetry = self._telemetry_payload()
            if not uploaded:
                return {
                    "ok": False,
                    "uploaded": False,
                    "message": "Feedback upload failed or no Hugging Face token is stored; the local queue was retained.",
                    "telemetry": telemetry,
                }
            return {"ok": True, "uploaded": True, "message": "Feedback uploaded", "telemetry": telemetry}

        return self._run_task("upload_feedback", work)

    def export_feedback(self) -> dict[str, Any]:
        if self.window is None:
            return {"ok": False, "message": "Window is not ready"}
        import webview

        result = self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename="feedback.jsonl",
            file_types=("JSON Lines (*.jsonl)",),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        filename = result if isinstance(result, str) else result[0]
        self.needs_log.flush()
        if self.paths.needs_log.exists():
            shutil.copyfile(self.paths.needs_log, filename)
        else:
            Path(filename).write_text("", encoding="utf-8")
        return {"ok": True, "path": str(filename)}

    def clear_feedback(self) -> dict[str, Any]:
        self.needs_log.clear()
        return {"ok": True, "telemetry": self._telemetry_payload()}

    # ------------------------------------------------------------------
    # Browser-native UI helpers, window controls and exports
    # ------------------------------------------------------------------
    def copy_text(self, text: str) -> dict[str, Any]:
        try:
            import pyperclip

            pyperclip.copy(str(text))
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def open_url(self, url: str) -> dict[str, Any]:
        clean = str(url or "").strip()
        try:
            parsed = urllib_parse.urlparse(clean)
            _port = parsed.port
        except (TypeError, ValueError):
            return {"ok": False, "message": "Invalid link"}
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or not clean.lower().startswith(("http://", "https://"))
            or len(clean) > 4096
        ):
            return {"ok": False, "message": "Only valid HTTP/HTTPS links without embedded credentials can be opened"}
        opened = webbrowser.open(clean)
        return {"ok": bool(opened), "message": "" if opened else "The system browser could not be opened"}

    def open_source_path(self, path: str) -> dict[str, Any]:
        executable_extensions = {
            ".bat", ".cmd", ".com", ".cpl", ".exe", ".hta", ".inf", ".ins", ".jar", ".jse", ".lnk",
            ".msc", ".msi", ".msp", ".pif", ".ps1", ".py", ".reg", ".scr", ".vb", ".vbe", ".vbs", ".ws", ".wsf", ".wsh",
        }
        try:
            target = Path(str(path or "")).expanduser().resolve(strict=True)
        except Exception:
            return {"ok": False, "message": "Source file no longer exists"}
        if not target.is_file() or target.suffix.lower() in executable_extensions:
            return {"ok": False, "message": "Executable or script sources cannot be opened from citations"}
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            webbrowser.open(target.as_uri())
        return {"ok": True}

    def save_export(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.window is None:
            return {"ok": False, "message": "Window is not ready"}
        payload = _payload_dict(payload)

        filename = Path(str(payload.get("filename") or "vision-export")).name
        if not filename or len(filename) > 255:
            return {"ok": False, "message": "Invalid export filename"}
        raw_data = payload.get("data")
        if not isinstance(raw_data, str):
            return {"ok": False, "message": "Export data must be text or base64"}
        try:
            if payload.get("encoding") == "base64":
                if len(raw_data) > ((MAX_EXPORT_BYTES + 2) // 3) * 4 + 4:
                    return {"ok": False, "message": f"Export exceeds the {MAX_EXPORT_BYTES // 1024 // 1024} MB limit"}
                data_bytes = base64.b64decode(raw_data, validate=True)
            else:
                data_bytes = raw_data.encode("utf-8")
        except Exception:
            return {"ok": False, "message": "Invalid export data"}
        if len(data_bytes) > MAX_EXPORT_BYTES:
            return {"ok": False, "message": f"Export exceeds the {MAX_EXPORT_BYTES // 1024 // 1024} MB limit"}
        import webview

        result = self.window.create_file_dialog(webview.SAVE_DIALOG, save_filename=filename)
        if not result:
            return {"ok": False, "cancelled": True}
        destination = Path(result if isinstance(result, str) else result[0])
        destination.write_bytes(data_bytes)
        return {"ok": True, "path": str(destination)}

    def window_minimize(self) -> dict[str, Any]:
        if self.window:
            self.window.minimize()
        return {"ok": True}

    def window_maximize(self) -> dict[str, Any]:
        if self.window:
            if self._window_maximized:
                self.window.restore()
                self._window_maximized = False
            else:
                self.window.maximize()
                self._window_maximized = True
        return {"ok": True, "maximized": self._window_maximized}

    def window_close(self) -> dict[str, Any]:
        if self.window:
            self.window.destroy()
        return {"ok": True}

    def _on_window_maximized(self) -> None:
        self._window_maximized = True

    def _on_window_restored(self) -> None:
        self._window_maximized = False

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._shutdown_called:
                return
            self._shutdown_called = True
            self._closed.set()

            def step(name: str, function: Any) -> None:
                try:
                    function()
                except Exception:
                    logger.exception("Web bridge shutdown step failed: %s", name)

            step("flush streamed output", self._flush_stream)

            def close_live_mini_window() -> None:
                mini = self._live_mini_window
                self._live_mini_window = None
                if mini is not None:
                    mini.destroy()

            step("close Vision Live mini", close_live_mini_window)
            step("stop generation", self.stop_generation)

            def deny_pending_confirmations() -> None:
                for _event, state in list(self._confirmations.values()):
                    state["approved"] = False
                    _event.set()

            def cancel_timers() -> None:
                for timer in list(self.tool_context.timers):
                    timer.cancel()

            def stop_sync() -> None:
                if self.sync_service is not None:
                    self.sync_service.stop()
                else:
                    self.needs_log.flush()

            step("deny pending confirmations", deny_pending_confirmations)
            step("cancel timers", cancel_timers)
            step("stop sync service", stop_sync)
            step("stop local server", self.server_manager.stop)
            step("stop extension host", self.extension_host.stop)


def run_web_app(services: SimpleNamespace | None = None, *, web_dir: str | Path | None = None, debug: bool = False) -> int:
    if services is None:
        from .app import build_services

        services = build_services()
    import webview

    root = find_web_dir(web_dir)
    bridge = VisionWebApi(services)
    bridge._live_mini_url = (root / "index.html").resolve().as_uri() + "?liveMini=1"
    # WebView developer tools are a source/debug escape hatch. They are never
    # enabled in the packaged release, even if --debug was supplied.
    effective_debug = bool(debug and not getattr(sys, "frozen", False))
    storage = services.paths.app_data / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    window = webview.create_window(
        PRODUCT_NAME,
        url=str(root / "index.html"),
        js_api=bridge,
        width=1440,
        height=920,
        # The React shell stays fully usable from very small snapped
        # windows up to large monitors (auto-collapsing rail + compact
        # breakpoints), so the native floor can be a true small window
        # instead of the old 980x640. See ARCHITECTURE.md section 17.
        min_size=(520, 460),
        frameless=True,
        easy_drag=False,
        background_color="#0B0D10",
        text_select=True,
    )
    if window is None:
        bridge.shutdown()
        return 1
    bridge.attach_window(window)
    window.events.loaded += bridge.start_background_services
    window.events.maximized += bridge._on_window_maximized
    window.events.restored += bridge._on_window_restored
    window.events.closed += bridge.shutdown
    try:
        webview.start(debug=effective_debug, private_mode=False, storage_path=str(storage))
    finally:
        bridge.shutdown()
    return 0

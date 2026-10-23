from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from . import BUILD_COMMIT, __version__
from .chat.controller import ChatCallbacks, TurnResult
from .config import AppConfig
from .constants import CREDITS_LINE, EXPECTED_GGUF_SIZE, GGUF_FILENAME, MIN_LLAMA_BUILD, PRODUCT_NAME
from .extensions.connectors import connector_as_server, load_connectors, save_connectors
from .extensions.mcp_config import McpServerConfig, load_mcp_config, save_mcp_config
from .extensions.mcp_tools import make_mcp_tool_definition
from .knowledge.retrieval import build_project_context
from .redaction import redact_value
from .server.discovery import build_is_supported, detect_build, find_llama_server
from .server.download_llama import download_llama
from .server.health import HealthState
from .telemetry.uploader import upload_in_background, upload_needs_log
from .tools.registry import ToolExecutionResult
from .updater.downloads import download_model
from .updater.model_updater import check_for_update


def _json_safe(value: Any) -> Any:
    value = redact_value(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return _json_safe(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


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
        self.registry = services.registry
        self.controller = services.controller
        self.tool_context = services.tool_context
        self.skill_manager = services.skill_manager
        self.server_manager = services.server_manager
        self.extension_host = services.extension_host
        self.needs_log = services.needs_log

        self.window: Any | None = None
        self._frontend_ready = False
        self._event_lock = threading.RLock()
        self._event_queue: list[dict[str, Any]] = []

        self._stream_lock = threading.Lock()
        self._pending_content = ""
        self._pending_reasoning = ""
        self._stream_timer: threading.Timer | None = None

        self._confirm_lock = threading.Lock()
        self._confirmations: dict[str, tuple[threading.Event, dict[str, bool]]] = {}

        self._attachments: dict[str, dict[str, Any]] = {}
        self._chat_thread: threading.Thread | None = None
        self._chat_loop: asyncio.AbstractEventLoop | None = None
        self._chat_task: asyncio.Task | None = None
        self._chat_busy = False

        self._closed = threading.Event()
        self._background_started = False
        self._tasks: dict[str, threading.Thread] = {}
        self._task_progress_at: dict[str, float] = {}

        self._health_state = HealthState()
        self._server_healthy: bool | None = None
        self._server_state = "Not checked"
        self._server_binary: Path | None = None
        self._server_build: int | None = None
        self._server_supported: bool | None = None
        self._server_binary_checked = False
        self._server_intentionally_stopped = False
        self._server_lock = threading.RLock()
        self._last_failover_notice = 0.0

        self._dynamic_names: set[str] = set()
        self._extension_messages: list[str] = []
        self._latest_remote_sha = ""
        self._window_maximized = False
        self._shutdown_called = False

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
        if self._background_started:
            return
        self._background_started = True
        threading.Thread(target=self._health_loop, daemon=True, name="vision-health").start()
        threading.Thread(target=self._refresh_binary, daemon=True, name="vision-binary-check").start()
        threading.Thread(target=self._refresh_extensions, daemon=True, name="vision-extensions").start()
        if self.config.auto_start_local_server:
            threading.Thread(target=self._start_server_internal, daemon=True, name="vision-server-start").start()
        # A quiet, non-blocking update check. It only creates a toast when a
        # different model build is available; downloads remain user-clicked.
        threading.Thread(target=self._silent_update_check, daemon=True, name="vision-update-check").start()

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
            queued, self._event_queue = self._event_queue, []
        for event in queued:
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

    def _config_payload(self) -> dict[str, Any]:
        payload = self.config.model_dump(mode="json")
        payload["local_base_url"] = self.config.local_base_url
        return _json_safe(payload)

    def _skills_payload(self) -> list[dict[str, Any]]:
        enabled = set(self.config.enabled_skills)
        rows: list[dict[str, Any]] = []
        for package in self.skill_manager.discover():
            rows.append(
                {
                    "name": package.name,
                    "description": package.metadata.description,
                    "enabled": package.name in enabled,
                    "files": package.files,
                    "scripts": package.scripts,
                    "sha256": package.sha256,
                }
            )
        return rows

    def _extension_records(self) -> list[tuple[str, McpServerConfig]]:
        records: list[tuple[str, McpServerConfig]] = []
        try:
            for server in load_mcp_config(self.paths.mcp_config).servers:
                if server.enabled:
                    records.append(("mcp", server))
        except Exception as exc:
            self._extension_messages = [f"MCP config: {exc}"]
        try:
            for connector in load_connectors(self.paths.connectors_config).connectors:
                if connector.enabled:
                    records.append(("connector", connector_as_server(connector)))
        except Exception as exc:
            self._extension_messages.append(f"Connector config: {exc}")
        return records

    def _extensions_payload(self) -> dict[str, Any]:
        servers: list[dict[str, Any]] = []
        connectors: list[dict[str, Any]] = []
        try:
            servers = [_json_safe(item.model_dump(mode="json")) for item in load_mcp_config(self.paths.mcp_config).servers]
        except Exception:
            servers = []
        try:
            connectors = [_json_safe(item.model_dump(mode="json")) for item in load_connectors(self.paths.connectors_config).connectors]
        except Exception:
            connectors = []
        return {
            "servers": servers,
            "connectors": connectors,
            "dynamic_tools": sorted(self._dynamic_names),
            "messages": list(self._extension_messages),
        }

    def _server_payload(self, *, include_logs: bool = False) -> dict[str, Any]:
        model = Path(self.config.model_path or self.paths.model_file)
        binary = self._server_binary
        if binary is None and not self._server_binary_checked:
            binary = find_llama_server(self.paths, self.config.llama_server_path)
        payload: dict[str, Any] = {
            "running": self.server_manager.is_running,
            "healthy": self._server_healthy,
            "state": self._server_state,
            "failures": self._health_state.failures,
            "intentionally_stopped": self._server_intentionally_stopped,
            "local_base_url": self.config.local_base_url,
            "model_path": str(model),
            "model_exists": model.exists(),
            "model_size": model.stat().st_size if model.exists() else 0,
            "expected_model_size": EXPECTED_GGUF_SIZE,
            "model_filename": GGUF_FILENAME,
            "binary": str(binary) if binary else "",
            "build": self._server_build,
            "build_supported": self._server_supported,
            "minimum_build": MIN_LLAMA_BUILD,
            "command": list(self.server_manager.command),
            "exit_code": self.server_manager.exit_code,
        }
        if include_logs:
            payload["logs"] = self.server_manager.logs(300)
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
            "paths": {
                "app_data": str(self.paths.app_data),
                "workspace": str(self.paths.workspace),
                "model_file": str(self.paths.model_file),
                "audit_log": str(self.paths.audit_log),
                "needs_log": str(self.paths.needs_log),
            },
            "conversations": _json_safe(conversations),
            "current_conversation_id": conversation_id,
            "projects": _json_safe(self.projects.list_projects()),
            "active_project_id": self.config.active_project_id,
            "skills": self._skills_payload(),
            "extensions": self._extensions_payload(),
            "tools": [
                {
                    "name": definition.name,
                    "description": definition.description,
                    "source": definition.source,
                    "side_effect": definition.side_effect,
                }
                for definition in self.registry.definitions.values()
            ],
            "server": self._server_payload(),
            "attachments": list(self._attachments.values()),
            "chat_busy": self._chat_busy,
        }

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------
    def list_conversations(self, query: str = "") -> dict[str, Any]:
        rows = self.history.search_conversations(query.strip()) if query.strip() else self.history.list_conversations()
        return {"conversations": _json_safe(rows)}

    def create_conversation(self, project_id: str = "") -> dict[str, Any]:
        selected = project_id or self.config.active_project_id
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
        self.history.delete_conversation(conversation_id)
        conversations = self.history.list_conversations()
        if not conversations:
            self.history.create_conversation(project_id=self.config.active_project_id or None)
            conversations = self.history.list_conversations()
        return {"conversations": _json_safe(conversations), "current_conversation_id": conversations[0]["id"] if conversations else ""}

    def set_active_project(self, project_id: str = "", conversation_id: str = "") -> dict[str, Any]:
        self.config.active_project_id = str(project_id or "")
        self.config.save(self.paths)
        if conversation_id:
            self.projects.assign_conversation(conversation_id, self.config.active_project_id or None)
        return {"active_project_id": self.config.active_project_id, "projects": _json_safe(self.projects.list_projects())}

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
    # Attachments and confirmations
    # ------------------------------------------------------------------
    def choose_attachments(self) -> dict[str, Any]:
        if self.window is None:
            return {"attachments": list(self._attachments.values()), "message": "Window is not ready"}
        import webview

        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=("Text files (*.txt;*.md;*.py;*.json;*.csv;*.log;*.yaml;*.yml;*.toml)", "All files (*.*)"),
        )
        if result:
            paths = [result] if isinstance(result, str) else list(result)
            for raw in paths:
                path = Path(raw)
                if not path.is_file():
                    continue
                attachment_id = uuid.uuid4().hex
                self._attachments[attachment_id] = {
                    "id": attachment_id,
                    "name": path.name,
                    "path": str(path),
                    "size": path.stat().st_size,
                }
        return {"attachments": list(self._attachments.values())}

    def remove_attachment(self, attachment_id: str) -> dict[str, Any]:
        self._attachments.pop(str(attachment_id), None)
        return {"attachments": list(self._attachments.values())}

    def _confirm(self, title: str, message: str, details: dict[str, Any]) -> bool:
        if self.window is None or not self._frontend_ready:
            return False
        request_id = uuid.uuid4().hex
        event = threading.Event()
        state = {"approved": False}
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
        return state["approved"]

    def resolve_confirmation(self, request_id: str, approved: bool) -> dict[str, Any]:
        with self._confirm_lock:
            stored = self._confirmations.get(str(request_id))
        if stored:
            event, state = stored
            state["approved"] = bool(approved)
            event.set()
            return {"ok": True}
        return {"ok": False}

    def _schedule_timer(self, minutes: float, label: str) -> None:
        seconds = max(0.1, float(minutes) * 60)
        timer = threading.Timer(seconds, lambda: self._emit("timer_fired", {"label": label, "minutes": minutes}))
        timer.daemon = True
        timer.start()
        self.tool_context.timers.append(timer)
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

    def _prepare_turn(self, payload: dict[str, Any], *, regenerate: bool) -> dict[str, Any]:
        conversation_id = str(payload.get("conversation_id") or "")
        if not conversation_id:
            conversation_id = self.history.create_conversation(project_id=self.config.active_project_id or None)
        text = "" if regenerate else str(payload.get("text") or "")
        retrieval_text = text
        if regenerate:
            retrieval_text, _answer = self.history.last_exchange(conversation_id)

        final_text = text
        attachment_ids = [str(item) for item in payload.get("attachment_ids") or []]
        for attachment_id in attachment_ids:
            record = self._attachments.get(attachment_id)
            if not record:
                continue
            path = Path(record["path"])
            if path.stat().st_size > 256 * 1024:
                raise RuntimeError(f"Attachment exceeds 256 KB: {path.name}")
            content = path.read_text(encoding="utf-8")
            final_text += f"\n\nAttachment: {path.name}\n```\n{content}\n```"
            self._attachments.pop(attachment_id, None)

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
        metadata = self.skill_manager.metadata_prompt(skills_enabled)
        if metadata:
            context_blocks.append(metadata)
        probe_text = retrieval_text or final_text
        for package in self.skill_manager.discover():
            if package.name in skills_enabled and (f"/{package.name}" in probe_text or f"@{package.name}" in probe_text):
                loaded = self.skill_manager.load(package.name)
                context_blocks.append(
                    f"Activated skill {package.name} instructions (untrusted workflow guidance):\n{loaded['instructions']}"
                )

        tools = self.registry.openai_tools(
            include_skill_load=bool(skills_enabled),
            dynamic_names=set(self._dynamic_names),
        )
        builtin_count = 29 if skills_enabled else 28
        tools = tools[: builtin_count + max(0, int(self.config.max_extension_tools))]
        return {
            "conversation_id": conversation_id,
            "user_text": final_text,
            "tools": tools,
            "system_context": "\n\n".join(context_blocks),
            "pre_sources": sources,
        }

    def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._chat_busy:
            return {"accepted": False, "message": "A response is already running"}
        self._chat_busy = True
        self._chat_thread = threading.Thread(
            target=self._run_chat_thread,
            args=(dict(payload or {}), False),
            daemon=True,
            name="vision-web-chat",
        )
        self._chat_thread.start()
        return {"accepted": True}

    def regenerate(self, conversation_id: str = "") -> dict[str, Any]:
        if self._chat_busy:
            return {"accepted": False, "message": "A response is already running"}
        self._chat_busy = True
        self._chat_thread = threading.Thread(
            target=self._run_chat_thread,
            args=({"conversation_id": conversation_id}, True),
            daemon=True,
            name="vision-web-regenerate",
        )
        self._chat_thread.start()
        return {"accepted": True}

    def _run_chat_thread(self, payload: dict[str, Any], regenerate: bool) -> None:
        conversation_id = str(payload.get("conversation_id") or "")
        try:
            request = self._prepare_turn(payload, regenerate=regenerate)
            conversation_id = str(request["conversation_id"])
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
                "conversations": _json_safe(self.history.list_conversations()),
            }
            self._emit("turn_finished", payload_result)
        finally:
            self._chat_busy = False

    def _rename_new_conversation(self, result: TurnResult) -> None:
        row = self._conversation_row(result.conversation_id)
        if row and row.get("title") == "New chat":
            prompt, _answer = self.history.last_exchange(result.conversation_id)
            if prompt:
                self.history.rename_conversation(result.conversation_id, prompt[:48])

    def stop_generation(self) -> dict[str, Any]:
        if self._chat_loop and self._chat_task and not self._chat_task.done():
            self._chat_loop.call_soon_threadsafe(self._chat_task.cancel)
            return {"ok": True}
        return {"ok": False}

    def record_feedback(self, conversation_id: str, detail: str = "Thumbs down") -> dict[str, Any]:
        self.controller.record_feedback(conversation_id, detail=str(detail or "Thumbs down"))
        self.needs_log.flush()
        return {"ok": True}

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
            "paths": {
                "app_data": str(self.paths.app_data),
                "workspace": str(self.paths.workspace),
            },
            "skills": self._skills_payload(),
            "projects": _json_safe(self.projects.list_projects()),
            "extensions": self._extensions_payload(),
            "server": self._server_payload(),
        }

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload or {})
        allowed = {
            "endpoint_mode", "local_port", "remote_base_url", "remote_model_override", "local_model_override",
            "context_size", "gpu_layers", "model_path", "llama_server_path", "theme", "telemetry_opt_in",
            "first_run_completed", "max_extension_tools", "auto_start_local_server", "enabled_skills",
            "enabled_mcp_servers", "enabled_connectors", "active_project_id",
        }
        current = self.config.model_dump(mode="json")
        updated = dict(current)
        for key in allowed:
            if key in payload:
                updated[key] = payload[key]
        validated = AppConfig.model_validate(updated)
        restart_relevant = {"local_port", "context_size", "gpu_layers", "model_path", "llama_server_path"}
        restart_needed = any(getattr(validated, key) != getattr(self.config, key) for key in restart_relevant)
        for key, value in validated.model_dump(mode="python").items():
            setattr(self.config, key, value)
        self.config.save(self.paths)

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
        payload = dict(payload or {})
        if "gpu_layers" in payload:
            self.config.gpu_layers = int(payload["gpu_layers"])
        self.config.first_run_completed = True
        self.config.save(self.paths)
        threading.Thread(target=self._start_server_internal, daemon=True, name="vision-setup-server").start()
        return {"ok": True, "config": self._config_payload(), "server": self._server_payload()}

    def _run_task(self, name: str, function: Any) -> dict[str, Any]:
        current = self._tasks.get(name)
        if current and current.is_alive():
            return {"accepted": False, "message": f"{name} is already running"}

        def runner() -> None:
            self._emit("task_started", {"name": name})
            try:
                result = function()
                self._emit("task_finished", {"name": name, "ok": True, "result": _json_safe(result)})
            except Exception as exc:
                self._emit("task_finished", {"name": name, "ok": False, "error": str(exc)})
            finally:
                self._tasks.pop(name, None)

        thread = threading.Thread(target=runner, daemon=True, name=f"vision-{name}")
        self._tasks[name] = thread
        thread.start()
        return {"accepted": True}

    def _task_progress(self, name: str, message: str, downloaded: int | None = None, total: int | None = None) -> None:
        now = time.monotonic()
        if now - self._task_progress_at.get(name, 0.0) < 0.25 and total:
            return
        self._task_progress_at[name] = now
        percent = round((downloaded / total * 100), 1) if downloaded is not None and total else None
        self._emit(
            "task_progress",
            {"name": name, "message": message, "downloaded": downloaded, "total": total, "percent": percent},
        )

    def download_model(self) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            existing = Path(self.config.model_path or self.paths.model_file).exists()
            path = download_model(
                self.paths,
                token=self.credentials.get("HF_TOKEN"),
                pending=existing,
                progress=lambda message: self._task_progress("download_model", message),
            )
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
            return {"path": str(path), "message": message, "server": self._server_payload(), "config": self._config_payload()}

        return self._run_task("download_model", work)

    def download_llama(self) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            path = download_llama(
                self.paths,
                "auto",
                progress=lambda name, downloaded, total: self._task_progress(
                    "download_llama", f"Downloading {name}", downloaded, total
                ),
            )
            self.config.llama_server_path = str(path)
            self.config.save(self.paths)
            self._refresh_binary()
            threading.Thread(target=self._start_server_internal, daemon=True).start()
            return {"path": str(path), "message": f"llama-server ready: {path}", "server": self._server_payload(), "config": self._config_payload()}

        return self._run_task("download_llama", work)

    def check_model_update(self) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            result = check_for_update(self.config.installed_model_sha, self.credentials.get("HF_TOKEN"))
            self._latest_remote_sha = result.remote_sha
            return _json_safe(result)

        return self._run_task("check_model_update", work)

    def _silent_update_check(self) -> None:
        try:
            result = check_for_update(self.config.installed_model_sha, self.credentials.get("HF_TOKEN"))
            self._latest_remote_sha = result.remote_sha
            if result.status in {"different", "newer_available"}:
                self._emit("toast", {"message": "New VISION model build available", "kind": "info"})
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Server lifecycle and health
    # ------------------------------------------------------------------
    def _refresh_binary(self) -> None:
        supported = find_llama_server(self.paths, self.config.llama_server_path, require_supported=True)
        binary = supported or find_llama_server(self.paths, self.config.llama_server_path)
        self._server_binary = binary
        self._server_build = detect_build(binary) if binary else None
        self._server_supported = build_is_supported(self._server_build) if binary else False
        self._server_binary_checked = True
        self._emit("server_state", self._server_payload())

    def _start_server_internal(self) -> dict[str, Any]:
        with self._server_lock:
            if self.server_manager.is_running:
                self._server_state = "Starting" if self._server_healthy is not True else "Healthy"
                self._emit("server_state", self._server_payload())
                return {"ok": True, "server": self._server_payload()}
            model = Path(self.config.model_path or self.paths.model_file)
            if not model.exists():
                self._server_state = "Model missing"
                self._emit("server_state", self._server_payload())
                return {"ok": False, "message": "Model missing", "server": self._server_payload()}
            self._refresh_binary()
            binary = self._server_binary
            if not binary:
                self._server_state = "llama-server missing"
                self._emit("server_state", self._server_payload())
                return {"ok": False, "message": "llama-server missing", "server": self._server_payload()}
            if not self._server_supported:
                detected = f"b{self._server_build}" if self._server_build else "unknown"
                self._server_state = f"Unsupported llama.cpp build ({detected})"
                message = f"Found llama-server build {detected}; VISION requires b{MIN_LLAMA_BUILD} or newer."
                self._emit("banner", {"message": message})
                self._emit("server_state", self._server_payload())
                return {"ok": False, "message": message, "server": self._server_payload()}
            try:
                self.server_manager.start(binary, model, self.config)
                self._server_intentionally_stopped = False
                self._server_state = "Starting"
                self._server_healthy = None
                self._emit("server_state", self._server_payload())
                return {"ok": True, "server": self._server_payload()}
            except Exception as exc:
                self._server_state = "Start failed"
                self._emit("banner", {"message": f"Could not start llama-server: {exc}"})
                self._emit("server_state", self._server_payload())
                return {"ok": False, "message": str(exc), "server": self._server_payload()}

    def start_server(self) -> dict[str, Any]:
        threading.Thread(target=self._start_server_internal, daemon=True, name="vision-server-start").start()
        return {"accepted": True}

    def stop_server(self) -> dict[str, Any]:
        self._server_intentionally_stopped = True

        def work() -> None:
            self.server_manager.stop()
            self._server_state = "Stopped"
            self._server_healthy = False
            self._emit("server_state", self._server_payload())

        threading.Thread(target=work, daemon=True, name="vision-server-stop").start()
        return {"accepted": True}

    def restart_server(self) -> dict[str, Any]:
        self._server_intentionally_stopped = False

        def work() -> None:
            self.server_manager.stop()
            self._start_server_internal()

        threading.Thread(target=work, daemon=True, name="vision-server-restart").start()
        return {"accepted": True}

    def get_server_state(self) -> dict[str, Any]:
        return {"server": self._server_payload(include_logs=True)}

    @staticmethod
    def _sync_health(url: str) -> bool:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/models", timeout=3) as response:
                return response.status == 200
        except Exception:
            return False

    def _health_loop(self) -> None:
        while not self._closed.is_set():
            healthy = self._sync_health(self.config.local_base_url)
            previous = self._server_healthy
            action = self._health_state.record(healthy)
            self._server_healthy = healthy
            if healthy:
                self._server_state = "Healthy"
            elif self._server_intentionally_stopped:
                self._server_state = "Stopped"
            elif self.server_manager.is_running:
                self._server_state = f"Unhealthy ({self._health_state.failures} failure(s))"
            elif Path(self.config.model_path or self.paths.model_file).exists():
                self._server_state = "Unhealthy"
            else:
                self._server_state = "Model missing"

            if action == "restart" and not self._server_intentionally_stopped:
                self._emit("banner", {"message": "Local server health failed twice. Restarting it once…"})
                threading.Thread(target=self.restart_server, daemon=True).start()
            elif action == "failover" and self.config.remote_base_url:
                now = time.monotonic()
                if now - self._last_failover_notice > 60:
                    self._last_failover_notice = now
                    self._emit(
                        "banner",
                        {"message": "Local server is still unhealthy. Auto mode will use cloud failover on the next request."},
                    )
            if healthy != previous or action != "ok":
                self._emit("server_state", self._server_payload())
            self._closed.wait(5)

    # ------------------------------------------------------------------
    # Extensions and activity
    # ------------------------------------------------------------------
    def _refresh_extensions(self) -> None:
        self.registry.definitions = {
            name: definition for name, definition in self.registry.definitions.items() if definition.source == "builtin"
        }
        self._dynamic_names.clear()
        messages: list[str] = []
        for _kind, record in self._extension_records():
            try:
                discovery = self.extension_host.discover(record)
            except Exception as exc:
                messages.append(f"{record.display_name}: {exc}")
                continue
            if not record.toolset_sha256:
                messages.append(f"{record.display_name}: discovery approval required")
                continue
            if discovery["fingerprint"] != record.toolset_sha256:
                messages.append(f"{record.display_name}: toolset changed — re-approval required")
                continue
            for descriptor in discovery["tools"]:
                definition = make_mcp_tool_definition(record, descriptor, self.extension_host.call_tool_async)
                self.registry.register_dynamic(definition)
                self._dynamic_names.add(definition.name)
        self._extension_messages = messages
        payload = self._extensions_payload()
        self._emit("extensions_updated", payload)
        if messages:
            self._emit("banner", {"message": "Extensions: " + "; ".join(messages[:3])})

    def refresh_extensions(self) -> dict[str, Any]:
        threading.Thread(target=self._refresh_extensions, daemon=True, name="vision-extensions-refresh").start()
        return {"accepted": True}

    def _find_extension_record(self, kind: str, extension_id: str) -> Any:
        if kind == "connector":
            return next((item for item in load_connectors(self.paths.connectors_config).connectors if item.id == extension_id), None)
        return next((item for item in load_mcp_config(self.paths.mcp_config).servers if item.id == extension_id), None)

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
        raw = self._find_extension_record(kind, extension_id)
        if raw is None:
            return {"ok": False, "message": "Extension not found"}
        raw.toolset_sha256 = str(fingerprint)
        existing = dict(getattr(raw, "permissions", {}) or {})
        for name in tool_names or []:
            existing.setdefault(str(name), "ask")
        raw.permissions = existing
        if kind == "connector":
            data = load_connectors(self.paths.connectors_config)
            data.connectors = [raw if item.id == raw.id else item for item in data.connectors]
            save_connectors(self.paths.connectors_config, data)
        else:
            data = load_mcp_config(self.paths.mcp_config)
            data.servers = [raw if item.id == raw.id else item for item in data.servers]
            save_mcp_config(self.paths.mcp_config, data)
        self.refresh_extensions()
        return {"ok": True}

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
        self.needs_log.flush()
        audit_text = ""
        if self.paths.audit_log.exists():
            audit_text = "\n".join(self.paths.audit_log.read_text(encoding="utf-8").splitlines()[-300:])
        rows = self.needs_log.read_all()
        return {
            "audit_text": audit_text,
            "feedback_rows": _json_safe(rows[-100:]),
            "feedback_count": len(rows),
            "audit_path": str(self.paths.audit_log),
            "feedback_path": str(self.paths.needs_log),
        }

    def upload_feedback(self) -> dict[str, Any]:
        return self._run_task(
            "upload_feedback",
            lambda: {"uploaded": bool(upload_needs_log(self.needs_log, self.credentials.get("HF_TOKEN")))},
        )

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
        return {"ok": True}

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
        clean = str(url)
        if not clean.startswith(("http://", "https://")):
            return {"ok": False, "message": "Only HTTP/HTTPS URLs can be opened"}
        webbrowser.open(clean)
        return {"ok": True}

    def open_source_path(self, path: str) -> dict[str, Any]:
        target = Path(str(path)).expanduser()
        if not target.exists():
            return {"ok": False, "message": "Source file no longer exists"}
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            webbrowser.open(target.as_uri())
        return {"ok": True}

    def save_export(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.window is None:
            return {"ok": False, "message": "Window is not ready"}
        import webview

        filename = Path(str(payload.get("filename") or "vision-export")).name
        result = self.window.create_file_dialog(webview.SAVE_DIALOG, save_filename=filename)
        if not result:
            return {"ok": False, "cancelled": True}
        destination = Path(result if isinstance(result, str) else result[0])
        if payload.get("encoding") == "base64":
            destination.write_bytes(base64.b64decode(str(payload.get("data") or "")))
        else:
            destination.write_text(str(payload.get("data") or ""), encoding="utf-8")
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
        if self._shutdown_called:
            return
        self._shutdown_called = True
        self._closed.set()
        self._flush_stream()
        self.stop_generation()
        for _event, state in list(self._confirmations.values()):
            state["approved"] = False
            _event.set()
        for timer in list(self.tool_context.timers):
            timer.cancel()
        try:
            self.needs_log.flush()
            if self.config.telemetry_opt_in:
                upload_in_background(self.needs_log, self.credentials.get("HF_TOKEN"))
        except Exception:
            pass
        try:
            self.server_manager.stop()
        except Exception:
            pass
        try:
            self.extension_host.stop()
        except Exception:
            pass


def run_web_app(services: SimpleNamespace | None = None, *, web_dir: str | Path | None = None, debug: bool = False) -> int:
    if services is None:
        from .app import build_services

        services = build_services()
    import webview

    root = find_web_dir(web_dir)
    bridge = VisionWebApi(services)
    storage = services.paths.app_data / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    window = webview.create_window(
        PRODUCT_NAME,
        url=str(root / "index.html"),
        js_api=bridge,
        width=1440,
        height=920,
        min_size=(980, 640),
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
        webview.start(debug=debug, private_mode=False, storage_path=str(storage))
    finally:
        bridge.shutdown()
    return 0

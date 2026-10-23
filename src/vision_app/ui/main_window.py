from __future__ import annotations

import json
import shutil
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psutil
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import BUILD_COMMIT, __version__
from ..chat.worker import ChatWorker
from ..code_workspace.git_status import git_status
from ..code_workspace.test_profiles import run_test_profile
from ..constants import CREDITS_LINE
from ..extensions.connectors import connector_as_server, load_connectors, save_connectors
from ..extensions.mcp_config import McpServerConfig, load_mcp_config, save_mcp_config
from ..extensions.mcp_tools import make_mcp_tool_definition
from ..genui.cards_widget import CardsWidget
from ..genui.factory import build_genui_widget
from ..knowledge.retrieval import build_project_context
from ..server.discovery import find_llama_server
from ..server.download_llama import download_llama
from ..server.health import HealthState
from ..telemetry.uploader import upload_in_background, upload_needs_log
from ..tools.registry import ToolExecutionResult
from ..updater.downloads import download_model
from ..updater.model_updater import check_for_update
from .code_panel import CodePanel
from .composer import ComposerWidget
from .confirmations import ConfirmationService
from .extensions_center import ExtensionsCenterDialog
from .model_manager import ModelManagerDialog
from .projects_panel import ProjectsDialog
from .server_console import ServerConsoleWidget
from .settings_dialog import SettingsDialog
from .sources_panel import SourcesPanel
from .toasts import Toast
from .transcript import TranscriptWidget
from .wizard import FirstRunWizard
from .workers import FunctionWorker


class _Bridge(QObject):
    server_log = Signal(str)
    timer_fired = Signal(str)
    schedule_timer = Signal(float, str)


class MainWindow(QMainWindow):
    run_chat_requested = Signal()
    regenerate_requested = Signal()

    def __init__(self, services: SimpleNamespace):
        super().__init__()
        self.services = services
        self.paths = services.paths
        self.config = services.config
        self.history = services.history
        self.projects = services.projects
        self.registry = services.registry
        self.controller = services.controller
        self.tool_context = services.tool_context
        self.server_manager = services.server_manager
        self.extension_host = services.extension_host
        self.skill_manager = services.skill_manager
        self.needs_log = services.needs_log
        self.credentials = services.credentials

        self.setWindowTitle("VISION")
        self.resize(1280, 820)
        self._background: list[tuple[QThread, FunctionWorker]] = []
        self._dynamic_names: set[str] = set()
        self._pending_context_blocks: list[str] = []
        self._latest_remote_sha = ""
        self._health_state = HealthState()
        self._health_check_running = False
        self._current_sources: list[dict] = []
        self._system_cards: CardsWidget | None = None

        self.bridge = _Bridge(self)
        self.confirmations = ConfirmationService(self)
        self.tool_context.confirm = self.confirmations.confirm
        self.tool_context.timer_callback = lambda minutes, label: self.bridge.schedule_timer.emit(minutes, label)
        self.bridge.schedule_timer.connect(self._schedule_timer)
        self.bridge.timer_fired.connect(lambda label: self._toast(f"Timer: {label}"))
        self.server_manager.on_log(self.bridge.server_log.emit)

        self._build_ui()
        self.toast = Toast(self)

        self.chat_thread = QThread(self)
        self.chat_worker = ChatWorker(self.controller)
        self.chat_worker.moveToThread(self.chat_thread)
        self.chat_thread.start()
        self.run_chat_requested.connect(self.chat_worker.run)
        self.regenerate_requested.connect(self.chat_worker.regenerate)
        self.chat_worker.content.connect(self._on_content)
        self.chat_worker.reasoning.connect(self._on_reasoning)
        self.chat_worker.tool_start.connect(self._on_tool_start)
        self.chat_worker.tool_result.connect(self._on_tool_result)
        self.chat_worker.endpoint.connect(self._on_endpoint)
        self.chat_worker.sources.connect(self._on_sources)
        self.chat_worker.error.connect(lambda text: self._show_banner(text))
        self.chat_worker.finished.connect(self._on_turn_finished)

        self._refresh_conversations()
        self._refresh_projects()
        if not self.history.list_conversations():
            self._new_conversation()
        elif self.conversations.count():
            self.conversations.setCurrentRow(0)
        self._refresh_extensions()
        self._maybe_start_server()

        self.health_timer = QTimer(self)
        self.health_timer.setInterval(5000)
        self.health_timer.timeout.connect(self._poll_health)
        self.health_timer.start()

        self.system_timer = QTimer(self)
        self.system_timer.setInterval(2000)
        self.system_timer.timeout.connect(self._update_live_system_cards)

        if not self.config.first_run_completed:
            QTimer.singleShot(300, self._show_wizard)
        QTimer.singleShot(1500, self._check_model_update_async)

    # ---------- construction ----------
    def _build_ui(self) -> None:
        root = QWidget(self)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(root)

        sidebar = QWidget(root)
        sidebar.setFixedWidth(270)
        side = QVBoxLayout(sidebar)
        side.addWidget(QLabel("<h2>VISION</h2>"))
        new_button = QPushButton("+ New chat")
        new_button.clicked.connect(self._new_conversation)
        side.addWidget(new_button)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search chats"); self.search.textChanged.connect(self._refresh_conversations)
        side.addWidget(self.search)
        self.conversations = QListWidget(); self.conversations.currentRowChanged.connect(self._conversation_changed)
        side.addWidget(self.conversations, 1)
        chat_row = QHBoxLayout()
        for label, fn in (("Rename", self._rename_conversation), ("Pin", self._pin_conversation), ("Archive", self._archive_conversation), ("Export", self._export_conversation)):
            button = QPushButton(label); button.clicked.connect(fn); chat_row.addWidget(button)
        side.addLayout(chat_row)

        side.addWidget(QLabel("<b>Projects</b>"))
        self.project_combo = QComboBox(); self.project_combo.currentIndexChanged.connect(self._project_changed)
        side.addWidget(self.project_combo)
        projects_button = QPushButton("Manage projects…"); projects_button.clicked.connect(self._show_projects); side.addWidget(projects_button)

        for label, fn in (
            ("Extensions", self._show_extensions), ("Models", self._show_models),
            ("Activity & Feedback", self._show_activity), ("Settings", self._show_settings), ("About", self._show_about),
        ):
            button = QPushButton(label); button.clicked.connect(fn); side.addWidget(button)
        self.sidebar = sidebar
        root_layout.addWidget(sidebar)

        main = QWidget(root)
        main_layout = QVBoxLayout(main)
        header = QHBoxLayout()
        sidebar_toggle = QPushButton("☰"); sidebar_toggle.setFixedWidth(36); sidebar_toggle.clicked.connect(lambda: self.sidebar.setVisible(not self.sidebar.isVisible()))
        self.header_title = QLabel("<b>New chat</b>")
        self.endpoint_label = QLabel("AUTO")
        self.health_label = QLabel("Server: unknown")
        self.model_label = QLabel(Path(self.config.model_path).name if self.config.model_path else "No model")
        header.addWidget(sidebar_toggle); header.addWidget(self.header_title); header.addStretch()
        header.addWidget(self.model_label); header.addWidget(self.endpoint_label); header.addWidget(self.health_label)
        main_layout.addLayout(header)

        self.banner = QLabel(""); self.banner.setWordWrap(True); self.banner.setStyleSheet("background:#4b3513;padding:8px;border-radius:6px;"); self.banner.hide()
        main_layout.addWidget(self.banner)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, main)
        self.transcript = TranscriptWidget(self.splitter)
        self.right_tabs = QTabWidget(self.splitter)
        self.sources_panel = SourcesPanel(); self.code_panel = CodePanel()
        self.artifact_panel = QWidget(); self.artifact_layout = QVBoxLayout(self.artifact_panel); self.artifact_layout.addWidget(QLabel("Generated tables, charts and diffs can open here."))
        self.right_tabs.addTab(self.sources_panel, "Sources")
        self.right_tabs.addTab(self.code_panel, "Code")
        self.right_tabs.addTab(self.artifact_panel, "Artifact")
        self.splitter.addWidget(self.transcript); self.splitter.addWidget(self.right_tabs)
        self.splitter.setSizes([760, 420])
        main_layout.addWidget(self.splitter, 1)

        self.composer = ComposerWidget(main)
        self.composer.send_requested.connect(self._send)
        self.composer.stop_requested.connect(lambda: self.chat_worker.stop())
        self.composer.attach_requested.connect(self._attach_files)
        self.composer.plus_requested.connect(self._plus_menu)
        main_layout.addWidget(self.composer)
        root_layout.addWidget(main, 1)

        self.server_console = ServerConsoleWidget(self)
        self.server_console.restart_requested.connect(self._restart_server)
        self.server_console.start_requested.connect(self._maybe_start_server)
        self.server_console.stop_requested.connect(self._stop_server)
        self.bridge.server_log.connect(self.server_console.append_line)
        self.server_dock = QDockWidget("Server console", self)
        self.server_dock.setWidget(self.server_console)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.server_dock)
        self.server_dock.hide()

        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self._new_conversation)
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(self._show_settings)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self).activated.connect(lambda: self.server_dock.setVisible(not self.server_dock.isVisible()))
        QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(lambda: self.search.setFocus())
        self.code_panel.refresh_git.connect(self._refresh_git)
        self.code_panel.run_tests.connect(self._run_test_profile)
        self._refresh_chips()

    def _toast(self, text: str) -> None:
        self.toast.show_message(text)
        self.statusBar().showMessage(text, 5000)

    def _show_banner(self, text: str) -> None:
        if text:
            self.banner.setText(text); self.banner.show()
        else:
            self.banner.hide()

    # ---------- background ----------
    def _run_background(self, function, on_success=None, on_failure=None) -> None:
        thread = QThread(self); worker = FunctionWorker(function); worker.moveToThread(thread)
        pair = (thread, worker); self._background.append(pair)
        thread.started.connect(worker.run)
        worker.finished.connect(lambda result: on_success and on_success(result))
        worker.failed.connect(lambda error: on_failure(error) if on_failure else self._show_banner(error))
        worker.finished.connect(thread.quit); worker.failed.connect(thread.quit)
        thread.finished.connect(lambda: self._background.remove(pair) if pair in self._background else None)
        thread.start()

    # ---------- conversations/projects ----------
    def _current_conversation_id(self) -> str:
        item = self.conversations.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    def _refresh_conversations(self) -> None:
        current = self._current_conversation_id() if hasattr(self, "conversations") else ""
        query = self.search.text().strip() if hasattr(self, "search") else ""
        rows = self.history.search_conversations(query) if query else self.history.list_conversations()
        self.conversations.blockSignals(True); self.conversations.clear()
        selected = -1
        for index, row in enumerate(rows):
            prefix = "📌 " if row.get("pinned") else ""
            item = QListWidgetItem(prefix + str(row["title"]))
            item.setData(Qt.ItemDataRole.UserRole, row["id"]); self.conversations.addItem(item)
            if row["id"] == current: selected = index
        self.conversations.blockSignals(False)
        if selected >= 0: self.conversations.setCurrentRow(selected)

    def _new_conversation(self) -> None:
        project_id = self._selected_project_id() if hasattr(self, "project_combo") else None
        conversation = self.history.create_conversation(project_id=project_id or None)
        self._refresh_conversations()
        for index in range(self.conversations.count()):
            if self.conversations.item(index).data(Qt.ItemDataRole.UserRole) == conversation:
                self.conversations.setCurrentRow(index); break

    def _conversation_changed(self) -> None:
        conversation_id = self._current_conversation_id()
        if not conversation_id: return
        row = next((item for item in self.history.list_conversations(include_archived=True) if item["id"] == conversation_id), None)
        self.header_title.setText(f"<b>{row['title'] if row else 'Chat'}</b>")
        if row and row.get("project_id"):
            index = self.project_combo.findData(row["project_id"])
            if index >= 0:
                self.project_combo.blockSignals(True); self.project_combo.setCurrentIndex(index); self.project_combo.blockSignals(False)
        self._load_conversation(conversation_id)
        self._refresh_code_panel()

    def _load_conversation(self, conversation_id: str) -> None:
        self.transcript.clear_transcript(); self._current_sources = []; self.sources_panel.set_sources([])
        rows = self.history.list_messages(conversation_id)[-100:]
        tool_index = 0
        for row in rows:
            if row["role"] in {"user", "assistant"}:
                widget = self.transcript.add_message(row["role"], row["content"], row.get("reasoning", ""))
                self._connect_message(widget)
                for call in row.get("tool_calls") or []:
                    tool_index += 1
                    function = call.get("function") or {}
                    self.transcript.add_tool_card(str(call.get("id") or tool_index), str(function.get("name") or "tool"), function.get("arguments", ""))
            elif row["role"] == "tool":
                result = ToolExecutionResult(
                    tool_call_id=str(row.get("tool_call_id") or tool_index), name="tool",
                    content_for_model=row["content"], summary=row["content"][:240], raw=row["content"],
                )
                self.transcript.finish_tool(result)
        self.transcript.refresh_item_sizes()

    def _rename_conversation(self) -> None:
        conversation_id = self._current_conversation_id()
        if not conversation_id: return
        name, ok = QInputDialog.getText(self, "Rename chat", "Title:")
        if ok and name.strip(): self.history.rename_conversation(conversation_id, name.strip()); self._refresh_conversations(); self._conversation_changed()

    def _pin_conversation(self) -> None:
        conversation_id = self._current_conversation_id()
        if not conversation_id: return
        row = next(item for item in self.history.list_conversations(include_archived=True) if item["id"] == conversation_id)
        self.history.set_conversation_flags(conversation_id, pinned=not bool(row["pinned"])); self._refresh_conversations()

    def _archive_conversation(self) -> None:
        conversation_id = self._current_conversation_id()
        if conversation_id: self.history.set_conversation_flags(conversation_id, archived=True); self._refresh_conversations()

    def _export_conversation(self) -> None:
        conversation_id = self._current_conversation_id()
        if not conversation_id: return
        filename, _ = QFileDialog.getSaveFileName(self, "Export conversation", "vision-chat.md", "Markdown (*.md);;JSON (*.json)")
        if not filename: return
        rows = self.history.list_messages(conversation_id)
        if filename.lower().endswith(".json"):
            Path(filename).write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            lines = ["# VISION conversation", ""]
            for row in rows:
                lines.extend([f"## {row['role'].title()}", "", row["content"], ""])
            Path(filename).write_text("\n".join(lines), encoding="utf-8")

    def _selected_project_id(self) -> str:
        return str(self.project_combo.currentData() or "") if hasattr(self, "project_combo") else ""

    def _refresh_projects(self) -> None:
        current = self._selected_project_id()
        self.project_combo.blockSignals(True); self.project_combo.clear(); self.project_combo.addItem("No project", "")
        for project in self.projects.list_projects():
            self.project_combo.addItem(project["name"], project["id"])
        index = self.project_combo.findData(current or self.config.active_project_id)
        if index >= 0: self.project_combo.setCurrentIndex(index)
        self.project_combo.blockSignals(False); self._refresh_code_panel(); self._refresh_chips()

    def _project_changed(self) -> None:
        project_id = self._selected_project_id()
        self.config.active_project_id = project_id; self.config.save(self.paths)
        conversation_id = self._current_conversation_id()
        if conversation_id: self.projects.assign_conversation(conversation_id, project_id or None)
        self._refresh_code_panel(); self._refresh_chips()

    def _active_project(self) -> dict | None:
        project_id = self._selected_project_id()
        return self.projects.get_project(project_id) if project_id else None

    def _refresh_code_panel(self) -> None:
        if not hasattr(self, "code_panel"): return
        project = self._active_project()
        self.code_panel.set_root(Path(project["root_path"]) if project else None)
        self.code_panel.set_profiles([profile["name"] for profile in self.projects.list_test_profiles(project["id"])] if project else [])

    def _refresh_chips(self) -> None:
        if not hasattr(self, "composer"): return
        project = self._active_project()
        chips = [f"Workspace: {self.paths.workspace}"]
        if project: chips.append(f"Project: {project['name']}")
        skills = self._enabled_skill_names()
        if skills: chips.append("Skills: " + ", ".join(sorted(skills)))
        if self._dynamic_names: chips.append(f"Extension tools: {len(self._dynamic_names)}")
        self.composer.set_chips(chips)

    def _enabled_skill_names(self) -> set[str]:
        project_id = self._selected_project_id()
        if project_id:
            project_enabled = self.projects.enabled_extensions(project_id, "skill")
            if project_enabled: return project_enabled
        return set(self.config.enabled_skills)

    # ---------- sending ----------
    def _connect_message(self, widget) -> None:
        if getattr(widget, "_vision_connected", False): return
        widget._vision_connected = True
        widget.regenerate_requested.connect(self._regenerate)
        widget.thumbs_down.connect(lambda: self.controller.record_feedback(self._current_conversation_id(), detail="Thumbs down"))
        widget.correct_requested.connect(self._correct_message)

    def _correct_message(self) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "Correct this", "What should VISION have done?")
        if ok and text.strip():
            self.controller.record_feedback(self._current_conversation_id(), detail=text.strip()); self._toast("Correction saved locally")

    def _regenerate(self) -> None:
        if self.composer._running: return
        # Prepare a request context identical in shape to a fresh send.
        self._prepare_and_start("", [], regenerate=True)

    def _attach_files(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(self, "Attach text files")
        if filenames: self.composer.set_attachments([Path(name) for name in filenames])

    def _plus_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Attach files…", self._attach_files)
        skills_menu = menu.addMenu("Use skill")
        for package in self.skill_manager.discover():
            skills_menu.addAction(package.name, lambda name=package.name: self.composer.insert_text(f"/{name} " + self.composer.edit.toPlainText()))
        menu.addAction("Attach MCP resource…", self._pick_mcp_resource)
        menu.addAction("Use MCP prompt…", self._pick_mcp_prompt)
        menu.exec(self.composer.plus_button.mapToGlobal(self.composer.plus_button.rect().bottomLeft()))

    def _pick_mcp_resource(self) -> None:
        options: list[tuple[str, str, str]] = []
        for server_id, resources in self.extension_host.manager.resources.items():
            for resource in resources:
                uri = str(resource.get("uri") if isinstance(resource, dict) else getattr(resource, "uri", ""))
                if uri: options.append((f"{server_id}: {uri}", server_id, uri))
        if not options: QMessageBox.information(self, "MCP resources", "No discovered MCP resources. Test/discover a server in Extensions first."); return
        label, ok = QInputDialog.getItem(self, "MCP resource", "Resource:", [o[0] for o in options], editable=False)
        if not ok: return
        _label, server_id, uri = next(o for o in options if o[0] == label)
        self._run_background(lambda: self.extension_host.read_resource(server_id, uri), lambda result: self._pending_context_blocks.append(f"MCP resource {uri}:\n{str(result)[:20000]}") or self._toast("Resource attached to next message"))

    def _pick_mcp_prompt(self) -> None:
        options: list[tuple[str, str, str]] = []
        for server_id, prompts in self.extension_host.manager.prompts.items():
            for prompt in prompts:
                name = str(prompt.get("name") if isinstance(prompt, dict) else getattr(prompt, "name", ""))
                if name: options.append((f"{server_id}: {name}", server_id, name))
        if not options: QMessageBox.information(self, "MCP prompts", "No discovered MCP prompts. Test/discover a server in Extensions first."); return
        label, ok = QInputDialog.getItem(self, "MCP prompt", "Prompt:", [o[0] for o in options], editable=False)
        if not ok: return
        _label, server_id, name = next(o for o in options if o[0] == label)
        self._run_background(lambda: self.extension_host.get_prompt(server_id, name), lambda result: self.composer.insert_text(str(result)[:20000]))

    def _send(self, text: str, attachments: list[Path]) -> None:
        self._prepare_and_start(text, attachments, regenerate=False)

    def _prepare_and_start(self, text: str, attachments: list[Path], *, regenerate: bool) -> None:
        conversation_id = self._current_conversation_id()
        if not conversation_id:
            self._new_conversation(); conversation_id = self._current_conversation_id()
        project = self._active_project()
        pending_context = list(self._pending_context_blocks); self._pending_context_blocks.clear()
        skills_enabled = self._enabled_skill_names()
        dynamic_names = set(self._dynamic_names)

        def prepare() -> dict[str, Any]:
            final_text = text
            for attachment in attachments:
                if attachment.stat().st_size > 256 * 1024: raise RuntimeError(f"Attachment exceeds 256 KB: {attachment.name}")
                content = attachment.read_text(encoding="utf-8")
                final_text += f"\n\nAttachment: {attachment.name}\n```\n{content}\n```"
            context_blocks: list[str] = []
            sources: list[dict] = []
            if project:
                if project.get("instructions"):
                    context_blocks.append("Project instructions (user-supplied, below system/safety):\n" + str(project["instructions"]))
                knowledge_context, knowledge_sources = build_project_context(self.services.knowledge, project["id"], final_text)
                if knowledge_context: context_blocks.append(knowledge_context); sources.extend(knowledge_sources)
            metadata = self.skill_manager.metadata_prompt(skills_enabled)
            if metadata: context_blocks.append(metadata)
            # Explicit /skill or @skill loads full body in addition to skill_load.
            for package in self.skill_manager.discover():
                if package.name in skills_enabled and (f"/{package.name}" in final_text or f"@{package.name}" in final_text):
                    loaded = self.skill_manager.load(package.name)
                    context_blocks.append(f"Activated skill {package.name} instructions (untrusted workflow guidance):\n{loaded['instructions']}")
            context_blocks.extend(pending_context)
            return {"text": final_text, "system_context": "\n\n".join(context_blocks), "sources": sources}

        def start(prepared: dict[str, Any]) -> None:
            if not regenerate:
                widget = self.transcript.add_message("user", prepared["text"] if len(prepared["text"]) < 5000 else text + "\n\n[Attachment content included in request]")
                self._connect_message(widget)
            self.transcript.start_assistant(); self._connect_message(self.transcript.current_assistant)
            self._current_sources = list(prepared["sources"]); self.sources_panel.set_sources(self._current_sources)
            tools = self.registry.openai_tools(include_skill_load=bool(skills_enabled), dynamic_names=dynamic_names)
            max_dynamic = max(0, self.config.max_extension_tools)
            builtins = 29 if skills_enabled else 28
            tools = tools[:builtins + max_dynamic]
            self.chat_worker.submit(
                conversation_id=conversation_id,
                user_text=prepared["text"],
                tools=tools,
                system_context=prepared["system_context"],
                pre_sources=prepared["sources"],
            )
            self.composer.set_running(True)
            if regenerate: self.regenerate_requested.emit()
            else: self.run_chat_requested.emit()

        self._run_background(prepare, start)

    def _on_content(self, text: str) -> None:
        self.transcript.append_content(text)
        if self.transcript.current_assistant: self._connect_message(self.transcript.current_assistant)

    def _on_reasoning(self, text: str) -> None:
        self.transcript.append_reasoning(text)

    def _on_tool_start(self, payload: dict) -> None:
        key = str(payload.get("id") or f"{payload.get('name')}-{len(self.transcript.tool_cards)}")
        self.transcript.add_tool_card(key, str(payload.get("name") or "tool"), payload.get("arguments", ""))

    def _on_tool_result(self, result: ToolExecutionResult) -> None:
        self.transcript.finish_tool(result)
        if result.genui:
            self._show_artifact(result.genui)
        if result.name == "system_status" and isinstance(result.raw, dict):
            payload = {"kind": "cards", "title": "System status", "cards": [
                {"title": "CPU", "value": f"{result.raw.get('cpu_percent', 0)}%"},
                {"title": "RAM", "value": f"{result.raw.get('ram_percent', 0)}%"},
                {"title": "Disk free", "value": f"{result.raw.get('disk_free_gb', 0)} GB"},
            ]}
            self._show_artifact(payload)
            if isinstance(self._system_cards, CardsWidget): self.system_timer.start()

    def _show_artifact(self, payload: dict) -> None:
        while self.artifact_layout.count():
            item = self.artifact_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        widget = build_genui_widget(payload, self.artifact_panel)
        self.artifact_layout.addWidget(widget)
        self._system_cards = widget if isinstance(widget, CardsWidget) and payload.get("title") == "System status" else self._system_cards
        self.right_tabs.setCurrentWidget(self.artifact_panel)
        self.splitter.setSizes([680, 520])

    def _update_live_system_cards(self) -> None:
        if not self._system_cards or not self._system_cards.isVisible():
            self.system_timer.stop(); return
        memory = psutil.virtual_memory(); disk = shutil.disk_usage(Path.cwd().anchor or "/")
        self._system_cards.update_values({
            "CPU": f"{psutil.cpu_percent(interval=None):.1f}%",
            "RAM": f"{memory.percent:.1f}%",
            "Disk free": f"{disk.free / 1024**3:.1f} GB",
        })

    def _on_sources(self, sources: list[dict]) -> None:
        self._current_sources = list(sources); self.sources_panel.set_sources(self._current_sources)
        if sources: self.right_tabs.setCurrentWidget(self.sources_panel)

    def _on_endpoint(self, selection) -> None:
        endpoint = getattr(selection, "endpoint", None)
        if endpoint:
            label = endpoint.label; self.endpoint_label.setText(label); self.composer.set_endpoint(label)
            if getattr(selection, "failed_over", False): self._show_banner("Local server failed health checks. VISION is using the configured cloud endpoint.")
        elif getattr(selection, "error", ""):
            self._show_banner(str(selection.error))

    def _on_turn_finished(self, result) -> None:
        self.composer.set_running(False)
        conversation_id = self._current_conversation_id()
        if conversation_id:
            rows = self.history.list_conversations(include_archived=True)
            row = next((item for item in rows if item["id"] == conversation_id), None)
            if row and row["title"] == "New chat":
                prompt, _answer = self.history.last_exchange(conversation_id)
                if prompt: self.history.rename_conversation(conversation_id, prompt[:48])
        self._refresh_conversations()
        if getattr(result, "error", ""): self._show_banner(str(result.error))

    def _schedule_timer(self, minutes: float, label: str) -> None:
        QTimer.singleShot(max(1, int(minutes * 60_000)), lambda: QMessageBox.information(self, "VISION timer", label))

    # ---------- server/health ----------
    def _maybe_start_server(self) -> None:
        if self.server_manager.is_running: return
        model = Path(self.config.model_path or self.paths.model_file)
        binary = find_llama_server(self.paths, self.config.llama_server_path)
        if not model.exists(): self.health_label.setText("Server: model missing"); self.server_console.set_state("Model missing"); return
        if not binary: self.health_label.setText("Server: llama-server missing"); self.server_console.set_state("llama-server missing"); return
        try:
            self.server_manager.start(binary, model, self.config)
            self.health_label.setText("Server: starting"); self.server_console.set_state("Starting")
        except Exception as exc:
            self._show_banner(f"Could not start llama-server: {exc}")

    def _restart_server(self) -> None:
        self.server_manager.stop(); self._maybe_start_server()

    def _stop_server(self) -> None:
        self.server_manager.stop(); self.health_label.setText("Server: stopped"); self.server_console.set_state("Stopped")

    @staticmethod
    def _sync_health(url: str) -> bool:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/models", timeout=3) as response:
                return response.status == 200
        except Exception:
            return False

    def _poll_health(self) -> None:
        if self._health_check_running: return
        self._health_check_running = True
        local_url = self.config.local_base_url

        def done(healthy: bool) -> None:
            self._health_check_running = False
            if healthy:
                self._health_state.record(True); self.health_label.setText("Server: healthy"); self.server_console.set_state("Healthy"); return
            action = self._health_state.record(False)
            self.health_label.setText("Server: unhealthy"); self.server_console.set_state(f"Unhealthy ({self._health_state.failures} failures)")
            if action == "restart" and (find_llama_server(self.paths, self.config.llama_server_path) and Path(self.config.model_path).exists()):
                self._show_banner("Local server health failed twice. Restarting it once…"); self._restart_server()
            elif action == "failover" and self.config.remote_base_url:
                self._show_banner("Local server is still unhealthy. Auto mode will offer/use cloud failover on the next request.")

        def failed(_error: str) -> None:
            self._health_check_running = False

        self._run_background(lambda: self._sync_health(local_url), done, failed)

    # ---------- dialogs/actions ----------
    def _show_settings(self) -> None:
        dialog = SettingsDialog(self.config, self.paths, self.credentials, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.setStyleSheet(__import__("vision_app.ui.theme", fromlist=["stylesheet"]).stylesheet(self.config.theme))
            self.model_label.setText(Path(self.config.model_path).name)
            self._toast("Settings saved. Some server changes apply after restart.")

    def _show_models(self) -> None:
        dialog = ModelManagerDialog(self.config, self.paths, self)
        dialog.check_updates.connect(lambda: self._check_model_update_async(dialog))
        dialog.download_model.connect(lambda: self._download_model_async(dialog))
        dialog.download_llama.connect(lambda: self._download_llama_async(dialog))
        dialog.exec()

    def _check_model_update_async(self, dialog=None) -> None:
        def done(result) -> None:
            self._latest_remote_sha = str(getattr(result, "remote_sha", ""))
            if getattr(result, "status", "") in {"different", "newer_available"}:
                self._toast("New VISION build available")
            if dialog: dialog.set_status(f"Update check: {result.status}; remote {result.remote_sha[:12] or 'unknown'}; size {result.size}")
        self._run_background(lambda: check_for_update(self.config.installed_model_sha, self.credentials.get("HF_TOKEN")), done, lambda err: dialog.set_status(err) if dialog else None)

    def _download_model_async(self, dialog=None) -> None:
        existing = Path(self.config.model_path or self.paths.model_file).exists()
        def work() -> Path:
            return download_model(self.paths, token=self.credentials.get("HF_TOKEN"), pending=existing)
        def done(path: Path) -> None:
            if existing:
                self.config.pending_model_path = str(path); self.config.pending_model_sha = self._latest_remote_sha; self.config.pending_restart = True
            else:
                self.config.model_path = str(path)
                if self._latest_remote_sha: self.config.installed_model_sha = self._latest_remote_sha
            self.config.save(self.paths)
            if dialog: dialog.set_status("Model downloaded. It will activate on next launch." if existing else "Model downloaded.")
            self._maybe_start_server()
        if dialog: dialog.set_status("Downloading model… this can take a while.")
        self._run_background(work, done, lambda err: dialog.set_status(err) if dialog else self._show_banner(err))

    def _download_llama_async(self, dialog=None) -> None:
        def done(path: Path) -> None:
            self.config.llama_server_path = str(path); self.config.save(self.paths)
            if dialog: dialog.set_status(f"llama-server ready: {path}")
            self._maybe_start_server()
        if dialog: dialog.set_status("Downloading llama-server…")
        self._run_background(lambda: download_llama(self.paths, "auto"), done, lambda err: dialog.set_status(err) if dialog else self._show_banner(err))

    def _show_extensions(self) -> None:
        dialog = ExtensionsCenterDialog(self.config, self.paths, self.credentials, self.registry, self)
        dialog.changed.connect(self._refresh_extensions)
        dialog.test_mcp_requested.connect(lambda config: self._test_extension(config, dialog, connector=False))
        dialog.test_connector_requested.connect(lambda config: self._test_extension(config, dialog, connector=True))
        dialog.exec(); self._refresh_chips()

    def _extension_records(self) -> list[tuple[str, McpServerConfig]]:
        records: list[tuple[str, McpServerConfig]] = []
        for server in load_mcp_config(self.paths.mcp_config).servers:
            if server.enabled: records.append(("mcp", server))
        for connector in load_connectors(self.paths.connectors_config).connectors:
            if connector.enabled: records.append(("connector", connector_as_server(connector)))
        return records

    def _refresh_extensions(self) -> None:
        self.registry.definitions = {name: d for name, d in self.registry.definitions.items() if d.source == "builtin"}
        self._dynamic_names.clear()
        records = self._extension_records()
        if not records: self._refresh_chips(); return

        def discover_all():
            results = []
            for kind, record in records:
                try: results.append((kind, record, self.extension_host.discover(record), ""))
                except Exception as exc: results.append((kind, record, None, str(exc)))
            return results

        def register(results) -> None:
            messages = []
            for _kind, record, discovery, error in results:
                if error: messages.append(f"{record.display_name}: {error}"); continue
                if not record.toolset_sha256:
                    messages.append(f"{record.display_name}: test/discover approval required"); continue
                if discovery["fingerprint"] != record.toolset_sha256:
                    messages.append(f"{record.display_name}: toolset changed — re-approval required"); continue
                for descriptor in discovery["tools"]:
                    definition = make_mcp_tool_definition(record, descriptor, self.extension_host.call_tool_async)
                    self.registry.register_dynamic(definition); self._dynamic_names.add(definition.name)
            self._refresh_chips()
            if messages: self._show_banner("Extensions: " + "; ".join(messages[:3]))

        self._run_background(discover_all, register)

    def _test_extension(self, raw_config, dialog=None, *, connector: bool = False) -> None:
        config = connector_as_server(raw_config) if connector else raw_config
        def done(discovery) -> None:
            names = [tool.name for tool in discovery["tools"]]
            message = f"Discovered {len(names)} tool(s): {', '.join(names) or 'none'}\n\nFingerprint: {discovery['fingerprint']}\n\nTrust this exact toolset? Tools still default to Ask every time."
            if QMessageBox.question(self, "Trust MCP toolset?", message) != QMessageBox.StandardButton.Yes: return
            raw_config.toolset_sha256 = discovery["fingerprint"]
            raw_config.permissions = {tool.name: raw_config.permissions.get(tool.name, "ask") for tool in discovery["tools"]}
            if connector:
                data = load_connectors(self.paths.connectors_config); data.connectors = [raw_config if c.id == raw_config.id else c for c in data.connectors]; save_connectors(self.paths.connectors_config, data)
            else:
                data = load_mcp_config(self.paths.mcp_config); data.servers = [raw_config if s.id == raw_config.id else s for s in data.servers]; save_mcp_config(self.paths.mcp_config, data)
            if dialog: dialog.set_status("Trusted toolset saved")
            self._refresh_extensions()
        self._run_background(lambda: self.extension_host.discover(config), done, lambda err: dialog.set_status(err) if dialog else self._show_banner(err))

    def _show_projects(self) -> None:
        dialog = ProjectsDialog(self.projects, self.paths, self)
        dialog.changed.connect(lambda: (self._refresh_projects(), self._refresh_code_panel()))
        dialog.index_requested.connect(self._index_project)
        dialog.run_profile_requested.connect(lambda project_id, name: self._run_named_profile(project_id, name))
        dialog.exec(); self._refresh_projects()

    def _index_project(self, project_id: str) -> None:
        def work():
            totals = {"files": 0, "chunks": 0, "errors": []}
            for source in self.projects.list_sources(project_id, enabled_only=True):
                try:
                    result = self.services.knowledge.index_path(project_id, Path(source["path"]))
                    totals["files"] += result["files"]; totals["chunks"] += result["chunks"]; totals["errors"].extend(result["errors"])
                    self.projects.mark_source_indexed(source["id"], "; ".join(result["errors"][:2]))
                except Exception as exc:
                    self.projects.mark_source_indexed(source["id"], str(exc)); totals["errors"].append(str(exc))
            return totals
        self._run_background(work, lambda result: self._toast(f"Indexed {result['files']} file(s), {result['chunks']} chunk(s)"))

    def _run_named_profile(self, project_id: str, name: str) -> None:
        profile = next((item for item in self.projects.list_test_profiles(project_id) if item["name"] == name), None)
        if not profile: return
        self._run_background(
            lambda: run_test_profile(profile),
            lambda result: self.code_panel.output.setPlainText(f"Exit {result['exit_code']}\n{result['stdout']}\n{result['stderr']}"),
        )

    def _run_test_profile(self, name: str) -> None:
        project_id = self._selected_project_id()
        if project_id and name: self._run_named_profile(project_id, name)

    def _refresh_git(self) -> None:
        project = self._active_project()
        if not project: return
        self._run_background(lambda: git_status(Path(project["root_path"])), lambda result: self.code_panel.git_label.setText(
            f"Git: {result.get('branch') or result.get('reason', 'unknown')} · {len(result.get('entries', []))} change(s)" if result.get("available") else f"Git: {result.get('reason', 'unavailable')}"
        ))

    def _show_activity(self) -> None:
        dialog = QDialog(self); dialog.setWindowTitle("Activity & Feedback"); dialog.resize(760, 560)
        layout = QVBoxLayout(dialog); tabs = QTabWidget(dialog); layout.addWidget(tabs)
        audit = QPlainTextEdit(); audit.setReadOnly(True)
        if self.paths.audit_log.exists(): audit.setPlainText("\n".join(self.paths.audit_log.read_text(encoding="utf-8").splitlines()[-300:]))
        tabs.addTab(audit, "Audit log")
        feedback = QPlainTextEdit(); feedback.setReadOnly(True)
        feedback.setPlainText(json.dumps(self.needs_log.read_all(), indent=2, ensure_ascii=False))
        tabs.addTab(feedback, "Feedback queue (needs.jsonl)")
        row = QHBoxLayout()
        export_button = QPushButton("Export feedback…")
        upload_button = QPushButton("Upload now")
        clear_button = QPushButton("Clear feedback")
        def export_feedback():
            filename, _ = QFileDialog.getSaveFileName(dialog, "Export feedback", "feedback.jsonl", "JSONL (*.jsonl)")
            if filename:
                self.needs_log.flush(); shutil.copyfile(self.paths.needs_log, filename) if self.paths.needs_log.exists() else Path(filename).write_text("")
        export_button.clicked.connect(export_feedback)
        upload_button.clicked.connect(lambda: self._run_background(lambda: upload_needs_log(self.needs_log, self.credentials.get("HF_TOKEN")), lambda ok: self._toast("Feedback uploaded" if ok else "Feedback upload failed; local queue retained")))
        def clear_feedback():
            if QMessageBox.question(dialog, "Clear feedback?", "Delete all pending local feedback?") == QMessageBox.StandardButton.Yes:
                self.needs_log.clear(); feedback.setPlainText("[]")
        clear_button.clicked.connect(clear_feedback)
        row.addWidget(export_button); row.addWidget(upload_button); row.addWidget(clear_button); row.addStretch(); layout.addLayout(row)
        dialog.exec()

    def _show_about(self) -> None:
        QMessageBox.about(self, "About VISION", f"VISION Desktop\nVersion {__version__}\nBuild {BUILD_COMMIT}\n\n{CREDITS_LINE}")

    def _show_wizard(self) -> None:
        def health() -> bool:
            self._maybe_start_server()
            deadline = time.time() + 120
            while time.time() < deadline:
                if self._sync_health(self.config.local_base_url): return True
                if self.server_manager.process is not None and self.server_manager.process.poll() is not None:
                    raise RuntimeError("llama-server exited. Open the server console for details.")
                time.sleep(1)
            raise RuntimeError("Timed out waiting for llama-server health")
        wizard = FirstRunWizard(
            self.config, self.paths,
            download_model=lambda: download_model(self.paths, token=self.credentials.get("HF_TOKEN")),
            download_llama=lambda: download_llama(self.paths, "auto"),
            health_check=health,
            parent=self,
        )
        wizard.exec(); self._maybe_start_server()

    def closeEvent(self, event) -> None:
        try:
            self.needs_log.flush()
            if self.config.telemetry_opt_in:
                upload_in_background(self.needs_log, self.credentials.get("HF_TOKEN"))
            self.chat_worker.stop()
            self.chat_thread.quit(); self.chat_thread.wait(2000)
            self.server_manager.stop()
            self.extension_host.stop()
        finally:
            super().closeEvent(event)

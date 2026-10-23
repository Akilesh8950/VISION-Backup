from __future__ import annotations

import json
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..credentials import CredentialStore
from ..extensions.connectors import ConnectorConfig, load_connectors, save_connectors
from ..extensions.mcp_config import (
    McpServerConfig,
    export_mcp_servers,
    import_mcp_servers,
    load_mcp_config,
    save_mcp_config,
)
from ..extensions.skill_import import export_skill, import_skill
from ..extensions.skills import SkillManager
from ..paths import AppPaths
from ..tools.registry import ToolRegistry
from ..tools.schemas import ORIGINAL_27_TOOLS, TOOL_DESCRIPTIONS


class ExtensionsCenterDialog(QDialog):
    changed = Signal()
    test_mcp_requested = Signal(object)
    test_connector_requested = Signal(object)

    def __init__(self, config: AppConfig, paths: AppPaths, credentials: CredentialStore, registry: ToolRegistry | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Extensions")
        self.resize(820, 620)
        self.config, self.paths, self.credentials = config, paths, credentials
        self.skill_manager = SkillManager(paths.skills_dir)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)
        self._builtins_tab()
        self._skills_tab()
        self._mcp_tab()
        self._connectors_tab()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject); buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _builtins_tab(self) -> None:
        widget = QWidget(); layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("The original 27 built-in tools are declared on every request. Approved read_url is also built in."))
        list_widget = QListWidget(widget)
        for name in ORIGINAL_27_TOOLS + ["read_url"]:
            list_widget.addItem(f"{name} — {TOOL_DESCRIPTIONS[name]}")
        layout.addWidget(list_widget)
        self.tabs.addTab(widget, "Built-in tools")

    def _skills_tab(self) -> None:
        widget = QWidget(); layout = QVBoxLayout(widget)
        row = QHBoxLayout()
        for label, fn in (("Import…", self._import_skill), ("Export…", self._export_skill), ("Remove", self._remove_skill), ("Refresh", self._refresh_skills)):
            button = QPushButton(label); button.clicked.connect(fn); row.addWidget(button)
        row.addStretch(); layout.addLayout(row)
        self.skills_list = QListWidget(widget)
        self.skills_list.itemChanged.connect(self._skill_enabled_changed)
        self.skills_list.currentItemChanged.connect(self._show_skill)
        layout.addWidget(self.skills_list)
        self.skill_details = QPlainTextEdit(widget); self.skill_details.setReadOnly(True); layout.addWidget(self.skill_details)
        self.tabs.addTab(widget, "Skills")
        self._refresh_skills()

    def _refresh_skills(self) -> None:
        if not hasattr(self, "skills_list"): return
        self.skills_list.blockSignals(True); self.skills_list.clear()
        enabled = set(self.config.enabled_skills)
        for package in self.skill_manager.discover():
            item = QListWidgetItem(f"{package.name} — {package.metadata.description}")
            item.setData(Qt.ItemDataRole.UserRole, package.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if package.name in enabled else Qt.CheckState.Unchecked)
            self.skills_list.addItem(item)
        for name, error in self.skill_manager.errors.items():
            self.skills_list.addItem(f"{name} — INVALID: {error}")
        self.skills_list.blockSignals(False)

    def _selected_skill(self) -> str:
        item = self.skills_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    def _show_skill(self) -> None:
        name = self._selected_skill()
        if not name: return
        try:
            package = self.skill_manager.get(name)
            self.skill_details.setPlainText("Files:\n" + "\n".join(package.files) + "\n\nInstructions:\n" + package.body[:12000])
        except Exception as exc:
            self.skill_details.setPlainText(str(exc))

    def _skill_enabled_changed(self, item: QListWidgetItem) -> None:
        name = str(item.data(Qt.ItemDataRole.UserRole))
        enabled = set(self.config.enabled_skills)
        if item.checkState() == Qt.CheckState.Checked: enabled.add(name)
        else: enabled.discard(name)
        self.config.enabled_skills = sorted(enabled); self.config.save(self.paths); self.changed.emit()

    def _import_skill(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Import skill", "", "Skills (*.zip *.skill *.md);;All files (*)")
        if not filename:
            folder = QFileDialog.getExistingDirectory(self, "Or choose a skill folder")
            if not folder: return
            filename = folder
        path = Path(filename)
        try:
            try:
                installed = import_skill(path, self.paths.skills_dir)
            except Exception:
                if path.suffix.lower() != ".md": raise
                name, ok = QInputDialog.getText(self, "Skill name", "Name (lowercase-with-hyphens):")
                if not ok: return
                description, ok = QInputDialog.getText(self, "Skill description", "What it does and when to use it:")
                if not ok: return
                installed = import_skill(path, self.paths.skills_dir, plain_name=name, plain_description=description)
            QMessageBox.information(self, "Skill imported", f"Installed {installed.name}. Nothing was executed.")
            self._refresh_skills(); self.changed.emit()
        except FileExistsError:
            if QMessageBox.question(self, "Replace skill?", "This skill already exists. Replace it after previewing the new package?") == QMessageBox.StandardButton.Yes:
                installed = import_skill(path, self.paths.skills_dir, overwrite=True)
                self._refresh_skills(); self.changed.emit()
        except Exception as exc:
            QMessageBox.warning(self, "Import failed", str(exc))

    def _export_skill(self) -> None:
        name = self._selected_skill()
        if not name: return
        filename, _ = QFileDialog.getSaveFileName(self, "Export skill", f"{name}.skill", "Skill (*.skill);;ZIP (*.zip)")
        if filename: export_skill(self.paths.skills_dir / name, Path(filename))

    def _remove_skill(self) -> None:
        name = self._selected_skill()
        if not name: return
        if QMessageBox.question(self, "Remove skill?", f"Remove installed skill '{name}'?") == QMessageBox.StandardButton.Yes:
            shutil.rmtree(self.paths.skills_dir / name, ignore_errors=True)
            self.config.enabled_skills = [item for item in self.config.enabled_skills if item != name]
            self.config.save(self.paths); self._refresh_skills(); self.changed.emit()

    def _mcp_tab(self) -> None:
        widget = QWidget(); layout = QVBoxLayout(widget)
        row = QHBoxLayout()
        for label, fn in (("Add…", self._add_mcp), ("Remove", self._remove_mcp), ("Test/discover", self._test_mcp), ("Import JSON…", self._import_mcp), ("Export JSON…", self._export_mcp)):
            button = QPushButton(label); button.clicked.connect(fn); row.addWidget(button)
        row.addStretch(); layout.addLayout(row)
        self.mcp_list = QListWidget(widget); self.mcp_list.itemChanged.connect(self._mcp_enabled_changed); layout.addWidget(self.mcp_list)
        self.mcp_status = QLabel("External tools default to Ask every time. Changed toolsets require re-approval."); self.mcp_status.setWordWrap(True); layout.addWidget(self.mcp_status)
        self.tabs.addTab(widget, "MCP Servers")
        self._refresh_mcp()

    def _refresh_mcp(self) -> None:
        if not hasattr(self, "mcp_list"): return
        data = load_mcp_config(self.paths.mcp_config)
        enabled = set(self.config.enabled_mcp_servers)
        self.mcp_list.blockSignals(True); self.mcp_list.clear()
        for server in data.servers:
            target = server.url if server.transport != "stdio" else f"{server.command} {' '.join(server.args)}"
            item = QListWidgetItem(f"{server.display_name} [{server.transport}] — {target}")
            item.setData(Qt.ItemDataRole.UserRole, server.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if server.id in enabled and server.enabled else Qt.CheckState.Unchecked)
            self.mcp_list.addItem(item)
        self.mcp_list.blockSignals(False)

    def _selected_mcp(self) -> McpServerConfig | None:
        item = self.mcp_list.currentItem(); 
        if not item: return None
        server_id = str(item.data(Qt.ItemDataRole.UserRole))
        return next((s for s in load_mcp_config(self.paths.mcp_config).servers if s.id == server_id), None)

    def _mcp_enabled_changed(self, item: QListWidgetItem) -> None:
        server_id = str(item.data(Qt.ItemDataRole.UserRole)); data = load_mcp_config(self.paths.mcp_config)
        enabled = item.checkState() == Qt.CheckState.Checked
        for server in data.servers:
            if server.id == server_id: server.enabled = enabled
        save_mcp_config(self.paths.mcp_config, data)
        values = set(self.config.enabled_mcp_servers); values.add(server_id) if enabled else values.discard(server_id)
        self.config.enabled_mcp_servers = sorted(values); self.config.save(self.paths); self.changed.emit()

    def _add_mcp(self) -> None:
        dialog = QDialog(self); dialog.setWindowTitle("Add MCP server"); form = QFormLayout(dialog)
        name = QLineEdit(); transport = QComboBox(); transport.addItems(["stdio", "streamable_http", "sse"])
        command = QLineEdit(); args = QLineEdit(); url = QLineEdit(); token = QLineEdit()
        token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Display name", name); form.addRow("Transport", transport); form.addRow("Command (stdio)", command); form.addRow("Args (space-separated display only; stored as a list)", args); form.addRow("URL", url); form.addRow("Bearer token (optional)", token)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel); form.addRow(buttons)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted or not name.text().strip(): return
        import shlex
        config = McpServerConfig(id=name.text(), display_name=name.text().strip(), transport=transport.currentText(), command=command.text().strip(), args=shlex.split(args.text()) if args.text().strip() else [], url=url.text().strip())
        if token.text():
            key = f"MCP/{config.id}/header/Authorization"; config.header_refs["Authorization"] = key; self.credentials.set(key, token.text() if token.text().startswith("Bearer ") else "Bearer " + token.text())
        data = load_mcp_config(self.paths.mcp_config); data.servers = [s for s in data.servers if s.id != config.id] + [config]; save_mcp_config(self.paths.mcp_config, data)
        self._refresh_mcp(); self.changed.emit()

    def _remove_mcp(self) -> None:
        server = self._selected_mcp()
        if not server: return
        data = load_mcp_config(self.paths.mcp_config); data.servers = [s for s in data.servers if s.id != server.id]; save_mcp_config(self.paths.mcp_config, data)
        self.config.enabled_mcp_servers = [x for x in self.config.enabled_mcp_servers if x != server.id]; self.config.save(self.paths); self._refresh_mcp(); self.changed.emit()

    def _test_mcp(self) -> None:
        server = self._selected_mcp()
        if server: self.test_mcp_requested.emit(server)

    def _import_mcp(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Import MCP JSON", "", "JSON (*.json)")
        if not filename: return
        try:
            configs, secrets = import_mcp_servers(json.loads(Path(filename).read_text(encoding="utf-8")))
            for key, value in secrets.items(): self.credentials.set(key, value)
            data = load_mcp_config(self.paths.mcp_config); ids = {c.id for c in configs}; data.servers = [s for s in data.servers if s.id not in ids] + configs; save_mcp_config(self.paths.mcp_config, data)
            self._refresh_mcp(); self.changed.emit(); QMessageBox.information(self, "Imported", f"Imported {len(configs)} server(s), disabled by default.")
        except Exception as exc: QMessageBox.warning(self, "Import failed", str(exc))

    def _export_mcp(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Export MCP JSON", "mcp-servers.json", "JSON (*.json)")
        if filename: Path(filename).write_text(json.dumps(export_mcp_servers(load_mcp_config(self.paths.mcp_config).servers), indent=2), encoding="utf-8")

    def _connectors_tab(self) -> None:
        widget = QWidget(); layout = QVBoxLayout(widget)
        row = QHBoxLayout()
        for label, fn in (("Add custom connector…", self._add_connector), ("Remove/revoke", self._remove_connector), ("Test/discover", self._test_connector)):
            button = QPushButton(label); button.clicked.connect(fn); row.addWidget(button)
        row.addStretch(); layout.addLayout(row)
        self.connector_list = QListWidget(widget); self.connector_list.itemChanged.connect(self._connector_enabled_changed); layout.addWidget(self.connector_list)
        label = QLabel("Baseline connectors are named remote MCP endpoints. OAuth/named service adapters are not bundled."); label.setWordWrap(True); layout.addWidget(label)
        self.tabs.addTab(widget, "Connectors")
        self._refresh_connectors()

    def _refresh_connectors(self) -> None:
        if not hasattr(self, "connector_list"): return
        values = set(self.config.enabled_connectors); self.connector_list.blockSignals(True); self.connector_list.clear()
        for connector in load_connectors(self.paths.connectors_config).connectors:
            item = QListWidgetItem(f"{connector.display_name} — {connector.url} ({connector.auth_type})")
            item.setData(Qt.ItemDataRole.UserRole, connector.id); item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if connector.id in values and connector.enabled else Qt.CheckState.Unchecked); self.connector_list.addItem(item)
        self.connector_list.blockSignals(False)

    def _selected_connector(self) -> ConnectorConfig | None:
        item = self.connector_list.currentItem()
        if not item: return None
        connector_id = str(item.data(Qt.ItemDataRole.UserRole))
        return next((c for c in load_connectors(self.paths.connectors_config).connectors if c.id == connector_id), None)

    def _connector_enabled_changed(self, item: QListWidgetItem) -> None:
        connector_id = str(item.data(Qt.ItemDataRole.UserRole)); enabled = item.checkState() == Qt.CheckState.Checked
        data = load_connectors(self.paths.connectors_config)
        for connector in data.connectors:
            if connector.id == connector_id: connector.enabled = enabled
        save_connectors(self.paths.connectors_config, data)
        values = set(self.config.enabled_connectors); values.add(connector_id) if enabled else values.discard(connector_id)
        self.config.enabled_connectors = sorted(values); self.config.save(self.paths); self.changed.emit()

    def _add_connector(self) -> None:
        dialog = QDialog(self); dialog.setWindowTitle("Add custom connector"); form = QFormLayout(dialog)
        name = QLineEdit(); url = QLineEdit(); auth = QComboBox(); auth.addItems(["none", "api_key", "bearer"]); token = QLineEdit()
        token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Display name", name); form.addRow("Remote MCP URL", url); form.addRow("Auth", auth); form.addRow("Token/API key", token)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel); form.addRow(buttons); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted or not name.text().strip() or not url.text().strip(): return
        connector = ConnectorConfig(id=name.text(), display_name=name.text().strip(), transport="streamable_http", url=url.text().strip(), auth_type=auth.currentText())
        if token.text():
            key = f"CONNECTOR/{connector.id}/token"
            header = "Authorization" if connector.auth_type == "bearer" else "X-API-Key"
            connector.header_refs[header] = key; self.credentials.set(key, "Bearer " + token.text() if header == "Authorization" and not token.text().startswith("Bearer ") else token.text())
        data = load_connectors(self.paths.connectors_config); data.connectors = [c for c in data.connectors if c.id != connector.id] + [connector]; save_connectors(self.paths.connectors_config, data)
        self._refresh_connectors(); self.changed.emit()

    def _remove_connector(self) -> None:
        connector = self._selected_connector()
        if not connector: return
        for key in set(connector.header_refs.values()) | set(connector.env_refs.values()): self.credentials.delete(key)
        data = load_connectors(self.paths.connectors_config); data.connectors = [c for c in data.connectors if c.id != connector.id]; save_connectors(self.paths.connectors_config, data)
        self.config.enabled_connectors = [x for x in self.config.enabled_connectors if x != connector.id]; self.config.save(self.paths); self._refresh_connectors(); self.changed.emit()

    def _test_connector(self) -> None:
        connector = self._selected_connector()
        if connector: self.test_connector_requested.emit(connector)

    def set_status(self, text: str) -> None:
        if hasattr(self, "mcp_status"): self.mcp_status.setText(text)

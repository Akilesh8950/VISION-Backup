from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig, EndpointMode
from ..credentials import CredentialStore
from ..paths import AppPaths


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, paths: AppPaths, credentials: CredentialStore, parent=None):
        super().__init__(parent)
        self.setWindowTitle("VISION Settings")
        self.resize(680, 560)
        self.config = config
        self.paths = paths
        self.credentials = credentials

        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        layout.addWidget(tabs)

        self.mode = QComboBox(); self.mode.addItems(["auto", "local", "cloud"]); self.mode.setCurrentText(config.endpoint_mode.value)
        self.port = QSpinBox(); self.port.setRange(1, 65535); self.port.setValue(config.local_port)
        self.context = QSpinBox(); self.context.setRange(1024, 262144); self.context.setSingleStep(1024); self.context.setValue(config.context_size)
        self.gpu_slider = QSlider(Qt.Orientation.Horizontal); self.gpu_slider.setRange(0, 120); self.gpu_slider.setValue(min(config.gpu_layers, 120))
        self.gpu_value = QLabel(str(config.gpu_layers)); self.gpu_slider.valueChanged.connect(lambda v: self.gpu_value.setText(str(v)))
        self.model_path = QLineEdit(config.model_path)
        self.server_path = QLineEdit(config.llama_server_path)
        self.remote_url = QLineEdit(config.remote_base_url)
        self.remote_model = QLineEdit(config.remote_model_override)
        self.local_model = QLineEdit(config.local_model_override)
        self.theme = QComboBox(); self.theme.addItems(["dark", "light"]); self.theme.setCurrentText(config.theme)
        self.telemetry = QCheckBox("Send anonymous feedback to SPIRIT ORG"); self.telemetry.setChecked(config.telemetry_opt_in)
        self.autostart = QCheckBox("Start local server automatically when available"); self.autostart.setChecked(config.auto_start_local_server)
        self.max_tools = QSpinBox(); self.max_tools.setRange(0, 128); self.max_tools.setValue(config.max_extension_tools)

        tabs.addTab(self._model_tab(), "Model & Server")
        tabs.addTab(self._endpoint_tab(), "Endpoints")
        tabs.addTab(self._privacy_tab(), "Privacy & Feedback")
        tabs.addTab(self._appearance_tab(), "Appearance")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _path_row(self, edit: QLineEdit, file_filter: str) -> QWidget:
        widget = QWidget(); row = QHBoxLayout(widget); row.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("Browse…")
        def choose():
            filename, _ = QFileDialog.getOpenFileName(self, "Choose file", edit.text(), file_filter)
            if filename: edit.setText(filename)
        button.clicked.connect(choose)
        row.addWidget(edit, 1); row.addWidget(button)
        return widget

    def _model_tab(self) -> QWidget:
        widget = QWidget(); form = QFormLayout(widget)
        gpu = QWidget(); row = QHBoxLayout(gpu); row.setContentsMargins(0, 0, 0, 0); row.addWidget(self.gpu_slider, 1); row.addWidget(self.gpu_value)
        form.addRow("Model GGUF", self._path_row(self.model_path, "GGUF (*.gguf)"))
        form.addRow("llama-server", self._path_row(self.server_path, "Executable (*.exe);;All files (*)"))
        form.addRow("Local port", self.port)
        form.addRow("Context size", self.context)
        form.addRow("GPU layers", gpu)
        form.addRow("Local model name override", self.local_model)
        form.addRow("", self.autostart)
        return widget

    def _endpoint_tab(self) -> QWidget:
        widget = QWidget(); form = QFormLayout(widget)
        self.remote_key = QLineEdit(); self.remote_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.remote_key.setPlaceholderText("Stored" if self.credentials.get("REMOTE_API_KEY") else "Not stored")
        self.clear_remote_key = QCheckBox("Remove stored remote key")
        form.addRow("Endpoint mode", self.mode)
        form.addRow("Remote base URL (/v1)", self.remote_url)
        form.addRow("Remote model override", self.remote_model)
        form.addRow("Remote Bearer key", self.remote_key)
        form.addRow("", self.clear_remote_key)
        form.addRow("Max extension tools/request", self.max_tools)
        return widget

    def _privacy_tab(self) -> QWidget:
        widget = QWidget(); form = QFormLayout(widget)
        self.hf_token = QLineEdit(); self.hf_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.hf_token.setPlaceholderText("Stored" if self.credentials.get("HF_TOKEN") else "Not stored")
        self.clear_hf = QCheckBox("Remove stored HF token")
        form.addRow("", self.telemetry)
        form.addRow("Hugging Face token", self.hf_token)
        form.addRow("", self.clear_hf)
        form.addRow("App data", QLabel(str(self.paths.app_data)))
        form.addRow("Workspace", QLabel(str(self.paths.workspace)))
        return widget

    def _appearance_tab(self) -> QWidget:
        widget = QWidget(); form = QFormLayout(widget)
        form.addRow("Theme", self.theme)
        return widget

    def _save(self) -> None:
        c = self.config
        c.endpoint_mode = EndpointMode(self.mode.currentText())
        c.local_port = self.port.value(); c.context_size = self.context.value(); c.gpu_layers = self.gpu_slider.value()
        c.model_path = self.model_path.text().strip(); c.llama_server_path = self.server_path.text().strip()
        c.remote_base_url = self.remote_url.text().strip(); c.remote_model_override = self.remote_model.text().strip()
        c.local_model_override = self.local_model.text().strip(); c.theme = self.theme.currentText()
        c.telemetry_opt_in = self.telemetry.isChecked(); c.auto_start_local_server = self.autostart.isChecked()
        c.max_extension_tools = self.max_tools.value(); c.save(self.paths)
        if self.clear_remote_key.isChecked(): self.credentials.delete("REMOTE_API_KEY")
        elif self.remote_key.text(): self.credentials.set("REMOTE_API_KEY", self.remote_key.text())
        if self.clear_hf.isChecked(): self.credentials.delete("HF_TOKEN")
        elif self.hf_token.text(): self.credentials.set("HF_TOKEN", self.hf_token.text())
        self.accept()

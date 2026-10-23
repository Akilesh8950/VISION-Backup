from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..config import AppConfig
from ..constants import GGUF_FILENAME, MIN_LLAMA_BUILD
from ..paths import AppPaths
from ..server.discovery import build_is_supported, detect_build, find_llama_server


class ModelManagerDialog(QDialog):
    check_updates = Signal()
    download_model = Signal()
    download_llama = Signal()

    def __init__(self, config: AppConfig, paths: AppPaths, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Model Manager")
        self.resize(620, 360)
        model = Path(config.model_path or paths.model_file)
        size = model.stat().st_size if model.exists() else 0
        server = find_llama_server(paths, config.llama_server_path, require_supported=True) or find_llama_server(paths, config.llama_server_path)
        build = detect_build(server) if server else None
        build_text = ""
        if server:
            detected = f"b{build}" if build else "unknown"
            support = "supported" if build_is_supported(build) else f"unsupported; requires b{MIN_LLAMA_BUILD}+"
            build_text = f" · Build: {detected} ({support})"
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>Model</b>: {GGUF_FILENAME}"))
        layout.addWidget(QLabel(f"Path: {model}"))
        layout.addWidget(QLabel(f"Installed: {'yes' if model.exists() else 'no'} · Size: {size / 1024**3:.2f} GiB"))
        layout.addWidget(QLabel(f"Installed repo SHA: {config.installed_model_sha or 'unknown'}"))
        layout.addWidget(QLabel(f"Pending restart: {'yes' if config.pending_restart else 'no'}"))
        layout.addWidget(QLabel(f"<b>llama-server</b>: {server or 'not found'}{build_text}"))
        layout.addWidget(QLabel(f"Context: {config.context_size} · GPU layers: {config.gpu_layers} · Port: {config.local_port}"))
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        for label, signal in (("Check for updates", self.check_updates), ("Download / repair model", self.download_model), ("Download llama-server", self.download_llama)):
            button = QPushButton(label); button.clicked.connect(signal.emit); row.addWidget(button)
        close = QPushButton("Close"); close.clicked.connect(self.accept); row.addWidget(close)
        layout.addLayout(row)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

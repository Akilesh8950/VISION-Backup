from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSlider, QVBoxLayout

from ..config import AppConfig
from ..constants import MIN_LLAMA_BUILD
from ..paths import AppPaths
from ..server.discovery import build_is_supported, detect_build, find_llama_server
from .workers import FunctionWorker


class FirstRunWizard(QDialog):
    finished_setup = Signal()

    def __init__(
        self,
        config: AppConfig,
        paths: AppPaths,
        *,
        download_model: Callable[[], Path],
        download_llama: Callable[[], Path],
        health_check: Callable[[], bool],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Welcome to VISION")
        self.resize(620, 430)
        self.config, self.paths = config, paths
        self._download_model, self._download_llama, self._health_check = download_model, download_llama, health_check
        self._thread: QThread | None = None
        self._worker: FunctionWorker | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<h2>VISION</h2><p>Set up the local model. You can skip and configure everything later in Settings.</p>"))
        self.status = QLabel(""); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.progress = QProgressBar(self); self.progress.setRange(0, 0); self.progress.hide(); layout.addWidget(self.progress)

        self.model_button = QPushButton("1. Download model (5.24 GiB)"); self.model_button.clicked.connect(lambda: self._run(self._download_model, "Model downloaded"))
        self.llama_button = QPushButton("2. Download llama-server"); self.llama_button.clicked.connect(lambda: self._run(self._download_llama, "llama-server ready"))
        layout.addWidget(self.model_button); layout.addWidget(self.llama_button)

        layout.addWidget(QLabel("GPU layers (99 offloads as much as possible; lower this if the server runs out of VRAM)"))
        slider_row = QHBoxLayout(); self.gpu = QSlider(Qt.Orientation.Horizontal); self.gpu.setRange(0, 120); self.gpu.setValue(min(120, config.gpu_layers)); self.gpu_label = QLabel(str(config.gpu_layers)); self.gpu.valueChanged.connect(lambda v: self.gpu_label.setText(str(v))); slider_row.addWidget(self.gpu, 1); slider_row.addWidget(self.gpu_label); layout.addLayout(slider_row)

        self.health_button = QPushButton("3. Start / health check"); self.health_button.clicked.connect(lambda: self._run(self._health_check, "Health check complete")); layout.addWidget(self.health_button)
        row = QHBoxLayout(); skip = QPushButton("Skip for now"); done = QPushButton("Finish"); skip.clicked.connect(self._finish); done.clicked.connect(self._finish); row.addStretch(); row.addWidget(skip); row.addWidget(done); layout.addLayout(row)
        self._refresh_status()

    def _refresh_status(self) -> None:
        model = Path(self.config.model_path or self.paths.model_file)
        server = find_llama_server(self.paths, self.config.llama_server_path, require_supported=True) or find_llama_server(self.paths, self.config.llama_server_path)
        build_text = ""
        if server:
            build = detect_build(server)
            detected = f"b{build}" if build else "unknown"
            support = "supported" if build_is_supported(build) else f"unsupported; requires b{MIN_LLAMA_BUILD}+"
            build_text = f" (build {detected}, {support})"
        self.status.setText(f"Model: {'installed' if model.exists() else 'not installed'} · llama-server: {server or 'not found'}{build_text}")

    def _run(self, function, success: str) -> None:
        if self._thread and self._thread.isRunning(): return
        self.progress.show(); self.status.setText("Working…")
        self._thread = QThread(self); self._worker = FunctionWorker(function); self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(lambda _result: (self.status.setText(success), self._refresh_status(), self.progress.hide()))
        self._worker.failed.connect(lambda error: (self.status.setText(f"Failed: {error}"), self.progress.hide()))
        self._worker.finished.connect(self._thread.quit); self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def _finish(self) -> None:
        self.config.gpu_layers = self.gpu.value(); self.config.first_run_completed = True; self.config.save(self.paths)
        self.finished_setup.emit(); self.accept()

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileSystemModel,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


class CodePanel(QWidget):
    run_tests = Signal(str)
    refresh_git = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.title = QLabel("<b>Code workspace</b>")
        layout.addWidget(self.title)
        self.model = QFileSystemModel(self)
        self.tree = QTreeView(self)
        self.tree.setModel(self.model)
        self.tree.setColumnWidth(0, 250)
        layout.addWidget(self.tree)
        self.preview = QPlainTextEdit(self)
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview)
        self.git_label = QLabel("Git: not checked")
        layout.addWidget(self.git_label)
        row = QHBoxLayout()
        self.profile = QComboBox(self)
        git_button = QPushButton("Refresh Git status")
        run_button = QPushButton("Run test profile")
        row.addWidget(self.profile, 1); row.addWidget(git_button); row.addWidget(run_button)
        layout.addLayout(row)
        self.output = QPlainTextEdit(self)
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(160)
        layout.addWidget(self.output)
        git_button.clicked.connect(self.refresh_git.emit)
        run_button.clicked.connect(lambda: self.run_tests.emit(self.profile.currentText()))
        self.tree.clicked.connect(self._preview)

    def set_root(self, path: Path | None) -> None:
        if path is None:
            self.title.setText("<b>Code workspace</b> — no project selected")
            return
        self.title.setText(f"<b>Code workspace</b> — {path.name}")
        index = self.model.setRootPath(str(path))
        self.tree.setRootIndex(index)

    def set_profiles(self, names: list[str]) -> None:
        self.profile.clear(); self.profile.addItems(names)

    def _preview(self, index) -> None:
        path = Path(self.model.filePath(index))
        if path.is_file() and path.stat().st_size <= 200_000:
            try:
                self.preview.setPlainText(path.read_text(encoding="utf-8"))
                return
            except Exception:
                pass
        self.preview.setPlainText("")

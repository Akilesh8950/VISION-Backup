from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class ServerConsoleWidget(QWidget):
    restart_requested = Signal()
    stop_requested = Signal()
    start_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.state = QLabel("Server: unknown")
        row.addWidget(self.state)
        row.addStretch()
        for label, signal in (("Start", self.start_requested), ("Restart", self.restart_requested), ("Stop", self.stop_requested)):
            button = QPushButton(label)
            button.clicked.connect(signal.emit)
            row.addWidget(button)
        clear = QPushButton("Clear")
        row.addWidget(clear)
        layout.addLayout(row)
        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        layout.addWidget(self.log)
        clear.clicked.connect(self.log.clear)

    def append_line(self, line: str) -> None:
        self.log.appendPlainText(line)

    def set_lines(self, lines: list[str]) -> None:
        self.log.setPlainText("\n".join(lines[-2000:]))

    def set_state(self, text: str) -> None:
        self.state.setText(text)

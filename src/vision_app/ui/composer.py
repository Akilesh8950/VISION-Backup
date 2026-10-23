from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout


class ComposerEdit(QPlainTextEdit):
    submit_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ComposerWidget(QFrame):
    send_requested = Signal(str, object)
    stop_requested = Signal()
    attach_requested = Signal()
    plus_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.attachments: list[Path] = []
        layout = QVBoxLayout(self)
        self.chips_label = QLabel("")
        self.chips_label.setWordWrap(True)
        layout.addWidget(self.chips_label)
        self.attachment_label = QLabel("")
        self.attachment_label.setWordWrap(True)
        layout.addWidget(self.attachment_label)
        row = QHBoxLayout()
        self.plus_button = QPushButton("+")
        self.plus_button.setFixedWidth(36)
        self.attach_button = QPushButton("Attach")
        self.edit = ComposerEdit(self)
        self.edit.setPlaceholderText("Message VISION… (/ commands, @ mentions)")
        self.edit.setMinimumHeight(52)
        self.edit.setMaximumHeight(150)
        self.send_button = QPushButton("Send")
        row.addWidget(self.plus_button)
        row.addWidget(self.attach_button)
        row.addWidget(self.edit, 1)
        row.addWidget(self.send_button)
        layout.addLayout(row)
        footer = QHBoxLayout()
        footer.addWidget(QLabel("Enter send · Shift+Enter newline"))
        footer.addStretch()
        self.endpoint_chip = QLabel("AUTO")
        self.token_label = QLabel("~0 tokens est.")
        footer.addWidget(self.endpoint_chip)
        footer.addWidget(self.token_label)
        layout.addLayout(footer)

        self.edit.submit_requested.connect(self._submit)
        self.send_button.clicked.connect(self._on_send_button)
        self.attach_button.clicked.connect(self.attach_requested.emit)
        self.plus_button.clicked.connect(self.plus_requested.emit)
        self.edit.textChanged.connect(self._update_tokens)
        self._running = False

    def _update_tokens(self) -> None:
        self.token_label.setText(f"~{max(0, len(self.edit.toPlainText()) // 4)} tokens est.")

    def set_running(self, running: bool) -> None:
        self._running = running
        self.send_button.setText("Stop" if running else "Send")

    def _on_send_button(self) -> None:
        if self._running:
            self.stop_requested.emit()
        else:
            self._submit()

    def _submit(self) -> None:
        text = self.edit.toPlainText().strip()
        if not text and not self.attachments:
            return
        attachments = list(self.attachments)
        self.edit.clear()
        self.set_attachments([])
        self.send_requested.emit(text, attachments)

    def set_attachments(self, attachments: list[Path]) -> None:
        self.attachments = list(attachments)
        self.attachment_label.setText("Attachments: " + ", ".join(path.name for path in self.attachments) if self.attachments else "")

    def set_chips(self, values: list[str]) -> None:
        self.chips_label.setText("  ·  ".join(values))

    def set_endpoint(self, label: str) -> None:
        self.endpoint_chip.setText(label)

    def insert_text(self, text: str) -> None:
        self.edit.setPlainText(text)
        self.edit.setFocus()

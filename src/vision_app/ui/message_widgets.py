from __future__ import annotations

import json
import re
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..genui.factory import build_genui_widget


class CodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument):
        super().__init__(document)
        self.rules: list[tuple[re.Pattern, QTextCharFormat]] = []
        keyword = QTextCharFormat(); keyword.setForeground(QColor("#7ab8ff")); keyword.setFontWeight(QFont.Weight.Bold)
        string = QTextCharFormat(); string.setForeground(QColor("#a7d7a8"))
        comment = QTextCharFormat(); comment.setForeground(QColor("#8b949e")); comment.setFontItalic(True)
        number = QTextCharFormat(); number.setForeground(QColor("#e3b341"))
        self.rules.append((re.compile(r"\b(class|def|return|if|else|elif|for|while|try|except|with|import|from|as|pass|None|True|False|async|await)\b"), keyword))
        self.rules.append((re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\""), string))
        self.rules.append((re.compile(r"#.*"), comment))
        self.rules.append((re.compile(r"\b\d+(?:\.\d+)?\b"), number))

    def highlightBlock(self, text: str) -> None:
        for pattern, char_format in self.rules:
            for match in pattern.finditer(text or ""):
                self.setFormat(match.start(), match.end() - match.start(), char_format)


def _markdown_browser(text: str) -> QTextBrowser:
    browser = QTextBrowser()
    browser.setOpenExternalLinks(True)
    browser.setFrameShape(QFrame.Shape.NoFrame)
    browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    browser.setMarkdown(text or "")
    browser.document().adjustSize()
    browser.setFixedHeight(max(28, int(browser.document().size().height()) + 8))
    return browser


class MessageWidget(QWidget):
    regenerate_requested = Signal()
    thumbs_up = Signal()
    thumbs_down = Signal()
    correct_requested = Signal(object)

    def __init__(self, role: str, content: str = "", reasoning: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        self.content_text = content
        self.reasoning_text = reasoning
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 6, 8, 6)
        label = "You" if role == "user" else "VISION" if role == "assistant" else role.title()
        self._layout.addWidget(QLabel(f"<b>{label}</b>"))

        self.think_button = QToolButton(self)
        self.think_button.setText("Thinking")
        self.think_button.setCheckable(True)
        self.think_button.setChecked(False)
        self.think_browser = QTextBrowser(self)
        self.think_browser.setFrameShape(QFrame.Shape.NoFrame)
        self.think_browser.setStyleSheet("font-style: italic; color: #9aa3b2;")
        self.think_browser.hide()
        self.think_button.toggled.connect(self.think_browser.setVisible)
        if reasoning:
            self.think_browser.setPlainText(reasoning)
        self.think_button.setVisible(bool(reasoning))
        self._layout.addWidget(self.think_button)
        self._layout.addWidget(self.think_browser)

        self.body_container = QWidget(self)
        self.body_layout = QVBoxLayout(self.body_container)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self.body_container)
        self._rebuild_body()

        actions = QHBoxLayout()
        copy_button = QToolButton(); copy_button.setText("Copy"); copy_button.clicked.connect(self.copy_text)
        actions.addWidget(copy_button)
        if role == "assistant":
            regenerate = QToolButton(); regenerate.setText("Regenerate"); regenerate.clicked.connect(self.regenerate_requested.emit)
            up = QToolButton(); up.setText("👍"); up.clicked.connect(self.thumbs_up.emit)
            down = QToolButton(); down.setText("👎"); down.clicked.connect(self.thumbs_down.emit)
            correct = QToolButton(); correct.setText("Correct this"); correct.clicked.connect(lambda: self.correct_requested.emit(self))
            actions.addWidget(regenerate); actions.addWidget(up); actions.addWidget(down); actions.addWidget(correct)
        actions.addStretch()
        self._layout.addLayout(actions)

    def _rebuild_body(self) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        parts = re.split(r"```(\w*)\n(.*?)```", self.content_text, flags=re.DOTALL)
        if len(parts) == 1:
            self.body_layout.addWidget(_markdown_browser(self.content_text))
            return
        for index in range(0, len(parts), 3):
            text = parts[index]
            if text.strip():
                self.body_layout.addWidget(_markdown_browser(text))
            if index + 2 < len(parts):
                code = parts[index + 2]
                editor = QPlainTextEdit(code, self)
                editor.setReadOnly(True)
                editor.setFont(QFont("Cascadia Code"))
                editor.setMaximumHeight(320)
                self._highlighter = CodeHighlighter(editor.document())
                self.body_layout.addWidget(editor)

    def append_content(self, text: str) -> None:
        self.content_text += text
        self._rebuild_body()
        self.updateGeometry()

    def append_reasoning(self, text: str) -> None:
        self.reasoning_text += text
        self.think_button.setVisible(True)
        self.think_browser.setPlainText(self.reasoning_text)

    def set_content(self, text: str, reasoning: str = "") -> None:
        self.content_text = text
        if reasoning:
            self.reasoning_text = reasoning
            self.think_button.setVisible(True)
            self.think_browser.setPlainText(reasoning)
        self._rebuild_body()
        self.updateGeometry()

    def copy_text(self) -> None:
        QApplication.clipboard().setText(self.content_text)


class ToolCardWidget(QFrame):
    def __init__(self, name: str, arguments: Any = "", source: str = "builtin", parent=None):
        super().__init__(parent)
        self.name = name
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title = QLabel(f"<b>{name}</b>  <span style='color:#9aa3b2'>({source})</span>")
        self.status = QLabel("Running…")
        header.addWidget(self.title); header.addStretch(); header.addWidget(self.status)
        layout.addLayout(header)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except Exception:
                pass
        details = arguments if isinstance(arguments, str) else json.dumps(arguments, indent=2, ensure_ascii=False, default=str)
        self.details = QPlainTextEdit(details, self)
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(150)
        layout.addWidget(self.details)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.genui_container = QVBoxLayout()
        layout.addLayout(self.genui_container)
        self.genui_widget = None

    def finish(self, result: Any) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        is_error = bool(getattr(result, "is_error", False))
        self.status.setText("Failed" if is_error else "Done")
        self.summary.setText(str(getattr(result, "summary", "") or ""))
        raw = getattr(result, "raw", None)
        if raw is not None:
            current = self.details.toPlainText()
            self.details.setPlainText(current + "\n\nResult:\n" + json.dumps(raw, indent=2, ensure_ascii=False, default=str)[:12000])
        genui = getattr(result, "genui", None)
        if genui:
            try:
                self.genui_widget = build_genui_widget(genui, self)
                self.genui_container.addWidget(self.genui_widget)
            except Exception as exc:
                self.summary.setText(f"Could not render widget: {exc}")
        self.updateGeometry()

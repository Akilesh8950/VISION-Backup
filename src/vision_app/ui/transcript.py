from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget

from .message_widgets import MessageWidget, ToolCardWidget


class TranscriptWidget(QListWidget):
    """Widget-hosting transcript list; MainWindow loads history in bounded pages."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.setSpacing(6)
        self.current_assistant: MessageWidget | None = None
        self.tool_cards: dict[str, ToolCardWidget] = {}

    def _add(self, widget: QWidget) -> QWidget:
        item = QListWidgetItem(self)
        widget.adjustSize()
        item.setSizeHint(widget.sizeHint().expandedTo(QSize(600, 60)))
        self.addItem(item)
        self.setItemWidget(item, widget)
        self.scrollToBottom()
        return widget

    def refresh_item_sizes(self) -> None:
        for index in range(self.count()):
            item = self.item(index)
            widget = self.itemWidget(item)
            if widget:
                widget.adjustSize()
                item.setSizeHint(widget.sizeHint().expandedTo(QSize(600, 50)))
        self.scrollToBottom()

    def add_message(self, role: str, content: str = "", reasoning: str = "") -> MessageWidget:
        widget = MessageWidget(role, content, reasoning, self)
        self._add(widget)
        if role == "assistant":
            self.current_assistant = widget
        return widget

    def start_assistant(self) -> MessageWidget:
        self.current_assistant = self.add_message("assistant")
        return self.current_assistant

    def append_content(self, text: str) -> None:
        if self.current_assistant is None:
            self.start_assistant()
        assert self.current_assistant is not None
        self.current_assistant.append_content(text)
        self.refresh_item_sizes()

    def append_reasoning(self, text: str) -> None:
        if self.current_assistant is None:
            self.start_assistant()
        assert self.current_assistant is not None
        self.current_assistant.append_reasoning(text)

    def add_tool_card(self, key: str, name: str, arguments="", source: str = "builtin") -> ToolCardWidget:
        existing = self.tool_cards.get(key)
        if existing:
            return existing
        card = ToolCardWidget(name, arguments, source, self)
        self._add(card)
        self.tool_cards[key] = card
        return card

    def finish_tool(self, result) -> None:
        key = str(getattr(result, "tool_call_id", "") or getattr(result, "name", "tool"))
        card = self.tool_cards.get(key) or self.add_tool_card(
            key, str(getattr(result, "name", "tool")), getattr(result, "arguments", {}), str(getattr(result, "source", "builtin"))
        )
        card.finish(result)
        self.refresh_item_sizes()

    def clear_transcript(self) -> None:
        self.clear()
        self.current_assistant = None
        self.tool_cards.clear()

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SourcesPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.sources: list[dict] = []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Sources</b>"))
        self.list = QListWidget(self)
        self.list.currentRowChanged.connect(self._show_source)
        layout.addWidget(self.list)
        self.preview = QPlainTextEdit(self)
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview)
        self.open_button = QPushButton("Open source")
        self.open_button.clicked.connect(self._open_source)
        layout.addWidget(self.open_button)

    def set_sources(self, sources: list[dict]) -> None:
        self.sources = list(sources)
        self.list.clear()
        for source in self.sources:
            label = f"[{source.get('source_id','?')}] {source.get('title') or source.get('url') or source.get('path')}"
            if source.get("locator"):
                label += f" ({source['locator']})"
            self.list.addItem(QListWidgetItem(label))
        if self.sources:
            self.list.setCurrentRow(0)

    def _selected(self) -> dict | None:
        row = self.list.currentRow()
        return self.sources[row] if 0 <= row < len(self.sources) else None

    def _show_source(self, _row: int) -> None:
        source = self._selected()
        if not source:
            self.preview.clear(); return
        lines = [str(source.get("title") or "")]
        if source.get("url"): lines.append(str(source["url"]))
        if source.get("path"): lines.append(str(source["path"]))
        if source.get("locator"): lines.append(str(source["locator"]))
        if source.get("snippet"): lines.extend(["", str(source["snippet"])])
        if source.get("path"):
            path = Path(str(source["path"]))
            if path.is_file() and path.stat().st_size <= 200_000:
                try: lines.extend(["", path.read_text(encoding="utf-8")[:20_000]])
                except Exception: pass
        self.preview.setPlainText("\n".join(lines))

    def _open_source(self) -> None:
        source = self._selected()
        if not source: return
        target = source.get("url") or source.get("path")
        if target:
            QDesktopServices.openUrl(QUrl.fromUserInput(str(target)))

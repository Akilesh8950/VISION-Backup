from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout


class TimelineWidget(QFrame):
    def __init__(self, payload: dict, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{payload.get('title', 'Timeline')}</b>"))
        for event in payload.get("events", []):
            row = QHBoxLayout()
            marker = QLabel("●")
            marker.setFixedWidth(18)
            row.addWidget(marker)
            text = QVBoxLayout()
            text.addWidget(QLabel(f"<b>{event.get('time', '')} — {event.get('title', '')}</b>"))
            if event.get("description"):
                description = QLabel(str(event["description"]))
                description.setWordWrap(True)
                text.addWidget(description)
            row.addLayout(text)
            row.addStretch()
            layout.addLayout(row)

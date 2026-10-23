from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox


class ConfirmationService(QObject):
    requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._events: dict[int, tuple[threading.Event, dict[str, bool]]] = {}
        self.requested.connect(self._show)

    def confirm(self, title: str, message: str, details: dict[str, Any]) -> bool:
        request_id = id(threading.current_thread()) + len(self._events) + hash(title)
        event = threading.Event()
        state: dict[str, bool] = {"approved": False}
        self._events[request_id] = (event, state)
        self.requested.emit({"id": request_id, "title": title, "message": message, "details": details})
        event.wait()
        self._events.pop(request_id, None)
        return state["approved"]

    def _show(self, payload: dict[str, Any]) -> None:
        request_id = int(payload["id"])
        stored = self._events.get(request_id)
        if not stored:
            return
        event, state = stored
        box = QMessageBox(self.parent())
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(str(payload.get("title") or "Allow?"))
        box.setText(str(payload.get("message") or "Allow this action?"))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        state["approved"] = box.exec() == QMessageBox.StandardButton.Yes
        event.set()

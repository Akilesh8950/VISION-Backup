from __future__ import annotations

import asyncio
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from .controller import ChatCallbacks, ChatController


class ChatWorker(QObject):
    content = Signal(str)
    reasoning = Signal(str)
    tool_start = Signal(object)
    tool_result = Signal(object)
    endpoint = Signal(object)
    sources = Signal(object)
    error = Signal(str)
    finished = Signal(object)

    def __init__(self, controller: ChatController):
        super().__init__()
        self.controller = controller
        self.controller.callbacks = ChatCallbacks(
            on_content=self.content.emit,
            on_reasoning=self.reasoning.emit,
            on_tool_start=self.tool_start.emit,
            on_tool_result=self.tool_result.emit,
            on_endpoint=self.endpoint.emit,
            on_sources=self.sources.emit,
            on_error=self.error.emit,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._request: dict[str, Any] | None = None

    def submit(self, **request: Any) -> None:
        self._request = request

    @Slot()
    def run(self) -> None:
        if not self._request:
            return
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._task = loop.create_task(self.controller.run_turn(**self._request))
            result = loop.run_until_complete(self._task)
            self.finished.emit(result)
        except asyncio.CancelledError:
            self.error.emit("Cancelled")
        finally:
            self._task = None
            self._loop = None
            loop.close()

    @Slot()
    def regenerate(self) -> None:
        if not self._request:
            return
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            request = {"conversation_id": self._request.get("conversation_id", "")}
            for key in ("tools", "system_context", "pre_sources"):
                if key in self._request:
                    request[key] = self._request[key]
            self._task = loop.create_task(self.controller.regenerate_last(**request))
            self.finished.emit(loop.run_until_complete(self._task))
        except asyncio.CancelledError:
            self.error.emit("Cancelled")
        finally:
            self._task = None
            self._loop = None
            loop.close()

    def stop(self) -> None:
        if self._loop and self._task and not self._task.done():
            self._loop.call_soon_threadsafe(self._task.cancel)

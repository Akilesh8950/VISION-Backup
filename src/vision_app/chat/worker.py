from __future__ import annotations

import asyncio
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from .controller import ChatCallbacks, ChatController, TurnResult


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
        self._running = False
        self._stopping = False
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def submit(self, **request: Any) -> bool:
        """Submit a request unless another turn is active or shutdown began."""
        with self._lock:
            if self._running or self._stopping:
                return False
            self._request = dict(request)
            return True

    @staticmethod
    def _terminal_result(request: dict[str, Any], *, message: str, cancelled: bool = False) -> TurnResult:
        return TurnResult(
            conversation_id=str(request.get("conversation_id") or ""),
            assistant_text="",
            reasoning="",
            tool_results=[],
            sources=list(request.get("pre_sources") or []),
            incomplete=True,
            cancelled=cancelled,
            error="" if cancelled else message,
        )

    def _take_request(self) -> tuple[dict[str, Any], str]:
        with self._lock:
            if self._running:
                return dict(self._request or {}), "busy"
            request = dict(self._request or {})
            if not request:
                return {}, "empty"
            if self._stopping:
                self._request = None
                return request, "stopping"
            self._running = True
            return request, "run"

    def _execute(self, *, regenerate: bool) -> None:
        request, state = self._take_request()
        if state != "run":
            if state == "busy":
                self.error.emit("A response is already running")
            elif state == "stopping":
                self.finished.emit(
                    self._terminal_result(request, message="Cancelled", cancelled=True)
                )
            else:
                empty_result = self._terminal_result(request, message="No chat request was submitted")
                try:
                    self.error.emit(empty_result.error)
                finally:
                    self.finished.emit(empty_result)
            return

        loop: asyncio.AbstractEventLoop | None = None
        result: Any = None
        result_error = ""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            with self._lock:
                self._loop = loop
            if regenerate:
                regen_request: dict[str, Any] = {"conversation_id": request.get("conversation_id", "")}
                for key in ("tools", "system_context", "pre_sources"):
                    if key in request:
                        regen_request[key] = request[key]
                coroutine = self.controller.regenerate_last(**regen_request)
            else:
                coroutine = self.controller.run_turn(**request)
            task = loop.create_task(coroutine)
            with self._lock:
                self._task = task
            result = loop.run_until_complete(task)
        except asyncio.CancelledError:
            result_error = "Cancelled"
            result = self._terminal_result(request, message=result_error, cancelled=True)
        except BaseException as exc:  # every started invocation needs a terminal UI signal
            result_error = str(exc) or exc.__class__.__name__
            result = self._terminal_result(request, message=result_error)
        finally:
            with self._lock:
                self._task = None
                self._loop = None
                self._request = None
                self._running = False
            if loop is not None:
                try:
                    loop.close()
                except Exception:
                    pass
                try:
                    asyncio.set_event_loop(None)
                except Exception:
                    pass

        if result_error and not getattr(result, "cancelled", False):
            try:
                self.error.emit(result_error)
            except Exception:
                pass
        self.finished.emit(result)

    @Slot()
    def run(self) -> None:
        self._execute(regenerate=False)

    @Slot()
    def regenerate(self) -> None:
        self._execute(regenerate=True)

    def _cancel_current(self) -> None:
        with self._lock:
            loop, task = self._loop, self._task
        if loop and task and not task.done():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except (RuntimeError, ValueError):
                pass

    def stop(self) -> None:
        """Cancel the current turn without preventing later submissions."""
        self._cancel_current()

    def prepare_shutdown(self) -> None:
        """Cancel work and reject queued/new work until either exit or resume."""
        with self._lock:
            self._stopping = True
        self._cancel_current()

    def resume(self) -> None:
        """Undo :meth:`prepare_shutdown` after an aborted window close."""
        with self._lock:
            self._stopping = False

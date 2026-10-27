from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot


class FunctionWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs

    def _emit_failure(self, exc: BaseException) -> None:
        try:
            self.failed.emit(str(exc) or exc.__class__.__name__)
        except Exception:
            # There is no second channel available from a worker thread. The
            # caller also connects thread completion/finally cleanup where a
            # flag could otherwise remain stuck.
            pass

    @Slot()
    def run(self) -> None:
        try:
            result = self.function(*self.args, **self.kwargs)
        except BaseException as exc:
            self._emit_failure(exc)
            return

        try:
            self.finished.emit(result)
        except BaseException as exc:
            # Signal delivery is normally queued for UI-thread slots, but a
            # direct connection can surface a slot exception here. Report it
            # on the failure channel instead of leaving the thread without a
            # terminal worker signal.
            self._emit_failure(exc)

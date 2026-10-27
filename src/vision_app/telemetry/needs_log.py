from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import __version__
from .telemetry import FIELDS, SIGNALS, build_report, make_event

logger = logging.getLogger(__name__)

MAX_EVENTS = 2_000
MAX_FILE_BYTES = 5 * 1024 * 1024

# Retention priority when the bounded queue must drop rows.  These rows
# become training data for the weekly model rebuild, so the highest-value
# signal (an explicit user correction) is dropped only when nothing else
# remains; legacy topic counters are worthless and leave first.
DROP_ORDER = (
    "topic_usage",
    "tool_runtime_error",
    "tool_schema_error",
    "code_error",
    "user_negative",
)


def _drop_rank(signal: object) -> int:
    try:
        return DROP_ORDER.index(str(signal or ""))
    except ValueError:
        return len(DROP_ORDER)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


class NeedsLog:
    """Non-blocking, bounded local telemetry/feedback queue.

    ``add()`` only validates/redacts and appends to an in-memory buffer.  A
    daemon writer periodically appends that buffer to ``needs.jsonl``,
    enforces the local retention cap, and rewrites the aggregate
    ``report.json``.  Explicit ``flush()`` remains synchronous for upload,
    export, shutdown, and tests.
    """

    def __init__(
        self,
        path: Path,
        flush_every: int = 50,
        *,
        session_id: str | None = None,
        app_version: str = __version__,
        model_sha_provider: Callable[[], str] | None = None,
        max_events: int = MAX_EVENTS,
        max_bytes: int = MAX_FILE_BYTES,
        flush_interval_seconds: float = 2.0,
    ):
        self.path = Path(path)
        self.flush_every = max(1, int(flush_every))
        self.session_id = str(session_id or uuid.uuid4().hex)
        self.app_version = str(app_version or __version__)
        self.model_sha_provider = model_sha_provider
        self.max_events = max(1, int(max_events))
        self.max_bytes = max(1, int(max_bytes))
        self.flush_interval_seconds = max(0.05, float(flush_interval_seconds))

        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self.upload_lock = threading.Lock()
        self._writer_stop = threading.Event()
        self._writer_thread: threading.Thread | None = None
        self._closed = False

    # ------------------------------------------------------------------
    # Paths and event creation
    # ------------------------------------------------------------------
    @property
    def report_path(self) -> Path:
        return self.path.parent / "report.json"

    @property
    def upload_status_path(self) -> Path:
        return self.path.parent / "upload-status.json"

    def _model_sha(self) -> str:
        if self.model_sha_provider is None:
            return ""
        try:
            return str(self.model_sha_provider() or "")
        except Exception:
            logger.debug("Telemetry model SHA provider failed", exc_info=True)
            return ""

    def _ensure_writer_locked(self) -> None:
        if self._closed:
            return
        if self._writer_thread is not None and self._writer_thread.is_alive():
            return
        self._writer_stop.clear()
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            daemon=True,
            name="vision-telemetry-writer",
        )
        self._writer_thread.start()

    def _writer_loop(self) -> None:
        while not self._writer_stop.wait(self.flush_interval_seconds):
            with self._lock:
                has_rows = bool(self._buffer)
            if not has_rows:
                continue
            try:
                self.flush()
            except Exception:
                logger.exception("Background telemetry flush failed; rows remain queued")

    def _trim_buffer_locked(self) -> None:
        while len(self._buffer) > self.max_events:
            # Drop the oldest row of the lowest-value signal present.
            index = min(
                range(len(self._buffer)),
                key=lambda i: (_drop_rank(self._buffer[i].get("signal")), i),
            )
            del self._buffer[index]

    def add(
        self,
        *,
        category: str,
        prompt: str,
        model_output: str,
        signal: str,
        detail: Any,
    ) -> dict[str, Any]:
        if signal not in SIGNALS:
            raise ValueError(f"Unsupported telemetry signal: {signal}")
        record = make_event(
            session_id=self.session_id,
            app_version=self.app_version,
            model_sha=self._model_sha(),
            signal=signal,
            category=category,
            prompt=prompt,
            model_output=model_output,
            detail=detail,
        )
        with self._lock:
            self._buffer.append(record)
            self._trim_buffer_locked()
            self._ensure_writer_locked()
        return record

    # ------------------------------------------------------------------
    # File helpers.  Methods ending in _locked assume self._lock is held.
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_rows(lines: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
        return rows

    def _read_lines_locked(self) -> list[str]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as stream:
            return [line.rstrip("\n") for line in stream if line.strip()]

    def _read_file_rows_locked(self) -> list[dict[str, Any]]:
        return self._parse_rows(self._read_lines_locked())

    def _rewrite_lines_locked(self, lines: list[str]) -> None:
        if not lines:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="needs-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write("\n".join(lines) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _line_signal(line: str) -> str:
        try:
            parsed = json.loads(line)
        except (TypeError, ValueError):
            return ""
        return str(parsed.get("signal") or "") if isinstance(parsed, dict) else ""

    def _enforce_limits_locked(self) -> None:
        if not self.path.exists():
            return
        lines = self._read_lines_locked()
        total_bytes = sum(len((line + "\n").encode("utf-8")) for line in lines)
        changed = False
        while lines and (len(lines) > self.max_events or total_bytes > self.max_bytes):
            # Retention follows DROP_ORDER (lowest-value signal first,
            # oldest first within a signal); user corrections go last.
            index = min(
                range(len(lines)),
                key=lambda i: (_drop_rank(self._line_signal(lines[i])), i),
            )
            removed = lines.pop(index)
            total_bytes -= len((removed + "\n").encode("utf-8"))
            changed = True
        if changed:
            self._rewrite_lines_locked(lines)

    def _write_report_locked(self) -> dict[str, Any]:
        rows = self._read_file_rows_locked() + list(self._buffer)
        report = build_report(rows[-self.max_events :])
        _atomic_write_json(self.report_path, report)
        return report

    # ------------------------------------------------------------------
    # Flush/read/clear API used by the UI and uploader
    # ------------------------------------------------------------------
    def flush(self) -> int:
        with self._lock:
            rows = list(self._buffer)
            if rows:
                payload = "".join(
                    json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n" for row in rows
                )
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Keep rows in memory until the payload has been written.  A
                # failed flush can therefore be retried instead of silently
                # losing feedback; removing exactly this prefix preserves
                # rows appended while the file was being written.
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                del self._buffer[: len(rows)]

            self._enforce_limits_locked()
            self._write_report_locked()
            return len(rows)

    def pending_count(self) -> int:
        with self._lock:
            count = len(self._buffer)
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as stream:
                    count += sum(1 for line in stream if line.strip())
            return count

    def snapshot_rows(self) -> list[dict[str, Any]]:
        """Return file + buffered rows without forcing a flush."""
        with self._lock:
            return self._read_file_rows_locked() + list(self._buffer)

    def read_all(self, *, flush_first: bool = True) -> list[dict[str, Any]]:
        if flush_first:
            self.flush()
        with self._lock:
            return self._read_file_rows_locked()

    def tail(self, limit: int = 100, *, flush_first: bool = True) -> tuple[list[dict[str, Any]], int]:
        """Return the newest rows and total count without retaining all rows."""
        if flush_first:
            self.flush()
        bounded_limit = max(0, limit)
        with self._lock:
            if not self.path.exists():
                return [], 0
            lines: deque[str] = deque(maxlen=bounded_limit or 1)
            total = 0
            with self.path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    total += 1
                    if bounded_limit:
                        lines.append(line)
            return (self._parse_rows(list(lines)) if bounded_limit else []), total

    def report(self, *, flush_first: bool = True) -> dict[str, Any]:
        if flush_first:
            self.flush()
            return build_report(self.read_all(flush_first=False))
        return build_report(self.snapshot_rows())

    def preview_payload(self) -> dict[str, Any]:
        rows = self.snapshot_rows()
        return {
            "needs_jsonl": "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n" for row in rows
            ),
            "report": build_report(rows),
            "event_count": len(rows),
        }

    @staticmethod
    def _canonical_line(line: str) -> str:
        try:
            return json.dumps(json.loads(line), ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return line.strip()

    def acknowledge_uploaded(self, uploaded_lines: list[str]) -> int:
        """Remove only an uploaded prefix, preserving rows added meanwhile."""
        if not uploaded_lines:
            return 0
        expected = [self._canonical_line(line) for line in uploaded_lines]
        with self._lock:
            self.flush()
            if not self.path.exists():
                return 0
            current_lines = self._read_lines_locked()
            matching = 0
            for actual, wanted in zip(current_lines, expected, strict=False):
                if self._canonical_line(actual) != wanted:
                    break
                matching += 1
            if matching:
                self._rewrite_lines_locked(current_lines[matching:])
            self._write_report_locked()
            return matching

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            self.path.unlink(missing_ok=True)
            self._write_report_locked()

    # ------------------------------------------------------------------
    # Upload status (shown in Settings → Privacy)
    # ------------------------------------------------------------------
    def upload_status(self) -> dict[str, Any]:
        default: dict[str, Any] = {
            "state": "never",
            "at": None,
            "message": "No upload has been recorded yet.",
            "uploaded_events": 0,
            "pending_events": self.pending_count(),
        }
        try:
            raw = self.upload_status_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return default
        if not isinstance(parsed, dict):
            return default
        status = {**default, **parsed}
        status["pending_events"] = self.pending_count()
        return status

    def record_upload_status(
        self,
        state: str,
        *,
        message: str = "",
        uploaded_events: int = 0,
    ) -> dict[str, Any]:
        payload = {
            "state": str(state or "failed")[:64],
            "at": datetime.now(UTC).isoformat(),
            "message": str(message or "")[:500],
            "uploaded_events": max(0, int(uploaded_events or 0)),
            "pending_events": self.pending_count(),
        }
        try:
            _atomic_write_json(self.upload_status_path, payload)
        except OSError:
            logger.warning("Could not persist telemetry upload status", exc_info=True)
        return payload

    def close(self) -> None:
        try:
            self.flush()
        finally:
            with self._lock:
                self._closed = True
                self._writer_stop.set()
                writer = self._writer_thread
            if writer is not None and writer is not threading.current_thread():
                writer.join(timeout=2.0)


# Backwards-compatible, descriptive alias for new sync code.
TelemetryLogger = NeedsLog

__all__ = ["FIELDS", "MAX_EVENTS", "MAX_FILE_BYTES", "SIGNALS", "NeedsLog", "TelemetryLogger"]

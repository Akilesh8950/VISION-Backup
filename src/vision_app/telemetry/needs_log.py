from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..redaction import redact_text

SIGNALS = {"tool_schema_error", "tool_runtime_error", "code_error", "user_negative", "topic_usage"}
FIELDS = ("ts", "category", "prompt", "model_output", "signal", "detail")


class NeedsLog:
    def __init__(self, path: Path, flush_every: int = 50):
        self.path = path
        self.flush_every = max(1, flush_every)
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def add(
        self,
        *,
        category: str,
        prompt: str,
        model_output: str,
        signal: str,
        detail: str,
    ) -> dict[str, Any]:
        if signal not in SIGNALS:
            raise ValueError(f"Unsupported telemetry signal: {signal}")
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "category": redact_text(str(category)),
            "prompt": redact_text(str(prompt)),
            "model_output": redact_text(str(model_output)),
            "signal": signal,
            "detail": redact_text(str(detail)),
        }
        with self._lock:
            self._buffer.append(record)
            should_flush = len(self._buffer) >= self.flush_every
        if should_flush:
            self.flush()
        return record

    def flush(self) -> int:
        with self._lock:
            rows, self._buffer = self._buffer, []
        if not rows:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n")
        return len(rows)

    def pending_count(self) -> int:
        with self._lock:
            count = len(self._buffer)
        if self.path.exists():
            count += sum(1 for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip())
        return count

    def read_all(self, *, flush_first: bool = True) -> list[dict[str, Any]]:
        if flush_first:
            self.flush()
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            if self.path.exists():
                self.path.unlink()

    def close(self) -> None:
        self.flush()

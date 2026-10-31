from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .paths import AppPaths
from .redaction import redact_text, redact_value


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def setup_logging(paths: AppPaths) -> logging.Logger:
    paths.ensure()
    logger = logging.getLogger("vision_app")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    desired_path = Path(paths.app_log).resolve()

    matching: RotatingFileHandler | None = None
    for handler in list(logger.handlers):
        if not isinstance(handler, RotatingFileHandler):
            continue
        try:
            current_path = Path(handler.baseFilename).resolve()
        except Exception:
            current_path = None
        if current_path == desired_path and matching is None:
            matching = handler
            continue
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    if matching is None:
        handler = RotatingFileHandler(desired_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    else:
        matching.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    return logger


class AuditLog:
    MAX_BYTES = 5_000_000
    BACKUP_COUNT = 3
    MAX_LINE_BYTES = 64 * 1024

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = MAX_BYTES,
        backup_count: int = BACKUP_COUNT,
        max_line_bytes: int = MAX_LINE_BYTES,
    ):
        self.path = path
        self.max_bytes = max(1024, int(max_bytes))
        self.backup_count = max(0, int(backup_count))
        self.max_line_bytes = max(1024, int(max_line_bytes))
        self._lock = threading.Lock()

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        try:
            current_bytes = self.path.stat().st_size
        except OSError:
            return
        if current_bytes == 0 or current_bytes + incoming_bytes <= self.max_bytes:
            return
        try:
            if self.backup_count <= 0:
                self.path.unlink(missing_ok=True)
                return
            oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
            oldest.unlink(missing_ok=True)
            for index in range(self.backup_count - 1, 0, -1):
                source = self.path.with_name(f"{self.path.name}.{index}")
                if source.exists():
                    source.replace(self.path.with_name(f"{self.path.name}.{index + 1}"))
            self.path.replace(self.path.with_name(f"{self.path.name}.1"))
        except OSError:
            # Rotation failure must not suppress the current audit record;
            # appending to the existing file is the safer fallback.
            return

    def write(self, name: str, args: Any, status: str, duration_ms: float, **extra: Any) -> None:
        try:
            args_hash = hashlib.sha256(
                json.dumps(args, sort_keys=True, default=str).encode("utf-8", "replace")
            ).hexdigest()
            record = {
                "ts": datetime.now(UTC).isoformat(),
                "name": str(name),
                "args": redact_value(args),
                "args_sha256": args_hash,
                "status": str(status),
                "duration_ms": round(float(duration_ms), 3),
                **redact_value(extra),
            }
            line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
            if len(line.encode("utf-8", "replace")) > self.max_line_bytes:
                # Keep an auditable record and the exact argument hash, but
                # do not let one huge tool call create an unbounded log line.
                record = {
                    "ts": record["ts"],
                    "name": record["name"],
                    "args": {"truncated": True},
                    "args_sha256": args_hash,
                    "status": record["status"],
                    "duration_ms": record["duration_ms"],
                }
                if "source" in extra:
                    record["source"] = str(extra["source"])
                line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            # Auditing must never turn an otherwise completed tool call into
            # a missing tool response. The in-app result still reports the
            # tool outcome if the audit destination itself is unavailable.
            return
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed(len(line.encode("utf-8", "replace")) + 1)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(line + "\n")
        except OSError:
            return

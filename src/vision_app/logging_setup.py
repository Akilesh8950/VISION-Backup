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
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

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
        except Exception:
            # Auditing must never turn an otherwise completed tool call into
            # a missing tool response. The in-app result still reports the
            # tool outcome if the audit destination itself is unavailable.
            return
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(line + "\n")
        except OSError:
            return

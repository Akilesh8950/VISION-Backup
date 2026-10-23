from __future__ import annotations

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
    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        handler = RotatingFileHandler(paths.app_log, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    return logger


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def write(self, name: str, args: Any, status: str, duration_ms: float, **extra: Any) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "name": name,
            "args": redact_value(args),
            "status": status,
            "duration_ms": round(float(duration_ms), 3),
            **redact_value(extra),
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")

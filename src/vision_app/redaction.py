from __future__ import annotations

import re
from typing import Any

PATTERNS = [
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(HF_TOKEN\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
]
SENSITIVE_KEYS = {"hf_token", "api_key", "apikey", "token", "access_token", "refresh_token", "authorization", "password", "secret"}


def redact_text(text: str) -> str:
    result = text
    for pattern in PATTERNS:
        result = pattern.sub(lambda m: (m.group(1) + "***") if m.groups() else "***", result)
    return result


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            key: ("***" if str(key).lower() in SENSITIVE_KEYS else redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value

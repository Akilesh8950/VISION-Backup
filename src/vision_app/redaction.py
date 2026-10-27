from __future__ import annotations

import re
from typing import Any

_TOKEN_PATTERNS = [
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]

_SECRET_ASSIGNMENT = re.compile(
    r"""
    (?P<prefix>
        ["']?
        (?:
            [A-Za-z0-9_-]*?
            (?:api[_-]?key|secret[_-]?access[_-]?key|client[_-]?secret|password|passwd|authorization)
            [A-Za-z0-9_-]*?
            |
            [A-Za-z0-9_-]*?
            (?:access|refresh|auth|hf|session|bearer)[_-]?token
            |
            [A-Za-z0-9_-]*?[_-]token
            |
            token|secret
        )
        ["']?
        \s*[:=]\s*
    )
    (?P<quote>["']?)
    (?P<value>[^\s,;}\]"']+)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_BEARER = re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic|token)\s+)[^\s\"']+")
_ABSOLUTE_USER_PATH = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|\\\\|/(?:home|Users|tmp|var/folders)/)[^\s\"'<>|]+"
)
_SENSITIVE_EXACT = {"apikey", "authorization", "passwd", "password", "secret", "token"}
_SENSITIVE_JOINED_PARTS = (
    "api_key",
    "access_token",
    "refresh_token",
    "auth_token",
    "hf_token",
    "session_token",
    "bearer_token",
    "client_secret",
    "secret_access_key",
    "secret_key",
)
_SENSITIVE_SUFFIXES = ("_token", "_secret", "_password", "_passwd", "_api_key")


def _is_sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
    if not normalized:
        return False
    if normalized in _SENSITIVE_EXACT:
        return True
    if any(part in normalized for part in _SENSITIVE_JOINED_PARTS):
        return True
    return normalized.endswith(_SENSITIVE_SUFFIXES)


def redact_absolute_paths(text: str) -> str:
    """Replace absolute local paths with ``<PATH:basename>``.

    Telemetry and other data that may leave the machine should retain the
    useful file name from a traceback or tool error without revealing the
    user's account name, home directory, or folder structure.
    """

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        suffix = ""
        while raw and raw[-1] in ".,;:)\"]":
            suffix = raw[-1] + suffix
            raw = raw[:-1]
        basename = re.split(r"[\\/]", raw.rstrip("\\/"))[-1] or "path"
        return f"<PATH:{basename}>{suffix}"

    return _ABSOLUTE_USER_PATH.sub(replace, str(text))


def redact_text(text: str) -> str:
    result = str(text)

    def replace_assignment(match: re.Match[str]) -> str:
        # If the original value was quoted, its closing quote remains in the
        # text immediately after the match, preserving JSON string syntax.
        return f"{match.group('prefix')}{match.group('quote')}***"

    # Authorization schemes must run first: otherwise the generic
    # assignment pattern treats "Bearer" itself as the value and leaves the
    # actual credential following it untouched.
    result = _BEARER.sub(lambda match: match.group(1) + "***", result)
    result = _SECRET_ASSIGNMENT.sub(replace_assignment, result)
    result = _BEARER.sub(lambda match: match.group(1) + "***", result)
    for pattern in _TOKEN_PATTERNS:
        result = pattern.sub("***", result)
    return result


def redact_value(value: Any) -> Any:
    """Return a redacted deep copy of JSON-like data.

    Cycles and excessively deep structures are represented explicitly instead
    of recursing forever; untrusted tool results are not guaranteed to be
    acyclic even though normal JSON payloads are.
    """

    def visit(item: Any, depth: int, seen: set[int]) -> Any:
        if isinstance(item, str):
            return redact_text(item)
        if depth > 30:
            return "<truncated>"
        if isinstance(item, dict):
            marker = id(item)
            if marker in seen:
                return "<cycle>"
            seen.add(marker)
            try:
                return {
                    key: ("***" if _is_sensitive_key(key) else visit(child, depth + 1, seen))
                    for key, child in item.items()
                }
            finally:
                seen.discard(marker)
        if isinstance(item, (list, tuple)):
            marker = id(item)
            if marker in seen:
                return "<cycle>"
            seen.add(marker)
            try:
                return [visit(child, depth + 1, seen) for child in item]
            finally:
                seen.discard(marker)
        return item

    return visit(value, 0, set())

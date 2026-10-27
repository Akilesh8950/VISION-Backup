from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..redaction import redact_absolute_paths, redact_text, redact_value

TelemetrySignal = Literal[
    "tool_schema_error",
    "tool_runtime_error",
    "code_error",
    "user_negative",
    "topic_usage",
]

SIGNALS: set[str] = {
    "tool_schema_error",
    "tool_runtime_error",
    "code_error",
    "user_negative",
    "topic_usage",
}
FIELDS = (
    "ts",
    "session_id",
    "app_version",
    "model_sha",
    "signal",
    "category",
    "prompt",
    "model_output",
    "detail",
)
REPORT_FIELDS = (
    "week",
    "total_events",
    "by_category",
    "by_signal",
    "fix_rate",
    "top_failing_tools",
)

MAX_CATEGORY_CHARS = 200
MAX_PROMPT_CHARS = 8_000
MAX_MODEL_OUTPUT_CHARS = 2_000
MAX_DETAIL_CHARS = 8_000
MAX_RAW_ARGUMENT_CHARS = 4_000

_BODY_VALUE_KEYS = {
    "body",
    "clipboard",
    "clipboard_contents",
    "clipboard_text",
    "code",
    "content",
    "file_body",
    "file_contents",
    "file_content",
    "script",
    "text",
}
_BODY_ASSIGNMENT = re.compile(
    r'(?i)("(?:body|clipboard(?:_text|_contents)?|code|content|file_(?:body|content|contents)|script|text)"\s*:\s*")'
    r'(?:\\.|[^"\\])*(")'
)


class TelemetryEvent(BaseModel):
    """One redacted row in ``telemetry/needs.jsonl``."""

    model_config = ConfigDict(extra="forbid")

    ts: str
    session_id: str = Field(min_length=1, max_length=128)
    app_version: str = Field(min_length=1, max_length=64)
    model_sha: str = Field(default="", max_length=256)
    signal: TelemetrySignal
    category: str = Field(default="general", max_length=MAX_CATEGORY_CHARS)
    prompt: str = Field(default="", max_length=MAX_PROMPT_CHARS)
    model_output: str = Field(default="", max_length=MAX_MODEL_OUTPUT_CHARS)
    detail: dict[str, Any] = Field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        # model_dump preserves the declared field order above.
        return self.model_dump(mode="json")


class TelemetryReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week: str
    total_events: int
    by_category: dict[str, int]
    by_signal: dict[str, int]
    fix_rate: float
    top_failing_tools: list[dict[str, Any]]


def prompt_hash(prompt: Any) -> str:
    """Return a stable SHA-256 fingerprint for prompt-level deduplication."""
    return hashlib.sha256(str(prompt or "").encode("utf-8", errors="replace")).hexdigest()


def event_fingerprint(row: dict[str, Any]) -> str:
    """Fingerprint used by append-merge so a re-upload cannot duplicate rows.

    New rows carry ``detail.prompt_hash``.  The fingerprint also includes the
    timestamp/session/category/detail, so two legitimate occurrences of the
    same prompt at different times remain distinct while the exact same
    event is not appended twice after an interrupted acknowledgement.
    """
    raw_detail: Any = row.get("detail")
    detail: dict[str, Any] = raw_detail if isinstance(raw_detail, dict) else {}
    material = {
        "ts": row.get("ts", ""),
        "session_id": row.get("session_id", ""),
        "app_version": row.get("app_version", ""),
        "model_sha": row.get("model_sha", ""),
        "signal": row.get("signal", ""),
        "category": row.get("category", ""),
        "prompt_hash": detail.get("prompt_hash") or prompt_hash(row.get("prompt", "")),
        "model_output": row.get("model_output", ""),
        "detail": detail,
    }
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sanitize_telemetry_text(value: Any, *, max_chars: int) -> str:
    text = redact_absolute_paths(redact_text(str(value or "")))
    return text[: max(0, max_chars)]


def _normalized_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")


def _sanitize_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    normalized = _normalized_key(key)
    if normalized in _BODY_VALUE_KEYS:
        if isinstance(value, str):
            return f"<redacted:{len(value)} chars>"
        return "<redacted>"
    if depth > 12:
        return "<truncated>"
    if isinstance(value, dict):
        return {
            str(child_key)[:200]: _sanitize_value(child, key=str(child_key), depth=depth + 1)
            for child_key, child in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(child, depth=depth + 1) for child in list(value)[:100]]
    if isinstance(value, str):
        return sanitize_telemetry_text(value, max_chars=4_000)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_telemetry_text(value, max_chars=1_000)


def sanitize_raw_arguments(raw: Any) -> Any:
    """Sanitize model-supplied tool arguments for a telemetry detail field.

    Parsed arguments are sanitized recursively.  Bodies such as
    ``write_file.content``, ``run_python.code`` and ``clipboard_set.text`` are
    always replaced by a length marker; paths retain only a basename through
    the normal path redactor.
    """
    if isinstance(raw, dict):
        return _sanitize_value(redact_value(raw))
    if isinstance(raw, str):
        if not raw:
            return ""
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            without_bodies = _BODY_ASSIGNMENT.sub(r"\1<redacted>\2", raw)
            return sanitize_telemetry_text(without_bodies, max_chars=MAX_RAW_ARGUMENT_CHARS)
        return _sanitize_value(redact_value(parsed))
    return _sanitize_value(redact_value(raw))


def sanitize_detail(detail: Any, *, prompt_fingerprint: str) -> dict[str, Any]:
    if isinstance(detail, dict):
        sanitized = _sanitize_value(redact_value(detail))
        if not isinstance(sanitized, dict):  # Defensive; _sanitize_value keeps dicts.
            sanitized = {"message": sanitize_telemetry_text(sanitized, max_chars=4_000)}
    elif detail is None:
        sanitized = {}
    else:
        sanitized = {"message": sanitize_telemetry_text(detail, max_chars=4_000)}

    if "raw_arguments" in sanitized:
        sanitized["raw_arguments"] = sanitize_raw_arguments(sanitized["raw_arguments"])
    sanitized["prompt_hash"] = prompt_fingerprint

    # Bound the complete detail object even if a caller supplied many fields.
    encoded = json.dumps(sanitized, ensure_ascii=False, sort_keys=True, default=str)
    if len(encoded) > MAX_DETAIL_CHARS:
        keep: dict[str, Any] = {"prompt_hash": prompt_fingerprint, "truncated": True}
        for key in ("tool", "command", "source", "target", "validation_error", "exit_code"):
            if key in sanitized:
                keep[key] = _sanitize_value(sanitized[key], key=key)
        if "message" in sanitized:
            keep["message"] = sanitize_telemetry_text(sanitized["message"], max_chars=1_000)
        sanitized = keep
    return sanitized


def make_event(
    *,
    session_id: str,
    app_version: str,
    model_sha: str,
    signal: str,
    category: str,
    prompt: Any,
    model_output: Any,
    detail: Any,
) -> dict[str, Any]:
    fingerprint = prompt_hash(prompt)
    topic_only = signal == "topic_usage"
    event = TelemetryEvent(
        ts=datetime.now(UTC).isoformat(),
        session_id=sanitize_telemetry_text(session_id, max_chars=128),
        app_version=sanitize_telemetry_text(app_version, max_chars=64),
        model_sha=sanitize_telemetry_text(model_sha, max_chars=256),
        signal=signal,  # type: ignore[arg-type]
        category=sanitize_telemetry_text(category or "general", max_chars=MAX_CATEGORY_CHARS),
        prompt="" if topic_only else sanitize_telemetry_text(prompt, max_chars=MAX_PROMPT_CHARS),
        model_output="" if topic_only else sanitize_telemetry_text(model_output, max_chars=MAX_MODEL_OUTPUT_CHARS),
        detail=sanitize_detail(detail, prompt_fingerprint=fingerprint),
    )
    return event.to_row()


def _week_for(rows: list[dict[str, Any]]) -> str:
    for row in reversed(rows):
        raw_ts = str(row.get("ts") or "")
        try:
            parsed = datetime.fromisoformat(raw_ts)
        except ValueError:
            continue
        iso = parsed.isocalendar()
        return f"{iso.year:04d}-W{iso.week:02d}"
    iso = datetime.now(UTC).isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the small aggregate written beside the local JSONL queue.

    ``fix_rate`` is the share of retained local events carrying the
    ``user_negative`` signal.  This report intentionally describes the
    currently retained local queue; uploaded JSONL rows remain the durable
    historical record in the dataset repository.
    """
    by_category: Counter[str] = Counter()
    by_signal: Counter[str] = Counter()
    failing_tools: Counter[str] = Counter()
    for row in rows:
        category = str(row.get("category") or "general")[:MAX_CATEGORY_CHARS]
        signal = str(row.get("signal") or "")
        by_category[category] += 1
        if signal:
            by_signal[signal] += 1
        if signal in {"tool_schema_error", "tool_runtime_error", "code_error"}:
            raw_detail: Any = row.get("detail")
            detail: dict[str, Any] = raw_detail if isinstance(raw_detail, dict) else {}
            tool = str(detail.get("tool") or category or "unknown")
            failing_tools[tool] += 1

    total = len(rows)
    report = TelemetryReport(
        week=_week_for(rows),
        total_events=total,
        by_category=dict(sorted(by_category.items())),
        by_signal=dict(sorted(by_signal.items())),
        fix_rate=(by_signal["user_negative"] / total if total else 0.0),
        top_failing_tools=[
            {"tool": tool, "count": count} for tool, count in failing_tools.most_common(5)
        ],
    )
    return report.model_dump(mode="json")


__all__ = [
    "FIELDS",
    "MAX_MODEL_OUTPUT_CHARS",
    "REPORT_FIELDS",
    "SIGNALS",
    "TelemetryEvent",
    "TelemetryReport",
    "build_report",
    "event_fingerprint",
    "make_event",
    "prompt_hash",
    "sanitize_detail",
    "sanitize_raw_arguments",
    "sanitize_telemetry_text",
]

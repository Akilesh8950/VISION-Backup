from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .schemas import ToolCall, ToolCallAccumulator

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

# The local model is run with a 16k-token context, so these ceilings are far
# above a legitimate single response. They exist to stop a malfunctioning or
# hostile compatible endpoint from forcing unbounded in-memory accumulation.
MAX_SSE_LINE_BYTES = 2 * 1024 * 1024
MAX_SSE_LINE_CHARS = MAX_SSE_LINE_BYTES
MAX_STREAM_TEXT_CHARS = 4_000_000
MAX_TOOL_CALLS = 128
MAX_TOOL_ARGUMENT_CHARS = 2_000_000
MAX_TOOL_IDENTITY_CHARS = 512


@dataclass
class StreamEvent:
    kind: str  # content, reasoning, tool_update, done, raw
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class ThinkSplitter:
    """Split streamed content into reasoning/content while tolerating split tags."""

    def __init__(self) -> None:
        self.in_think = False
        self.buffer = ""

    @staticmethod
    def _partial_suffix(text: str, tag: str) -> int:
        max_keep = min(len(text), len(tag) - 1)
        for size in range(max_keep, 0, -1):
            if tag.startswith(text[-size:]):
                return size
        return 0

    def feed(self, chunk: str, *, final: bool = False) -> list[StreamEvent]:
        self.buffer += chunk
        events: list[StreamEvent] = []
        while self.buffer:
            tag = THINK_CLOSE if self.in_think else THINK_OPEN
            index = self.buffer.find(tag)
            if index == -1:
                keep = 0 if final else self._partial_suffix(self.buffer, tag)
                emit = self.buffer[: len(self.buffer) - keep] if keep else self.buffer
                self.buffer = self.buffer[len(emit):]
                if emit:
                    events.append(StreamEvent("reasoning" if self.in_think else "content", emit))
                break
            if index:
                events.append(
                    StreamEvent("reasoning" if self.in_think else "content", self.buffer[:index])
                )
            self.buffer = self.buffer[index + len(tag):]
            self.in_think = not self.in_think
        return events

    def flush(self) -> list[StreamEvent]:
        return self.feed("", final=True)


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or error.get("error")
        if message:
            code = error.get("code") or error.get("type")
            return f"{message} ({code})" if code else str(message)
        return json.dumps(error, ensure_ascii=False, default=str)[:1000]
    return str(error or "Model endpoint returned an error")[:1000]


class StreamAccumulator:
    def __init__(self) -> None:
        self.content = ""
        self.reasoning = ""
        self.tool_calls: dict[int, ToolCallAccumulator] = {}
        self.finish_reason: str | None = None
        self.done = False
        self.error = ""
        self._error_emitted = False
        self.usage: dict[str, Any] = {}
        self.think = ThinkSplitter()

    @property
    def incomplete(self) -> bool:
        return bool(self.error) or not self.done or self.finish_reason in (None, "length", "error")

    def _set_error(self, message: str) -> StreamEvent:
        if not self.error:
            self.error = message
        self._error_emitted = True
        return StreamEvent("error", self.error)

    def _error_event(self) -> StreamEvent:
        self._error_emitted = True
        return StreamEvent("error", self.error)

    def _append_text(self, kind: str, text: str) -> str:
        current = len(self.content) + len(self.reasoning)
        remaining = max(0, MAX_STREAM_TEXT_CHARS - current)
        accepted = text[:remaining]
        if accepted != text and not self.error:
            self.error = "Model response exceeded the maximum supported size"
        if kind == "reasoning":
            self.reasoning += accepted
        elif kind == "content":
            self.content += accepted
        return accepted

    def _record_think_events(self, events: list[StreamEvent]) -> None:
        for event in events:
            if event.kind in {"reasoning", "content"}:
                event.text = self._append_text(event.kind, event.text)

    def ordered_tool_calls(self) -> list[ToolCall]:
        return [self.tool_calls[index].to_tool_call() for index in sorted(self.tool_calls)]

    def feed_line(self, line: str | bytes) -> list[StreamEvent]:
        if isinstance(line, bytes):
            if len(line) > MAX_SSE_LINE_BYTES:
                return [self._set_error("Model endpoint sent an oversized streaming event")]
            line = line.decode("utf-8", errors="replace")
        if len(line) > MAX_SSE_LINE_CHARS:
            return [self._set_error("Model endpoint sent an oversized streaming event")]
        if self.error and line.strip() not in {"[DONE]", "data: [DONE]"}:
            return []
        line = line.strip()
        if not line or line.startswith(":"):
            return []
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            self.done = True
            events = self.think.flush()
            # The splitter may have been holding a partial tag suffix. Record
            # that flushed text in the accumulator as well as yielding it;
            # otherwise it can appear live but disappear after reload.
            self._record_think_events(events)
            events.append(StreamEvent("done"))
            return events
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return [StreamEvent("raw", line)]
        if not isinstance(payload, dict):
            return [StreamEvent("raw", line)]
        return self.feed_payload(payload)

    def feed_payload(self, payload: dict[str, Any]) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        if self.error:
            return []
        if not isinstance(payload, dict):
            return [StreamEvent("raw", str(payload))]
        if payload.get("error") is not None:
            self.error = _error_text(payload.get("error"))
            self._error_emitted = True
            events.append(StreamEvent("error", self.error, payload={"error": payload.get("error")}))
            return events
        if isinstance(payload.get("usage"), dict):
            self.usage = payload["usage"]
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return events
        choice = choices[0] if isinstance(choices[0], dict) else {}
        if choice.get("error") is not None:
            self.error = _error_text(choice.get("error"))
            self._error_emitted = True
            events.append(StreamEvent("error", self.error, payload={"error": choice.get("error")}))
            return events
        if choice.get("finish_reason"):
            self.finish_reason = str(choice["finish_reason"])
        raw_delta = choice.get("delta") if choice.get("delta") is not None else choice.get("message")
        delta = raw_delta if isinstance(raw_delta, dict) else {}

        reasoning = delta.get("reasoning_content")
        if reasoning is None:
            reasoning = delta.get("reasoning")
        if reasoning:
            text = self._append_text("reasoning", str(reasoning))
            if text:
                events.append(StreamEvent("reasoning", text))
            if self.error:
                events.append(self._error_event())
                return events

        content = delta.get("content")
        if content:
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    text_value = item.get("text", "")
                    if isinstance(text_value, dict):
                        text_value = text_value.get("value", "")
                    parts.append(str(text_value or ""))
                content = "".join(parts)
            think_events = self.think.feed(str(content))
            self._record_think_events(think_events)
            events.extend(event for event in think_events if event.text)
            if self.error:
                events.append(self._error_event())
                return events

        refusal = delta.get("refusal")
        if refusal:
            refusal_text = self._append_text("content", str(refusal))
            if refusal_text:
                events.append(StreamEvent("content", refusal_text))
            if self.error:
                events.append(self._error_event())
                return events

        raw_tool_calls = delta.get("tool_calls")
        for raw_call in raw_tool_calls if isinstance(raw_tool_calls, list) else []:
            if not isinstance(raw_call, dict):
                continue
            try:
                raw_index = raw_call.get("index", len(self.tool_calls))
                index = int(raw_index)  # type: ignore[arg-type]
                if isinstance(raw_index, bool) or index < 0:
                    raise ValueError("invalid tool call index")
            except (TypeError, ValueError):
                index = len(self.tool_calls)
            if index not in self.tool_calls and len(self.tool_calls) >= MAX_TOOL_CALLS:
                events.append(self._set_error("Model endpoint sent too many tool calls"))
                return events
            accumulator = self.tool_calls.setdefault(index, ToolCallAccumulator(index=index))
            accumulator.merge(raw_call)
            if (
                len(accumulator.id) > MAX_TOOL_IDENTITY_CHARS
                or len(accumulator.name) > MAX_TOOL_IDENTITY_CHARS
                or len(accumulator.arguments) > MAX_TOOL_ARGUMENT_CHARS
            ):
                accumulator.id = accumulator.id[:MAX_TOOL_IDENTITY_CHARS]
                accumulator.name = accumulator.name[:MAX_TOOL_IDENTITY_CHARS]
                accumulator.arguments = accumulator.arguments[:MAX_TOOL_ARGUMENT_CHARS]
                events.append(self._set_error("Model endpoint sent an oversized tool call"))
                return events
            events.append(
                StreamEvent(
                    "tool_update",
                    payload={"index": index, "name": accumulator.name, "arguments": accumulator.arguments},
                )
            )
        return events

    def close(self) -> list[StreamEvent]:
        events = self.think.flush()
        self._record_think_events(events)
        recorded = [event for event in events if event.text]
        if self.error and not self._error_emitted:
            recorded.append(self._error_event())
        return recorded

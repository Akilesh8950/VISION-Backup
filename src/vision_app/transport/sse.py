from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .schemas import ToolCall, ToolCallAccumulator

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"


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


class StreamAccumulator:
    def __init__(self) -> None:
        self.content = ""
        self.reasoning = ""
        self.tool_calls: dict[int, ToolCallAccumulator] = {}
        self.finish_reason: str | None = None
        self.done = False
        self.usage: dict[str, Any] = {}
        self.think = ThinkSplitter()

    @property
    def incomplete(self) -> bool:
        return not self.done or self.finish_reason in (None, "length")

    def ordered_tool_calls(self) -> list[ToolCall]:
        return [self.tool_calls[index].to_tool_call() for index in sorted(self.tool_calls)]

    def feed_line(self, line: str | bytes) -> list[StreamEvent]:
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        line = line.strip()
        if not line or line.startswith(":"):
            return []
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            self.done = True
            events = self.think.flush()
            events.append(StreamEvent("done"))
            return events
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return [StreamEvent("raw", line)]
        return self.feed_payload(payload)

    def feed_payload(self, payload: dict[str, Any]) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        if isinstance(payload.get("usage"), dict):
            self.usage = payload["usage"]
        choices = payload.get("choices") or []
        if not choices:
            return events
        choice = choices[0] or {}
        if choice.get("finish_reason"):
            self.finish_reason = str(choice["finish_reason"])
        delta = choice.get("delta") or choice.get("message") or {}

        reasoning = delta.get("reasoning_content")
        if reasoning is None:
            reasoning = delta.get("reasoning")
        if reasoning:
            text = str(reasoning)
            self.reasoning += text
            events.append(StreamEvent("reasoning", text))

        content = delta.get("content")
        if content:
            if isinstance(content, list):
                content = "".join(
                    str(item.get("text", "")) for item in content if isinstance(item, dict)
                )
            for event in self.think.feed(str(content)):
                if event.kind == "reasoning":
                    self.reasoning += event.text
                else:
                    self.content += event.text
                events.append(event)

        for raw_call in delta.get("tool_calls") or []:
            if not isinstance(raw_call, dict):
                continue
            try:
                index = int(raw_call.get("index", len(self.tool_calls)))
            except (TypeError, ValueError):
                index = len(self.tool_calls)
            accumulator = self.tool_calls.setdefault(index, ToolCallAccumulator(index=index))
            accumulator.merge(raw_call)
            events.append(
                StreamEvent(
                    "tool_update",
                    payload={"index": index, "name": accumulator.name, "arguments": accumulator.arguments},
                )
            )
        return events

    def close(self) -> list[StreamEvent]:
        events = self.think.flush()
        for event in events:
            if event.kind == "reasoning":
                self.reasoning += event.text
            elif event.kind == "content":
                self.content += event.text
        return events

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field


class FunctionCall(BaseModel):
    name: str = ""
    arguments: str = ""


class ToolCall(BaseModel):
    id: str = ""
    type: Literal["function"] = "function"
    function: FunctionCall = Field(default_factory=FunctionCall)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_wire(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "content": self.content or ""}
        if self.tool_calls:
            data["tool_calls"] = [call.model_dump(exclude_none=True) for call in self.tool_calls]
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        return data


class ToolCallAccumulator(BaseModel):
    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""

    def merge(self, delta: dict[str, Any]) -> None:
        if delta.get("id"):
            self.id = str(delta["id"])
        function = delta.get("function")
        if not isinstance(function, dict):
            return
        if function.get("name"):
            self.name += str(function["name"])
        arguments = function.get("arguments")
        if arguments is None:
            return
        if isinstance(arguments, str):
            self.arguments += arguments
        elif isinstance(arguments, (dict, list, int, float, bool)):
            # Some compatible servers emit an already-decoded JSON value in a
            # non-streamed choice. Preserve it as JSON instead of Python repr.
            self.arguments += json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        else:
            self.arguments += str(arguments)

    def to_tool_call(self) -> ToolCall:
        return ToolCall(
            id=self.id or f"call_{self.index}",
            function=FunctionCall(name=self.name, arguments=self.arguments or "{}"),
        )

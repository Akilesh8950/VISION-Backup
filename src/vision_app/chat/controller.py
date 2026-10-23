from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import AppConfig
from ..constants import SYSTEM_PROMPT
from ..history import HistoryStore
from ..telemetry.needs_log import NeedsLog
from ..tools.context import ToolContext
from ..tools.registry import ToolExecutionResult, ToolRegistry
from ..transport.client import ChatClient
from ..transport.endpoints import EndpointResolver, EndpointSelection
from ..transport.schemas import ChatMessage
from ..transport.sse import StreamAccumulator
from .commands import parse_command


@dataclass
class ChatCallbacks:
    on_content: Callable[[str], None] | None = None
    on_reasoning: Callable[[str], None] | None = None
    on_tool_start: Callable[[dict[str, Any]], None] | None = None
    on_tool_result: Callable[[ToolExecutionResult], None] | None = None
    on_endpoint: Callable[[EndpointSelection], None] | None = None
    on_sources: Callable[[list[dict[str, Any]]], None] | None = None
    on_error: Callable[[str], None] | None = None

    def emit(self, name: str, *args: Any) -> None:
        callback = getattr(self, name, None)
        if callback:
            callback(*args)


@dataclass
class TurnResult:
    conversation_id: str
    assistant_text: str = ""
    reasoning: str = ""
    incomplete: bool = False
    cancelled: bool = False
    error: str = ""
    tool_results: list[ToolExecutionResult] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    endpoint_label: str = ""


class ChatController:
    def __init__(
        self,
        *,
        config: AppConfig,
        history: HistoryStore,
        client: ChatClient,
        endpoint_resolver: EndpointResolver,
        registry: ToolRegistry,
        tool_context: ToolContext,
        needs_log: NeedsLog | None = None,
        callbacks: ChatCallbacks | None = None,
        max_model_rounds: int = 8,
    ):
        self.config = config
        self.history = history
        self.client = client
        self.endpoint_resolver = endpoint_resolver
        self.registry = registry
        self.tool_context = tool_context
        self.needs_log = needs_log
        self.callbacks = callbacks or ChatCallbacks()
        self.max_model_rounds = max_model_rounds

    def record_feedback(
        self,
        conversation_id: str,
        *,
        signal: str = "user_negative",
        detail: str = "",
        category: str = "general",
    ) -> None:
        if not self.needs_log:
            return
        prompt, answer = self.history.last_exchange(conversation_id)
        self.needs_log.add(
            category=category,
            prompt=prompt,
            model_output=answer,
            signal=signal,
            detail=detail,
        )

    def _handle_command(self, conversation_id: str, user_text: str) -> TurnResult | None:
        command = parse_command(user_text)
        if command is None:
            return None
        prompt, answer = self.history.last_exchange(conversation_id)
        if self.needs_log:
            detail = command.argument
            if command.name == "teach":
                detail = f"Correct answer: {command.argument}"
            self.needs_log.add(
                category="general",
                prompt=prompt,
                model_output=answer,
                signal="user_negative",
                detail=detail,
            )
        self.history.append_message(conversation_id, "user", user_text)
        confirmation = (
            "Thanks — I saved that correction to the local feedback queue."
            if command.name == "fix"
            else "Thanks — I saved that teaching example to the local feedback queue."
        )
        self.history.append_message(conversation_id, "assistant", confirmation)
        self.callbacks.emit("on_content", confirmation)
        return TurnResult(conversation_id=conversation_id, assistant_text=confirmation)

    async def run_turn(
        self,
        conversation_id: str,
        user_text: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        system_context: str = "",
        pre_sources: list[dict[str, Any]] | None = None,
        append_user: bool = True,
        is_regeneration: bool = False,
    ) -> TurnResult:
        if append_user:
            command_result = self._handle_command(conversation_id, user_text)
            if command_result is not None:
                return command_result
            self.history.append_message(conversation_id, "user", user_text)
        elif is_regeneration:
            self.record_feedback(conversation_id, detail="User requested regeneration")

        self.tool_context.conversation_id = conversation_id
        result = TurnResult(conversation_id=conversation_id, sources=list(pre_sources or []))
        tools = tools if tools is not None else self.registry.openai_tools()

        try:
            selection = await self.endpoint_resolver.select(self.client.health)
        except Exception as exc:  # health implementations should not raise, but never crash UI
            selection = EndpointSelection(None, error=str(exc))
        self.callbacks.emit("on_endpoint", selection)
        if selection.endpoint is None:
            message = selection.error or "No model endpoint is currently available."
            result.error = message
            result.incomplete = True
            self.history.append_message(conversation_id, "assistant", message, incomplete=True)
            self.callbacks.emit("on_error", message)
            return result

        endpoint = selection.endpoint
        result.endpoint_label = endpoint.label
        system = SYSTEM_PROMPT
        if system_context.strip():
            system += "\n\n" + system_context.strip()
        messages = [ChatMessage(role="system", content=system)]
        messages.extend(self.history.chat_messages(conversation_id))

        validation_batches_with_errors = 0
        current_accumulator: StreamAccumulator | None = None
        try:
            for _round in range(self.max_model_rounds):
                accumulator = StreamAccumulator()
                current_accumulator = accumulator
                async for event in self.client.stream_chat(endpoint, messages, tools, accumulator):
                    if event.kind == "content":
                        self.callbacks.emit("on_content", event.text)
                    elif event.kind == "reasoning":
                        self.callbacks.emit("on_reasoning", event.text)

                result.assistant_text = accumulator.content
                result.reasoning = accumulator.reasoning
                result.incomplete = accumulator.incomplete
                tool_calls = accumulator.ordered_tool_calls()
                assistant_message = ChatMessage(
                    role="assistant",
                    content=accumulator.content,
                    reasoning_content=accumulator.reasoning or None,
                    tool_calls=tool_calls or None,
                )
                messages.append(assistant_message)
                self.history.append_message(
                    conversation_id,
                    "assistant",
                    accumulator.content,
                    reasoning=accumulator.reasoning,
                    tool_calls=tool_calls or None,
                    incomplete=accumulator.incomplete,
                )

                if not tool_calls:
                    if self.needs_log:
                        category = result.tool_results[0].name if result.tool_results else "general"
                        self.needs_log.add(
                            category=category,
                            prompt=user_text,
                            model_output=accumulator.content,
                            signal="topic_usage",
                            detail=f"endpoint={endpoint.label}",
                        )
                    return result

                for call in tool_calls:
                    self.callbacks.emit(
                        "on_tool_start",
                        {"id": call.id, "name": call.function.name, "arguments": call.function.arguments},
                    )

                batch = await self.registry.execute_many(tool_calls, self.tool_context)
                result.tool_results.extend(batch)
                for tool_result in batch:
                    self.callbacks.emit("on_tool_result", tool_result)
                    if tool_result.sources:
                        result.sources.extend(tool_result.sources)
                        self.callbacks.emit("on_sources", result.sources)
                    if self.needs_log and tool_result.error_kind in {
                        "tool_schema_error", "tool_runtime_error", "code_error"
                    }:
                        self.needs_log.add(
                            category=tool_result.name,
                            prompt=user_text,
                            model_output=tool_result.content_for_model,
                            signal=tool_result.error_kind,
                            detail=tool_result.summary,
                        )
                    tool_message = ChatMessage(
                        role="tool",
                        content=tool_result.content_for_model,
                        tool_call_id=tool_result.tool_call_id,
                        name=tool_result.name,
                    )
                    messages.append(tool_message)
                    self.history.append_message(
                        conversation_id,
                        "tool",
                        tool_result.content_for_model,
                        tool_call_id=tool_result.tool_call_id,
                    )

                if any(item.is_validation_error for item in batch):
                    validation_batches_with_errors += 1
                    if validation_batches_with_errors > 2:
                        message = (
                            "I couldn't form a valid tool call after two corrected retries. "
                            "Please rephrase the request or tell me how you'd like to proceed."
                        )
                        result.assistant_text = message
                        result.incomplete = True
                        self.history.append_message(conversation_id, "assistant", message, incomplete=True)
                        self.callbacks.emit("on_content", message)
                        return result

            message = "I stopped after reaching the maximum number of tool rounds for this turn."
            result.assistant_text = message
            result.incomplete = True
            self.history.append_message(conversation_id, "assistant", message, incomplete=True)
            self.callbacks.emit("on_content", message)
            return result
        except asyncio.CancelledError:
            if current_accumulator is not None:
                result.assistant_text = current_accumulator.content
                result.reasoning = current_accumulator.reasoning
            result.cancelled = True
            result.incomplete = True
            if result.assistant_text:
                self.history.append_message(
                    conversation_id,
                    "assistant",
                    result.assistant_text,
                    reasoning=result.reasoning,
                    incomplete=True,
                )
            return result
        except Exception as exc:
            if current_accumulator is not None and not result.assistant_text:
                result.assistant_text = current_accumulator.content
                result.reasoning = current_accumulator.reasoning
            result.error = str(exc)
            result.incomplete = True
            message = result.assistant_text or f"The model request failed: {exc}"
            self.history.append_message(
                conversation_id,
                "assistant",
                message,
                reasoning=result.reasoning,
                incomplete=True,
            )
            self.callbacks.emit("on_error", str(exc))
            return result

    async def regenerate_last(self, conversation_id: str, **kwargs: Any) -> TurnResult:
        rows = self.history.list_messages(conversation_id)
        prompt = next((row["content"] for row in reversed(rows) if row["role"] == "user"), "")
        if not prompt:
            return TurnResult(conversation_id=conversation_id, error="Nothing to regenerate", incomplete=True)
        return await self.run_turn(
            conversation_id,
            prompt,
            append_user=False,
            is_regeneration=True,
            **kwargs,
        )

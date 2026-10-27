import asyncio
import json

from vision_app.chat.controller import ChatCallbacks, ChatController
from vision_app.config import AppConfig, EndpointMode
from vision_app.history import HistoryStore
from vision_app.paths import AppPaths
from vision_app.telemetry.needs_log import NeedsLog
from vision_app.tools.context import ToolContext
from vision_app.tools.registry import ToolRegistry
from vision_app.tools.sandbox import PathSandbox
from vision_app.transport.endpoints import EndpointResolver
from vision_app.transport.sse import StreamAccumulator


class FakeClient:
    def __init__(self):
        self.rounds = 0

    async def health(self, _url):
        return True

    async def stream_chat(self, endpoint, messages, tools, accumulator: StreamAccumulator):
        self.rounds += 1
        if self.rounds == 1:
            payload = {
                "choices": [{
                    "delta": {
                        "reasoning_content": "checking",
                        "tool_calls": [{
                            "index": 0,
                            "id": "call-weather",
                            "function": {"name": "memory_get", "arguments": json.dumps({"key": "city"})},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }]
            }
            for event in accumulator.feed_payload(payload):
                yield event
            accumulator.done = True
        else:
            assert any(m.role == "tool" and m.tool_call_id == "call-weather" for m in messages)
            for event in accumulator.feed_payload({"choices": [{"delta": {"content": "Your city is Coimbatore."}, "finish_reason": "stop"}]}):
                yield event
            accumulator.done = True


def test_controller_executes_tool_and_followup(tmp_path):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    paths.ensure()
    history = HistoryStore(paths.history_db)
    history.memory_set("city", "Coimbatore")
    conversation = history.create_conversation()
    context = ToolContext(paths=paths, history=history, sandbox=PathSandbox(paths.workspace), confirm=lambda *_: True)
    config = AppConfig(endpoint_mode=EndpointMode.LOCAL)
    needs = NeedsLog(paths.needs_log)
    seen = []
    controller = ChatController(
        config=config,
        history=history,
        client=FakeClient(),  # type: ignore[arg-type]
        endpoint_resolver=EndpointResolver(config),
        registry=ToolRegistry(),
        tool_context=context,
        needs_log=needs,
        callbacks=ChatCallbacks(on_content=seen.append),
    )
    result = asyncio.run(controller.run_turn(conversation, "What city did I save?"))
    assert result.assistant_text == "Your city is Coimbatore."
    assert result.reasoning == ""
    assert [row["role"] for row in history.list_messages(conversation)] == ["user", "assistant", "tool", "assistant"]
    needs.flush()
    # A clean, successful turn records no telemetry: needs.jsonl holds
    # only failures/corrections for the weekly model rebuild.
    assert needs.pending_count() == 0


class PartialFailureClient(FakeClient):
    async def stream_chat(self, endpoint, messages, tools, accumulator: StreamAccumulator):
        self.rounds += 1
        if self.rounds == 1:
            payload = {
                "choices": [{
                    "delta": {
                        "content": "Checking…",
                        "tool_calls": [{
                            "index": 0,
                            "id": "call-city",
                            "function": {"name": "memory_get", "arguments": json.dumps({"key": "city"})},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }]
            }
            for event in accumulator.feed_payload(payload):
                yield event
            accumulator.done = True
            return
        for event in accumulator.feed_payload({"choices": [{"delta": {"content": "Partial final"}}]}):
            yield event
        raise RuntimeError("stream died")


def _controller(tmp_path, client, registry=None):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    paths.ensure()
    history = HistoryStore(paths.history_db)
    history.memory_set("city", "Coimbatore")
    conversation = history.create_conversation()
    context = ToolContext(paths=paths, history=history, sandbox=PathSandbox(paths.workspace), confirm=lambda *_: True)
    config = AppConfig(endpoint_mode=EndpointMode.LOCAL)
    controller = ChatController(
        config=config,
        history=history,
        client=client,  # type: ignore[arg-type]
        endpoint_resolver=EndpointResolver(config),
        registry=registry or ToolRegistry(),
        tool_context=context,
    )
    return history, conversation, controller


def test_second_round_stream_failure_persists_current_partial_not_first_round_text(tmp_path):
    history, conversation, controller = _controller(tmp_path, PartialFailureClient())
    result = asyncio.run(controller.run_turn(conversation, "What city did I save?"))
    rows = history.list_messages(conversation)
    assert result.error == "stream died"
    assert result.assistant_text == "Partial final"
    assert [row["role"] for row in rows] == ["user", "assistant", "tool", "assistant"]
    assert [row["content"] for row in rows if row["role"] == "assistant"] == ["Checking…", "Partial final"]


def test_cancellation_during_tool_execution_does_not_duplicate_tool_call_message(tmp_path):
    registry = ToolRegistry()

    async def cancelled_execute_many(*_args, **_kwargs):
        raise asyncio.CancelledError

    registry.execute_many = cancelled_execute_many  # type: ignore[method-assign]
    history, conversation, controller = _controller(tmp_path, PartialFailureClient(), registry)
    result = asyncio.run(controller.run_turn(conversation, "What city did I save?"))
    rows = history.list_messages(conversation)
    assert result.cancelled
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert [row["content"] for row in rows if row["role"] == "assistant"] == ["Checking…"]


def test_fix_command_writes_feedback_without_model(tmp_path):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    paths.ensure()
    history = HistoryStore(paths.history_db)
    conversation = history.create_conversation()
    history.append_message(conversation, "user", "Weather?")
    history.append_message(conversation, "assistant", "Wrong")
    context = ToolContext(paths=paths, history=history, sandbox=PathSandbox(paths.workspace))
    config = AppConfig()
    needs = NeedsLog(paths.needs_log)
    controller = ChatController(
        config=config, history=history, client=FakeClient(), endpoint_resolver=EndpointResolver(config),
        registry=ToolRegistry(), tool_context=context, needs_log=needs,
    )  # type: ignore[arg-type]
    result = asyncio.run(controller.run_turn(conversation, "/fix check the actual weather"))
    assert "saved that correction" in result.assistant_text
    row = needs.read_all()[0]
    assert row["signal"] == "user_negative"
    assert row["detail"]["target"] == "check the actual weather"
    assert row["detail"]["command"] == "fix"


def test_trim_history_to_budget_never_orphans_tool_replies():
    from vision_app.chat.controller import trim_history_to_budget
    from vision_app.transport.schemas import ChatMessage, FunctionCall, ToolCall

    call = ToolCall(id="c1", function=FunctionCall(name="get_weather", arguments='{"city":"Chennai"}'))
    messages = [
        ChatMessage(role="user", content="u" * 500),
        ChatMessage(role="assistant", content="", tool_calls=[call]),
        ChatMessage(role="tool", content="t" * 500, tool_call_id="c1", name="get_weather"),
        ChatMessage(role="assistant", content="a" * 500),
        ChatMessage(role="user", content="latest question"),
        ChatMessage(role="assistant", content="latest answer"),
    ]
    trimmed = trim_history_to_budget(messages, 700)
    assert len(trimmed) < len(messages)
    assert trimmed[0].role != "tool"
    assert trimmed[-1].content == "latest answer"
    # A tiny budget still keeps the most recent exchange.
    tiny = trim_history_to_budget(messages, 1)
    assert len(tiny) == 2
    assert tiny[0].role != "tool"

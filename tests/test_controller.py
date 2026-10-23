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
    assert needs.pending_count() == 1


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
    assert row["signal"] == "user_negative" and row["detail"] == "check the actual weather"

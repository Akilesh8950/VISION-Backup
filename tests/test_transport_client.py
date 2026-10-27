import asyncio
import json

import httpx
import pytest

import vision_app.transport.client as client_module
from vision_app.transport.client import ChatClient, ChatClientError
from vision_app.transport.endpoints import Endpoint
from vision_app.transport.schemas import ChatMessage
from vision_app.transport.sse import StreamAccumulator


async def _run_stream():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "vision-test"}]})
        body = json.loads(request.content)
        assert body["model"] == "vision-test"
        assert body["stream"] is True
        lines = [
            'data: {"choices":[{"delta":{"reasoning_content":"plan","content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}',
            "data: [DONE]",
            "",
        ]
        return httpx.Response(200, text="\n".join(lines), headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        client = ChatClient(http_client)
        accumulator = StreamAccumulator()
        events = []
        async for event in client.stream_chat(
            Endpoint("local", "http://test/v1"),
            [ChatMessage(role="user", content="hi")],
            tools=[],
            accumulator=accumulator,
        ):
            events.append(event)
    return accumulator, events, requests


def test_chat_client_streams_and_resolves_model():
    accumulator, events, requests = asyncio.run(_run_stream())
    assert accumulator.content == "Hello"
    assert accumulator.reasoning == "plan"
    assert not accumulator.incomplete
    assert len(requests) == 2
    assert any(event.kind == "done" for event in events)


async def _run_oversized_stream():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "vision-test"}]})
        return httpx.Response(
            200,
            text="data: " + "x" * 64,
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        client = ChatClient(http_client)
        accumulator = StreamAccumulator()
        async for _event in client.stream_chat(
            Endpoint("local", "http://test/v1"),
            [ChatMessage(role="user", content="hi")],
            tools=[],
            accumulator=accumulator,
        ):
            pass


def test_chat_client_rejects_oversized_sse_line_without_newline(monkeypatch):
    monkeypatch.setattr(client_module, "MAX_SSE_LINE_BYTES", 8)
    with pytest.raises(ChatClientError, match="oversized"):
        asyncio.run(_run_oversized_stream())

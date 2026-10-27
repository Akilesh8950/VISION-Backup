from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .endpoints import Endpoint
from .schemas import ChatMessage
from .sse import MAX_SSE_LINE_BYTES, StreamAccumulator, StreamEvent


class ChatClientError(RuntimeError):
    pass


async def _response_error_detail(response: httpx.Response, limit: int = 2048) -> str:
    chunks: list[bytes] = []
    total = 0
    try:
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            total += len(chunk)
            if total >= limit:
                break
    except Exception:
        return ""
    text = b"".join(chunks)[:limit].decode("utf-8", errors="replace").strip()
    return text[:1000]


class ChatClient:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    def _headers(self, endpoint: Endpoint) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if endpoint.api_key:
            headers["Authorization"] = f"Bearer {endpoint.api_key}"
        return headers

    async def list_models(self, endpoint: Endpoint, timeout: float = 5.0) -> list[str]:
        url = endpoint.base_url.rstrip("/") + "/models"
        try:
            if self._client is not None:
                response = await self._client.get(url, headers=self._headers(endpoint), timeout=timeout)
            else:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.get(url, headers=self._headers(endpoint))
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ChatClientError(str(exc)) from exc
        if not isinstance(payload, dict):
            raise ChatClientError("Endpoint returned an invalid model list")
        models = []
        for item in payload.get("data") or []:
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
        return models

    async def health(self, endpoint: Endpoint) -> bool:
        try:
            await self.list_models(endpoint)
            return True
        except Exception:
            return False

    async def resolve_model(self, endpoint: Endpoint) -> Endpoint:
        if endpoint.model:
            return endpoint
        models = await self.list_models(endpoint)
        if not models:
            raise ChatClientError("Endpoint returned no models")
        return Endpoint(endpoint.kind, endpoint.base_url, models[0], endpoint.api_key)

    async def stream_chat(
        self,
        endpoint: Endpoint,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]],
        accumulator: StreamAccumulator,
        *,
        temperature: float | None = None,
        first_token_timeout: float = 120.0,
        total_timeout: float = 600.0,
    ) -> AsyncIterator[StreamEvent]:
        endpoint = await self.resolve_model(endpoint)
        payload: dict[str, Any] = {
            "model": endpoint.model,
            "messages": [message.to_wire() for message in messages],
            "stream": True,
            "tools": tools,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        url = endpoint.base_url.rstrip("/") + "/chat/completions"
        timeout = httpx.Timeout(connect=5.0, read=total_timeout, write=30.0, pool=5.0)
        client = self._client or httpx.AsyncClient(timeout=timeout)
        owns_client = self._client is None
        started = time.monotonic()
        first_event_seen = False
        try:
            async with client.stream(
                "POST", url, json=payload, headers=self._headers(endpoint), timeout=timeout
            ) as response:
                if response.is_error:
                    detail = await _response_error_detail(response)
                    suffix = f": {detail}" if detail else ""
                    raise ChatClientError(f"Model endpoint returned HTTP {response.status_code}{suffix}")
                iterator = response.aiter_bytes(chunk_size=8192).__aiter__()
                buffer = bytearray()

                def take_line() -> bytes | None:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        if len(buffer) > MAX_SSE_LINE_BYTES:
                            raise ChatClientError("Model endpoint sent an oversized streaming event")
                        return None
                    if newline > MAX_SSE_LINE_BYTES:
                        raise ChatClientError("Model endpoint sent an oversized streaming event")
                    raw = bytes(buffer[:newline].rstrip(b"\r"))
                    del buffer[: newline + 1]
                    return raw

                while True:
                    elapsed = time.monotonic() - started
                    remaining_total = total_timeout - elapsed
                    if remaining_total <= 0:
                        raise ChatClientError("Response exceeded the 600 second total timeout")
                    wait = remaining_total
                    if not first_event_seen:
                        wait = min(wait, max(0.01, first_token_timeout - elapsed))
                    try:
                        chunk = await asyncio.wait_for(iterator.__anext__(), timeout=wait)
                    except StopAsyncIteration:
                        break
                    except TimeoutError as exc:
                        if not first_event_seen:
                            raise ChatClientError("Timed out waiting for the first token") from exc
                        raise ChatClientError("Response exceeded the total timeout") from exc
                    if chunk:
                        buffer.extend(chunk)
                    while (line := take_line()) is not None:
                        for event in accumulator.feed_line(line):
                            first_event_seen = True
                            yield event
                        if accumulator.error:
                            raise ChatClientError(accumulator.error)
                if buffer:
                    if len(buffer) > MAX_SSE_LINE_BYTES:
                        raise ChatClientError("Model endpoint sent an oversized streaming event")
                    for event in accumulator.feed_line(bytes(buffer.rstrip(b"\r"))):
                        first_event_seen = True
                        yield event
                    if accumulator.error:
                        raise ChatClientError(accumulator.error)
            for event in accumulator.close():
                yield event
            if accumulator.error:
                raise ChatClientError(accumulator.error)
        except asyncio.CancelledError:
            # Flush the think splitter into the accumulator for the caller,
            # but do not attempt to yield from a generator being cancelled.
            accumulator.close()
            raise
        except ChatClientError:
            raise
        except Exception as exc:
            raise ChatClientError(str(exc)) from exc
        finally:
            if owns_client:
                await client.aclose()

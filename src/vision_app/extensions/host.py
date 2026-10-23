from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any

from .mcp_client import McpClientManager
from .mcp_config import McpServerConfig


class ExtensionHost:
    """Owns MCP sessions on one dedicated asyncio loop/thread."""

    def __init__(self, secret_resolver=None):
        self.manager = McpClientManager(secret_resolver=secret_resolver)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="vision-mcp-host")
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def submit(self, coroutine) -> concurrent.futures.Future:
        self.start()
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def discover(self, config: McpServerConfig, timeout: float = 30.0) -> dict[str, Any]:
        return self.submit(self.manager.discover(config)).result(timeout=timeout)

    async def call_tool_async(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        future = self.submit(self.manager.call_tool(server_id, tool_name, arguments))
        return await asyncio.wrap_future(future)

    def read_resource(self, server_id: str, uri: str, timeout: float = 30.0) -> Any:
        return self.submit(self.manager.read_resource(server_id, uri)).result(timeout=timeout)

    def get_prompt(self, server_id: str, name: str, arguments: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
        return self.submit(self.manager.get_prompt(server_id, name, arguments or {})).result(timeout=timeout)

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            try:
                self.submit(self.manager.close_all()).result(timeout=5)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from typing import Any

from .mcp_client import McpClientManager
from .mcp_config import McpServerConfig

logger = logging.getLogger(__name__)


class ExtensionHost:
    """Owns MCP sessions on one dedicated asyncio loop/thread.

    The host is intentionally single-use: once ``stop`` begins, late dynamic
    tool calls fail instead of resurrecting MCP subprocesses during app
    shutdown. Create a new host if a caller truly needs a fresh lifecycle.
    """

    def __init__(self, secret_resolver=None, *, sampler=None, roots_provider=None, default_cwd=None):
        self.manager = McpClientManager(
            secret_resolver=secret_resolver, sampler=sampler, roots_provider=roots_provider
        )
        # stdio servers run with their working directory inside the
        # workspace unless their own config pins a different cwd.
        self.manager.default_cwd = str(default_cwd) if default_cwd else None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._stop_requested = False

    def start(self) -> None:
        with self._start_lock:
            with self._lifecycle_lock:
                if self._stop_requested:
                    raise RuntimeError("Extension host has been stopped")
                if self._thread and self._thread.is_alive() and self._loop and self._loop.is_running():
                    return
                ready = threading.Event()
                thread = threading.Thread(target=self._run, args=(ready,), daemon=True, name="vision-mcp-host")
                self._thread = thread
                self._loop = None
                try:
                    thread.start()
                except BaseException:
                    self._thread = None
                    raise
            if not ready.wait(timeout=5) or self._loop is None or not self._loop.is_running():
                raise RuntimeError("Extension host event loop did not start")

    def _run(self, ready: threading.Event) -> None:
        loop = asyncio.new_event_loop()
        with self._lifecycle_lock:
            self._loop = loop
        asyncio.set_event_loop(loop)
        ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                logger.exception("Error while shutting down the extension event loop")
            finally:
                try:
                    loop.close()
                finally:
                    with self._lifecycle_lock:
                        if self._loop is loop:
                            self._loop = None

    def submit(self, coroutine) -> concurrent.futures.Future:
        try:
            with self._lifecycle_lock:
                if self._stop_requested:
                    raise RuntimeError("Extension host has been stopped")
            self.start()
            with self._lifecycle_lock:
                if self._stop_requested or self._loop is None:
                    raise RuntimeError("Extension host has been stopped")
                loop = self._loop
            return asyncio.run_coroutine_threadsafe(coroutine, loop)
        except BaseException:
            try:
                coroutine.close()
            except Exception:
                pass
            raise

    def discover(self, config: McpServerConfig, timeout: float = 30.0) -> dict[str, Any]:
        return self.submit(self.manager.discover(config)).result(timeout=timeout)

    async def call_tool_async(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        future = self.submit(self.manager.call_tool(server_id, tool_name, arguments))
        return await asyncio.wrap_future(future)

    def read_resource(self, server_id: str, uri: str, timeout: float = 30.0) -> Any:
        return self.submit(self.manager.read_resource(server_id, uri)).result(timeout=timeout)

    def get_prompt(self, server_id: str, name: str, arguments: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
        return self.submit(self.manager.get_prompt(server_id, name, arguments or {})).result(timeout=timeout)

    def set_user_callbacks(self, *, confirm=None, elicit=None, event_sink=None) -> None:
        """Bridge-injected UI callbacks for sampling/elicitation/events."""
        if confirm is not None:
            self.manager.confirm_user = confirm
        if elicit is not None:
            self.manager.elicit_user = elicit
        if event_sink is not None:
            self.manager.event_sink = event_sink

    def disconnect(self, server_id: str, timeout: float = 10.0) -> None:
        try:
            self.submit(self.manager.disconnect(server_id)).result(timeout=timeout)
        except Exception:
            logger.debug("disconnect(%s) failed", server_id, exc_info=True)

    def ping(self, server_id: str, timeout: float = 10.0) -> bool:
        try:
            return bool(self.submit(self.manager.ping(server_id)).result(timeout=timeout))
        except Exception:
            return False

    def stop(self) -> None:
        with self._lifecycle_lock:
            if self._stop_requested and not (self._thread and self._thread.is_alive()):
                return
            self._stop_requested = True
            loop = self._loop
            thread = self._thread

        if loop is not None and loop.is_running():
            if thread is threading.current_thread():
                async def close_then_stop() -> None:
                    try:
                        await self.manager.close_all()
                    finally:
                        loop.stop()

                asyncio.run_coroutine_threadsafe(close_then_stop(), loop)
                return
            try:
                future = asyncio.run_coroutine_threadsafe(self.manager.close_all(), loop)
                try:
                    future.result(timeout=5)
                except concurrent.futures.TimeoutError:
                    future.cancel()
                    logger.warning("Timed out closing MCP sessions")
            except Exception:
                logger.exception("Could not close MCP sessions cleanly")
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
            if thread.is_alive():
                logger.warning("Extension host thread did not stop within 5 seconds")

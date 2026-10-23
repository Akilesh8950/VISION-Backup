from __future__ import annotations

import inspect
import os
import shutil
from contextlib import AsyncExitStack
from typing import Any, Protocol

from .mcp_config import McpServerConfig
from .mcp_tools import McpToolDescriptor
from .trust import toolset_fingerprint


class SessionProtocol(Protocol):
    async def initialize(self) -> Any: ...
    async def list_tools(self) -> Any: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def _items(result: Any, *names: str) -> list[Any]:
    for name in names:
        value = result.get(name) if isinstance(result, dict) else getattr(result, name, None)
        if value is not None:
            return list(value)
    return list(result) if isinstance(result, (list, tuple)) else []


class McpClientManager:
    """Long-lived MCP sessions behind a small adapter.

    Tests may inject fake sessions with `register_session`. Real sessions use the
    official `mcp` SDK when installed; SDK calls are isolated in `_open_real_session`.
    """

    def __init__(self, secret_resolver=None):
        self.secret_resolver = secret_resolver or (lambda _key: "")
        self.sessions: dict[str, Any] = {}
        self.configs: dict[str, McpServerConfig] = {}
        self._stacks: dict[str, AsyncExitStack] = {}
        self.tools: dict[str, list[McpToolDescriptor]] = {}
        self.resources: dict[str, list[Any]] = {}
        self.prompts: dict[str, list[Any]] = {}
        self.last_errors: dict[str, str] = {}

    def register_session(self, config: McpServerConfig, session: Any) -> None:
        self.configs[config.id] = config
        self.sessions[config.id] = session

    def _resolve_refs(self, refs: dict[str, str], plain: dict[str, str]) -> dict[str, str]:
        result = dict(plain)
        for name, credential_key in refs.items():
            result[name] = str(self.secret_resolver(credential_key) or "")
        return result

    async def _open_real_session(self, config: McpServerConfig) -> Any:
        try:
            from mcp import ClientSession, StdioServerParameters  # type: ignore
        except Exception as exc:
            raise RuntimeError("The official MCP Python SDK (`mcp`) is not installed") from exc

        stack = AsyncExitStack()
        self._stacks[config.id] = stack
        if config.transport == "stdio":
            if not config.command:
                raise RuntimeError("stdio MCP server has no command")
            from mcp.client.stdio import stdio_client  # type: ignore

            resolved = shutil.which(config.command) or config.command
            env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
            env.update(self._resolve_refs(config.env_refs, config.env_plain))
            params = StdioServerParameters(command=resolved, args=config.args, env=env, cwd=config.cwd or None)
            read, write = await stack.enter_async_context(stdio_client(params))
        elif config.transport == "streamable_http":
            from mcp.client import streamable_http as module  # type: ignore

            factory = getattr(module, "streamable_http_client", None) or module.streamablehttp_client
            headers = self._resolve_refs(config.header_refs, config.headers_plain)
            parameters = inspect.signature(factory).parameters
            if "headers" in parameters:
                transport = factory(config.url, headers=headers)
            elif "http_client" in parameters:
                try:
                    import httpx2 as http_lib  # type: ignore
                except Exception:
                    import httpx as http_lib  # type: ignore
                http_client = await stack.enter_async_context(http_lib.AsyncClient(headers=headers, timeout=30.0))
                transport = factory(config.url, http_client=http_client)
            else:
                transport = factory(config.url)
            streams = await stack.enter_async_context(transport)
            read, write = streams[0], streams[1]
        elif config.transport == "sse":
            from mcp.client.sse import sse_client  # type: ignore

            headers = self._resolve_refs(config.header_refs, config.headers_plain)
            read, write = await stack.enter_async_context(sse_client(config.url, headers=headers))
        else:
            raise RuntimeError(f"Unsupported MCP transport {config.transport}")

        session = await stack.enter_async_context(ClientSession(read, write))
        try:
            await session.initialize()
        except TypeError:
            # Newer stateless protocol revisions may not require the same call shape;
            # list operations in discover() will surface a real incompatibility.
            pass
        return session

    async def connect(self, config: McpServerConfig) -> Any:
        self.configs[config.id] = config
        if config.id in self.sessions:
            return self.sessions[config.id]
        try:
            session = await self._open_real_session(config)
            self.sessions[config.id] = session
            self.last_errors.pop(config.id, None)
            return session
        except Exception as exc:
            self.last_errors[config.id] = str(exc)
            raise

    async def discover(self, config: McpServerConfig) -> dict[str, Any]:
        session = await self.connect(config)
        tools: list[McpToolDescriptor] = []
        resources: list[Any] = []
        prompts: list[Any] = []
        try:
            tools = [McpToolDescriptor.from_any(item) for item in _items(await session.list_tools(), "tools")]
        except Exception as exc:
            self.last_errors[config.id] = str(exc)
        if hasattr(session, "list_resources"):
            try:
                resources = _items(await session.list_resources(), "resources")
            except Exception:
                resources = []
        if hasattr(session, "list_resource_templates"):
            try:
                resources.extend(_items(await session.list_resource_templates(), "resourceTemplates", "resource_templates"))
            except Exception:
                pass
        if hasattr(session, "list_prompts"):
            try:
                prompts = _items(await session.list_prompts(), "prompts")
            except Exception:
                prompts = []
        self.tools[config.id] = tools
        self.resources[config.id] = resources
        self.prompts[config.id] = prompts
        all_items = [
            {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}
            for tool in tools
        ]
        return {
            "tools": tools,
            "resources": resources,
            "prompts": prompts,
            "fingerprint": toolset_fingerprint(all_items),
        }

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        config = self.configs.get(server_id)
        if config is None:
            raise RuntimeError(f"MCP server is not configured: {server_id}")
        session = await self.connect(config)
        return await session.call_tool(tool_name, arguments)

    async def read_resource(self, server_id: str, uri: str) -> Any:
        config = self.configs[server_id]
        session = await self.connect(config)
        return await session.read_resource(uri)

    async def get_prompt(self, server_id: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
        config = self.configs[server_id]
        session = await self.connect(config)
        return await session.get_prompt(name, arguments or {})

    async def disconnect(self, server_id: str) -> None:
        self.sessions.pop(server_id, None)
        stack = self._stacks.pop(server_id, None)
        if stack is not None:
            await stack.aclose()

    async def close_all(self) -> None:
        for server_id in list(self.sessions):
            await self.disconnect(server_id)

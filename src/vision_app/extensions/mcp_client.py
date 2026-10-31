from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Any, Protocol

from .mcp_config import McpServerConfig
from .mcp_tools import McpToolDescriptor
from .trust import toolset_fingerprint

MAX_DISCOVERED_ITEMS_PER_KIND = 256


class SessionProtocol(Protocol):
    async def initialize(self) -> Any: ...
    async def list_tools(self) -> Any: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def _items(result: Any, *names: str) -> list[Any]:
    for name in names:
        value = result.get(name) if isinstance(result, dict) else getattr(result, name, None)
        if isinstance(value, (list, tuple)):
            return list(value)
        if value is not None:
            return [value]
    return list(result) if isinstance(result, (list, tuple)) else []


def _connection_fingerprint(config: McpServerConfig) -> str:
    payload = {
        "transport": config.transport,
        "command": config.command,
        "args": config.args,
        "cwd": config.cwd,
        "env_refs": config.env_refs,
        "env_plain": config.env_plain,
        "url": config.url,
        "header_refs": config.header_refs,
        "headers_plain": config.headers_plain,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return "timeout" in name or "timed out" in message or "read timeout" in message


def _is_connection_error(exc: BaseException) -> bool:
    if isinstance(exc, (BrokenPipeError, ConnectionError, EOFError)):
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    markers = (
        "closedresource",
        "brokenresource",
        "endofstream",
        "connection closed",
        "session closed",
        "not connected",
        "broken pipe",
        "eof occurred",
        "stream closed",
    )
    return any(marker in name or marker in message for marker in markers)


def _content_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(filter(None, (_content_text(item) for item in content)))
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return ""


def sampling_messages_as_chat(params: Any) -> tuple[list[dict[str, str]], int]:
    """Convert MCP sampling params into OpenAI-style messages + max tokens."""
    messages: list[dict[str, str]] = []
    system_prompt = getattr(params, "system_prompt", None)
    if isinstance(params, dict):
        system_prompt = params.get("system_prompt", system_prompt)
    if system_prompt:
        messages.append({"role": "system", "content": str(system_prompt)[:4000]})
    raw_messages = getattr(params, "messages", None)
    if isinstance(params, dict):
        raw_messages = params.get("messages", raw_messages)
    for item in raw_messages or []:
        role = getattr(item, "role", None)
        content = getattr(item, "content", None)
        if isinstance(item, dict):
            role = item.get("role", role)
            content = item.get("content", content)
        text = _content_text(content)[:8000]
        if text:
            messages.append(
                {"role": "assistant" if role == "assistant" else "user", "content": text}
            )
    max_tokens = getattr(params, "max_tokens", None)
    if isinstance(params, dict):
        max_tokens = params.get("max_tokens", max_tokens)
    try:
        max_tokens = int(max_tokens or 512)
    except Exception:
        max_tokens = 512
    return messages, max(16, min(4096, max_tokens))


def elicitation_fields(schema: Any) -> list[dict[str, Any]]:
    """Flatten an MCP elicitation requestedSchema into UI field descriptors."""
    fields: list[dict[str, Any]] = []
    if not isinstance(schema, dict):
        return fields
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return fields
    required = {str(name) for name in (schema.get("required") or [])}
    for name, spec in list(properties.items())[:32]:
        spec = spec if isinstance(spec, dict) else {}
        choices = spec.get("enum") or []
        if not choices:
            one_of = spec.get("oneOf") or spec.get("anyOf") or []
            choices = [
                str(item.get("const"))
                for item in one_of
                if isinstance(item, dict) and "const" in item
            ]
        fields.append(
            {
                "name": str(name),
                "title": str(spec.get("title") or name),
                "description": str(spec.get("description") or ""),
                "type": str(spec.get("type") or "string"),
                "enum": [str(choice) for choice in choices[:24]],
                "required": str(name) in required,
            }
        )
    return fields


def coerce_elicitation_values(
    fields: list[dict[str, Any]], values: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields:
        name = field["name"]
        if name not in values:
            continue
        raw = values[name]
        kind = field.get("type", "string")
        if kind == "boolean":
            result[name] = raw if isinstance(raw, bool) else str(raw).lower() in {"true", "yes", "1"}
        elif kind == "integer":
            try:
                result[name] = int(raw)
            except Exception:
                result[name] = 0
        elif kind == "number":
            try:
                result[name] = float(raw)
            except Exception:
                result[name] = 0.0
        else:
            result[name] = str(raw)[:4096]
    return result


class McpClientManager:
    """Long-lived MCP sessions behind a small adapter.

    Tests may inject fake sessions with `register_session`. Real sessions use
    the official `mcp` SDK when installed; SDK calls are isolated in
    `_open_real_session`.
    """

    def __init__(
        self,
        secret_resolver=None,
        *,
        sampler: Callable[..., str] | None = None,
        roots_provider: Callable[[], Any] | None = None,
    ):
        self.secret_resolver = secret_resolver or (lambda _key: "")
        # Injected by the app/bridge; sync callables run via to_thread.
        self.sampler = sampler  # (messages, max_tokens) -> str
        self.roots_provider = roots_provider  # () -> list[(uri, name)]
        self.confirm_user: Callable[..., bool] | None = None
        self.elicit_user: Callable[..., Any] | None = None
        self.event_sink: Callable[..., Any] | None = None
        self.server_logs: dict[str, list[str]] = {}
        self.last_progress: dict[str, dict[str, Any]] = {}
        self.stale: set[str] = set()
        # Servers that timed out are parked here for the rest of the app
        # session (cleared by a successful explicit re-discovery).
        self.unavailable: set[str] = set()
        self.default_cwd: str | None = None
        self.sessions: dict[str, Any] = {}
        self.configs: dict[str, McpServerConfig] = {}
        self._stacks: dict[str, AsyncExitStack] = {}
        # anyio contexts (stdio_client/ClientSession) must be exited in
        # the same task that entered them, so every real session is owned
        # by a dedicated task that opens it, waits for the close signal,
        # and unwinds the stack itself.
        self._owners: dict[str, asyncio.Task] = {}
        self._close_events: dict[str, asyncio.Event] = {}
        self._connection_fingerprints: dict[str, str] = {}
        self._connect_locks: dict[str, asyncio.Lock] = {}
        self.tools: dict[str, list[McpToolDescriptor]] = {}
        self.resources: dict[str, list[Any]] = {}
        self.prompts: dict[str, list[Any]] = {}
        self.last_errors: dict[str, str] = {}

    def register_session(self, config: McpServerConfig, session: Any) -> None:
        self.configs[config.id] = config
        self.sessions[config.id] = session
        self._connection_fingerprints[config.id] = _connection_fingerprint(config)

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
        try:
            if config.transport == "stdio":
                if not config.command:
                    raise RuntimeError("stdio MCP server has no command")
                from mcp.client.stdio import stdio_client  # type: ignore

                resolved = shutil.which(config.command) or config.command
                env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
                env.update(self._resolve_refs(config.env_refs, config.env_plain))
                params = StdioServerParameters(command=resolved, args=config.args, env=env, cwd=config.cwd or self.default_cwd)
                read, write = await stack.enter_async_context(stdio_client(params))
            elif config.transport == "streamable_http":
                from mcp.client import streamable_http as module  # type: ignore

                factory = getattr(module, "streamable_http_client", None) or getattr(
                    module, "streamablehttp_client", None
                )
                if factory is None:
                    raise RuntimeError("The installed MCP SDK has no Streamable HTTP client")
                headers = self._resolve_refs(config.header_refs, config.headers_plain)
                try:
                    parameters: Any = inspect.signature(factory).parameters
                except (TypeError, ValueError):
                    parameters = {}
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

            from mcp import types as mcp_types

            manager = self

            async def sampling_callback(_context, params):
                if manager.sampler is None or not config.allow_sampling:
                    raise RuntimeError("Sampling is disabled for this server in VISION")
                messages, max_tokens = sampling_messages_as_chat(params)
                last_user = next(
                    (item["content"] for item in reversed(messages) if item["role"] == "user"), ""
                )
                if manager.confirm_user is None:
                    raise RuntimeError("Sampling requires interactive user approval")
                approved = await asyncio.to_thread(
                    manager.confirm_user,
                    f"{config.display_name} wants VISION to generate text",
                    "An MCP server asked VISION's model to generate text. "
                    "Only allow this if you trust this server:",
                    {
                        "server": config.id,
                        "prompt": last_user[:1000],
                        "max_tokens": max_tokens,
                    },
                )
                if not approved:
                    raise RuntimeError("User declined sampling")
                text = await asyncio.to_thread(manager.sampler, messages, max_tokens)
                result_kwargs: dict[str, Any] = {
                    "role": "assistant",
                    "content": mcp_types.TextContent(type="text", text=str(text)[:200_000]),
                    "model": "VISION-local",
                }
                try:
                    return mcp_types.CreateMessageResult(**result_kwargs, stop_reason="endTurn")
                except Exception:
                    return mcp_types.CreateMessageResult(**result_kwargs)

            async def elicitation_callback(_context, params):
                if getattr(params, "mode", "form") == "url" or manager.elicit_user is None:
                    return mcp_types.ElicitResult(action="decline")
                message = str(getattr(params, "message", "") or "")[:4000]
                schema = getattr(params, "requested_schema", None)
                if hasattr(schema, "model_dump"):
                    schema = schema.model_dump()
                fields = elicitation_fields(schema if isinstance(schema, dict) else {})
                values = await asyncio.to_thread(
                    manager.elicit_user,
                    f"{config.display_name} is asking",
                    message or "This MCP server needs some information to continue.",
                    fields,
                )
                if not isinstance(values, dict):
                    return mcp_types.ElicitResult(action="decline")
                return mcp_types.ElicitResult(
                    action="accept", content=coerce_elicitation_values(fields, values)
                )

            async def list_roots_callback(_context=None, _params=None):
                roots = []
                try:
                    for entry in manager.roots_provider() if manager.roots_provider else []:
                        uri, name = entry if isinstance(entry, tuple) else (entry, "VISION")
                        roots.append(mcp_types.Root(uri=str(uri), name=str(name)[:120]))
                except Exception:
                    roots = []
                return mcp_types.ListRootsResult(roots=roots)

            async def logging_callback(params):
                try:
                    line = f"[{getattr(params, 'level', 'info')}] {getattr(params, 'data', '')}"[:1000]
                    entries = manager.server_logs.setdefault(config.id, [])
                    entries.append(line)
                    del entries[:-50]
                    if manager.event_sink is not None:
                        manager.event_sink("log", config.id, {"line": line})
                except Exception:
                    pass

            async def message_handler(msg):
                try:
                    root = getattr(msg, "root", msg)
                    method = str(getattr(root, "method", "") or "")
                    if "list_changed" in method or "ListChanged" in type(root).__name__:
                        manager.stale.add(config.id)
                        if manager.event_sink is not None:
                            manager.event_sink("list_changed", config.id, {"method": method})
                except Exception:
                    pass

            extras: dict[str, Any] = {
                "read_timeout_seconds": float(config.timeout_seconds or 60),
                "logging_callback": logging_callback,
                "message_handler": message_handler,
            }
            try:
                extras["client_info"] = mcp_types.Implementation(name="VISION", version="1.0.0")
            except Exception:
                pass
            if config.allow_sampling and manager.sampler is not None and manager.confirm_user is not None:
                extras["sampling_callback"] = sampling_callback
            if config.allow_elicitation and manager.elicit_user is not None:
                extras["elicitation_callback"] = elicitation_callback
            if manager.roots_provider is not None:
                extras["list_roots_callback"] = list_roots_callback
            parameter_names = set(inspect.signature(ClientSession).parameters)
            session_kwargs = {k: v for k, v in extras.items() if k in parameter_names}
            try:
                session = await stack.enter_async_context(ClientSession(read, write, **session_kwargs))
            except TypeError:
                session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except BaseException:
            # Whether opening the transport, entering the session, or
            # initialization failed, the partially entered stack owns real
            # subprocess/socket resources and must be unwound immediately.
            try:
                await stack.aclose()
            except Exception:
                pass
            raise
        self._stacks[config.id] = stack
        return session

    async def _start_owned_session(self, config: McpServerConfig) -> Any:
        loop = asyncio.get_running_loop()
        ready: asyncio.Future = loop.create_future()
        close_event = asyncio.Event()
        owner = asyncio.create_task(self._own_session(config, close_event, ready))
        self._owners[config.id] = owner
        self._close_events[config.id] = close_event
        try:
            return await ready
        except BaseException:
            self._owners.pop(config.id, None)
            self._close_events.pop(config.id, None)
            raise

    async def _own_session(
        self, config: McpServerConfig, close_event: asyncio.Event, ready: asyncio.Future
    ) -> None:
        try:
            session = await self._open_real_session(config)
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            return
        if not ready.done():
            ready.set_result(session)
        try:
            await close_event.wait()
        finally:
            stack = self._stacks.pop(config.id, None)
            if stack is not None:
                try:
                    await stack.aclose()
                except Exception as exc:  # close must never wedge shutdown
                    self.last_errors[config.id] = str(exc)

    async def connect(self, config: McpServerConfig) -> Any:
        fingerprint = _connection_fingerprint(config)
        lock = self._connect_locks.setdefault(config.id, asyncio.Lock())
        async with lock:
            existing = self.sessions.get(config.id)
            if existing is not None and self._connection_fingerprints.get(config.id) != fingerprint:
                await self._disconnect_locked(config.id)
                existing = None
            self.configs[config.id] = config
            if existing is not None:
                return existing
            try:
                session = await self._start_owned_session(config)
            except Exception as exc:
                self.last_errors[config.id] = str(exc)
                raise
            self.sessions[config.id] = session
            self._connection_fingerprints[config.id] = fingerprint
            self.last_errors.pop(config.id, None)
            return session

    async def _evict_if_connection_error(self, server_id: str, exc: BaseException) -> None:
        self.last_errors[server_id] = str(exc)
        if _is_timeout_error(exc):
            self.unavailable.add(server_id)
            await self.disconnect(server_id)
        elif _is_connection_error(exc):
            await self.disconnect(server_id)

    def clear_unavailable(self, server_id: str) -> None:
        self.unavailable.discard(server_id)
        self.last_errors.pop(server_id, None)

    async def discover(self, config: McpServerConfig) -> dict[str, Any]:
        # An explicit discovery is the user's "try again": it clears the
        # session unavailable mark before reconnecting.
        self.clear_unavailable(config.id)
        session = await self.connect(config)
        tools: list[McpToolDescriptor] = []
        resources: list[Any] = []
        prompts: list[Any] = []
        try:
            raw_tools = _items(await session.list_tools(), "tools")
            if len(raw_tools) > MAX_DISCOVERED_ITEMS_PER_KIND:
                self.last_errors[config.id] = (
                    f"Ignored {len(raw_tools) - MAX_DISCOVERED_ITEMS_PER_KIND} MCP tools above the "
                    f"{MAX_DISCOVERED_ITEMS_PER_KIND}-item discovery limit"
                )
            for item in raw_tools[:MAX_DISCOVERED_ITEMS_PER_KIND]:
                try:
                    tools.append(McpToolDescriptor.from_any(item))
                except Exception as exc:
                    self.last_errors[config.id] = f"Ignored an invalid MCP tool descriptor: {exc}"
        except Exception as exc:
            self.last_errors[config.id] = str(exc)
            await self._evict_if_connection_error(config.id, exc)
        if hasattr(session, "list_resources"):
            try:
                resources = _items(await session.list_resources(), "resources")[:MAX_DISCOVERED_ITEMS_PER_KIND]
            except Exception as exc:
                resources = []
                await self._evict_if_connection_error(config.id, exc)
        if hasattr(session, "list_resource_templates"):
            try:
                remaining = max(0, MAX_DISCOVERED_ITEMS_PER_KIND - len(resources))
                resources.extend(
                    _items(
                        await session.list_resource_templates(),
                        "resourceTemplates",
                        "resource_templates",
                    )[:remaining]
                )
            except Exception as exc:
                await self._evict_if_connection_error(config.id, exc)
        if hasattr(session, "list_prompts"):
            try:
                prompts = _items(await session.list_prompts(), "prompts")[:MAX_DISCOVERED_ITEMS_PER_KIND]
            except Exception as exc:
                prompts = []
                await self._evict_if_connection_error(config.id, exc)
        self.tools[config.id] = tools
        self.resources[config.id] = resources
        self.prompts[config.id] = prompts
        self.stale.discard(config.id)
        all_items = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "annotations": tool.annotations,
            }
            for tool in tools
        ]
        return {
            "tools": tools,
            "resources": resources,
            "prompts": prompts,
            "fingerprint": toolset_fingerprint(all_items),
        }

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        if server_id in self.unavailable:
            raise RuntimeError(
                "This MCP server timed out earlier and is unavailable for this session. "
                "Use Check on its card in Settings to reconnect."
            )
        config = self.configs.get(server_id)
        if config is None:
            raise RuntimeError(f"MCP server is not configured: {server_id}")
        session = await self.connect(config)
        call_kwargs: dict[str, Any] = {}
        try:
            parameters: Any = inspect.signature(session.call_tool).parameters
        except Exception:
            parameters = {}
        if "progress_callback" in parameters:

            async def progress_callback(progress, total, message=None):
                self.last_progress[server_id] = {
                    "progress": progress,
                    "total": total,
                    "message": str(message or ""),
                }
                if self.event_sink is not None:
                    self.event_sink(
                        "progress",
                        server_id,
                        {"progress": progress, "total": total, "message": str(message or "")},
                    )

            call_kwargs["progress_callback"] = progress_callback
        try:
            return await session.call_tool(tool_name, arguments, **call_kwargs)
        except Exception as exc:
            await self._evict_if_connection_error(server_id, exc)
            raise

    async def ping(self, server_id: str) -> bool:
        session = self.sessions.get(server_id)
        if session is None or not hasattr(session, "send_ping"):
            return False
        try:
            await session.send_ping()
            return True
        except Exception:
            return False

    async def read_resource(self, server_id: str, uri: str) -> Any:
        config = self.configs.get(server_id)
        if config is None:
            raise RuntimeError(f"MCP server is not configured: {server_id}")
        session = await self.connect(config)
        try:
            return await session.read_resource(uri)
        except Exception as exc:
            await self._evict_if_connection_error(server_id, exc)
            raise

    async def get_prompt(self, server_id: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
        config = self.configs.get(server_id)
        if config is None:
            raise RuntimeError(f"MCP server is not configured: {server_id}")
        session = await self.connect(config)
        try:
            return await session.get_prompt(name, arguments or {})
        except Exception as exc:
            await self._evict_if_connection_error(server_id, exc)
            raise

    async def _disconnect_locked(self, server_id: str) -> None:
        self.sessions.pop(server_id, None)
        self._connection_fingerprints.pop(server_id, None)
        self.tools.pop(server_id, None)
        self.resources.pop(server_id, None)
        self.prompts.pop(server_id, None)
        owner = self._owners.pop(server_id, None)
        close_event = self._close_events.pop(server_id, None)
        if owner is not None:
            # The owner task unwinds the transport stack in the task that
            # entered it; we only signal and wait (bounded).
            if close_event is not None:
                close_event.set()
            try:
                await asyncio.wait_for(asyncio.shield(owner), timeout=10)
            except TimeoutError:
                owner.cancel()
                try:
                    await owner
                except BaseException:
                    pass
                self._stacks.pop(server_id, None)
            except BaseException:
                pass
            return
        stack = self._stacks.pop(server_id, None)
        if stack is not None:
            try:
                await asyncio.wait_for(stack.aclose(), timeout=10)
            except Exception as exc:
                self.last_errors[server_id] = str(exc)

    async def disconnect(self, server_id: str) -> None:
        lock = self._connect_locks.setdefault(server_id, asyncio.Lock())
        async with lock:
            await self._disconnect_locked(server_id)

    async def close_all(self) -> None:
        server_ids = set(self.sessions) | set(self._stacks) | set(self.tools) | set(self.resources) | set(self.prompts)
        results = await asyncio.gather(
            *(self.disconnect(server_id) for server_id in server_ids),
            return_exceptions=True,
        )
        for server_id, result in zip(server_ids, results, strict=False):
            if isinstance(result, Exception):
                self.last_errors[server_id] = str(result)

import asyncio
import json
from typing import ClassVar

from vision_app.extensions.mcp_client import McpClientManager
from vision_app.extensions.mcp_config import McpServerConfig
from vision_app.extensions.mcp_tools import McpToolDescriptor, make_mcp_tool_definition, namespaced_name
from vision_app.extensions.trust import toolset_fingerprint
from vision_app.history import HistoryStore
from vision_app.paths import AppPaths
from vision_app.tools.context import ToolContext
from vision_app.tools.registry import ToolRegistry
from vision_app.tools.sandbox import PathSandbox
from vision_app.transport.schemas import FunctionCall, ToolCall


class Result:
    tools: ClassVar[list[dict]] = [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]


class FakeSession:
    async def list_tools(self):
        return Result()

    async def call_tool(self, name, arguments):
        return {"content": [{"type": "text", "text": arguments["text"]}], "isError": False}


def test_namespace_and_fingerprint_change():
    assert namespaced_name("My Server", "read file") == "mcp__My_Server__read_file"
    a = toolset_fingerprint([{"name": "x", "inputSchema": {"type": "object"}}])
    b = toolset_fingerprint([{"name": "x", "inputSchema": {"type": "object", "properties": {"y": {}}}}])
    assert a != b


def test_fake_mcp_discovery_and_registry_call_requires_consent(tmp_path):
    config = McpServerConfig(id="fake", display_name="Fake", transport="stdio", command="fake")
    manager = McpClientManager()
    manager.register_session(config, FakeSession())
    discovery = asyncio.run(manager.discover(config))
    assert discovery["tools"][0].name == "echo"

    descriptor = McpToolDescriptor.from_any(Result.tools[0])
    definition = make_mcp_tool_definition(config, descriptor, manager.call_tool)
    registry = ToolRegistry()
    registry.register_dynamic(definition)

    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    paths.ensure()
    history = HistoryStore(paths.history_db)
    denied = ToolContext(paths=paths, history=history, sandbox=PathSandbox(paths.workspace), confirm=lambda *_: False)
    allowed = ToolContext(paths=paths, history=history, sandbox=PathSandbox(paths.workspace), confirm=lambda *_: True)
    call = ToolCall(id="1", function=FunctionCall(name=definition.name, arguments=json.dumps({"text": "hello"})))
    assert asyncio.run(registry.execute(call, denied)).error_kind == "blocked"
    result = asyncio.run(registry.execute(call, allowed))
    assert result.content_for_model == "hello"

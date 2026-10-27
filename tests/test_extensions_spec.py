"""Spec tests: MCP client, Skills, Connectors, Tool Router, safety."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from vision_app.app import build_services
from vision_app.extensions.mcp_client import McpClientManager
from vision_app.extensions.mcp_config import McpServerConfig
from vision_app.extensions.mcp_tools import (
    McpToolDescriptor,
    enforce_allow_paths,
    is_destructive_tool_name,
    make_mcp_tool_definition,
)
from vision_app.extensions.skills import SkillManager
from vision_app.history import HistoryStore
from vision_app.logging_setup import AuditLog
from vision_app.paths import AppPaths
from vision_app.tools import schemas as tool_schemas
from vision_app.tools.context import ToolContext
from vision_app.tools.registry import ToolDefinition, ToolRegistry
from vision_app.tools.router import estimate_tokens, select_tool_schemas
from vision_app.tools.sandbox import PathSandbox
from vision_app.transport.schemas import ToolCall
from vision_app.web_bridge import VisionWebApi

FAKE_SERVER = Path(__file__).with_name("fake_mcp_server.py")


class DummyCredentials:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key, "")

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


def make_api(tmp_path):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "workspace")
    services = build_services(paths)
    services.credentials = DummyCredentials()
    api = VisionWebApi(services)
    api.credentials = services.credentials
    return api, paths, services


def _context(tmp_path, **overrides):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    paths.ensure()
    history = HistoryStore(paths.history_db)
    kwargs = {
        "paths": paths,
        "history": history,
        "sandbox": PathSandbox(paths.workspace),
        "confirm": lambda *_args: True,
    }
    kwargs.update(overrides)
    return ToolContext(**kwargs)


def _call(name: str, args: dict | None = None) -> ToolCall:
    return ToolCall(id="c1", function={"name": name, "arguments": json.dumps(args or {})})


def _fake_config(tmp_path, **overrides) -> McpServerConfig:
    data = {
        "id": "fake",
        "display_name": "Fake Tools",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(FAKE_SERVER)],
        "enabled": True,
        "env_plain": {"DECLARED_VAR": "declared-value"},
        "timeout_seconds": 10,
    }
    data.update(overrides)
    return McpServerConfig.model_validate(data)


# ---------------------------------------------------------------------------
# 1) Real MCP handshake + discovery + namespacing + env/cwd isolation
# ---------------------------------------------------------------------------

def test_real_stdio_handshake_discovery_and_namespacing(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TEST_VAR", "must-never-leak")
    monkeypatch.setenv("HF_TOKEN", "must-never-leak-either")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = McpClientManager(secret_resolver=lambda _key: "")
    manager.default_cwd = str(workspace)
    config = _fake_config(tmp_path)

    async def run():
        discovery = await manager.discover(config)
        try:
            names = {tool.name for tool in discovery["tools"]}
            assert {"echo_text", "weather_lookup", "delete_everything"} <= names
            assert discovery["fingerprint"]

            result = await manager.call_tool("fake", "echo_text", {"text": "hello"})
            text = str(getattr(result, "content", result))
            assert "echo:hello" in text

            # Only explicitly declared env reaches the subprocess.
            leaked = str(await manager.call_tool("fake", "env_probe", {"name": "SECRET_TEST_VAR"}))
            hf = str(await manager.call_tool("fake", "env_probe", {"name": "HF_TOKEN"}))
            declared = str(await manager.call_tool("fake", "env_probe", {"name": "DECLARED_VAR"}))
            assert "must-never-leak" not in leaked and "<missing>" in leaked
            assert "must-never-leak" not in hf and "<missing>" in hf
            assert "declared-value" in declared

            # stdio servers run with cwd inside the workspace by default.
            cwd = str(await manager.call_tool("fake", "cwd_probe", {}))
            assert str(workspace) in cwd

            # Namespacing makes collisions impossible.
            descriptor = next(t for t in discovery["tools"] if t.name == "echo_text")

            async def call_tool(server_id, tool_name, arguments):
                return await manager.call_tool(server_id, tool_name, arguments)

            definition = make_mcp_tool_definition(config, descriptor, call_tool)
            assert definition.name == "mcp__fake__echo_text"
            assert definition.source == "mcp:fake"
        finally:
            await manager.close_all()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 2) Timeout: server parked as unavailable; chat never breaks
# ---------------------------------------------------------------------------

class _TimeoutSession:
    async def call_tool(self, name, arguments):
        raise TimeoutError("read timed out")


def test_timeout_marks_server_unavailable_without_raising_to_chat(tmp_path):
    manager = McpClientManager(secret_resolver=lambda _key: "")
    config = McpServerConfig.model_validate(
        {"id": "slow", "display_name": "Slow", "transport": "stdio", "command": "python", "enabled": True}
    )
    manager.register_session(config, _TimeoutSession())

    async def failing_call(server_id, tool_name, arguments):
        return await manager.call_tool(server_id, tool_name, arguments)

    descriptor = McpToolDescriptor(name="slow_tool", description="A slow tool", input_schema={"type": "object", "properties": {}})
    definition = make_mcp_tool_definition(config, descriptor, failing_call)
    registry = ToolRegistry()
    registry.register_dynamic(definition)
    context = _context(tmp_path)

    result = asyncio.run(registry.execute(_call(definition.name), context))
    assert result.is_error  # surfaced as a tool error, never an exception
    assert "slow" in manager.unavailable

    # Subsequent calls short-circuit with a clear message, still no raise.
    result = asyncio.run(registry.execute(_call(definition.name), context))
    assert result.is_error
    assert "unavailable for this session" in result.content_for_model


# ---------------------------------------------------------------------------
# 3) Tool Router: caps + ranking
# ---------------------------------------------------------------------------

def _fake_definition(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        handler=lambda args, ctx: {"ok": True},
        parameters={"type": "object", "properties": {}},
        source="mcp:fake",
        side_effect="external",
        permission="ask",
    )


def test_router_respects_caps_and_ranks_by_relevance():
    registry = ToolRegistry()
    builtins = registry.openai_tools(include_skill_load=True, dynamic_names=set())
    builtin_names = {t["function"]["name"] for t in builtins}
    assert set(tool_schemas.ORIGINAL_27_TOOLS) <= builtin_names

    extensions = [_fake_definition("mcp__fake__weather_lookup", "Look up weather and forecast for a city")]
    extensions += [
        _fake_definition(f"mcp__fake__filler_{i}", f"Unrelated filler capability number {i}")
        for i in range(30)
    ]
    tools, report = select_tool_schemas(
        builtins, extensions, "what is the weather forecast in my city today?",
    )
    names = [t["function"]["name"] for t in tools]
    assert len(tools) <= 40
    assert sum(estimate_tokens(t) for t in tools) <= 4000
    assert set(tool_schemas.ORIGINAL_27_TOOLS) <= set(names)  # builtins always present
    assert "mcp__fake__weather_lookup" in names  # relevant tool wins a slot
    assert report["included_extension_tools"][0] == "mcp__fake__weather_lookup"

    # Routing off: caps still enforced, lowest-scored dropped first.
    tools_off, report_off = select_tool_schemas(
        builtins, extensions, "weather", routing_enabled=False, max_tokens=100_000,
    )
    assert len(tools_off) == 40
    assert report_off["tool_count"] == 40


# ---------------------------------------------------------------------------
# 4) Skills: index is metadata-only and capped; body injected when relevant
# ---------------------------------------------------------------------------

def _write_skill(root: Path, name: str, description: str, body: str) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8"
    )


def test_skill_index_metadata_only_and_capped(tmp_path):
    root = tmp_path / "skills"
    for i in range(30):
        _write_skill(root, f"skill-{i:02d}", f"Description number {i} with detail", f"SECRET_BODY_{i} do the steps")
    manager = SkillManager(root)
    prompt = manager.metadata_prompt()
    assert "SECRET_BODY" not in prompt  # bodies are never in the index
    assert len(prompt) <= 2200  # ~500 token budget for the whole index
    assert "skill-00" in prompt


def test_skill_body_injected_only_when_relevant(tmp_path):
    api, paths, _services = make_api(tmp_path)
    _write_skill(
        paths.skills_dir, "weekly-report",
        "Use when the user asks for a weekly status report summary",
        "WEEKLY_BODY_SENTINEL: gather updates then summarize.",
    )
    api.config.enabled_skills = ["weekly-report"]

    relevant = api._prepare_turn({"text": "Please write my weekly status report summary"}, regenerate=False)
    assert "Relevant skills" in relevant["system_context"]
    assert "WEEKLY_BODY_SENTINEL" in relevant["system_context"]

    irrelevant = api._prepare_turn({"text": "What is the capital of France?"}, regenerate=False)
    assert "WEEKLY_BODY_SENTINEL" not in irrelevant["system_context"]


def test_disable_all_extensions_reverts_to_27_builtins(tmp_path):
    api, _paths, _services = make_api(tmp_path)
    api.config.extensions_enabled = False
    request = api._prepare_turn({"text": "hello"}, regenerate=False)
    names = [t["function"]["name"] for t in request["tools"]]
    assert names == tool_schemas.ORIGINAL_27_TOOLS


# ---------------------------------------------------------------------------
# 5) Connector install: credential store yes, config files no
# ---------------------------------------------------------------------------

def test_connector_install_stores_credential_not_in_files(tmp_path):
    from vision_app.updates.service import ConnectorDirectoryEntry

    api, paths, services = make_api(tmp_path)
    entry = ConnectorDirectoryEntry.model_validate(
        {
            "id": "demo-api",
            "name": "Demo API",
            "description": "Demo connector",
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "auth_type": "api_key",
            "token_field": "X-API-Key",
            "preset_tools": ["lookup"],
        }
    )
    api.update_service = SimpleNamespace(connector_directory=[entry], ui_state=dict)
    result = api.install_connector("demo-api", "key-12345-secret")
    assert result.get("accepted"), result

    deadline = time.time() + 15
    installed = None
    while time.time() < deadline:
        editor = api.get_extension_config("connector", "demo-api")
        if editor.get("ok"):
            installed = editor
            break
        time.sleep(0.2)
    assert installed, "connector was not installed"
    assert services.credentials.get("EXT_DEMO_API_KEY") == "key-12345-secret"
    assert "key-12345-secret" not in paths.connectors_config.read_text(encoding="utf-8")
    config_file = paths.config_file
    if config_file.exists():
        assert "key-12345-secret" not in config_file.read_text(encoding="utf-8")
    assert installed["header_secret_names"] == ["X-API-Key"]
    assert installed["permissions"] == {"lookup": "ask"}


# ---------------------------------------------------------------------------
# 6) Safety: destructive names hard-blocked; ask-once; allow_paths; audit
# ---------------------------------------------------------------------------

async def _ok_call(server_id, tool_name, arguments):
    return {"content": [{"type": "text", "text": "done"}]}


def test_destructive_named_tool_is_hard_blocked(tmp_path):
    assert is_destructive_tool_name("delete_everything")
    assert is_destructive_tool_name("remove_file")
    assert not is_destructive_tool_name("remove_item_from_cart")
    assert not is_destructive_tool_name("read_registry_value")

    config = McpServerConfig.model_validate(
        {"id": "fake", "display_name": "Fake", "transport": "stdio", "command": "python",
         "enabled": True, "permissions": {"delete_everything": "allow"}}
    )
    descriptor = McpToolDescriptor(
        name="delete_everything", description="Deletes things",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    definition = make_mcp_tool_definition(config, descriptor, _ok_call)
    # Even an explicit stored "allow" cannot override the hard block.
    assert definition.permission == "blocked"
    registry = ToolRegistry()
    registry.register_dynamic(definition)
    result = asyncio.run(registry.execute(_call(definition.name, {"path": "/tmp/x"}), _context(tmp_path)))
    assert result.is_error
    assert "Blocked by VISION safety policy" in result.content_for_model


def test_ask_tool_prompts_once_per_session(tmp_path):
    prompts = {"count": 0}

    def confirm(*_args):
        prompts["count"] += 1
        return True

    config = McpServerConfig.model_validate(
        {"id": "fake", "display_name": "Fake", "transport": "stdio", "command": "python", "enabled": True}
    )
    descriptor = McpToolDescriptor(name="echo_text", description="Echo", input_schema={"type": "object", "properties": {}})
    definition = make_mcp_tool_definition(config, descriptor, _ok_call)
    registry = ToolRegistry()
    registry.register_dynamic(definition)
    context = _context(tmp_path, confirm=confirm)
    for _ in range(3):
        result = asyncio.run(registry.execute(_call(definition.name), context))
        assert not result.is_error
    assert prompts["count"] == 1


def test_allow_paths_enforced(tmp_path):
    inside = tmp_path / "allowed"
    inside.mkdir()
    enforce_allow_paths({"path": str(inside / "note.txt")}, [str(inside)])
    enforce_allow_paths({"note": "relative mention /etc/passwd in prose key"}, [str(inside)])
    with pytest.raises(PermissionError):
        enforce_allow_paths({"path": "/etc/passwd"}, [str(inside)])


def test_audit_entry_written_for_extension_call(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    registry = ToolRegistry(audit_log=audit)
    config = McpServerConfig.model_validate(
        {"id": "fake", "display_name": "Fake", "transport": "stdio", "command": "python",
         "enabled": True, "auto_approve": True}
    )
    descriptor = McpToolDescriptor(name="echo_text", description="Echo", input_schema={"type": "object", "properties": {"text": {"type": "string"}}})
    definition = make_mcp_tool_definition(config, descriptor, _ok_call)
    registry.register_dynamic(definition)
    result = asyncio.run(
        registry.execute(_call(definition.name, {"text": "hi"}), _context(tmp_path))
    )
    assert not result.is_error, result.content_for_model
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines if "mcp__fake__echo_text" in line]
    assert records
    record = records[-1]
    assert record["source"] == "mcp:fake"
    assert record["status"] == "ok"
    assert len(record["args_sha256"]) == 64
    assert record["duration_ms"] >= 0


def test_servers_folder_import(tmp_path):
    api, paths, _services = make_api(tmp_path)
    folder = paths.extensions_dir / "servers"
    folder.mkdir(parents=True)
    (folder / "drop-in.json").write_text(
        json.dumps(
            {
                "name": "Drop In Tools",
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "demo"],
                "env": {"DEMO": "1"},
                "enabled": False,
                "auto_approve": True,
                "allow_paths": ["C:\\Users\\demo\\Docs"],
            }
        ),
        encoding="utf-8",
    )
    payload = api._extensions_payload()
    row = next(item for item in payload["servers"] if item["id"] == "drop-in-tools")
    assert row["imported"] is True
    assert row["auto_approve"] is True
    assert row["allow_paths"] == ["C:\\Users\\demo\\Docs"]
    assert os.environ.get("DEMO") is None  # import never touches process env

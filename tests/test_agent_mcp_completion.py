"""Agentic mode + MCP completion + extension/skill CRUD tests."""

from __future__ import annotations

import asyncio

from vision_app.app import build_services
from vision_app.config import AppConfig
from vision_app.extensions.mcp_client import (
    coerce_elicitation_values,
    elicitation_fields,
    sampling_messages_as_chat,
)
from vision_app.extensions.mcp_config import McpServerConfig
from vision_app.history import HistoryStore
from vision_app.paths import AppPaths
from vision_app.tools import code_files, schemas
from vision_app.tools.context import ToolContext
from vision_app.tools.registry import ToolDefinition, ToolRegistry
from vision_app.tools.sandbox import PathSandbox
from vision_app.transport.schemas import ToolCall
from vision_app.web_bridge import VisionWebApi


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
        "confirm": lambda *_args: False,
    }
    kwargs.update(overrides)
    return ToolContext(**kwargs)


def _call(name: str) -> ToolCall:
    return ToolCall(id="c1", function={"name": name, "arguments": "{}"})


# ---------------------------------------------------------------------------
# Config / pure helpers
# ---------------------------------------------------------------------------

def test_agent_max_steps_clamped():
    assert AppConfig(agent_max_steps=0).agent_max_steps == 1
    assert AppConfig(agent_max_steps=10_000).agent_max_steps == 100
    assert AppConfig().agent_max_steps == 30


def test_mcp_server_config_new_fields_and_timeout_clamp():
    config = McpServerConfig(id="srv", display_name="S", timeout_seconds=99999)
    assert config.allow_sampling is False
    assert config.allow_elicitation is True
    assert config.timeout_seconds == 600
    assert McpServerConfig(id="srv", display_name="S", timeout_seconds="x").timeout_seconds == 60


def test_sampling_messages_as_chat_from_dict():
    params = {
        "system_prompt": "Be brief",
        "messages": [
            {"role": "user", "content": {"type": "text", "text": "Hello"}},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
            {"role": "user", "content": {"type": "text", "text": ""}},
        ],
        "max_tokens": 99999,
    }
    messages, max_tokens = sampling_messages_as_chat(params)
    assert messages[0] == {"role": "system", "content": "Be brief"}
    assert {"role": "user", "content": "Hello"} in messages
    assert {"role": "assistant", "content": "Hi"} in messages
    assert max_tokens == 4096


def test_elicitation_fields_and_coercion():
    schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "title": "City"},
            "count": {"type": "integer"},
            "ok": {"type": "boolean"},
            "mode": {"type": "string", "enum": ["fast", "slow"]},
        },
        "required": ["city"],
    }
    fields = elicitation_fields(schema)
    by_name = {f["name"]: f for f in fields}
    assert by_name["city"]["required"] is True
    assert by_name["mode"]["enum"] == ["fast", "slow"]
    values = coerce_elicitation_values(fields, {"city": "Trichy", "count": "3", "ok": "yes", "mode": "fast"})
    assert values == {"city": "Trichy", "count": 3, "ok": True, "mode": "fast"}


# ---------------------------------------------------------------------------
# Agent-mode registry behaviour
# ---------------------------------------------------------------------------

def test_agent_auto_approves_builtin_ask_but_not_mcp_or_openers(tmp_path):
    registry = ToolRegistry()
    handler = lambda args, ctx: {"ok": True}
    registry.register_dynamic(
        ToolDefinition(name="fake_ask", description="x", handler=handler, source="builtin", permission="ask")
    )
    registry.register_dynamic(
        ToolDefinition(name="mcp_srv_tool", description="x", handler=handler, source="mcp:srv", permission="ask")
    )
    context = _context(tmp_path)
    context.agent_auto_approve = True

    result = asyncio.run(registry.execute(_call("fake_ask"), context))
    assert not result.is_error  # auto-approved despite confirm returning False

    result = asyncio.run(registry.execute(_call("mcp_srv_tool"), context))
    assert result.is_error  # MCP tools still ask; user (fake) declined

    # open_path is a builtin but never auto-approved in agent mode.
    registry.register_dynamic(
        ToolDefinition(name="fake_open_path", description="x", handler=handler, source="builtin", permission="ask")
    )
    registry.definitions["open_path"] = ToolDefinition(
        name="open_path", description="x", handler=handler, source="builtin", permission="ask"
    )
    result = asyncio.run(registry.execute(_call("open_path"), context))
    assert result.is_error

    # The approval above is remembered for the rest of the session
    # (extensions ask once per session), even with agent mode off.
    context.agent_auto_approve = False
    result = asyncio.run(registry.execute(_call("fake_ask"), context))
    assert not result.is_error

    # A fresh session (approvals cleared) prompts again — and is declined.
    registry._session_approved.clear()
    result = asyncio.run(registry.execute(_call("fake_ask"), context))
    assert result.is_error


# ---------------------------------------------------------------------------
# Skill resource reading via read_file
# ---------------------------------------------------------------------------

def test_read_file_skill_scheme(tmp_path):
    def reader(name, relative):
        assert name == "demo"
        assert relative == "notes.md"
        return "skill body"

    context = _context(tmp_path, skill_resource_reader=reader)
    result = code_files.read_file(schemas.ReadFileArgs(path="skill://demo/notes.md"), context)
    assert result["content"] == "skill body"
    assert result["path"] == "skill://demo/notes.md"


# ---------------------------------------------------------------------------
# Bridge: extension CRUD + skills CRUD
# ---------------------------------------------------------------------------

def test_extension_crud_roundtrip_and_secret_storage(tmp_path):
    api, _paths, services = make_api(tmp_path)
    result = api.save_extension(
        "connector",
        {
            "id": "demo-conn",
            "display_name": "Demo Conn",
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "auth_type": "bearer",
            "secret": "s3cr3t-value",
            "enabled": False,
        },
    )
    assert result["ok"], result
    # Secret went to the credential store (with Bearer prefix), never the file.
    assert services.credentials.get("EXT_DEMO_CONN_KEY") == "Bearer s3cr3t-value"
    config_text = _paths.connectors_config.read_text(encoding="utf-8")
    assert "s3cr3t-value" not in config_text

    editor = api.get_extension_config("connector", "demo-conn")
    assert editor["ok"]
    assert editor["url"] == "https://example.com/mcp"
    assert editor["header_secret_names"] == ["Authorization"]
    assert "secret" not in editor

    # Save again without a secret: stored secret preserved, name updated.
    result = api.save_extension("connector", {"id": "demo-conn", "display_name": "Renamed"})
    assert result["ok"]
    editor = api.get_extension_config("connector", "demo-conn")
    assert editor["display_name"] == "Renamed"
    assert services.credentials.get("EXT_DEMO_CONN_KEY") == "Bearer s3cr3t-value"

    # Clearing removes it.
    result = api.save_extension("connector", {"id": "demo-conn", "clear_secret": True})
    assert result["ok"]
    assert services.credentials.get("EXT_DEMO_CONN_KEY") == ""
    assert api.get_extension_config("connector", "demo-conn")["header_secret_names"] == []

    # Connection change resets trust; delete removes the record.
    result = api.delete_extension("connector", "demo-conn")
    assert result["ok"]
    assert not api.get_extension_config("connector", "demo-conn")["ok"]


def test_save_extension_rejects_invalid_and_resets_trust_on_move(tmp_path):
    api, _paths, _services = make_api(tmp_path)
    result = api.save_extension("mcp", {"id": "srv1", "display_name": "S", "transport": "stdio", "command": "python", "args": "-m demo", "enabled": False})
    assert result["ok"], result
    editor = api.get_extension_config("mcp", "srv1")
    assert editor["args"] == "-m demo"

    # Simulate an approved toolset, then move the server: trust must reset.
    from vision_app.extensions.mcp_config import load_mcp_config, save_mcp_config

    data = load_mcp_config(_paths.mcp_config)
    data.servers[0].toolset_sha256 = "abc123"
    save_mcp_config(_paths.mcp_config, data)
    assert api.save_extension("mcp", {"id": "srv1", "command": "other-python"})["ok"]
    data = load_mcp_config(_paths.mcp_config)
    assert data.servers[0].toolset_sha256 == ""

    # IDs are slugified by the config model; an enabled stdio server without
    # a command is rejected by the connectability validator.
    slug = api.save_extension("mcp", {"id": "Bad ID!", "display_name": "x", "enabled": False})
    assert slug["ok"] and slug["id"] == "bad-id"
    bad = api.save_extension("mcp", {"id": "no-cmd", "display_name": "x", "transport": "stdio", "enabled": True})
    assert not bad["ok"]


def test_skill_create_and_delete(tmp_path):
    api, paths, _services = make_api(tmp_path)
    result = api.create_skill(
        {
            "name": "weekly-report",
            "description": "Use for weekly status reports",
            "instructions": "1. Gather updates. 2. Summarize them briefly.",
        }
    )
    assert result["ok"], result
    skill_file = paths.skills_dir / "weekly-report" / "SKILL.md"
    assert skill_file.exists()
    assert "name: weekly-report" in skill_file.read_text(encoding="utf-8")
    assert any(row["name"] == "weekly-report" for row in result["skills"])

    duplicate = api.create_skill(
        {"name": "weekly-report", "description": "Use for weekly status reports", "instructions": "Do the thing properly."}
    )
    assert not duplicate["ok"]

    deleted = api.delete_skill("weekly-report")
    assert deleted["ok"], deleted
    assert not skill_file.exists()
    assert not api.delete_skill("weekly-report")["ok"]


def test_create_skill_validation(tmp_path):
    api, _paths, _services = make_api(tmp_path)
    assert not api.create_skill({"name": "Bad Name", "description": "long enough desc", "instructions": "long enough instructions"})["ok"]
    assert not api.create_skill({"name": "ok-name", "description": "short", "instructions": "long enough instructions"})["ok"]

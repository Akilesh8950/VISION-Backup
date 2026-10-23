import asyncio
import json

from vision_app.history import HistoryStore
from vision_app.logging_setup import AuditLog
from vision_app.paths import AppPaths
from vision_app.tools.context import ToolContext
from vision_app.tools.registry import ToolRegistry
from vision_app.tools.sandbox import PathSandbox
from vision_app.tools.schemas import ORIGINAL_27_TOOLS, TOOL_MODELS
from vision_app.transport.schemas import FunctionCall, ToolCall


def make_context(tmp_path):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    paths.ensure()
    return ToolContext(
        paths=paths,
        history=HistoryStore(paths.history_db),
        sandbox=PathSandbox(paths.workspace),
        confirm=lambda *_args: True,
        auto_approve_python=True,
    )


def call(name, args, call_id="c1"):
    return ToolCall(id=call_id, function=FunctionCall(name=name, arguments=json.dumps(args)))


def test_original_27_exact_and_schemas_export(tmp_path):
    assert len(ORIGINAL_27_TOOLS) == 27
    assert all(name in TOOL_MODELS for name in ORIGINAL_27_TOOLS)
    registry = ToolRegistry()
    exported = registry.openai_tools()
    assert len(exported) == 28  # 27 + approved read_url
    assert exported[0]["function"]["name"] == "web_search"


def test_validation_error_is_corrective(tmp_path):
    registry = ToolRegistry()
    result = asyncio.run(registry.execute(call("get_weather", {"city": "Coimbatore", "units": "celsius"}), make_context(tmp_path)))
    assert result.is_validation_error
    assert result.error_kind == "tool_schema_error"
    assert "correct the arguments" in result.content_for_model


def test_unknown_tool_blocked(tmp_path):
    registry = ToolRegistry()
    result = asyncio.run(registry.execute(call("delete_everything", {}), make_context(tmp_path)))
    assert result.content_for_model.startswith("Blocked by VISION safety policy")


def test_file_memory_and_python_tools(tmp_path):
    context = make_context(tmp_path)
    registry = ToolRegistry(AuditLog(context.paths.audit_log))
    write = asyncio.run(registry.execute(call("write_file", {"path": "a.txt", "content": "hello"}), context))
    assert not write.is_error
    read = asyncio.run(registry.execute(call("read_file", {"path": "a.txt"}), context))
    assert read.raw["content"] == "hello"
    asyncio.run(registry.execute(call("memory_set", {"key": "city", "value": "Coimbatore"}), context))
    memory = asyncio.run(registry.execute(call("memory_get", {"key": "city"}), context))
    assert memory.raw["value"] == "Coimbatore"
    code = asyncio.run(registry.execute(call("run_python", {"code": "print(6*7)"}), context))
    assert code.raw["stdout"].strip() == "42"
    assert context.paths.audit_log.exists()


def test_genui_chart_payload(tmp_path):
    registry = ToolRegistry()
    result = asyncio.run(registry.execute(
        call("ui_chart", {"title": "Squares", "chart_type": "bar", "labels": ["1", "2", "3", "4"], "values": [1, 4, 9, 16]}),
        make_context(tmp_path),
    ))
    assert result.genui["kind"] == "chart"
    assert result.genui["values"] == [1.0, 4.0, 9.0, 16.0]

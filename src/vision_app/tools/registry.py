from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ..constants import SAFETY_BLOCK_MESSAGE
from ..logging_setup import AuditLog
from ..redaction import redact_value
from ..transport.schemas import ToolCall
from . import code_files, genui_tools, live_data, memory, schemas, web_reader, windows_tools
from .context import ToolContext

Handler = Callable[[Any, ToolContext], Any | Awaitable[Any]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    handler: Handler
    model: type[BaseModel] | None = None
    parameters: dict[str, Any] | None = None
    source: str = "builtin"
    side_effect: str = "read"
    permission: str = "policy"  # policy, ask, blocked

    def openai_schema(self) -> dict[str, Any]:
        parameters = self.parameters
        if parameters is None and self.model is not None:
            parameters = schemas.clean_json_schema(self.model)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters or {"type": "object", "properties": {}},
            },
        }


@dataclass
class ToolExecutionResult:
    tool_call_id: str
    name: str
    content_for_model: str
    summary: str
    raw: Any = None
    is_error: bool = False
    is_validation_error: bool = False
    error_kind: str = ""  # tool_schema_error, tool_runtime_error, code_error, blocked
    genui: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0
    source: str = "builtin"
    arguments: dict[str, Any] = field(default_factory=dict)


def _without_context(function):
    return lambda args, _context: function(args)


def _with_context(function):
    return lambda args, context: function(args, context)


def _skill_load(args: schemas.SkillLoadArgs, context: ToolContext) -> dict:
    if context.skill_loader is None:
        raise RuntimeError("Skills are not enabled for this conversation")
    return context.skill_loader(args.name)


SIDE_EFFECTS = {
    "write_file": "write", "run_python": "execute", "memory_set": "write",
    "open_app": "launch", "open_path": "launch", "open_url": "launch",
    "clipboard_set": "write", "set_timer": "schedule",
}


class ToolRegistry:
    def __init__(self, audit_log: AuditLog | None = None):
        self.audit_log = audit_log
        self.definitions: dict[str, ToolDefinition] = {}
        self._register_builtins()

    def _add(self, name: str, handler: Handler) -> None:
        self.definitions[name] = ToolDefinition(
            name=name,
            description=schemas.TOOL_DESCRIPTIONS[name],
            model=schemas.TOOL_MODELS[name],
            handler=handler,
            side_effect=SIDE_EFFECTS.get(name, "read"),
        )

    def _register_builtins(self) -> None:
        handlers: dict[str, Handler] = {
            "web_search": _without_context(live_data.web_search),
            "get_news": _without_context(live_data.get_news),
            "get_weather": _without_context(live_data.get_weather),
            "get_stock_price": _without_context(live_data.get_stock_price),
            "get_crypto_price": _without_context(live_data.get_crypto_price),
            "lookup_docs": _without_context(live_data.lookup_docs),
            "run_python": _with_context(code_files.run_python),
            "read_file": _with_context(code_files.read_file),
            "write_file": _with_context(code_files.write_file),
            "list_files": _with_context(code_files.list_files),
            "search_code": _with_context(code_files.search_code),
            "memory_get": _with_context(memory.memory_get),
            "memory_set": _with_context(memory.memory_set),
            "ui_table": _without_context(genui_tools.ui_table),
            "ui_chart": _without_context(genui_tools.ui_chart),
            "ui_cards": _without_context(genui_tools.ui_cards),
            "ui_timeline": _without_context(genui_tools.ui_timeline),
            "ui_code_diff": _without_context(genui_tools.ui_code_diff),
            "list_running_apps": _with_context(windows_tools.list_running_apps),
            "open_app": _with_context(windows_tools.open_app),
            "focus_window": _with_context(windows_tools.focus_window),
            "open_path": _with_context(windows_tools.open_path),
            "open_url": _with_context(windows_tools.open_url),
            "clipboard_get": _with_context(windows_tools.clipboard_get),
            "clipboard_set": _with_context(windows_tools.clipboard_set),
            "system_status": _with_context(windows_tools.system_status),
            "set_timer": _with_context(windows_tools.set_timer),
            "read_url": _without_context(web_reader.read_url),
            "skill_load": _skill_load,
        }
        for name, handler in handlers.items():
            self._add(name, handler)

    def register_dynamic(self, definition: ToolDefinition) -> None:
        if definition.name in self.definitions and self.definitions[definition.name].source == "builtin":
            raise ValueError("Dynamic tools cannot shadow built-in tools")
        self.definitions[definition.name] = definition

    def unregister_dynamic_source(self, source: str) -> None:
        self.definitions = {
            name: definition
            for name, definition in self.definitions.items()
            if definition.source != source or definition.source == "builtin"
        }

    def openai_tools(
        self,
        *,
        include_read_url: bool = True,
        include_skill_load: bool = False,
        dynamic_names: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for name in schemas.ORIGINAL_27_TOOLS:
            tools.append(self.definitions[name].openai_schema())
        if include_read_url:
            tools.append(self.definitions["read_url"].openai_schema())
        if include_skill_load:
            tools.append(self.definitions["skill_load"].openai_schema())
        for name, definition in self.definitions.items():
            if definition.source == "builtin":
                continue
            if dynamic_names is not None and name not in dynamic_names:
                continue
            tools.append(definition.openai_schema())
        return tools

    def _audit(self, result: ToolExecutionResult) -> None:
        if self.audit_log:
            status = result.error_kind or ("ok" if not result.is_error else "error")
            self.audit_log.write(result.name, result.arguments, status, result.duration_ms, source=result.source)

    def _make_error(
        self,
        call: ToolCall,
        name: str,
        message: str,
        *,
        kind: str,
        validation: bool = False,
        args: dict[str, Any] | None = None,
        started: float | None = None,
        source: str = "builtin",
    ) -> ToolExecutionResult:
        result = ToolExecutionResult(
            tool_call_id=call.id,
            name=name,
            content_for_model=message,
            summary=message,
            is_error=True,
            is_validation_error=validation,
            error_kind=kind,
            duration_ms=((time.perf_counter() - started) * 1000) if started else 0.0,
            source=source,
            arguments=args or {},
        )
        self._audit(result)
        return result

    @staticmethod
    def _assign_sources(raw: Any, context: ToolContext) -> tuple[Any, list[dict[str, Any]]]:
        # Deep-copy JSON-like structures while assigning stable per-conversation source IDs.
        import copy

        value = copy.deepcopy(raw)
        sources: list[dict[str, Any]] = []

        def visit(node: Any) -> None:
            if len(sources) >= 20:
                return
            if isinstance(node, dict):
                url = node.get("final_url") or node.get("url")
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    next_id = int(context.metadata.get("_next_source_id", 1))
                    context.metadata["_next_source_id"] = next_id + 1
                    source_id = f"S{next_id}"
                    node["source_id"] = source_id
                    sources.append(
                        {
                            "source_id": source_id,
                            "title": str(node.get("title") or url),
                            "url": url,
                            "snippet": str(node.get("snippet") or "")[:500],
                            "fetched_at": node.get("fetched_at", ""),
                        }
                    )
                for child in node.values():
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)

        visit(value)
        return value, sources

    @staticmethod
    def _serialise(raw: Any) -> str:
        if isinstance(raw, str):
            text = raw
        else:
            text = json.dumps(redact_value(raw), ensure_ascii=False, default=str)
        if len(text) > 50_000:
            text = text[:50_000] + "\n[Result truncated by VISION]"
        return text

    @staticmethod
    def _summary(name: str, raw: Any) -> str:
        if isinstance(raw, dict):
            if "_genui" in raw:
                return f"Rendered {raw['_genui'].get('kind', 'widget')}"
            if name == "get_weather":
                return f"{raw.get('city', '')}: {raw.get('temperature')} {raw.get('temperature_unit', '')}, {raw.get('condition', '')}".strip()
            if name == "run_python":
                return f"Exited with code {raw.get('exit_code')}"
            if "results" in raw and isinstance(raw["results"], list):
                return f"{len(raw['results'])} result(s)"
            if raw.get("written") or raw.get("stored") or raw.get("opened") or raw.get("scheduled"):
                return "Completed"
        text = ToolRegistry._serialise(raw)
        return text[:240] + ("…" if len(text) > 240 else "")

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolExecutionResult:
        started = time.perf_counter()
        name = call.function.name.strip()
        raw_args = call.function.arguments or "{}"
        definition = self.definitions.get(name)
        try:
            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            if not isinstance(parsed_args, dict):
                raise ValueError("tool arguments must be a JSON object")
        except Exception as exc:
            return self._make_error(
                call,
                name,
                f"Invalid arguments for {name}: expected a JSON object. {exc}. Please correct the JSON and retry.",
                kind="tool_schema_error",
                validation=True,
                started=started,
            )

        if definition is None:
            return self._make_error(
                call,
                name or "unknown",
                f"{SAFETY_BLOCK_MESSAGE}: unknown or unavailable tool '{name}'.",
                kind="blocked",
                validation=True,
                args=parsed_args,
                started=started,
            )

        if definition.permission == "blocked":
            return self._make_error(
                call, name, f"{SAFETY_BLOCK_MESSAGE}: tool is blocked by the user.",
                kind="blocked", args=parsed_args, started=started, source=definition.source,
            )
        if definition.permission == "ask":
            approved = context.require_confirmation(
                f"Allow {name}?",
                f"Source: {definition.source}\nArguments:\n{json.dumps(redact_value(parsed_args), indent=2, ensure_ascii=False)}",
                {"tool": name, "source": definition.source, "arguments": parsed_args},
            )
            if not approved:
                return self._make_error(
                    call, name, "User declined this tool call. Continue without it or ask how to proceed.",
                    kind="blocked", args=parsed_args, started=started, source=definition.source,
                )

        handler_args: Any = parsed_args
        if definition.model is not None:
            try:
                handler_args = definition.model.model_validate(parsed_args)
            except ValidationError as exc:
                details = "; ".join(
                    f"{'.'.join(str(part) for part in error.get('loc', ()))}: {error.get('msg')}"
                    for error in exc.errors()[:4]
                )
                return self._make_error(
                    call,
                    name,
                    f"Invalid arguments for {name}: {details}. Please correct the arguments and retry.",
                    kind="tool_schema_error",
                    validation=True,
                    args=parsed_args,
                    started=started,
                    source=definition.source,
                )

        try:
            if inspect.iscoroutinefunction(definition.handler):
                raw = await definition.handler(handler_args, context)
            else:
                raw = await asyncio.to_thread(definition.handler, handler_args, context)
                if inspect.isawaitable(raw):  # defensive for sync wrappers returning awaitables
                    raw = await raw
        except PermissionError as exc:
            return self._make_error(
                call, name, str(exc), kind="blocked", args=parsed_args,
                started=started, source=definition.source,
            )
        except Exception as exc:
            return self._make_error(
                call, name, f"Tool {name} failed: {exc}", kind="tool_runtime_error",
                args=parsed_args, started=started, source=definition.source,
            )

        raw_with_sources, sources = self._assign_sources(raw, context)
        genui = raw_with_sources.get("_genui") if isinstance(raw_with_sources, dict) else None
        content = self._serialise(raw_with_sources)
        if genui:
            content = f"Rendered a native {genui.get('kind')} widget for the user titled '{genui.get('title') or genui.get('filename') or ''}'."
        error_kind = ""
        is_error = False
        if name == "run_python" and isinstance(raw, dict) and raw.get("exit_code") not in (0, None):
            error_kind = "code_error"
            is_error = True
        result = ToolExecutionResult(
            tool_call_id=call.id,
            name=name,
            content_for_model=content,
            summary=self._summary(name, raw_with_sources),
            raw=raw_with_sources,
            is_error=is_error,
            error_kind=error_kind,
            genui=genui,
            sources=sources,
            duration_ms=(time.perf_counter() - started) * 1000,
            source=definition.source,
            arguments=parsed_args,
        )
        self._audit(result)
        return result

    async def execute_many(
        self, calls: list[ToolCall], context: ToolContext
    ) -> list[ToolExecutionResult]:
        if len(calls) == 1:
            return [await self.execute(calls[0], context)]
        return list(await asyncio.gather(*(self.execute(call, context) for call in calls)))

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ..constants import SAFETY_BLOCK_MESSAGE

# Agent mode never auto-approves these, even for builtin tools.
_AGENT_NEVER_AUTO_APPROVE = {"open_path", "open_app", "open_url", "run_python"}
from ..logging_setup import AuditLog
from ..redaction import redact_value
from ..transport.schemas import ToolCall
from . import code_files, genui_tools, live_data, memory, schemas, web_reader, windows_tools
from .context import ToolContext

Handler = Callable[[Any, ToolContext], Any | Awaitable[Any]]
logger = logging.getLogger(__name__)


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
    # Backend-only diagnostics for the local telemetry queue.  Bridge payload
    # builders deliberately do not serialize these fields to the frontend.
    raw_arguments: str = ""
    validation_error: str = ""
    traceback_text: str = ""


def _without_context(function):
    return lambda args, _context: function(args)


def _with_context(function):
    return lambda args, context: function(args, context)


def _skill_load(args: schemas.SkillLoadArgs, context: ToolContext) -> dict:
    if context.skill_loader is None:
        raise RuntimeError("Skills are not enabled for this conversation")
    return context.skill_loader(args.name)


def _read_skill_file(args: schemas.ReadSkillFileArgs, context: ToolContext) -> dict:
    if context.skill_resource_reader is None:
        raise RuntimeError("Skill files are not available for this conversation")
    content = context.skill_resource_reader(args.skill, args.path)
    return {"skill": args.skill, "path": args.path, "content": content}


SIDE_EFFECTS = {
    "write_file": "write", "run_python": "execute", "memory_set": "write",
    "open_app": "launch", "open_path": "launch", "open_url": "launch",
    "clipboard_set": "write", "set_timer": "schedule",
}


class ToolRegistry:
    def __init__(self, audit_log: AuditLog | None = None):
        self.audit_log = audit_log
        self.definitions: dict[str, ToolDefinition] = {}
        # Remote config may refresh only the model-facing description and
        # JSON schema for the 27 baseline tools.  Local Pydantic models and
        # handlers remain authoritative for validation and execution.
        self._schema_overrides: dict[str, dict[str, Any]] = {}
        self._schema_overrides_lock = threading.RLock()
        # Extension tools set to "ask" prompt once per app session; the
        # approval is remembered here (never persisted) so a long agent
        # run is not interrupted for every repeated call.
        self._session_approved: set[tuple[str, str]] = set()
        self._session_approved_lock = threading.Lock()
        self._register_builtins()

    def apply_schema_overrides(self, tools: Any) -> None:
        replacement: dict[str, dict[str, Any]] = {}
        for tool in tools or []:
            if isinstance(tool, dict):
                name = str(tool.get("name") or "")
                description = str(tool.get("description") or "")
                parameters = tool.get("parameters")
            else:
                name = str(getattr(tool, "name", "") or "")
                description = str(getattr(tool, "description", "") or "")
                parameters = getattr(tool, "parameters", None)
            if name not in schemas.ORIGINAL_27_TOOLS or not isinstance(parameters, dict):
                continue
            replacement[name] = {
                "description": description[:2_000],
                "parameters": parameters,
            }
        # Apply all-or-nothing: a partial remote set must not mix two schema
        # generations in one model request.
        if set(replacement) != set(schemas.ORIGINAL_27_TOOLS):
            return
        with self._schema_overrides_lock:
            self._schema_overrides = replacement

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
            "read_skill_file": _read_skill_file,
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

    def replace_dynamic(self, definitions: list[ToolDefinition]) -> set[str]:
        """Atomically swap all dynamic definitions after discovery finishes."""

        builtins = {
            name: definition
            for name, definition in self.definitions.items()
            if definition.source == "builtin"
        }
        replacement = dict(builtins)
        names: set[str] = set()
        for definition in definitions:
            if definition.source == "builtin" or definition.name in builtins:
                raise ValueError(f"Dynamic tool cannot shadow built-in tool: {definition.name}")
            if definition.name in names:
                raise ValueError(f"Duplicate dynamic tool name: {definition.name}")
            names.add(definition.name)
            replacement[definition.name] = definition
        self.definitions = replacement
        return set(names)

    def openai_tools(
        self,
        *,
        include_read_url: bool = True,
        include_skill_load: bool = False,
        dynamic_names: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        with self._schema_overrides_lock:
            overrides = dict(self._schema_overrides)

        def schema_for(name: str) -> dict[str, Any]:
            schema = self.definitions[name].openai_schema()
            override = overrides.get(name)
            if override:
                function = schema.setdefault("function", {})
                if override.get("description"):
                    function["description"] = override["description"]
                function["parameters"] = override["parameters"]
            return schema

        tools: list[dict[str, Any]] = []
        for name in schemas.ORIGINAL_27_TOOLS:
            tools.append(schema_for(name))
        if include_read_url:
            tools.append(schema_for("read_url"))
        if include_skill_load:
            tools.append(schema_for("skill_load"))
            tools.append(schema_for("read_skill_file"))
        for name, definition in self.definitions.items():
            if definition.source == "builtin":
                continue
            if dynamic_names is not None and name not in dynamic_names:
                continue
            tools.append(definition.openai_schema())
        return tools

    def _audit(self, result: ToolExecutionResult) -> None:
        if not self.audit_log:
            return
        try:
            status = result.error_kind or ("ok" if not result.is_error else "error")
            self.audit_log.write(result.name, result.arguments, status, result.duration_ms, source=result.source)
        except Exception:
            logger.exception("Could not write the tool audit record for %s", result.name)

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
        raw_arguments: str = "",
        validation_error: str = "",
        traceback_text: str = "",
    ) -> ToolExecutionResult:
        if not raw_arguments:
            supplied = getattr(getattr(call, "function", None), "arguments", "")
            raw_arguments = supplied if isinstance(supplied, str) else json.dumps(supplied, ensure_ascii=False, default=str)
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
            raw_arguments=raw_arguments,
            validation_error=validation_error,
            traceback_text=traceback_text,
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
            genui = raw.get("_genui")
            if isinstance(genui, dict):
                return f"Rendered {genui.get('kind', 'widget')}"
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

    async def _execute_once(self, call: ToolCall, context: ToolContext) -> ToolExecutionResult:
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
                validation_error=str(exc),
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
            approval_key = (definition.source, name)
            with self._session_approved_lock:
                remembered = approval_key in self._session_approved
            if remembered:
                approved = True
            elif (
                getattr(context, "agent_auto_approve", False)
                and definition.source == "builtin"
                and name not in _AGENT_NEVER_AUTO_APPROVE
            ):
                logger.info("Agent mode auto-approved builtin tool %s", name)
                approved = True
            else:
                approved = await asyncio.to_thread(
                    context.require_confirmation,
                    f"Allow {name}?",
                    f"Source: {definition.source}\nArguments:\n{json.dumps(redact_value(parsed_args), indent=2, ensure_ascii=False, default=str)}",
                    {"tool": name, "source": definition.source, "arguments": parsed_args},
                )
            if approved:
                with self._session_approved_lock:
                    self._session_approved.add(approval_key)
            else:
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
                    validation_error=details,
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
                traceback_text=traceback.format_exc(),
            )

        raw_with_sources, sources = self._assign_sources(raw, context)
        raw_genui = raw_with_sources.get("_genui") if isinstance(raw_with_sources, dict) else None
        genui = raw_genui if isinstance(raw_genui, dict) else None
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
            raw_arguments=raw_args if isinstance(raw_args, str) else json.dumps(raw_args, ensure_ascii=False, default=str),
        )
        self._audit(result)
        return result

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolExecutionResult:
        """Execute one call while preserving the always-reply invariant.

        Confirmation, source assignment, serialization, summary creation and
        auditing all sit after the assistant tool-call has been persisted by
        the controller. An ordinary exception in any of them must therefore
        become a tool error result, not escape and orphan the call.
        Cancellation is deliberately not converted: the controller owns the
        user-visible cancelled-turn state.
        """

        started = time.perf_counter()
        try:
            return await self._execute_once(call, context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Unhandled tool registry failure")
            try:
                name = str(call.function.name or "unknown").strip() or "unknown"
            except Exception:
                name = "unknown"
            definition = self.definitions.get(name) if "name" in locals() else None
            result = ToolExecutionResult(
                tool_call_id=str(getattr(call, "id", "") or ""),
                name=name,
                content_for_model=f"Tool {name} failed: {exc}",
                summary=f"Tool {name} failed: {exc}",
                is_error=True,
                error_kind="tool_runtime_error",
                duration_ms=(time.perf_counter() - started) * 1000,
                source=definition.source if definition else "builtin",
                raw_arguments=str(getattr(getattr(call, "function", None), "arguments", "") or ""),
                traceback_text=traceback.format_exc(),
            )
            self._audit(result)
            return result

    async def execute_many(
        self, calls: list[ToolCall], context: ToolContext
    ) -> list[ToolExecutionResult]:
        if len(calls) == 1:
            return [await self.execute(calls[0], context)]
        return list(await asyncio.gather(*(self.execute(call, context) for call in calls)))

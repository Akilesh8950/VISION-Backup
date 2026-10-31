from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..constants import SYSTEM_PROMPT
from ..tools.schemas import TOOL_MODELS, clean_json_schema

GGUF_MAGIC = b"GGUF"
MIN_MODEL_BYTES = 5_000_000_000
SMOKE_PORT = 18081
SMOKE_TIMEOUT_SECONDS = 120.0
SMOKE_CONTEXT_SIZE = 4096
SMOKE_RAM_OVERHEAD_BYTES = 1536 * 1024 * 1024


@dataclass
class ModelSmokeResult:
    ok: bool
    reason: str = ""
    checks: list[dict[str, Any]] = field(default_factory=list)


def validate_gguf(path: Path, *, min_bytes: int = MIN_MODEL_BYTES) -> bool:
    """Cheap structural gate: plausible size + GGUF magic header."""
    try:
        if path.stat().st_size <= min_bytes:
            return False
        with path.open("rb") as handle:
            return handle.read(4) == GGUF_MAGIC
    except OSError:
        return False


def _available_ram_bytes() -> int | None:
    try:
        import psutil  # type: ignore

        return int(psutil.virtual_memory().available)
    except Exception:
        return None


def _chat(base_url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        response = client.post("/v1/chat/completions", json=payload)
        response.raise_for_status()
        body = response.json()
    return body if isinstance(body, dict) else {}


def run_smoke_against(base_url: str, *, timeout: float = SMOKE_TIMEOUT_SECONDS) -> ModelSmokeResult:
    """Prove the served model behaves like VISION: identity + one tool call."""
    deadline = time.monotonic() + max(1.0, timeout)
    checks: list[dict[str, Any]] = []

    def remaining() -> float:
        return max(1.0, deadline - time.monotonic())

    try:
        identity = _chat(
            base_url,
            {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "Who built you? Answer in one short line."},
                ],
                "stream": False,
                "max_tokens": 96,
            },
            remaining(),
        )
        content = str((((identity.get("choices") or [{}])[0]).get("message") or {}).get("content") or "")
        identity_ok = "SPIRIT ORG" in content.upper()
        checks.append({"name": "identity", "ok": identity_ok})
        if not identity_ok:
            return ModelSmokeResult(False, "identity check failed", checks)

        weather_schema = {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather for a city.",
                "parameters": clean_json_schema(TOOL_MODELS["get_weather"]),
            },
        }
        tool_reply = _chat(
            base_url,
            {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "What is the weather in Chennai right now? Use the tool."},
                ],
                "tools": [weather_schema],
                "tool_choice": "auto",
                "stream": False,
                "max_tokens": 256,
            },
            remaining(),
        )
        message = (((tool_reply.get("choices") or [{}])[0]).get("message") or {})
        tool_calls = message.get("tool_calls") or []
        parsable = False
        if tool_calls and isinstance(tool_calls[0], dict):
            function = tool_calls[0].get("function") or {}
            arguments = function.get("arguments") or "{}"
            try:
                parsable = function.get("name") == "get_weather" and isinstance(json.loads(arguments), dict)
            except (TypeError, ValueError):
                parsable = False
        checks.append({"name": "tool_call", "ok": parsable})
        if not parsable:
            return ModelSmokeResult(False, "tool-call check failed", checks)
    except Exception as exc:
        return ModelSmokeResult(False, f"smoke request failed: {type(exc).__name__}", checks)
    return ModelSmokeResult(True, "", checks)


def run_model_smoke_test(
    server_executable: Path,
    model_path: Path,
    *,
    timeout: float = SMOKE_TIMEOUT_SECONDS,
    port: int = SMOKE_PORT,
    context_size: int = SMOKE_CONTEXT_SIZE,
    gpu_layers: int = 0,
) -> ModelSmokeResult:
    """Start a bounded throwaway llama-server and smoke-test the GGUF.

    The side server is CPU-only with a 4k context by default.  That proves
    the downloaded weights/template/tool behaviour without allocating a
    second multi-gigabyte GPU copy beside the user's running server.  A RAM
    preflight accounts for whatever the normal server is already using.
    """
    try:
        model_bytes = int(model_path.stat().st_size)
    except OSError as exc:
        return ModelSmokeResult(False, f"could not read model: {exc}")
    available_ram = _available_ram_bytes()
    required_ram = model_bytes + SMOKE_RAM_OVERHEAD_BYTES
    if available_ram is not None and available_ram < required_ram:
        return ModelSmokeResult(
            False,
            "insufficient available RAM for a safe model smoke test; "
            "close the running model server or other large apps and retry",
        )
    command = [
        str(server_executable),
        "-m", str(model_path),
        "--host", "127.0.0.1",
        "--port", str(port),
        "-c", str(max(1024, int(context_size))),
        "-ngl", str(max(0, int(gpu_layers))),
        "--jinja",
        "--flash-attn", "on",
        "-np", "1",
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
    ]
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + max(10.0, timeout)
    try:
        process = subprocess.Popen(  # The binary path comes from VISION's own discovery/settings.
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return ModelSmokeResult(False, f"could not start llama-server: {exc}")
    try:
        ready = False
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return ModelSmokeResult(False, "llama-server exited during model load")
            try:
                with httpx.Client(base_url=base_url, timeout=2.0) as client:
                    if client.get("/v1/models").status_code == 200:
                        ready = True
                        break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        if not ready:
            return ModelSmokeResult(False, "llama-server did not become ready in time")
        return run_smoke_against(base_url, timeout=max(1.0, deadline - time.monotonic()))
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


__all__ = [
    "GGUF_MAGIC",
    "MIN_MODEL_BYTES",
    "SMOKE_CONTEXT_SIZE",
    "ModelSmokeResult",
    "run_model_smoke_test",
    "run_smoke_against",
    "validate_gguf",
]

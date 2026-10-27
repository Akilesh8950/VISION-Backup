from __future__ import annotations

import importlib
import json
import os
import pkgutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import AppConfig
from .constants import DEFAULT_LOCAL_PORT as LOCAL_PORT
from .paths import get_paths
from .server.discovery import find_llama_server
from .tools.registry import ToolRegistry
from .tools.schemas import ORIGINAL_27_TOOLS, TOOL_MODELS

# Legacy Qt / WebView surfaces depend on GUI packages that exist in the
# packaged build.  In a source checkout without them, an import failure for
# those modules is recorded as skipped instead of failing the whole test —
# the packaged VISION.exe always bundles them, so there they are real.
_OPTIONAL_MISSING = ("PySide6", "webview", "PyQt6", "PyQt5")


def _check_imports() -> dict[str, Any]:
    import vision_app

    failures: list[str] = []
    skipped: list[str] = []
    imported = 0
    for module in pkgutil.walk_packages(vision_app.__path__, prefix="vision_app."):
        try:
            importlib.import_module(module.name)
            imported += 1
        except Exception as exc:  # The selftest reports failures; it must not crash first.
            message = str(exc)
            if any(name in message for name in _OPTIONAL_MISSING):
                skipped.append(module.name)
            else:
                failures.append(f"{module.name}: {type(exc).__name__}: {message}"[:300])
    return {
        "name": "imports",
        "ok": not failures,
        "detail": f"imported={imported} skipped_optional={len(skipped)} failures={failures[:5]}",
    }


def _check_tools() -> dict[str, Any]:
    missing = [name for name in ORIGINAL_27_TOOLS if name not in TOOL_MODELS]
    exported = ToolRegistry().openai_tools()
    names = {entry["function"]["name"] for entry in exported}
    absent = [name for name in ORIGINAL_27_TOOLS if name not in names]
    ok = not missing and not absent and len(TOOL_MODELS) >= 27
    return {
        "name": "tool_schemas",
        "ok": ok,
        "detail": f"models={len(TOOL_MODELS)} exported={len(exported)} missing={missing} absent={absent}",
    }


def _check_llama_binary() -> dict[str, Any]:
    try:
        paths = get_paths()
        config = AppConfig.load(paths)
        binary = find_llama_server(paths, config.llama_server_path)
    except Exception as exc:
        return {"name": "llama_server_binary", "ok": False, "detail": f"lookup failed: {exc}"[:300]}
    if binary is None:
        # A fresh install legitimately has no server binary until the wizard
        # downloads one; that must not fail an app update.
        return {"name": "llama_server_binary", "ok": True, "detail": "not installed yet (wizard will fetch it)"}
    return {"name": "llama_server_binary", "ok": True, "detail": f"found {binary.name}"}


def _check_running_server() -> dict[str, Any]:
    import httpx

    url = f"http://127.0.0.1:{LOCAL_PORT}/v1/models"
    try:
        response = httpx.get(url, timeout=2.0)
    except Exception:
        return {"name": "running_server", "ok": True, "detail": "no server running (skipped)"}
    if response.status_code != 200:
        return {"name": "running_server", "ok": True, "detail": f"server answered HTTP {response.status_code} (warning only)"}
    try:
        payload = response.json()
        count = len(payload.get("data") or []) if isinstance(payload, dict) else 0
    except Exception:
        count = 0
    return {"name": "running_server", "ok": True, "detail": f"server ok, models={count}"}


def run_selftest(output_path: Path | None = None) -> int:
    """Run the post-update self-check and write ``result.json``.

    Returns 0 when every required check passes, 1 otherwise.  The launcher
    runs this once for a freshly staged ``versions/<ver>`` build before it is
    allowed to become the running version.
    """
    if output_path is None:
        override = os.environ.get("VISION_SELFTEST_OUTPUT", "").strip()
        output_path = Path(override) if override else Path.cwd() / "result.json"

    started = time.monotonic()
    checks = [_check_imports(), _check_tools(), _check_llama_binary(), _check_running_server()]
    ok = all(bool(check["ok"]) for check in checks)
    payload = {
        "ok": ok,
        "version": __version__,
        "checks": checks,
        "duration_seconds": round(time.monotonic() - started, 3),
        "ts": datetime.now(UTC).isoformat(),
    }
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        # The exit code is the contract; result.json is diagnostic.
        pass
    return 0 if ok else 1


__all__ = ["run_selftest"]

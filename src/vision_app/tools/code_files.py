from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import schemas
from .context import ToolContext

MAX_FILE_BYTES = 1_000_000
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "dist", "build", ".mypy_cache", ".pytest_cache"}


def _confirmed_path(context: ToolContext, raw: str, action: str) -> Path:
    resolved = context.sandbox.resolve(raw)
    if context.sandbox.needs_confirmation(raw, resolved, action=action):
        approved = context.require_confirmation(
            f"Allow {action}?",
            f"VISION wants to {action} this path:\n{resolved}",
            {"path": str(resolved), "action": action, "raw_path": raw},
        )
        if not approved:
            raise PermissionError(f"User declined {action} for path {resolved}")
    return resolved


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def read_file(args: schemas.ReadFileArgs, context: ToolContext) -> dict:
    path = _confirmed_path(context, args.path, "read")
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise RuntimeError("File exceeds the 1 MB text read limit")
    data = path.read_bytes()
    if _is_binary(data):
        raise RuntimeError("Binary files cannot be read as text")
    return {"path": str(path), "content": data.decode("utf-8", errors="strict")}


def write_file(args: schemas.WriteFileArgs, context: ToolContext) -> dict:
    path = _confirmed_path(context, args.path, "write")
    context.sandbox.validate_write(path)
    encoded = args.content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise RuntimeError("Content exceeds the 1 MB write limit")
    if path.exists() and context.checkpoint_callback:
        context.checkpoint_callback(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return {"path": str(path), "bytes_written": len(encoded), "written": True}


def list_files(args: schemas.ListFilesArgs, context: ToolContext) -> dict:
    path = _confirmed_path(context, args.directory, "read")
    if not path.is_dir():
        raise NotADirectoryError(f"Directory not found: {path}")
    entries = []
    for child in sorted(path.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower()))[:200]:
        try:
            stat = child.stat()
            size = stat.st_size if child.is_file() else None
        except OSError:
            size = None
        entries.append({"name": child.name, "path": str(child), "type": "directory" if child.is_dir() else "file", "size": size})
    return {"directory": str(path), "entries": entries, "truncated": len(entries) == 200}


def search_code(args: schemas.SearchCodeArgs, context: ToolContext) -> dict:
    root = _confirmed_path(context, args.path, "read")
    try:
        pattern = re.compile(args.pattern)
    except re.error as exc:
        raise RuntimeError(f"Invalid regular expression: {exc}") from exc
    files: list[Path]
    if root.is_file():
        files = [root]
    elif root.is_dir():
        files = []
        for current, dirs, names in os.walk(root):
            dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS)
            for name in sorted(names):
                files.append(Path(current) / name)
                if len(files) >= 5000:
                    break
            if len(files) >= 5000:
                break
    else:
        raise FileNotFoundError(f"Path not found: {root}")

    matches: list[dict] = []
    for path in files:
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            data = path.read_bytes()
            if _is_binary(data):
                continue
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                matches.append({"path": str(path), "line": line_number, "text": line[:500]})
                if len(matches) >= 200:
                    return {"path": str(root), "matches": matches, "truncated": True}
    return {"path": str(root), "matches": matches, "truncated": False}


def _scrubbed_environment() -> dict[str, str]:
    blocked_parts = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "HF_")
    env: dict[str, str] = {}
    allowed = {"PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
    for key, value in os.environ.items():
        if any(part in key.upper() for part in blocked_parts):
            continue
        if key in allowed:
            env[key] = value
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def run_python(args: schemas.RunPythonArgs, context: ToolContext) -> dict:
    if not context.auto_approve_python:
        approved = context.require_confirmation(
            "Run Python code?",
            "The following code will run in a separate Python process for up to 15 seconds:\n\n" + args.code,
            {"code": args.code, "action": "run_python"},
        )
        if not approved:
            raise PermissionError("User declined to run the Python code")
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", prefix="vision-run-", delete=False, encoding="utf-8") as stream:
            stream.write(args.code)
            temp_path = Path(stream.name)
        completed = subprocess.run(
            [sys.executable, "-I", "-S", str(temp_path)],
            cwd=str(context.sandbox.workspace),
            env=_scrubbed_environment(),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=15,
            check=False,
        )
        return {
            "exit_code": completed.returncode,
            "stdout": (completed.stdout or "")[-20000:],
            "stderr": (completed.stderr or "")[-20000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {"exit_code": -1, "stdout": (exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "", "stderr": "Timed out after 15 seconds"}
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()

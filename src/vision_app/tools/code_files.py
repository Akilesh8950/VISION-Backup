from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from ..python_helper import RUN_PYTHON_HELPER_ARG
from ..subprocess_utils import run_bounded
from . import schemas
from .context import ToolContext

MAX_FILE_BYTES = 1_000_000
MAX_LIST_SCAN = 5_000
MAX_SEARCH_BYTES = 25_000_000
SEARCH_TIME_LIMIT_SECONDS = 10.0
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "dist", "build", ".mypy_cache", ".pytest_cache"}

# Regex evaluation is deliberately not performed in the app process: a
# catastrophic pattern can monopolise a Python thread in a way no in-process
# timer can stop.  This fixed, stdlib-only worker receives its job as JSON
# data (never as code) and is killed as a whole process on timeout.
_SEARCH_WORKER_SOURCE = r'''
import json
import re
import time
from pathlib import Path


def is_binary(data):
    return b"\x00" in data[:8192]


def main():
    with open(JOB_PATH, encoding="utf-8") as stream:
        job = json.load(stream)
    pattern = re.compile(str(job["pattern"]))
    max_file_bytes = int(job["max_file_bytes"])
    max_search_bytes = int(job["max_search_bytes"])
    deadline = time.monotonic() + float(job["deadline_seconds"])
    limit_reached = bool(job["scan_truncated"])
    matches = []
    scanned_bytes = 0
    for raw_path in job["files"]:
        if time.monotonic() >= deadline or scanned_bytes >= max_search_bytes:
            limit_reached = True
            break
        path = Path(str(raw_path))
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            with path.open("rb") as stream:
                data = stream.read(max_file_bytes + 1)
            if len(data) > max_file_bytes:
                continue
            scanned_bytes += len(data)
            if is_binary(data):
                continue
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if line_number % 200 == 0 and time.monotonic() >= deadline:
                limit_reached = True
                break
            if pattern.search(line):
                matches.append(
                    {"path": str(path), "line": line_number, "text": line[:500]}
                )
                if len(matches) >= 200:
                    print(json.dumps({"matches": matches, "truncated": True}))
                    return
        if limit_reached:
            break
    print(json.dumps({"matches": matches, "truncated": limit_reached}))


main()
'''


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
    raw_path = str(args.path or "")
    if raw_path.startswith("skill://"):
        # Bundled skill resource: read-only, confined to the skill folder by
        # SkillManager.read_resource. Path form: skill://<name>/<relative>.
        if context.skill_resource_reader is None:
            raise RuntimeError("Skill resources are not available")
        remainder = raw_path[len("skill://"):]
        name, separator, relative = remainder.partition("/")
        if not name or not separator or not relative:
            raise RuntimeError("Skill resource path must look like skill://<name>/<file>")
        content = context.skill_resource_reader(name, relative)
        return {"path": raw_path, "content": content}
    path = _confirmed_path(context, args.path, "read")
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise RuntimeError("File exceeds the 1 MB text read limit")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise RuntimeError("File exceeds the 1 MB text read limit")
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
    scanned: list[tuple[str, str, bool, int | None]] = []
    scan_truncated = False
    with os.scandir(path) as iterator:
        for entry in iterator:
            if len(scanned) >= MAX_LIST_SCAN:
                scan_truncated = True
                break
            try:
                is_directory = entry.is_dir(follow_symlinks=True)
                size = entry.stat(follow_symlinks=True).st_size if entry.is_file(follow_symlinks=True) else None
            except OSError:
                is_directory = False
                size = None
            scanned.append((entry.name, entry.path, is_directory, size))
    scanned.sort(key=lambda value: (not value[2], value[0].lower()))
    entries = [
        {"name": name, "path": child_path, "type": "directory" if is_directory else "file", "size": size}
        for name, child_path, is_directory, size in scanned[:200]
    ]
    return {
        "directory": str(path),
        "entries": entries,
        "truncated": scan_truncated or len(scanned) > len(entries),
        "scanned": len(scanned),
    }


def search_code(args: schemas.SearchCodeArgs, context: ToolContext) -> dict:
    root = _confirmed_path(context, args.path, "read")
    try:
        re.compile(args.pattern)
    except re.error as exc:
        raise RuntimeError(f"Invalid regular expression: {exc}") from exc
    files: list[Path]
    scan_truncated = False
    if root.is_file():
        files = [root]
    elif root.is_dir():
        files = []
        for current, dirs, names in os.walk(root):
            current_path = Path(current)
            dirs[:] = sorted(
                name for name in dirs
                if name not in SKIP_DIRS and not (current_path / name).is_symlink()
            )
            for name in sorted(names):
                candidate = current_path / name
                try:
                    resolved = candidate.resolve(strict=False)
                except OSError:
                    continue
                # A symlinked file inside an approved directory must not turn
                # search into a read of an unrelated location outside it.
                if candidate.is_symlink() and resolved != root and root not in resolved.parents:
                    continue
                files.append(candidate)
                if len(files) >= 5000:
                    scan_truncated = True
                    break
            if scan_truncated:
                break
    else:
        raise FileNotFoundError(f"Path not found: {root}")

    result = _run_isolated_search(
        context,
        pattern=args.pattern,
        files=files,
        scan_truncated=scan_truncated,
    )
    return {"path": str(root), **result}


def _python_command(script: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        # In the PyInstaller app sys.executable is VISION.exe, not a Python
        # interpreter. The app's entrypoint dispatches this exact helper mode
        # before constructing any GUI services.
        return [sys.executable, RUN_PYTHON_HELPER_ARG, str(script)]
    return [sys.executable, "-I", "-S", str(script)]


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


def _run_isolated_search(
    context: ToolContext,
    *,
    pattern: str,
    files: list[Path],
    scan_truncated: bool,
) -> dict:
    job_path: Path | None = None
    worker_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", prefix="vision-search-", delete=False, encoding="utf-8"
        ) as stream:
            json.dump(
                {
                    "pattern": pattern,
                    "files": [str(path) for path in files],
                    "max_file_bytes": MAX_FILE_BYTES,
                    "max_search_bytes": MAX_SEARCH_BYTES,
                    "deadline_seconds": SEARCH_TIME_LIMIT_SECONDS,
                    "scan_truncated": scan_truncated,
                },
                stream,
            )
            job_path = Path(stream.name)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", prefix="vision-search-worker-", delete=False, encoding="utf-8"
        ) as stream:
            stream.write(f"JOB_PATH = {str(job_path)!r}\n")
            stream.write(_SEARCH_WORKER_SOURCE)
            worker_path = Path(stream.name)
        try:
            completed = run_bounded(
                _python_command(worker_path),
                cwd=context.sandbox.workspace,
                env=_scrubbed_environment(),
                timeout=SEARCH_TIME_LIMIT_SECONDS + 2.0,
                stdout_chars=1_000_000,
                stderr_chars=4_000,
                stdout_mode="head",
                stderr_mode="head",
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "Search timed out; the regular expression may be too expensive. "
                "Try a simpler pattern or a narrower folder."
            ) from exc
        except OSError as exc:
            raise RuntimeError("The isolated search worker could not start") from exc
    finally:
        for candidate in (worker_path, job_path):
            if candidate is not None:
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass

    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1:] or []
        suffix = f": {detail[0][:300]}" if detail else ""
        raise RuntimeError(f"The isolated search worker failed{suffix}")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("The isolated search worker returned malformed results") from exc
    raw_matches = payload.get("matches") if isinstance(payload, dict) else None
    if not isinstance(raw_matches, list):
        raise RuntimeError("The isolated search worker returned malformed results")
    matches: list[dict] = []
    for item in raw_matches[:200]:
        if not isinstance(item, dict):
            continue
        try:
            line = int(item.get("line") or 0)
        except (TypeError, ValueError):
            continue
        matches.append(
            {
                "path": str(item.get("path") or ""),
                "line": line,
                "text": str(item.get("text") or "")[:500],
            }
        )
    return {"matches": matches, "truncated": bool(payload.get("truncated"))}


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
        completed = run_bounded(
            _python_command(temp_path),
            cwd=context.sandbox.workspace,
            env=_scrubbed_environment(),
            timeout=15,
            stdout_chars=20_000,
            stderr_chars=20_000,
        )
        return {
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {"exit_code": -1, "stdout": (exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "", "stderr": "Timed out after 15 seconds"}
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

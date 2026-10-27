from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import psutil

from ..constants import SAFETY_BLOCK_MESSAGE
from ..subprocess_utils import run_bounded
from . import schemas
from .context import ToolContext
from .sandbox import EXECUTABLE_SUFFIXES

MAX_CLIPBOARD_CHARS = 50_000
_PLAIN_PROGRAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+_-]*$")
_SINGLE_PROGRAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]*$")
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:(?:[\\/]|$)")
_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_DIRECT_PROGRAM_SUFFIXES = {".exe", ".com", ".bat", ".cmd"}
_BLOCKED_PROGRAM_SUFFIXES = {".msi", ".scr", ".ps1", ".vbs", ".js"}

APP_MAP = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "notepad": "notepad.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "calc": "calc.exe",
    "calculator": "calc.exe",
    "vscode": "code",
    "visual studio code": "code",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "paint": "mspaint.exe",
}


def list_running_apps(_args, _context: ToolContext) -> dict:
    apps: dict[str, dict] = {}
    for process in psutil.process_iter(["pid", "name"]):
        try:
            info = process.info or {}
            name = str(info.get("name") or "").strip()
            pid = info.get("pid")
            if name and isinstance(pid, int) and not isinstance(pid, bool):
                apps.setdefault(name, {"name": name, "pids": []})["pids"].append(pid)
        except (psutil.Error, AttributeError, TypeError, ValueError):
            continue
    values = sorted(apps.values(), key=lambda item: item["name"].lower())
    return {"apps": values[:200], "count": len(values)}


def _launch_program(target: str, *, allow_windows_shell_lookup: bool = False) -> str:
    try:
        subprocess.Popen(
            [target],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return target
    except OSError:
        if sys.platform == "win32" and allow_windows_shell_lookup:
            # ShellExecute can resolve registered Windows "App Paths" for a
            # plain known program name such as chrome. It is not used for
            # paths, documents, URIs, or arbitrary multi-word input.
            os.startfile(target)  # type: ignore[attr-defined]
            return target
        raise


def open_app(args: schemas.OpenAppArgs, context: ToolContext) -> dict:
    requested = args.app_name.strip().strip("\"'")
    if not requested:
        raise ValueError("Application name is empty")
    mapped = APP_MAP.get(requested.lower(), requested)
    mapped_path = Path(mapped).expanduser()
    is_drive_path = bool(_DRIVE_PATH_RE.match(mapped))
    is_uri = "://" in mapped or (bool(_URI_SCHEME_RE.match(mapped)) and not is_drive_path)
    if is_uri:
        raise PermissionError(f"{SAFETY_BLOCK_MESSAGE}: open_app does not launch URIs or protocol handlers")

    looks_like_path = (
        is_drive_path
        or mapped.startswith(("\\\\", "/", "\\", "~", "."))
        or "/" in mapped
        or "\\" in mapped
        or mapped_path.is_absolute()
        or mapped_path.exists()
    )
    if looks_like_path:
        resolved = context.sandbox.resolve(mapped)
        suffix = resolved.suffix.lower()
        if suffix in _BLOCKED_PROGRAM_SUFFIXES:
            raise PermissionError(f"{SAFETY_BLOCK_MESSAGE}: open_app does not launch script or installer files")
        if not resolved.is_file():
            raise FileNotFoundError(f"Application not found: {resolved}")
        is_direct_program = suffix in _DIRECT_PROGRAM_SUFFIXES or (
            suffix == "" and os.access(resolved, os.X_OK)
        )
        if not is_direct_program:
            raise PermissionError(f"{SAFETY_BLOCK_MESSAGE}: use open_path for documents and non-program files")
        approved = context.require_confirmation(
            "Open application file?",
            f"VISION wants to launch this executable:\n{resolved}",
            {"path": str(resolved), "action": "open_app"},
        )
        if not approved:
            raise PermissionError(f"User declined to launch {resolved}")
        target = _launch_program(str(resolved))
        return {"app_name": requested, "opened": True, "target": target}

    if not _PLAIN_PROGRAM_RE.fullmatch(mapped):
        raise PermissionError(f"{SAFETY_BLOCK_MESSAGE}: application name contains unsupported path or shell characters")
    if Path(mapped).suffix.lower() in _BLOCKED_PROGRAM_SUFFIXES | {".bat", ".cmd"}:
        raise PermissionError(f"{SAFETY_BLOCK_MESSAGE}: open_app does not launch script files by name")

    executable = shutil.which(mapped)
    if executable:
        return {"app_name": requested, "opened": True, "target": _launch_program(executable)}

    allow_shell_lookup = requested.lower() in APP_MAP or bool(_SINGLE_PROGRAM_RE.fullmatch(mapped))
    try:
        target = _launch_program(mapped, allow_windows_shell_lookup=allow_shell_lookup)
    except OSError as exc:
        raise RuntimeError(f"Application not found: {requested}") from exc
    return {"app_name": requested, "opened": True, "target": target}


def focus_window(args: schemas.FocusWindowArgs, _context: ToolContext) -> dict:
    if sys.platform != "win32":
        raise RuntimeError("focus_window is available on Windows only")
    import win32con  # type: ignore
    import win32gui  # type: ignore

    matches: list[tuple[int, str]] = []

    def collect(hwnd, _extra):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd) or ""
            if args.title_substring.lower() in title.lower():
                matches.append((int(hwnd), title))
        return True

    win32gui.EnumWindows(collect, None)
    if not matches:
        raise RuntimeError(f"No visible window title contains '{args.title_substring}'")
    hwnd, title = matches[0]
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    return {"focused": True, "title": title}


def open_path(args: schemas.OpenPathArgs, context: ToolContext) -> dict:
    from .code_files import _confirmed_path

    path = _confirmed_path(context, args.path, "open")
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        opener = shutil.which("open")
        if not opener:
            raise RuntimeError("The macOS 'open' command is not available")
        subprocess.Popen([opener, str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        opener = shutil.which("xdg-open")
        if not opener:
            raise RuntimeError("xdg-open is not available on this system")
        subprocess.Popen([opener, str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"path": str(path), "opened": True, "executable": path.suffix.lower() in EXECUTABLE_SUFFIXES}


def open_url(args: schemas.OpenUrlArgs, _context: ToolContext) -> dict:
    opened = webbrowser.open(args.url)
    return {"url": args.url, "opened": bool(opened)}


def clipboard_get(_args, _context: ToolContext) -> dict:
    try:
        import pyperclip  # type: ignore

        text = str(pyperclip.paste() or "")
        return {
            "text": text[:MAX_CLIPBOARD_CHARS],
            "characters": len(text),
            "truncated": len(text) > MAX_CLIPBOARD_CHARS,
        }
    except Exception as exc:
        raise RuntimeError(f"Clipboard read failed: {exc}") from exc


def clipboard_set(args: schemas.ClipboardSetArgs, context: ToolContext) -> dict:
    if len(args.text) > 2000:
        approved = context.require_confirmation(
            "Copy large text to clipboard?",
            f"VISION wants to copy {len(args.text)} characters to the clipboard.",
            {"characters": len(args.text), "action": "clipboard_set"},
        )
        if not approved:
            raise PermissionError("User declined the large clipboard write")
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(args.text)
    except Exception as exc:
        raise RuntimeError(f"Clipboard write failed: {exc}") from exc
    return {"written": True, "characters": len(args.text)}


def _gpu_status() -> dict:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {"available": False}
    try:
        completed = run_bounded(
            [executable, "--query-gpu=name,memory.total,memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
            timeout=5,
            stdout_chars=20_000,
            stderr_chars=4_000,
            stdout_mode="head",
            stderr_mode="head",
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return {"available": False}
        devices = []
        for line in completed.stdout.strip().splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 4:
                continue
            # GPU product names may themselves contain commas. The three
            # numeric fields are always last, so parse from the right and
            # skip only the malformed row instead of discarding every GPU.
            try:
                values = [float(part) for part in parts[-3:]]
            except ValueError:
                continue
            if not all(math.isfinite(value) for value in values):
                continue
            devices.append(
                {
                    "name": ", ".join(parts[:-3]),
                    "vram_total_mb": values[0],
                    "vram_used_mb": values[1],
                    "utilization_percent": values[2],
                }
            )
        return {"available": bool(devices), "devices": devices}
    except Exception:
        return {"available": False}


def system_status(_args, _context: ToolContext) -> dict:
    memory = psutil.virtual_memory()
    root = Path(sys.executable).anchor or "/"
    disk = shutil.disk_usage(root)
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "cpu_count": psutil.cpu_count(logical=True),
        "ram_total_gb": round(memory.total / 1024**3, 2),
        "ram_used_gb": round(memory.used / 1024**3, 2),
        "ram_percent": memory.percent,
        "disk_root": root,
        "disk_total_gb": round(disk.total / 1024**3, 2),
        "disk_free_gb": round(disk.free / 1024**3, 2),
        "disk_used_percent": round((disk.used / disk.total * 100) if disk.total else 0, 1),
        "gpu": _gpu_status(),
    }


def set_timer(args: schemas.SetTimerArgs, context: ToolContext) -> dict:
    seconds = args.minutes * 60
    if context.timer_callback:
        context.timer_callback(args.minutes, args.label)
    else:
        def _fire() -> None:
            try:
                context.timers.remove(timer)
            except ValueError:
                pass

        timer = threading.Timer(seconds, _fire)
        timer.daemon = True
        context.timers.append(timer)
        timer.start()
    return {"scheduled": True, "minutes": args.minutes, "label": args.label, "scope": "while VISION is running"}

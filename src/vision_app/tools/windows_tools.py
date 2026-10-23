from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import psutil

from . import schemas
from .context import ToolContext
from .sandbox import EXECUTABLE_SUFFIXES

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
            name = str(process.info.get("name") or "").strip()
            if name:
                apps.setdefault(name, {"name": name, "pids": []})["pids"].append(int(process.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    values = sorted(apps.values(), key=lambda item: item["name"].lower())
    return {"apps": values[:200], "count": len(values)}


def open_app(args: schemas.OpenAppArgs, _context: ToolContext) -> dict:
    requested = args.app_name.strip()
    mapped = APP_MAP.get(requested.lower(), requested)
    path = Path(mapped).expanduser()
    try:
        if sys.platform == "win32" and path.exists():
            os.startfile(str(path))  # type: ignore[attr-defined]
            return {"app_name": requested, "opened": True, "target": str(path)}
        executable = shutil.which(mapped) or mapped
        subprocess.Popen([executable], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"app_name": requested, "opened": True, "target": executable}
    except Exception:
        if sys.platform == "win32":
            os.startfile(requested)  # type: ignore[attr-defined]
            return {"app_name": requested, "opened": True, "target": requested}
        raise


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
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return {"path": str(path), "opened": True, "executable": path.suffix.lower() in EXECUTABLE_SUFFIXES}


def open_url(args: schemas.OpenUrlArgs, _context: ToolContext) -> dict:
    opened = webbrowser.open(args.url)
    return {"url": args.url, "opened": bool(opened)}


def clipboard_get(_args, _context: ToolContext) -> dict:
    try:
        import pyperclip  # type: ignore

        return {"text": pyperclip.paste() or ""}
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
        completed = subprocess.run(
            [executable, "--query-gpu=name,memory.total,memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return {"available": False}
        devices = []
        for line in completed.stdout.strip().splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 4:
                devices.append({"name": parts[0], "vram_total_mb": float(parts[1]), "vram_used_mb": float(parts[2]), "utilization_percent": float(parts[3])})
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
        timer = threading.Timer(seconds, lambda: None)
        timer.daemon = True
        timer.start()
        context.timers.append(timer)
    return {"scheduled": True, "minutes": args.minutes, "label": args.label, "scope": "while VISION is running"}

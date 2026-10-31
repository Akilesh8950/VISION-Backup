from __future__ import annotations

import os
import re
from pathlib import Path

SYSTEM_PREFIXES_POSIX = ("/etc", "/usr", "/bin", "/sbin", "/boot", "/proc", "/sys", "/dev")
EXECUTABLE_SUFFIXES = {
    ".bat", ".cmd", ".com", ".cpl", ".docm", ".dotm", ".exe", ".hta",
    ".inf", ".ins", ".jar", ".js", ".jse", ".lnk", ".msc", ".msi",
    ".msp", ".mst", ".pif", ".ppam", ".ppsm", ".pptm", ".ps1", ".py",
    ".pyw", ".reg", ".scr", ".sct", ".shb", ".shs", ".u3p", ".vb",
    ".vbe", ".vbs", ".vsdm", ".ws", ".wsf", ".wsh", ".xlam", ".xlsm",
    ".xltm",
}
_WINDOWS_SYSTEM_FOLDERS = ("windows", "program files", "program files (x86)", "programdata")
_WINDOWS_ROOT_ENV_VARS = ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData")


def _at_or_under(value: str, prefix: str, separator: str) -> bool:
    clean_prefix = prefix.rstrip(separator)
    return value == clean_prefix or value.startswith(clean_prefix + separator)


def _windows_normalized(value: str) -> str:
    return value.replace("/", "\\").lower().rstrip("\\")


def is_system_path(path: str | Path) -> bool:
    text = str(path)
    windows_text = _windows_normalized(text)

    # The actual installation may live on a drive other than C:. Prefer the
    # roots Windows itself reports, then retain drive-agnostic checks for the
    # conventional system directory names.
    for env_name in _WINDOWS_ROOT_ENV_VARS:
        root = os.environ.get(env_name)
        if root and _at_or_under(windows_text, _windows_normalized(root), "\\"):
            return True
    drive_match = re.match(r"^([a-z]:\\)(.+)$", windows_text)
    if drive_match:
        first_part = drive_match.group(2).split("\\", 1)[0]
        if first_part in _WINDOWS_SYSTEM_FOLDERS:
            return True

    posix = "/" + text.strip("/").lower() if text.startswith("/") else text.lower()
    return any(_at_or_under(posix, prefix, "/") for prefix in SYSTEM_PREFIXES_POSIX)


def has_parent_reference(raw: str) -> bool:
    return ".." in re.split(r"[\\/]", raw)


class PathSandbox:
    def __init__(self, workspace: Path, extra_read_roots: list[Path] | None = None):
        self.workspace = workspace.expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.extra_read_roots = [path.expanduser().resolve() for path in extra_read_roots or []]

    def resolve(self, raw: str) -> Path:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        # strict=False lets callers target a not-yet-created file while still normalising .. and links in parents.
        return candidate.resolve(strict=False)

    def is_inside_workspace(self, path: Path) -> bool:
        resolved = path.resolve(strict=False)
        return resolved == self.workspace or self.workspace in resolved.parents

    def is_read_allowed_root(self, path: Path) -> bool:
        resolved = path.resolve(strict=False)
        if self.is_inside_workspace(resolved):
            return True
        return any(resolved == root or root in resolved.parents for root in self.extra_read_roots)

    def needs_confirmation(self, raw: str, resolved: Path, *, action: str) -> bool:
        if has_parent_reference(raw) or is_system_path(resolved) or is_system_path(raw):
            return True
        if action == "write" and not self.is_inside_workspace(resolved):
            return True
        if action == "open":
            # Every model-requested shell open asks.  An extension list
            # cannot prove that a Windows file association is inert.
            return True
        return action == "read" and not self.is_read_allowed_root(resolved)

    def validate_write(self, resolved: Path) -> None:
        if is_system_path(resolved):
            raise PermissionError("Writing to a system path is blocked by VISION safety policy")

    def display_path(self, path: Path) -> str:
        return str(path.resolve(strict=False))

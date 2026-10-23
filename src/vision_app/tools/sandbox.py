from __future__ import annotations

import os
import re
from pathlib import Path

SYSTEM_PREFIXES_WINDOWS = ("c:\\windows", "c:\\program files", "c:\\program files (x86)")
SYSTEM_PREFIXES_POSIX = ("/etc", "/usr", "/bin", "/sbin", "/boot", "/proc", "/sys", "/dev")
EXECUTABLE_SUFFIXES = {".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1", ".vbs", ".js"}


def _norm(value: str) -> str:
    return value.replace("/", "\\").lower() if ":\\" in value or "\\" in value else value.lower()


def is_system_path(path: str | Path) -> bool:
    text = str(path)
    lower = text.lower().replace("/", "\\")
    if any(lower.startswith(prefix) for prefix in SYSTEM_PREFIXES_WINDOWS):
        return True
    posix = "/" + text.strip("/").lower() if text.startswith("/") else text.lower()
    return any(posix == prefix or posix.startswith(prefix + "/") for prefix in SYSTEM_PREFIXES_POSIX)


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
        if action == "open" and resolved.suffix.lower() in EXECUTABLE_SUFFIXES:
            return True
        if action == "read" and not self.is_read_allowed_root(resolved):
            return True
        return False

    def validate_write(self, resolved: Path) -> None:
        if is_system_path(resolved):
            raise PermissionError("Writing to a system path is blocked by VISION safety policy")

    def display_path(self, path: Path) -> str:
        return str(path.resolve(strict=False))

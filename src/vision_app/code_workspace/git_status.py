from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..subprocess_utils import run_bounded

MAX_GIT_STATUS_ENTRIES = 2_000
MAX_GIT_STATUS_CHARS = 200_000


def _environment() -> dict[str, str]:
    blocked = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "HF_")
    return {key: value for key, value in os.environ.items() if not any(part in key.upper() for part in blocked)}


def git_status(root: Path, timeout: float = 10.0) -> dict:
    executable = shutil.which("git")
    if not executable:
        return {"available": False, "reason": "Git is not installed"}
    try:
        completed = run_bounded(
            [executable, "-c", "core.fsmonitor=false", "-C", str(root), "status", "--porcelain=v1", "-b"],
            timeout=timeout,
            env=_environment(),
            stdout_chars=MAX_GIT_STATUS_CHARS,
            stderr_chars=4_000,
            stdout_mode="head",
            stderr_mode="head",
        )
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    if completed.returncode != 0:
        return {"available": False, "reason": "Not a Git worktree"}
    lines = completed.stdout.splitlines()
    branch = lines[0][3:].strip() if lines and lines[0].startswith("##") else ""
    entries = []
    status_lines = lines[1:] if branch else lines
    truncated = completed.stdout_truncated or len(status_lines) > MAX_GIT_STATUS_ENTRIES
    for line in status_lines[:MAX_GIT_STATUS_ENTRIES]:
        if len(line) >= 4:
            entries.append({"status": line[:2], "path": line[3:]})
    return {
        "available": True,
        "branch": branch,
        "entries": entries,
        "clean": not entries and not truncated,
        "truncated": truncated,
    }

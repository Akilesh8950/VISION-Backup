from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def git_status(root: Path, timeout: float = 10.0) -> dict:
    executable = shutil.which("git")
    if not executable:
        return {"available": False, "reason": "Git is not installed"}
    try:
        completed = subprocess.run(
            [executable, "-C", str(root), "status", "--porcelain=v1", "-b"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    if completed.returncode != 0:
        return {"available": False, "reason": "Not a Git worktree"}
    lines = completed.stdout.splitlines()
    branch = lines[0][3:].strip() if lines and lines[0].startswith("##") else ""
    entries = []
    for line in lines[1:] if branch else lines:
        if len(line) >= 4:
            entries.append({"status": line[:2], "path": line[3:]})
    return {"available": True, "branch": branch, "entries": entries, "clean": not entries}

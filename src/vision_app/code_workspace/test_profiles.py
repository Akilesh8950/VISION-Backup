from __future__ import annotations

import os
from pathlib import Path

from ..subprocess_utils import run_bounded


def _environment() -> dict[str, str]:
    blocked = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "HF_")
    return {k: v for k, v in os.environ.items() if not any(part in k.upper() for part in blocked)}


def run_test_profile(profile: dict, timeout: float = 300.0) -> dict:
    program = str(profile.get("program") or "")
    args = [str(arg) for arg in profile.get("args") or []]
    if not program:
        raise ValueError("Test profile has no program")
    cwd = Path(str(profile.get("cwd") or ".")).expanduser()
    if not cwd.is_dir():
        raise FileNotFoundError(f"Test profile working directory does not exist: {cwd}")
    completed = run_bounded(
        [program, *args],
        cwd=cwd,
        env=_environment(),
        timeout=timeout,
        stdout_chars=50_000,
        stderr_chars=50_000,
    )
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "command": [program, *args],
    }

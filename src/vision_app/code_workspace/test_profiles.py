from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _environment() -> dict[str, str]:
    blocked = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "HF_")
    return {k: v for k, v in os.environ.items() if not any(part in k.upper() for part in blocked)}


def run_test_profile(profile: dict, timeout: float = 300.0) -> dict:
    program = str(profile.get("program") or "")
    args = [str(arg) for arg in profile.get("args") or []]
    if not program:
        raise ValueError("Test profile has no program")
    cwd = Path(str(profile.get("cwd") or ".")).expanduser()
    completed = subprocess.run(
        [program, *args],
        cwd=str(cwd),
        env=_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
    )
    return {
        "exit_code": completed.returncode,
        "stdout": (completed.stdout or "")[-50_000:],
        "stderr": (completed.stderr or "")[-50_000:],
        "command": [program, *args],
    }

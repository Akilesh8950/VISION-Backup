from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..constants import MIN_LLAMA_BUILD
from ..paths import AppPaths

BUILD_RE = re.compile(r"\bb(\d{4,6})\b")


def _exe_name() -> str:
    return "llama-server.exe" if sys.platform == "win32" else "llama-server"


def candidate_paths(paths: AppPaths, configured_path: str = "") -> list[Path]:
    candidates: list[Path] = []
    exe = _exe_name()
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / exe)
        candidates.append(Path(sys.executable).resolve().parent / "llama.cpp" / exe)
    if configured_path:
        configured = Path(configured_path).expanduser()
        candidates.append(configured / exe if configured.is_dir() else configured)
    candidates.extend(sorted(paths.bin_dir.rglob(exe)) if paths.bin_dir.exists() else [])
    candidates.append(Path.cwd() / "third_party/llama.cpp" / exe)
    found = shutil.which("llama-server")
    if found:
        candidates.append(Path(found))
    # Preserve order while removing duplicates.
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def find_llama_server(paths: AppPaths, configured_path: str = "") -> Path | None:
    for candidate in candidate_paths(paths, configured_path):
        if candidate.is_file():
            return candidate
    return None


def parse_build_number(text: str) -> int | None:
    match = BUILD_RE.search(text)
    return int(match.group(1)) if match else None


def detect_build(binary: Path, timeout: float = 5.0) -> int | None:
    from_path = parse_build_number(str(binary))
    try:
        completed = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return parse_build_number((completed.stdout or "") + (completed.stderr or "")) or from_path
    except Exception:
        return from_path


def build_is_supported(build: int | None) -> bool:
    return build is not None and build >= MIN_LLAMA_BUILD

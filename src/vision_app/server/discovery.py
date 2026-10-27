from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..constants import MIN_LLAMA_BUILD
from ..paths import AppPaths
from ..subprocess_utils import run_bounded

BUILD_RES = (
    re.compile(r"\bbuild\s+(\d{4,6})\b", re.IGNORECASE),
    re.compile(r"\bb(\d{4,6})\b", re.IGNORECASE),
    re.compile(r"\bversion\s*[:=]\s*(\d{4,6})\b", re.IGNORECASE),
)
_BUILD_CACHE: dict[tuple[str, int, int], int | None] = {}


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
    found = shutil.which("llama-server")
    if found:
        candidates.append(Path(found))
    # Locked discovery order: bundled -> Settings path -> PATH -> the copy
    # downloaded into VISION's app-data bin directory. An arbitrary current
    # working directory is never treated as a trusted bundled location.
    candidates.extend(sorted(paths.bin_dir.rglob(exe)) if paths.bin_dir.exists() else [])
    # Preserve order while removing duplicates.
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def find_llama_server(
    paths: AppPaths,
    configured_path: str = "",
    *,
    require_supported: bool = False,
) -> Path | None:
    for candidate in candidate_paths(paths, configured_path):
        if not candidate.is_file():
            continue
        if require_supported and not build_is_supported(detect_build(candidate)):
            continue
        return candidate
    return None


def parse_build_number(text: str) -> int | None:
    for pattern in BUILD_RES:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def detect_build(binary: Path, timeout: float = 5.0) -> int | None:
    from_path = parse_build_number(str(binary))
    try:
        stat = binary.stat()
        cache_key = (os.path.normcase(str(binary.resolve())), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return from_path
    if cache_key in _BUILD_CACHE:
        return _BUILD_CACHE[cache_key]

    detected = from_path
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        completed = run_bounded(
            [str(binary), "--version"],
            timeout=timeout,
            stdout_chars=20_000,
            stderr_chars=20_000,
            stdout_mode="head",
            stderr_mode="head",
            creationflags=creationflags,
        )
        detected = parse_build_number((completed.stdout or "") + "\n" + (completed.stderr or "")) or from_path
    except Exception:
        detected = from_path
    _BUILD_CACHE[cache_key] = detected
    return detected


def build_is_supported(build: int | None) -> bool:
    return build is not None and build >= MIN_LLAMA_BUILD

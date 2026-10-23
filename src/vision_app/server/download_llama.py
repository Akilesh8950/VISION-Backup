from __future__ import annotations

import platform
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

from ..constants import PINNED_LLAMA_TAG
from ..paths import AppPaths

BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{PINNED_LLAMA_TAG}"
Progress = Callable[[str, int, int], None]


def detect_variant(has_nvidia: bool | None = None, machine: str | None = None) -> str:
    machine = (machine or platform.machine()).lower()
    if "arm" in machine:
        return "vulkan-arm64"
    if has_nvidia is None:
        has_nvidia = shutil.which("nvidia-smi") is not None
    return "cuda-12.4-x64" if has_nvidia else "vulkan-x64"


def asset_names(variant: str) -> list[str]:
    tag = PINNED_LLAMA_TAG
    assets = {
        "cpu-x64": [f"llama-{tag}-bin-win-cpu-x64.zip"],
        "vulkan-x64": [f"llama-{tag}-bin-win-vulkan-x64.zip"],
        "vulkan-arm64": [f"llama-{tag}-bin-win-vulkan-arm64.zip"],
        "cuda-12.4-x64": [
            f"llama-{tag}-bin-win-cuda-12.4-x64.zip",
            "cudart-llama-bin-win-cuda-12.4-x64.zip",
        ],
        "cuda-13.4-x64": [
            f"llama-{tag}-bin-win-cuda-13.4-x64.zip",
            "cudart-llama-bin-win-cuda-13.4-x64.zip",
        ],
    }
    if variant not in assets:
        raise ValueError(f"Unsupported llama.cpp variant: {variant}")
    return assets[variant]


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            target = (destination / info.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Unsafe path in archive: {info.filename}")
            if ((getattr(info, "external_attr", 0) >> 16) & 0o170000) == 0o120000:
                raise ValueError(f"Symlinks are not allowed in llama.cpp archive: {info.filename}")
        zf.extractall(destination)


def download_file(url: str, destination: Path, progress: Progress | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".part")
    downloaded = temp.stat().st_size if temp.exists() else 0
    headers = {"Range": f"bytes={downloaded}-"} if downloaded else {}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=120.0) as response:
        if downloaded and response.status_code != 206:
            downloaded = 0
            mode = "wb"
        else:
            mode = "ab" if downloaded else "wb"
        response.raise_for_status()
        total_header = response.headers.get("content-length")
        total = downloaded + int(total_header) if total_header and total_header.isdigit() else 0
        with temp.open(mode) as stream:
            for chunk in response.iter_bytes(1024 * 256):
                stream.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(destination.name, downloaded, total)
    temp.replace(destination)
    return destination


def download_llama(paths: AppPaths, variant: str = "auto", progress: Progress | None = None) -> Path:
    actual_variant = detect_variant() if variant == "auto" else variant
    target_dir = paths.bin_dir / f"llama-{PINNED_LLAMA_TAG}-{actual_variant}"
    target_dir.mkdir(parents=True, exist_ok=True)
    for asset in asset_names(actual_variant):
        archive = target_dir / asset
        if not archive.exists():
            download_file(f"{BASE}/{asset}", archive, progress)
        safe_extract_zip(archive, target_dir)
    for executable in target_dir.rglob("llama-server.exe"):
        return executable
    for executable in target_dir.rglob("llama-server"):
        return executable
    raise FileNotFoundError("llama-server executable was not found after extraction")

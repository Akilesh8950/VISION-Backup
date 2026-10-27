from __future__ import annotations

import platform
import re
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

from ..connectivity import ConnectivityMonitor, ReconnectWaiter, is_network_error
from ..constants import PINNED_LLAMA_TAG
from ..paths import AppPaths

BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{PINNED_LLAMA_TAG}"
Progress = Callable[[str, int, int], None]
MAX_DOWNLOAD_BYTES = 4 * 1024**3
MAX_EXTRACTED_BYTES = 4 * 1024**3
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


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
    max_total_bytes = MAX_EXTRACTED_BYTES
    actual_total = 0
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > 10_000:
            raise ValueError("llama.cpp archive contains too many files")
        if sum(info.file_size for info in infos) > max_total_bytes:
            raise ValueError("llama.cpp archive expands beyond the 4 GiB limit")
        for info in infos:
            normalized = info.filename.replace("\\", "/")
            if normalized.startswith("/") or any(ord(char) < 32 or char in '<>:"|?*' for char in normalized):
                raise ValueError(f"Unsafe path in archive: {info.filename}")
            parts = [part for part in normalized.split("/") if part not in ("", ".")]
            if not parts:
                if info.is_dir():
                    continue
                raise ValueError(f"Unsafe path in archive: {info.filename}")
            if any(
                part == ".."
                or part.endswith((" ", "."))
                or part.split(".", 1)[0].lower() in _WINDOWS_RESERVED_NAMES
                for part in parts
            ):
                raise ValueError(f"Unsafe path in archive: {info.filename}")
            if ((getattr(info, "external_attr", 0) >> 16) & 0o170000) == 0o120000:
                raise ValueError(f"Symlinks are not allowed in llama.cpp archive: {info.filename}")
            target = (root / Path(*parts)).resolve() if parts else root
            if target != root and root not in target.parents:
                raise ValueError(f"Unsafe path in archive: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    actual_total += len(chunk)
                    if actual_total > max_total_bytes:
                        raise ValueError("llama.cpp archive expands beyond the 4 GiB limit")
                    output.write(chunk)


def _parse_content_range(value: str | None) -> tuple[int, int, int | None] | None:
    if not value:
        return None
    match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", value.strip(), flags=re.IGNORECASE)
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2))
    total = None if match.group(3) == "*" else int(match.group(3))
    if end < start or (total is not None and (total <= 0 or end >= total)):
        return None
    return start, end, total


class _IncompleteDownload(RuntimeError):
    """The stream ended early; the .part file is a valid resume point."""


def _download_file_once(url: str, destination: Path, progress: Progress | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".part")

    for _attempt in range(2):
        downloaded = temp.stat().st_size if temp.exists() else 0
        if downloaded > MAX_DOWNLOAD_BYTES:
            temp.unlink(missing_ok=True)
            downloaded = 0
        headers = {"Range": f"bytes={downloaded}-"} if downloaded else {}
        restart_from_scratch = False
        oversize = False
        with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=120.0) as response:
            total = 0
            mode = "wb"
            if downloaded and response.status_code == 416:
                restart_from_scratch = True
            elif downloaded and response.status_code == 206:
                parsed_range = _parse_content_range(response.headers.get("content-range"))
                if parsed_range is None or parsed_range[0] != downloaded:
                    restart_from_scratch = True
                else:
                    mode = "ab"
                    total = parsed_range[2] or 0
            elif downloaded:
                # The server ignored the Range header; the body is the full
                # file, so discard the old partial prefix by truncating.
                downloaded = 0
                mode = "wb"

            if restart_from_scratch:
                # Consume no body; closing the stream and retrying without
                # the stale partial file is safer than appending bytes from
                # an unverified range.
                pass
            else:
                response.raise_for_status()
                total_header = response.headers.get("content-length")
                content_length = int(total_header) if total_header and total_header.isdigit() else 0
                if not total:
                    total = downloaded + content_length if content_length else 0
                if total > MAX_DOWNLOAD_BYTES:
                    oversize = True
                else:
                    with temp.open(mode) as stream:
                        for chunk in response.iter_bytes(1024 * 256):
                            if downloaded + len(chunk) > MAX_DOWNLOAD_BYTES:
                                oversize = True
                                break
                            stream.write(chunk)
                            downloaded += len(chunk)
                            if progress:
                                progress(destination.name, downloaded, total)
        if oversize:
            # A partial copy of a file that can never satisfy the size cap
            # is not a useful resume point; remove it so a later retry does
            # not repeat the same doomed range request.
            temp.unlink(missing_ok=True)
            raise RuntimeError(f"Download exceeds the 4 GiB safety limit: {destination.name}")
        if restart_from_scratch:
            temp.unlink(missing_ok=True)
            continue
        if total and downloaded != total:
            raise _IncompleteDownload(f"Download incomplete for {destination.name}: {downloaded} of {total} bytes")
        temp.replace(destination)
        return destination
    raise RuntimeError(f"Could not resume or restart download for {destination.name}")


def download_file(
    url: str,
    destination: Path,
    progress: Progress | None = None,
    *,
    monitor: ConnectivityMonitor | None = None,
    status: Callable[[str], None] | None = None,
    max_offline_wait_seconds: float = 2 * 3600.0,
) -> Path:
    """Download with reconnect resilience.

    ``_download_file_once`` resumes from the ``.part`` file via HTTP
    Range; this wrapper waits out a lost connection (or backs off a
    struggling server) and resumes instead of failing.  Non-network
    failures - oversize, bad ranges, HTTP errors - propagate at once.
    The partial file is kept even when giving up, so the next attempt
    continues where this one stopped.
    """

    waiter = ReconnectWaiter(monitor, max_offline_wait_seconds=max_offline_wait_seconds)

    def _waiting() -> None:
        if status:
            status("Internet connection lost — download paused; it will resume automatically…")

    def _resumed() -> None:
        if status:
            status(f"Back online — resuming {destination.name}…")

    while True:
        try:
            result = _download_file_once(url, destination, progress)
            waiter.monitor.note_success()
            return result
        except _IncompleteDownload:
            waiter.monitor.note_failure()
            if not waiter.wait_after_failure(on_waiting=_waiting, on_resumed=_resumed):
                raise RuntimeError(
                    f"Download incomplete for {destination.name}. The partial download "
                    "was kept — reconnect to the internet and try again to resume it."
                ) from None
        except Exception as exc:
            if not is_network_error(exc):
                raise
            waiter.monitor.note_failure()
            if not waiter.wait_after_failure(on_waiting=_waiting, on_resumed=_resumed):
                raise RuntimeError(
                    f"Download interrupted for {destination.name}. The partial download "
                    "was kept — reconnect to the internet and try again to resume it."
                ) from exc


def download_llama(
    paths: AppPaths,
    variant: str = "auto",
    progress: Progress | None = None,
    *,
    status: Callable[[str], None] | None = None,
) -> Path:
    actual_variant = detect_variant() if variant == "auto" else variant
    target_dir = paths.bin_dir / f"llama-{PINNED_LLAMA_TAG}-{actual_variant}"
    target_dir.mkdir(parents=True, exist_ok=True)
    for asset in asset_names(actual_variant):
        archive = target_dir / asset
        for attempt in range(2):
            if not archive.exists():
                download_file(f"{BASE}/{asset}", archive, progress, status=status)
            try:
                safe_extract_zip(archive, target_dir)
                break
            except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, ValueError):
                if attempt:
                    raise
                # A pre-existing corrupt archive previously made every retry
                # fail at the same extraction step. Remove it and fetch a
                # fresh copy once.
                archive.unlink(missing_ok=True)
                archive.with_suffix(archive.suffix + ".part").unlink(missing_ok=True)
    for executable in target_dir.rglob("llama-server.exe"):
        return executable
    for executable in target_dir.rglob("llama-server"):
        return executable
    raise FileNotFoundError("llama-server executable was not found after extraction")

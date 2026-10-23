from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from ..config import AppConfig
from ..constants import EXPECTED_GGUF_SIZE, GGUF_FILENAME, MODEL_REPO_ID
from ..paths import AppPaths

REQUIRED_FREE_BYTES = 7 * 1024**3
Progress = Callable[[str], None]


def ensure_disk_space(path: Path, required: int = REQUIRED_FREE_BYTES) -> int:
    free = shutil.disk_usage(path if path.exists() else path.parent).free
    if free < required:
        raise RuntimeError(f"At least 7 GB free disk space is required; only {free / 1024**3:.1f} GB is available")
    return free


def download_model(
    paths: AppPaths,
    *,
    token: str = "",
    revision: str | None = None,
    pending: bool = False,
    expected_size: int = EXPECTED_GGUF_SIZE,
    progress: Progress | None = None,
) -> Path:
    destination_dir = paths.models_dir / "pending" if pending else paths.models_dir
    destination_dir.mkdir(parents=True, exist_ok=True)
    ensure_disk_space(destination_dir)
    if progress:
        progress(f"Downloading {GGUF_FILENAME}…")
    from huggingface_hub import hf_hub_download  # type: ignore

    downloaded = Path(hf_hub_download(
        repo_id=MODEL_REPO_ID,
        filename=GGUF_FILENAME,
        revision=revision,
        local_dir=str(destination_dir),
        token=token or None,
    ))
    destination = destination_dir / GGUF_FILENAME
    if downloaded != destination and downloaded.exists():
        if destination.exists():
            destination.unlink()
        shutil.move(str(downloaded), str(destination))
    if expected_size and destination.stat().st_size != expected_size:
        raise RuntimeError(
            f"Downloaded model size {destination.stat().st_size} does not match expected {expected_size}"
        )
    if progress:
        progress("Model downloaded and size verified")
    return destination


def activate_pending_model(paths: AppPaths, config: AppConfig) -> bool:
    if not config.pending_restart or not config.pending_model_path:
        return False
    pending = Path(config.pending_model_path)
    if not pending.is_file():
        return False
    target = paths.model_file
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_suffix(".previous.gguf")
        if backup.exists():
            backup.unlink()
        target.replace(backup)
    try:
        shutil.move(str(pending), str(target))
    except Exception:
        backup = target.with_suffix(".previous.gguf")
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    config.model_path = str(target)
    config.installed_model_sha = config.pending_model_sha
    config.pending_model_path = ""
    config.pending_model_sha = ""
    config.pending_restart = False
    config.save(paths)
    return True

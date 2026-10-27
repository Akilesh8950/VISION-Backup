from __future__ import annotations

import errno
import logging
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from ..config import AppConfig
from ..connectivity import ConnectivityMonitor, ReconnectWaiter, is_network_error
from ..constants import EXPECTED_GGUF_SIZE, GGUF_FILENAME, MODEL_REPO_ID
from ..paths import AppPaths

REQUIRED_FREE_BYTES = 7 * 1024**3
Progress = Callable[[str], None]
logger = logging.getLogger(__name__)


def ensure_disk_space(path: Path, required: int = REQUIRED_FREE_BYTES) -> int:
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < required:
        raise RuntimeError(
            f"At least {required / 1024**3:.1f} GiB free disk space is required; "
            f"only {free / 1024**3:.1f} GiB is available"
        )
    return free


def download_model(
    paths: AppPaths,
    *,
    token: str = "",
    revision: str | None = None,
    pending: bool = False,
    expected_size: int = EXPECTED_GGUF_SIZE,
    progress: Progress | None = None,
    monitor: ConnectivityMonitor | None = None,
    max_offline_wait_seconds: float = 2 * 3600.0,
) -> Path:
    destination_dir = paths.models_dir / "pending" if pending else paths.models_dir
    destination_dir.mkdir(parents=True, exist_ok=True)
    required_space = max(REQUIRED_FREE_BYTES, int(expected_size or 0) + 256 * 1024**2)
    ensure_disk_space(destination_dir, required_space)
    if progress:
        progress(f"Downloading {GGUF_FILENAME}…")
    from huggingface_hub import hf_hub_download  # type: ignore

    # hf_hub_download keeps its partial transfer under the destination
    # directory and resumes it on the next call, so a dropped connection
    # only needs patience: wait for the internet to come back and call
    # again.  Non-network errors (missing file, auth, disk) fail fast.
    waiter = ReconnectWaiter(monitor, max_offline_wait_seconds=max_offline_wait_seconds)

    def _waiting() -> None:
        if progress:
            progress("Internet connection lost — download paused; it will resume automatically…")

    def _resumed() -> None:
        if progress:
            progress("Back online — resuming the model download…")

    while True:
        try:
            downloaded = Path(hf_hub_download(
                repo_id=MODEL_REPO_ID,
                filename=GGUF_FILENAME,
                revision=revision,
                local_dir=str(destination_dir),
                token=token or None,
            ))
            waiter.monitor.note_success()
            break
        except Exception as exc:
            if not is_network_error(exc):
                raise
            waiter.monitor.note_failure()
            if not waiter.wait_after_failure(on_waiting=_waiting, on_resumed=_resumed):
                raise RuntimeError(
                    "The model download was interrupted and could not resume. "
                    "The partial download was kept — reconnect to the internet "
                    "and try again to continue where it left off."
                ) from exc
    destination = destination_dir / GGUF_FILENAME
    if downloaded != destination and downloaded.exists():
        if destination.exists():
            destination.unlink()
        shutil.move(str(downloaded), str(destination))
    if expected_size and destination.stat().st_size != expected_size:
        actual_size = destination.stat().st_size
        try:
            destination.unlink()
        except OSError:
            logger.exception("Could not remove the size-mismatched model download")
        raise RuntimeError(
            f"Downloaded model size {actual_size} does not match expected {expected_size}"
        )
    if progress:
        progress("Model downloaded and size verified")
    return destination


def _move_model_file(source: Path, target: Path) -> None:
    """Move a model without exposing a partially copied target path."""
    try:
        source.replace(target)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    temporary = target.with_name(f".{target.name}.activating-{uuid.uuid4().hex}")
    try:
        shutil.copy2(source, temporary)
        if temporary.stat().st_size != source.stat().st_size:
            raise OSError("Copied model size does not match the pending model")
        temporary.replace(target)
        source.unlink()
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                logger.warning("Could not remove the temporary model activation file", exc_info=True)


def activate_pending_model(paths: AppPaths, config: AppConfig) -> bool:
    if not config.pending_restart or not config.pending_model_path:
        return False
    pending = Path(config.pending_model_path)
    target = paths.model_file
    backup = target.with_suffix(".previous.gguf")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not pending.is_file():
        # A previous activation may have been interrupted after the current
        # model was moved aside. Recover that known-good file rather than
        # leaving the installation with no model at all.
        if not target.exists() and backup.is_file():
            try:
                backup.replace(target)
                logger.warning("Restored the previous model because the pending model was missing")
            except OSError:
                logger.exception("Could not restore the previous model after an interrupted activation")
        return False

    if pending.resolve() != target.resolve():
        if target.exists():
            if backup.exists():
                backup.unlink()
            target.replace(backup)
        try:
            _move_model_file(pending, target)
        except Exception:
            if backup.exists():
                try:
                    if target.exists():
                        target.unlink()
                    backup.replace(target)
                except OSError:
                    logger.exception("Model activation failed and the previous model could not be restored")
            raise

    config.model_path = str(target)
    config.installed_model_sha = config.pending_model_sha
    config.pending_model_path = ""
    config.pending_model_sha = ""
    config.pending_restart = False
    config.save(paths)

    # The backup is only a swap-time rollback. Keeping it forever would leave
    # a second multi-gigabyte model on disk with no user-facing purpose.
    if backup.exists():
        try:
            backup.unlink()
        except OSError:
            logger.warning(
                "The new model was activated, but the previous model backup could not be removed",
                exc_info=True,
            )
    return True

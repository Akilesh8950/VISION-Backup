from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

MAX_CHECKPOINT_BYTES = 25 * 1024 * 1024
MAX_CHECKPOINT_METADATA_BYTES = 1024 * 1024
MAX_CHECKPOINTS = 100
MAX_CHECKPOINT_TOTAL_BYTES = 250 * 1024 * 1024
_CHECKPOINT_ID_RE = re.compile(r"^[a-f0-9]{32}$")
logger = logging.getLogger(__name__)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_metadata(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(MAX_CHECKPOINT_METADATA_BYTES + 1)
    if len(raw) > MAX_CHECKPOINT_METADATA_BYTES:
        raise ValueError("Checkpoint metadata exceeds 1 MB")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Checkpoint metadata must be an object")
    return parsed


class CheckpointManager:
    def __init__(
        self,
        root: Path,
        *,
        max_checkpoints: int = MAX_CHECKPOINTS,
        max_total_bytes: int = MAX_CHECKPOINT_TOTAL_BYTES,
    ):
        self.root = root
        self.max_checkpoints = max(1, int(max_checkpoints))
        self.max_total_bytes = max(1024, int(max_total_bytes))
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.prune()

    def _entries_by_age(self) -> list[tuple[str, Path, int]]:
        entries: list[tuple[str, float, str, Path, int]] = []
        for directory in self.root.iterdir():
            if not directory.is_dir() or not _CHECKPOINT_ID_RE.fullmatch(directory.name):
                continue
            metadata_file = directory / "metadata.json"
            created = ""
            if metadata_file.is_file():
                try:
                    metadata = _read_metadata(metadata_file)
                    created = str(metadata.get("created_at") or "")
                except (OSError, ValueError, json.JSONDecodeError):
                    created = ""
            try:
                mtime = directory.stat().st_mtime
                size = sum(
                    child.stat().st_size
                    for child in directory.iterdir()
                    if child.is_file()
                )
            except OSError:
                continue
            entries.append((created, mtime, directory.name, directory, size))
        entries.sort(key=lambda item: (item[0], item[1], item[2]))
        return [(name, directory, size) for _created, _mtime, name, directory, size in entries]

    def prune(self, *, keep_id: str = "") -> list[str]:
        """Enforce count/total-size retention, always preserving the newest."""

        removed: list[str] = []
        with self._lock:
            entries = self._entries_by_age()
            total = sum(size for _name, _directory, size in entries)
            # entries are oldest-first.  Never delete the final (newest)
            # entry merely because a caller configured a tiny total cap.
            while len(entries) > 1 and (
                len(entries) > self.max_checkpoints or total > self.max_total_bytes
            ):
                name, directory, size = entries[0]
                if name == keep_id:
                    break
                entries.pop(0)
                try:
                    shutil.rmtree(directory)
                except OSError:
                    logger.warning("Could not prune old checkpoint %s", name, exc_info=True)
                    break
                removed.append(name)
                total -= size
        return removed

    def snapshot(self, path: Path, conversation_id: str = "") -> str:
        with self._lock:
            return self._snapshot_locked(path, conversation_id)

    def _snapshot_locked(self, path: Path, conversation_id: str = "") -> str:
        checkpoint_id = uuid.uuid4().hex
        directory = self.root / checkpoint_id
        directory.mkdir(parents=True)
        try:
            existed = path.is_file()
            digest = ""
            if existed:
                if path.stat().st_size > MAX_CHECKPOINT_BYTES:
                    raise RuntimeError("File is too large to checkpoint safely (25 MB limit)")
                digest_obj = hashlib.sha256()
                copied = 0
                with path.open("rb") as source, (directory / "content.bin").open("wb") as output:
                    while block := source.read(1024 * 1024):
                        copied += len(block)
                        if copied > MAX_CHECKPOINT_BYTES:
                            raise RuntimeError("File is too large to checkpoint safely (25 MB limit)")
                        digest_obj.update(block)
                        output.write(block)
                digest = digest_obj.hexdigest()
            metadata = {
                "id": checkpoint_id,
                "conversation_id": conversation_id,
                "original_path": str(path.resolve()),
                "existed": existed,
                "sha256": digest,
                "created_at": datetime.now(UTC).isoformat(),
            }
            (directory / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            self.prune(keep_id=checkpoint_id)
            return checkpoint_id
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def list(self) -> list[dict]:
        result: list[dict] = []
        skipped = 0
        for metadata_file in self.root.glob("*/metadata.json"):
            try:
                result.append(_read_metadata(metadata_file))
            except Exception:
                skipped += 1
                continue
        if skipped:
            logger.warning("Skipped %d malformed checkpoint metadata file(s)", skipped)
        return sorted(result, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def restore(self, checkpoint_id: str) -> Path:
        if not _CHECKPOINT_ID_RE.fullmatch(str(checkpoint_id)):
            raise ValueError("Invalid checkpoint id")
        directory = (self.root / checkpoint_id).resolve()
        root = self.root.resolve()
        if root not in directory.parents:
            raise ValueError("Invalid checkpoint id")
        metadata = _read_metadata(directory / "metadata.json")
        if not metadata.get("original_path"):
            raise ValueError("Checkpoint metadata is invalid")
        target = Path(str(metadata["original_path"]))
        if metadata.get("existed"):
            content = directory / "content.bin"
            expected = str(metadata.get("sha256") or "")
            if not content.is_file() or (expected and _hash_file(content) != expected):
                raise RuntimeError("Checkpoint content is missing or corrupt")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(content, target)
        elif target.exists():
            target.unlink()
        return target

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path


class CheckpointManager:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def snapshot(self, path: Path, conversation_id: str = "") -> str:
        checkpoint_id = uuid.uuid4().hex
        directory = self.root / checkpoint_id
        directory.mkdir(parents=True)
        existed = path.is_file()
        digest = ""
        if existed:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            shutil.copyfile(path, directory / "content.bin")
        metadata = {
            "id": checkpoint_id,
            "conversation_id": conversation_id,
            "original_path": str(path.resolve()),
            "existed": existed,
            "sha256": digest,
            "created_at": datetime.now(UTC).isoformat(),
        }
        (directory / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return checkpoint_id

    def list(self) -> list[dict]:
        result = []
        for metadata_file in sorted(self.root.glob("*/metadata.json"), reverse=True):
            try:
                result.append(json.loads(metadata_file.read_text(encoding="utf-8")))
            except Exception:
                continue
        return result

    def restore(self, checkpoint_id: str) -> Path:
        directory = self.root / checkpoint_id
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        target = Path(metadata["original_path"])
        if metadata.get("existed"):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(directory / "content.bin", target)
        elif target.exists():
            target.unlink()
        return target

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from ..constants import DATASET_REPO_ID
from .needs_log import NeedsLog

REMOTE_PATH = "telemetry/needs.jsonl"


def _default_hf_api(token: str):
    from huggingface_hub import HfApi  # type: ignore

    return HfApi(token=token)


def _read_remote_lines(api: Any) -> list[str]:
    try:
        local = api.hf_hub_download(
            repo_id=DATASET_REPO_ID,
            repo_type="dataset",
            filename=REMOTE_PATH,
        )
        return Path(local).read_text(encoding="utf-8").splitlines()
    except Exception:
        return []


def upload_needs_log(
    needs_log: NeedsLog,
    token: str,
    *,
    api_factory=_default_hf_api,
    retries: int = 3,
    sleep_seconds: float = 0.5,
) -> bool:
    """Append-merge local rows after existing remote rows; clear only on success."""
    if not token:
        return False
    rows = [json.dumps(row, ensure_ascii=False) for row in needs_log.read_all(flush_first=True)]
    if not rows:
        return True
    for attempt in range(max(1, retries)):
        try:
            api = api_factory(token)
            remote = _read_remote_lines(api)
            merged = "\n".join(remote + rows) + "\n"
            api.upload_file(
                path_or_fileobj=merged.encode("utf-8"),
                path_in_repo=REMOTE_PATH,
                repo_id=DATASET_REPO_ID,
                repo_type="dataset",
                commit_message="Append VISION feedback telemetry",
            )
            needs_log.clear()
            return True
        except Exception:
            if attempt + 1 < retries:
                time.sleep(sleep_seconds * (attempt + 1))
    return False


def upload_in_background(needs_log: NeedsLog, token: str) -> threading.Thread:
    thread = threading.Thread(
        target=upload_needs_log,
        args=(needs_log, token),
        daemon=True,
        name="vision-telemetry-upload",
    )
    thread.start()
    return thread

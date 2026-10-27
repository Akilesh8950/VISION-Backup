from __future__ import annotations

import concurrent.futures
import json
import threading
import time
import uuid
from datetime import UTC, datetime

from ..constants import DATASET_REPO_ID
from .needs_log import NeedsLog

# Telemetry uploads land as immutable, timestamped shards in the dataset
# inbox.  The training job consumes inbox shards; the app never reads,
# merges, or overwrites telemetry already uploaded (including by other
# devices), which makes retries and multi-device uploads race-free.
REMOTE_INBOX_DIR = "telemetry/inbox"
REMOTE_REPORT_PATH = "telemetry/report.json"


def _default_hf_api(token: str):
    from huggingface_hub import HfApi  # type: ignore

    return HfApi(token=token)


def _shard_path() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{REMOTE_INBOX_DIR}/{stamp}-{uuid.uuid4().hex[:8]}.jsonl"


def _record_status(needs_log: NeedsLog, state: str, *, message: str, uploaded: int = 0) -> None:
    try:
        needs_log.record_upload_status(state, message=message, uploaded_events=uploaded)
    except Exception:
        # Status persistence is informational; never let it turn a confirmed
        # upload into a failure or make an offline queue noisy.
        pass


def _upload_needs_log_attempts(
    needs_log: NeedsLog,
    token: str,
    *,
    api_factory=_default_hf_api,
    retries: int = 3,
    sleep_seconds: float = 0.5,
) -> bool:
    if not token:
        _record_status(
            needs_log,
            "skipped_no_token",
            message="No Hugging Face token is stored; telemetry remains queued locally.",
        )
        return False

    with needs_log.upload_lock:
        row_objects = needs_log.read_all(flush_first=True)
        rows = [json.dumps(row, ensure_ascii=False) for row in row_objects]
        if not rows:
            _record_status(needs_log, "success", message="No pending telemetry to upload.")
            return True

        # One shard path per snapshot, fixed before the retry loop: if an
        # attempt fails ambiguously after the server accepted the file,
        # the retry overwrites the identical shard instead of creating a
        # duplicate.
        shard_path = _shard_path()
        shard_bytes = ("\n".join(rows) + "\n").encode("utf-8")
        report_bytes = (
            json.dumps(needs_log.report(flush_first=False), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        attempts = max(1, int(retries))

        for attempt in range(attempts):
            try:
                api = api_factory(token)
                api.upload_file(
                    path_or_fileobj=shard_bytes,
                    path_in_repo=shard_path,
                    repo_id=DATASET_REPO_ID,
                    repo_type="dataset",
                    commit_message="Add VISION feedback telemetry shard",
                )
                # The aggregate report is regenerable, so its failure must
                # not fail (and re-queue) a shard that already landed.
                try:
                    api.upload_file(
                        path_or_fileobj=report_bytes,
                        path_in_repo=REMOTE_REPORT_PATH,
                        repo_id=DATASET_REPO_ID,
                        repo_type="dataset",
                        commit_message="Update VISION feedback telemetry report",
                    )
                except Exception:
                    pass

                needs_log.acknowledge_uploaded(rows)
                _record_status(
                    needs_log,
                    "success",
                    message="Telemetry uploaded successfully.",
                    uploaded=len(rows),
                )
                return True
            except Exception:
                if attempt + 1 < attempts:
                    delay = min(30.0, max(0.0, float(sleep_seconds)) * (2**attempt))
                    if delay:
                        time.sleep(delay)

        _record_status(
            needs_log,
            "queued_offline",
            message="Telemetry upload did not complete; events remain queued for a later launch.",
        )
        return False


def upload_needs_log(
    needs_log: NeedsLog,
    token: str,
    *,
    api_factory=_default_hf_api,
    retries: int = 3,
    sleep_seconds: float = 0.5,
    timeout_seconds: float | None = 60.0,
) -> bool:
    """Upload pending rows as one timestamped shard in telemetry/inbox/.

    Only the exact snapshot that reached the dataset is acknowledged.
    Rows added while the network request is in flight remain pending.
    Nothing remote is read or modified besides the aggregate report, so
    shards from different devices can never overwrite each other.  The
    60-second default bounds the caller even though the underlying
    Hugging Face client does not expose a single timeout argument.
    """

    if timeout_seconds is None or timeout_seconds <= 0:
        return _upload_needs_log_attempts(
            needs_log,
            token,
            api_factory=api_factory,
            retries=retries,
            sleep_seconds=sleep_seconds,
        )

    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="vision-telemetry-upload",
    )
    future = executor.submit(
        _upload_needs_log_attempts,
        needs_log,
        token,
        api_factory=api_factory,
        retries=retries,
        sleep_seconds=sleep_seconds,
    )
    try:
        return bool(future.result(timeout=float(timeout_seconds)))
    except concurrent.futures.TimeoutError:
        _record_status(
            needs_log,
            "queued_offline",
            message="Telemetry upload timed out; events remain queued locally.",
        )
        return False
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def upload_in_background(needs_log: NeedsLog, token: str) -> threading.Thread:
    thread = threading.Thread(
        target=upload_needs_log,
        args=(needs_log, token),
        daemon=True,
        name="vision-telemetry-upload",
    )
    thread.start()
    return thread

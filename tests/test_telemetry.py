import json
import threading
import time
from pathlib import Path

from vision_app.sync_service import SyncService
from vision_app.telemetry.needs_log import FIELDS, NeedsLog
from vision_app.telemetry.telemetry import REPORT_FIELDS, TelemetryEvent
from vision_app.telemetry.uploader import REMOTE_INBOX_DIR, REMOTE_REPORT_PATH, upload_needs_log


def test_needs_log_schema_flush_redaction_and_report(tmp_path):
    path = tmp_path / "telemetry/needs.jsonl"
    log = NeedsLog(path, flush_every=2, session_id="session-test", model_sha_provider=lambda: "model-sha-test")
    token = "hf_" + "a" * 34
    secret_path = "C:\\Users\\Alice\\Documents\\secret.txt"
    log.add(
        category="general",
        prompt=f"Use {token} on {secret_path}",
        model_output="x" * 3_000,
        signal="user_negative",
        detail={"target": f"Read {secret_path} with {token}"},
    )
    assert not path.exists()
    log.add(category="general", prompt="private topic prompt", model_output="private output", signal="topic_usage", detail={"count": 1})
    log.flush()

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert list(rows[0]) == list(FIELDS)
    event = TelemetryEvent.model_validate(rows[0])
    assert event.session_id == "session-test"
    assert event.app_version
    assert event.model_sha == "model-sha-test"
    assert len(event.model_output) == 2_000
    assert event.detail["target"].startswith("Read <PATH:secret.txt>")
    assert event.detail["prompt_hash"]

    text = path.read_text()
    assert token not in text
    assert "C:\\Users\\Alice" not in text
    assert "<PATH:secret.txt>" in text
    assert rows[1]["prompt"] == ""
    assert rows[1]["model_output"] == ""

    report = json.loads((tmp_path / "telemetry/report.json").read_text())
    assert set(report) == set(REPORT_FIELDS)
    assert report["total_events"] == 2
    assert report["by_signal"] == {"topic_usage": 1, "user_negative": 1}
    assert report["fix_rate"] == 0.5
    log.close()


def test_telemetry_detail_strips_clipboard_and_file_bodies(tmp_path):
    log = NeedsLog(tmp_path / "needs.jsonl", session_id="s")
    log.add(
        category="write_file",
        prompt="write it",
        model_output="failed",
        signal="tool_schema_error",
        detail={
            "tool": "write_file",
            "validation_error": "content: field required",
            "raw_arguments": {
                "path": "C:\\Users\\Bob\\notes.txt",
                "content": "FILE BODY THAT MUST NOT LEAK",
                "text": "CLIPBOARD THAT MUST NOT LEAK",
                "api_key": "hf_" + "b" * 34,
            },
        },
    )
    row = log.read_all()[0]
    raw = row["detail"]["raw_arguments"]
    assert raw["path"] == "<PATH:notes.txt>"
    assert raw["content"].startswith("<redacted:")
    assert raw["text"].startswith("<redacted:")
    assert raw["api_key"] == "***"
    serialized = json.dumps(row)
    assert "FILE BODY" not in serialized
    assert "CLIPBOARD THAT" not in serialized
    assert ("hf_" + "b" * 34) not in serialized
    log.close()


def test_needs_log_cap_drops_topic_usage_before_other_events(tmp_path):
    log = NeedsLog(tmp_path / "needs.jsonl", max_events=3, max_bytes=10_000, session_id="s")
    log.add(category="general", prompt="p", model_output="", signal="topic_usage", detail={"count": 1})
    for index in range(3):
        log.add(
            category="tool",
            prompt=f"prompt {index}",
            model_output="error",
            signal="tool_runtime_error",
            detail={"tool": "tool", "message": str(index)},
        )
    log.flush()
    rows = log.read_all(flush_first=False)
    assert len(rows) == 3
    assert {row["signal"] for row in rows} == {"tool_runtime_error"}

    byte_log = NeedsLog(tmp_path / "byte-needs.jsonl", max_events=100, max_bytes=1_200, session_id="s")
    byte_log.add(category="general", prompt="topic", model_output="", signal="topic_usage", detail={"count": 1})
    byte_log.flush()
    for index in range(4):
        byte_log.add(
            category="tool",
            prompt="p" * 100,
            model_output="e" * 100,
            signal="code_error",
            detail={"tool": "run_python", "exit_code": 1, "message": str(index)},
        )
        byte_log.flush()
    assert (tmp_path / "byte-needs.jsonl").stat().st_size <= 1_200
    assert all(row["signal"] == "code_error" for row in byte_log.read_all(flush_first=False))
    log.close()
    byte_log.close()


class FakeApi:
    def __init__(self):
        self.uploads: dict[str, str] = {}

    def upload_file(self, **kwargs):
        body = kwargs["path_or_fileobj"].decode()
        self.uploads[kwargs["path_in_repo"]] = body
        return {"oid": "fake-commit"}

    def shard_paths(self) -> list[str]:
        return sorted(path for path in self.uploads if path.startswith(REMOTE_INBOX_DIR + "/"))


def test_uploader_clears_only_after_success_and_uploads_report(tmp_path):
    log = NeedsLog(tmp_path / "needs.jsonl", session_id="s")
    log.add(category="c", prompt="p", model_output="o", signal="user_negative", detail={"target": "better"})
    fake = FakeApi()
    assert upload_needs_log(log, "token", api_factory=lambda _t: fake, retries=1, timeout_seconds=5)
    shards = fake.shard_paths()
    assert len(shards) == 1
    # Timestamped shard name: YYYYMMDDTHHMMSSZ-<8 hex>.jsonl in the inbox.
    name = shards[0].rsplit("/", 1)[-1]
    stamp, suffix = name[:-len(".jsonl")].split("-", 1)
    assert len(stamp) == 16 and stamp.endswith("Z") and len(suffix) == 8
    assert "user_negative" in fake.uploads[shards[0]]
    report = json.loads(fake.uploads[REMOTE_REPORT_PATH])
    assert report["total_events"] == 1
    assert log.pending_count() == 0
    assert log.upload_status()["state"] == "success"
    log.close()


def test_uploader_second_upload_with_nothing_pending_creates_no_shard(tmp_path):
    log = NeedsLog(tmp_path / "needs.jsonl", session_id="s")
    log.add(category="c", prompt="same prompt", model_output="o", signal="user_negative", detail={"target": "better"})
    fake = FakeApi()
    assert upload_needs_log(log, "token", api_factory=lambda _t: fake, retries=1, timeout_seconds=5)
    assert len(fake.shard_paths()) == 1

    # Acknowledged rows are gone locally, so a later upload is a no-op:
    # shards are immutable inbox files and are never re-sent or merged.
    assert upload_needs_log(log, "token", api_factory=lambda _t: fake, retries=1, timeout_seconds=5)
    assert len(fake.shard_paths()) == 1
    assert log.pending_count() == 0
    log.close()


def test_uploader_keeps_rows_after_failure_and_without_token(tmp_path):
    log = NeedsLog(tmp_path / "needs.jsonl", session_id="s")
    log.add(category="c", prompt="p", model_output="o", signal="topic_usage", detail={"count": 1})

    def fail(_token):
        raise RuntimeError("offline")

    assert not upload_needs_log(log, "token", api_factory=fail, retries=1, sleep_seconds=0, timeout_seconds=5)
    assert log.pending_count() == 1
    assert log.upload_status()["state"] == "queued_offline"

    assert not upload_needs_log(log, "", api_factory=fail, retries=1, sleep_seconds=0, timeout_seconds=5)
    assert log.pending_count() == 1
    assert log.upload_status()["state"] == "skipped_no_token"
    log.close()


class FakeUpdateService:
    def __init__(self):
        self.calls = 0

    def check_all(self, *, apply_config=False):
        self.calls += 1
        return {"apply_config": apply_config}


class FakeSyncConfig:
    updates_auto_check = True
    telemetry_opt_in = True


def test_sync_service_launch_and_interval_are_quiet_and_nonblocking(tmp_path):
    log = NeedsLog(tmp_path / "needs.jsonl", session_id="s")
    log.add(category="c", prompt="p", model_output="o", signal="user_negative", detail={"target": "fixed"})
    uploads: list[str] = []
    uploaded = threading.Event()

    def fake_upload(_needs, token, **_kwargs):
        uploads.append(token)
        uploaded.set()
        return True

    updates = FakeUpdateService()
    seen_updates = []
    service = SyncService(
        config=FakeSyncConfig(),  # type: ignore[arg-type]
        needs_log=log,
        update_service=updates,  # type: ignore[arg-type]
        token_provider=lambda: "credential-manager-token",
        on_updates=seen_updates.append,
        upload_function=fake_upload,
        interval_seconds=0.05,
    )
    started = time.monotonic()
    service.start()
    assert time.monotonic() - started < 1
    assert uploaded.wait(timeout=5)
    service.stop(join_timeout=0.1)

    assert updates.calls >= 1
    assert seen_updates and seen_updates[0]["apply_config"] is True
    assert uploads and set(uploads) == {"credential-manager-token"}
    assert log.pending_count() == 1  # Fake upload did not acknowledge rows.
    log.close()


def test_privacy_preview_status_and_scheduler_wiring_are_present():
    root = Path(__file__).resolve().parents[1]
    app = (root / "app/frontend/src/App.jsx").read_text(encoding="utf-8")
    bridge = (root / "src/vision_app/web_bridge.py").read_text(encoding="utf-8")
    sync = (root / "src/vision_app/sync_service.py").read_text(encoding="utf-8")

    assert "Preview what will be sent" in app
    assert "Clear local telemetry" in app
    assert "Last upload" in app
    assert "get_telemetry_status" in app and "preview_telemetry" in app
    assert "def get_telemetry_status" in bridge
    assert "def preview_telemetry" in bridge
    assert "self.sync_service.start()" in bridge
    assert "TELEMETRY_SYNC_INTERVAL_SECONDS = 6 * 60 * 60" in sync
    assert "check_all(apply_config=True)" in sync


def test_needs_log_retention_drops_lowest_value_signals_first(tmp_path):
    """Bounded-queue eviction follows training value, corrections last."""
    log = NeedsLog(tmp_path / "needs.jsonl", session_id="s", max_events=3)
    log.add(category="c", prompt="fix me", model_output="bad", signal="user_negative", detail={"target": "better"})
    log.add(category="t", prompt="p", model_output="o", signal="tool_runtime_error", detail={})
    log.add(category="t", prompt="p", model_output="o", signal="tool_schema_error", detail={})
    log.add(category="t", prompt="p", model_output="o", signal="code_error", detail={})

    signals = [row["signal"] for row in log.read_all(flush_first=True)]
    # tool_runtime_error (lowest value present) was dropped; the user
    # correction survives even though it is the oldest row.
    assert signals == ["user_negative", "tool_schema_error", "code_error"]
    log.close()


def test_sync_service_scheduled_tick_rechecks_and_uploads(tmp_path):
    """The 6h tick re-checks updates once and retries the upload queue."""
    log = NeedsLog(tmp_path / "needs.jsonl", session_id="s")
    log.add(category="c", prompt="p", model_output="o", signal="user_negative", detail={"target": "fixed"})
    updates = FakeUpdateService()
    calls: list[str] = []

    def fake_upload(_needs, token, **_kwargs):
        calls.append(token)
        return True

    service = SyncService(
        config=FakeSyncConfig(),  # type: ignore[arg-type]
        needs_log=log,
        update_service=updates,  # type: ignore[arg-type]
        token_provider=lambda: "tok",
        upload_function=fake_upload,
        interval_seconds=60,
    )
    service.scheduled_tick()
    assert updates.calls == 1
    assert calls == ["tok"]
    log.close()

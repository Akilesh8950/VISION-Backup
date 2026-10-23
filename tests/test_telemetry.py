import json

from vision_app.telemetry.needs_log import FIELDS, NeedsLog
from vision_app.telemetry.uploader import upload_needs_log


def test_needs_log_fields_flush_and_redaction(tmp_path):
    path = tmp_path / "telemetry/needs.jsonl"
    log = NeedsLog(path, flush_every=2)
    token = "[SECURITY_DATA]"
    log.add(category="general", prompt=token, model_output="out", signal="user_negative", detail="bad")
    assert not path.exists()
    log.add(category="general", prompt="p", model_output="o", signal="topic_usage", detail="d")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert list(rows[0]) == list(FIELDS)
    assert "hf_123" not in path.read_text()


class FakeApi:
    def __init__(self):
        self.uploaded = None

    def hf_hub_download(self, **_kwargs):
        raise FileNotFoundError("not started")

    def upload_file(self, **kwargs):
        self.uploaded = kwargs["path_or_fileobj"].decode()


def test_uploader_clears_only_after_success(tmp_path):
    log = NeedsLog(tmp_path / "needs.jsonl")
    log.add(category="c", prompt="p", model_output="o", signal="topic_usage", detail="d")
    fake = FakeApi()
    assert upload_needs_log(log, "token", api_factory=lambda _t: fake, retries=1)
    assert "topic_usage" in fake.uploaded
    assert log.pending_count() == 0


def test_uploader_keeps_rows_after_failure(tmp_path):
    log = NeedsLog(tmp_path / "needs.jsonl")
    log.add(category="c", prompt="p", model_output="o", signal="topic_usage", detail="d")

    def fail(_token):
        raise RuntimeError("offline")

    assert not upload_needs_log(log, "token", api_factory=fail, retries=1, sleep_seconds=0)
    assert log.pending_count() == 1

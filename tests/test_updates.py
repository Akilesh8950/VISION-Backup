from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from vision_app import __version__
from vision_app.config import AppConfig
from vision_app.connectivity import ConnectivityMonitor
from vision_app.paths import AppPaths
from vision_app.updates.service import (
    APP_VERSION,
    MODEL_FILE,
    UPDATES_ENABLED,
    SemVer,
    UpdateError,
    UpdateService,
    UpdateValidationError,
    bundled_remote_config,
    compare_semver,
)

ROOT = Path(__file__).resolve().parents[1]


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return stream.getvalue()


class _UpdateHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _send(self, body: bytes, *, status: int = 200, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        self.server.requests.append((path, dict(self.headers)))  # type: ignore[attr-defined]
        if path == "/latest.json":
            self._send(self.server.manifest_body)  # type: ignore[attr-defined]
            return
        if path == "/config.json":
            self._send(self.server.config_body)  # type: ignore[attr-defined]
            return
        if path == "/model":
            self._send(self.server.model_body)  # type: ignore[attr-defined]
            return
        if path == "/VISION.zip":
            body = self.server.zip_body  # type: ignore[attr-defined]
            self.server.zip_requests += 1  # type: ignore[attr-defined]
            mode = self.server.zip_mode  # type: ignore[attr-defined]
            if mode == "wrong" or (mode == "wrong-once" and self.server.zip_requests == 1):  # type: ignore[attr-defined]
                body = b"X" * len(body)
            if mode == "truncate-once" and self.server.zip_requests == 1:  # type: ignore[attr-defined]
                # Simulate the internet dropping mid-download: promise the
                # full body, deliver half of it (and pause so the client
                # flushes it to the .part file), then hang up.
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                # Deliver past the client's 1 MiB read chunk so the first
                # chunk is flushed to the .part file before the drop
                # (httpx only yields full read chunks).
                deliver = min(len(body) - 1, 1_100_000) if len(body) > 1_100_000 else len(body) // 2
                self.wfile.write(body[:deliver])
                self.wfile.flush()
                time.sleep(0.3)
                self.close_connection = True
                self.connection.close()
                return
            range_header = self.headers.get("Range", "")
            if range_header.startswith("bytes="):
                start = int(range_header.removeprefix("bytes=").split("-", 1)[0] or 0)
                if start:
                    chunk = body[start:]
                    self.send_response(206)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(len(chunk)))
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                    self.end_headers()
                    self.wfile.write(chunk)
                    return
            self._send(body, content_type="application/zip")
            return
        self._send(b"not found", status=404, content_type="text/plain")


@pytest.fixture()
def update_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UpdateHandler)
    server.daemon_threads = True
    server.requests = []  # type: ignore[attr-defined]
    server.zip_requests = 0  # type: ignore[attr-defined]
    server.zip_mode = "good"  # type: ignore[attr-defined]
    server.zip_body = _zip_bytes({"VISION.exe": b"fake vision exe", "version.json": b'{"version": "9.9.9"}', "_internal/readme.txt": b"ok"})  # type: ignore[attr-defined]
    server.manifest_body = b"{}"  # type: ignore[attr-defined]
    server.config_body = json.dumps(bundled_remote_config().model_dump(mode="json")).encode()  # type: ignore[attr-defined]
    server.model_body = json.dumps(  # type: ignore[attr-defined]
        {"sha": "remote-model-sha", "siblings": [{"rfilename": MODEL_FILE, "size": 5_629_108_640}]}
    ).encode()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _base(server) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


def _set_manifest(server, *, version="9.9.9", min_launcher="1.0.0", sha256: str | None = None) -> bytes:
    body = server.zip_body  # type: ignore[attr-defined]
    manifest = {
        "version": version,
        "url": f"{_base(server)}/VISION.zip",
        "sha256": sha256 or hashlib.sha256(body).hexdigest(),
        "min_launcher": min_launcher,
        "notes": "Fake local proof release.",
        "channel": "stable",
        "platform": "windows-x64",
        "size_bytes": len(body),
        "released_at": "2026-10-25T00:00:00Z",
    }
    server.manifest_body = json.dumps(manifest).encode()  # type: ignore[attr-defined]
    return body


def _service(tmp_path: Path, server, **kwargs) -> UpdateService:
    kwargs.setdefault("enabled", True)
    kwargs.setdefault("model_smoke_runner", lambda _path: None)
    return UpdateService(
        root=tmp_path / "install",
        manifest_url=f"{_base(server)}/latest.json",
        config_url=f"{_base(server)}/config.json",
        model_api_url=f"{_base(server)}/model",
        **kwargs,
    )


def test_updates_are_enabled_after_local_proof():
    assert UPDATES_ENABLED is True
    assert APP_VERSION == __version__ == "1.0.0"


def test_semver_is_numeric_and_handles_prereleases():
    assert SemVer.parse("1.10.0") > SemVer.parse("1.9.0")
    assert compare_semver("1.10.0", "1.9.0") == 1
    assert SemVer.parse("1.0.0") > SemVer.parse("1.0.0-rc.1")
    assert SemVer.parse("1.0.0-alpha.2") > SemVer.parse("1.0.0-alpha.1")
    assert SemVer.parse("1.0.0+build.9") == SemVer.parse("1.0.0")
    with pytest.raises(ValueError):
        SemVer.parse("1.0")


def test_ui_state_does_not_block_behind_a_long_update_worker(tmp_path):
    service = UpdateService(root=tmp_path / "install", enabled=False)
    held = threading.Event()
    release = threading.Event()

    def hold_lock():
        with service._lock:  # Simulates a long download in a worker thread.
            held.set()
            release.wait(timeout=5)

    worker = threading.Thread(target=hold_lock)
    worker.start()
    try:
        assert held.wait(timeout=5)
        assert service.ui_state()["channels"]["app"]["status"] == "up_to_date"
    finally:
        release.set()
        worker.join(timeout=5)


def test_disabled_service_renders_mock_data_and_downloads_nothing(tmp_path, update_server):
    _set_manifest(update_server)
    service = _service(tmp_path, update_server, enabled=False)

    result = service.check_all()
    status = service.download("9.9.9")

    assert result.app.status == "up_to_date"
    assert status.status == "up_to_date"
    assert update_server.requests == []
    assert not (tmp_path / "install" / "downloads").exists()


def test_offline_check_is_quiet_and_makes_no_requests(tmp_path, update_server):
    _set_manifest(update_server)
    service = _service(tmp_path, update_server, offline=True)

    result = service.check_all()

    assert result.offline is True
    assert result.app.status == "up_to_date"
    assert result.model.status == "up_to_date"
    assert result.config.status == "up_to_date"
    assert update_server.requests == []


def test_malformed_manifest_is_rejected(tmp_path, update_server):
    update_server.manifest_body = json.dumps({"version": "9.9.9"}).encode()  # type: ignore[attr-defined]
    service = _service(tmp_path, update_server)

    with pytest.raises(UpdateValidationError):
        service.fetch_manifest()

    result = service.check_all()
    assert result.app.status == "failed"
    assert result.app.installed_version == "1.0.0"


def test_min_launcher_requires_manual_install(tmp_path, update_server):
    _set_manifest(update_server, min_launcher="99.0.0")
    service = _service(tmp_path, update_server)

    result = service.check_all()

    assert result.app.status == "failed"
    assert "manually" in result.app.error
    with pytest.raises(UpdateError, match="manually"):
        service.download("9.9.9")


def test_local_999_end_to_end_resume_extract_and_pending(tmp_path, update_server):
    zip_body = _set_manifest(update_server)
    service = _service(tmp_path, update_server)
    service.root.mkdir(parents=True)
    current_body = {"version": "1.0.0", "previous": "", "model_sha": "", "last_checked": None}
    service.current_file.write_text(json.dumps(current_body), encoding="utf-8")
    downloads = service.root / "downloads"
    downloads.mkdir()
    (downloads / "9.9.9.zip.part").write_bytes(zip_body[:17])

    checked = service.check_all()
    assert checked.app.status == "update_available"
    assert checked.app.available_version == "9.9.9"

    status = service.download("9.9.9")

    assert status.status == "restart_to_apply"
    assert (service.root / "versions" / "9.9.9" / "VISION.exe").is_file()
    assert (service.root / "versions" / "9.9.9" / "_internal" / "readme.txt").read_bytes() == b"ok"
    pending = json.loads(service.pending_file.read_text(encoding="utf-8"))
    assert pending["version"] == "9.9.9"
    assert pending["previous"] == "1.0.0"
    assert json.loads(service.current_file.read_text(encoding="utf-8")) == current_body
    ranges = [headers.get("Range") for path, headers in update_server.requests if path == "/VISION.zip"]
    assert "bytes=17-" in ranges


def test_sha_mismatch_retries_once_and_can_succeed(tmp_path, update_server):
    _set_manifest(update_server)
    update_server.zip_mode = "wrong-once"  # type: ignore[attr-defined]
    service = _service(tmp_path, update_server)

    status = service.download("9.9.9")

    assert status.status == "restart_to_apply"
    assert update_server.zip_requests == 2


def test_persistent_sha_mismatch_never_stages_pending(tmp_path, update_server):
    _set_manifest(update_server)
    update_server.zip_mode = "wrong"  # type: ignore[attr-defined]
    service = _service(tmp_path, update_server)

    with pytest.raises(UpdateValidationError, match="SHA-256"):
        service.download("9.9.9")

    assert update_server.zip_requests == 2
    assert not service.pending_file.exists()
    assert not (service.root / "versions" / "9.9.9").exists()
    assert not (service.root / "downloads" / "9.9.9.zip").exists()


def test_atomic_extract_rejects_zip_without_vision_exe(tmp_path, update_server):
    update_server.zip_body = _zip_bytes({"readme.txt": b"not an app"})  # type: ignore[attr-defined]
    _set_manifest(update_server)
    service = _service(tmp_path, update_server)

    with pytest.raises(UpdateValidationError, match="VISION.exe"):
        service.download("9.9.9")

    assert not (service.root / "versions" / "9.9.9").exists()
    assert not (service.root / "versions" / "9.9.9.tmp").exists()
    assert not service.pending_file.exists()


def test_remote_config_validation_and_live_apply(tmp_path, update_server):
    _set_manifest(update_server)
    remote = bundled_remote_config()
    remote.version = "9.9.9"
    remote.system_prompt = "You are VISION, a helpful desktop assistant built by SPIRIT ORG. Remote proof prompt."
    remote.tool_schemas[0].description = "Remote proof tool description."
    remote.ui_strings["hero_title"] = "Remote proof title"
    update_server.config_body = json.dumps(remote.model_dump(mode="json")).encode()  # type: ignore[attr-defined]
    applied = []
    service = _service(tmp_path, update_server, on_config_applied=applied.append)

    checked = service.check_all()
    assert checked.config.status == "update_available"
    status = service.download_channel("config")

    assert status.status == "up_to_date"
    assert service.system_prompt.endswith("Remote proof prompt.")
    assert applied and applied[-1].version == "9.9.9"
    assert service.ui_state()["ui_strings"]["hero_title"] == "Remote proof title"


def test_malformed_remote_config_keeps_bundled_fallback(tmp_path, update_server):
    _set_manifest(update_server)
    update_server.config_body = json.dumps(  # type: ignore[attr-defined]
        {"version": "9.9.9", "system_prompt": "short", "tool_schemas": [], "ui_strings": {}}
    ).encode()
    service = _service(tmp_path, update_server)
    original_prompt = service.system_prompt

    result = service.check_all()

    assert result.config.status == "failed"
    assert service.system_prompt == original_prompt


def test_model_download_stages_pending_for_next_launch(tmp_path, update_server, monkeypatch):
    _set_manifest(update_server)
    paths = AppPaths(app_data=tmp_path / "data", workspace=tmp_path / "workspace")
    paths.ensure()
    config = AppConfig.defaults(paths)
    config.installed_model_sha = "installed-model-sha"
    paths.model_file.write_bytes(b"old model")

    def fake_download(paths_arg, **kwargs):
        assert kwargs["pending"] is True
        assert kwargs["revision"] == "remote-model-sha"
        target = paths_arg.models_dir / "pending" / MODEL_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"GGUFnew-model")
        return target

    monkeypatch.setattr("vision_app.updates.service.download_model", fake_download)
    service = _service(tmp_path, update_server, paths=paths, config=config, min_model_bytes=1)

    checked = service.check_all()
    assert checked.model.status == "update_available"
    status = service.download_channel("model")

    assert status.status == "restart_to_apply"
    assert config.pending_restart is True
    assert config.pending_model_sha == "remote-model-sha"
    assert paths.model_file.read_bytes() == b"old model"

    applied = service.apply_pending(allow_model_swap=True)
    assert paths.model_file.read_bytes() == b"GGUFnew-model"
    assert applied.model.installed_version == "remote-model"


def test_service_rollback_immediate_rewrites_current_selection(tmp_path, update_server):
    service = _service(tmp_path, update_server, current_version="1.1.0")
    for version in ("1.1.0", "1.0.0"):
        exe = service.root / "versions" / version / "VISION.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"exe")
    service.state.version = "1.1.0"
    service.state.previous = "1.0.0"

    result = service.rollback(immediate=True)

    assert result.app.installed_version == "1.0.0"
    assert json.loads(service.current_file.read_text(encoding="utf-8"))["version"] == "1.0.0"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("vision_launcher_stub", ROOT / "launcher_stub.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _launcher_install(tmp_path: Path, versions: list[str], current: str, previous: str = "") -> Path:
    root = tmp_path / "launcher"
    for version in versions:
        exe = root / "versions" / version / "VISION.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"exe")
    (root / "current.json").write_text(
        json.dumps({"version": current, "previous": previous, "model_sha": "", "last_checked": None}),
        encoding="utf-8",
    )
    return root


def test_launcher_rejects_traversal_version_in_current_selection(tmp_path):
    launcher = _load_launcher()
    root = _launcher_install(tmp_path, ["1.0.0"], "../../outside", "1.0.0")
    outside = tmp_path / "outside" / "VISION.exe"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"outside")

    assert launcher.initial_selection(root) == ("1.0.0", "")
    invalid_path = launcher.version_exe(root, "../../outside").resolve()
    assert invalid_path != outside.resolve()
    assert (root / "versions").resolve() in invalid_path.parents


def test_launcher_consumes_pending_only_after_child_exit(tmp_path):
    launcher = _load_launcher()
    root = _launcher_install(tmp_path, ["1.0.0", "1.1.0"], "1.0.0")
    launches: list[str] = []

    def runner(executable, args, root_arg):
        launches.append(executable.parent.name)
        if len(launches) == 1:
            (root_arg / "pending.json").write_text(
                json.dumps({"version": "1.1.0", "previous": "1.0.0"}),
                encoding="utf-8",
            )
        return 0, 1.0

    assert launcher.run_launcher(root, runner=runner, notice=lambda *_: None, selftest_runner=lambda *_: True) == 0
    assert launches == ["1.0.0", "1.1.0"]
    assert json.loads((root / "current.json").read_text(encoding="utf-8"))["version"] == "1.1.0"
    assert not (root / "pending.json").exists()


def test_launcher_rollback_after_two_quick_nonzero_exits(tmp_path):
    launcher = _load_launcher()
    root = _launcher_install(tmp_path, ["1.0.0", "1.1.0"], "1.1.0", "1.0.0")
    launches: list[str] = []
    notices: list[str] = []

    def runner(executable, args, root_arg):
        version = executable.parent.name
        launches.append(version)
        return (1, 1.0) if version == "1.1.0" else (0, 1.0)

    code = launcher.run_launcher(root, runner=runner, notice=lambda _root, message: notices.append(message), selftest_runner=lambda *_: True)

    assert code == 0
    assert launches == ["1.1.0", "1.1.0", "1.0.0"]
    current = json.loads((root / "current.json").read_text(encoding="utf-8"))
    assert current["version"] == "1.0.0"
    assert current["previous"] == "1.1.0"
    assert len(notices) == 1


def test_launcher_prunes_to_current_and_previous_after_success(tmp_path):
    launcher = _load_launcher()
    root = _launcher_install(tmp_path, ["0.9.0", "1.0.0", "1.1.0"], "1.1.0", "1.0.0")

    assert launcher.run_launcher(root, runner=lambda *_: (0, 1.0), notice=lambda *_: None, selftest_runner=lambda *_: True) == 0

    assert not (root / "versions" / "0.9.0").exists()
    assert (root / "versions" / "1.1.0").exists()
    assert (root / "versions" / "1.0.0").exists()


def test_release_script_and_updates_ui_contract():
    release = (ROOT / "scripts" / "release.ps1").read_text(encoding="utf-8")
    assert "--onedir" in release
    assert "Get-FileHash -Algorithm SHA256" in release
    assert "gh release upload" in release
    assert "latest-dev.json" in release

    app = (ROOT / "app" / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    for expected in (
        "<h3>Updates</h3>",
        "'updates', 'download', 'Updates'",
        "check_updates",
        "download_update",
        "restart_to_apply_update",
        "Automatically check for updates",
        "Changelog",
    ):
        assert expected in app


# ---------------------------------------------------------------------------
# Hardened update gates
# ---------------------------------------------------------------------------

def _rewrite_manifest(server, **overrides):
    manifest = json.loads(server.manifest_body)  # type: ignore[attr-defined]
    manifest.update(overrides)
    server.manifest_body = json.dumps(manifest).encode()  # type: ignore[attr-defined]


def test_channel_mismatch_is_refused_with_one_clear_line(tmp_path, update_server):
    _set_manifest(update_server)
    _rewrite_manifest(update_server, channel="dev")
    service = _service(tmp_path, update_server)

    result = service.check_all()
    assert result.app.status == "failed"
    assert "dev channel" in result.app.error
    assert result.app.available_version == ""


def test_platform_mismatch_is_refused_with_one_clear_line(tmp_path, update_server):
    _set_manifest(update_server)
    _rewrite_manifest(update_server, platform="linux-x64")
    service = _service(tmp_path, update_server)

    result = service.check_all()
    assert result.app.status == "failed"
    assert "linux-x64" in result.app.error


def test_manifest_accepts_legacy_size_field():
    from vision_app.updates.service import AppManifest

    manifest = AppManifest.model_validate(
        {
            "version": "1.2.3",
            "url": "https://example.com/VISION.zip",
            "sha256": "a" * 64,
            "size": 1234,
        }
    )
    assert manifest.expected_size == 1234
    assert manifest.channel == "stable"
    assert manifest.platform == "windows-x64"


@pytest.mark.parametrize(
    "payload",
    [
        {"version": "1.2.3", "url": "https://example.com/VISION.zip", "sha256": "a" * 64},
        {
            "version": "1.2.3",
            "url": "https://example.com/VISION.zip",
            "sha256": "a" * 64,
            "size_bytes": 0,
        },
        {
            "version": "1.2.3",
            "url": "https://example.com/VISION.zip",
            "sha256": "a" * 64,
            "size_bytes": 4 * 1024**3 + 1,
        },
    ],
)
def test_manifest_requires_bounded_positive_size(payload):
    from pydantic import ValidationError

    from vision_app.updates.service import AppManifest

    with pytest.raises(ValidationError):
        AppManifest.model_validate(payload)


def test_size_mismatch_aborts_without_staging(tmp_path, update_server):
    _set_manifest(update_server)
    _rewrite_manifest(update_server, size_bytes=10)
    service = _service(tmp_path, update_server)

    with pytest.raises(UpdateError):
        service.download("9.9.9")
    assert not service.pending_file.exists()
    assert not (service.root / "versions" / "9.9.9").exists()


def test_zip_slip_entries_are_rejected_before_extract(tmp_path, update_server):
    update_server.zip_body = _zip_bytes(  # type: ignore[attr-defined]
        {
            "VISION.exe": b"fake",
            "version.json": b'{"version": "9.9.9"}',
            "../evil.txt": b"escape",
        }
    )
    _set_manifest(update_server)
    service = _service(tmp_path, update_server)

    with pytest.raises(UpdateValidationError, match="unsafe"):
        service.download("9.9.9")
    assert not (service.root / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()
    assert not (service.root / "versions" / "9.9.9").exists()


def test_version_json_mismatch_is_rejected(tmp_path, update_server):
    update_server.zip_body = _zip_bytes(  # type: ignore[attr-defined]
        {"VISION.exe": b"fake", "version.json": b'{"version": "8.8.8"}'}
    )
    _set_manifest(update_server)
    service = _service(tmp_path, update_server)

    with pytest.raises(UpdateValidationError, match="does not match"):
        service.download("9.9.9")
    assert not service.pending_file.exists()


def test_missing_version_json_is_rejected(tmp_path, update_server):
    update_server.zip_body = _zip_bytes({"VISION.exe": b"fake"})  # type: ignore[attr-defined]
    _set_manifest(update_server)
    service = _service(tmp_path, update_server)

    with pytest.raises(UpdateValidationError, match="version.json"):
        service.download("9.9.9")


def test_selftest_writes_result_and_passes(tmp_path):
    from vision_app.selftest import run_selftest

    output = tmp_path / "result.json"
    assert run_selftest(output) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert {check["name"] for check in payload["checks"]} >= {"imports", "tool_schemas"}


def test_launcher_selftest_failure_keeps_previous_and_deletes_new(tmp_path):
    launcher = _load_launcher()
    root = _launcher_install(tmp_path, ["1.0.0", "1.1.0"], "1.0.0")
    launches: list[str] = []
    notices: list[str] = []

    def runner(executable, args, root_arg):
        launches.append(executable.parent.name)
        if len(launches) == 1:
            (root_arg / "pending.json").write_text(
                json.dumps({"version": "1.1.0", "previous": "1.0.0"}),
                encoding="utf-8",
            )
        return 0, 1.0

    code = launcher.run_launcher(
        root,
        runner=runner,
        notice=lambda _root, message: notices.append(message),
        selftest_runner=lambda *_: False,
    )

    assert code == 0
    assert launches == ["1.0.0", "1.0.0"]
    assert not (root / "versions" / "1.1.0").exists()
    assert json.loads((root / "current.json").read_text(encoding="utf-8"))["version"] == "1.0.0"
    assert len(notices) == 1
    state = json.loads((root / "launcher-state.json").read_text(encoding="utf-8"))
    assert state["last_selftest_failure"]["version"] == "1.1.0"


def test_validate_gguf_magic_and_size(tmp_path):
    from vision_app.updates.model_verify import validate_gguf

    good = tmp_path / "good.gguf"
    good.write_bytes(b"GGUF" + b"x" * 100)
    bad = tmp_path / "bad.gguf"
    bad.write_bytes(b"NOPE" + b"x" * 100)
    assert validate_gguf(good, min_bytes=10) is True
    assert validate_gguf(bad, min_bytes=10) is False
    assert validate_gguf(good, min_bytes=10_000) is False


class _FakeModelHandler(BaseHTTPRequestHandler):
    identity_ok = True

    def log_message(self, *_args):  # keep pytest output quiet
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if payload.get("tools"):
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Chennai"}'},
                    }
                ],
            }
        else:
            text = "I am VISION, built by SPIRIT ORG." if type(self).identity_ok else "I am a generic model."
            message = {"role": "assistant", "content": text}
        body = json.dumps({"choices": [{"message": message}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_model_smoke_against_fake_server():
    from vision_app.updates.model_verify import run_smoke_against

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _FakeModelHandler.identity_ok = True
        assert run_smoke_against(base, timeout=10).ok is True
        _FakeModelHandler.identity_ok = False
        failed = run_smoke_against(base, timeout=10)
        assert failed.ok is False
        assert "identity" in failed.reason
    finally:
        _FakeModelHandler.identity_ok = True
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_model_smoke_failure_discards_pending_and_keeps_current(tmp_path, update_server, monkeypatch):
    from vision_app.updates.model_verify import ModelSmokeResult

    _set_manifest(update_server)
    paths = AppPaths(app_data=tmp_path / "data", workspace=tmp_path / "workspace")
    paths.ensure()
    config = AppConfig.defaults(paths)
    config.installed_model_sha = "installed-model-sha"
    paths.model_file.write_bytes(b"GGUFold-model")

    def fake_download(paths_arg, **_kwargs):
        target = paths_arg.models_dir / "pending" / MODEL_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"GGUFnew-model")
        return target

    monkeypatch.setattr("vision_app.updates.service.download_model", fake_download)
    service = _service(
        tmp_path,
        update_server,
        paths=paths,
        config=config,
        min_model_bytes=1,
        model_smoke_runner=lambda _p: ModelSmokeResult(False, "identity check failed"),
    )

    with pytest.raises(UpdateValidationError, match="failed verification"):
        service.download_channel("model")

    assert paths.model_file.read_bytes() == b"GGUFold-model"
    assert config.pending_restart is False
    assert not (paths.models_dir / "pending" / MODEL_FILE).exists()


def test_update_channel_authorities_are_split_by_host() -> None:
    """GitHub serves app updates only; the model channel is Hugging Face.

    Owner clarification: the ``Akilesh8950/VISION`` GitHub repository is
    the app-update authority (release manifests + ZIPs, plus the live
    remote config). It must never become a model host — model checks
    and downloads stay on Hugging Face under ``MODEL_REPO_ID``.
    """
    from urllib.parse import urlparse

    from vision_app.constants import MODEL_REPO_ID
    from vision_app.updates import service

    for url in (
        service.MANIFEST_URL,
        service.MANIFEST_URL_DEV,
        service.CONFIG_URL,
        service.CONFIG_URL_DEV,
    ):
        parsed = urlparse(url)
        assert parsed.hostname == "raw.githubusercontent.com"
        assert parsed.path.startswith("/Akilesh8950/VISION/")

    # The GGUF repo is the only model repo the app may ever poll.  The
    # base/LoRA repo (same name without the -GGUF suffix) gets daily
    # adapter commits from training; those are invisible to the app.
    assert MODEL_REPO_ID == "SpiritKing/VISION-Ornith-1.5-9B-GGUF"
    model = urlparse(service.MODEL_API_URL)
    assert model.hostname == "huggingface.co"
    assert MODEL_REPO_ID in model.path
    assert "github" not in service.MODEL_API_URL


def test_ui_state_exposes_installed_model_sha_and_change_time(tmp_path: Path) -> None:
    """Settings -> Model honesty data: installed sha + when it changed."""
    service = UpdateService(root=tmp_path / "install", enabled=False)
    state = service.ui_state()
    assert state["installed_model_sha"] == ""
    assert state["model_updated_at"] is None

    service.state.model_sha = "a" * 40
    service.state.model_updated_at = "2026-10-25T00:00:00+00:00"
    state = service.ui_state()
    assert state["installed_model_sha"] == "a" * 40
    assert state["model_updated_at"] == "2026-10-25T00:00:00+00:00"


def test_app_download_survives_mid_download_connection_drop(tmp_path, update_server):
    # >1 MiB stored payload so a full client read chunk lands in .part
    # before the simulated drop.
    big_stream = io.BytesIO()
    with zipfile.ZipFile(big_stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("VISION.exe", b"VISION-UPDATE-PAYLOAD-" * 70_000)
        archive.writestr("version.json", b'{"version": "9.9.9"}')
    update_server.zip_body = big_stream.getvalue()  # type: ignore[attr-defined]
    _set_manifest(update_server)
    update_server.zip_mode = "truncate-once"  # type: ignore[attr-defined]
    service = _service(
        tmp_path,
        update_server,
        connectivity=ConnectivityMonitor(probe=lambda: True),
        network_retry_delay_seconds=0,
    )

    status = service.download("9.9.9")

    assert status.status == "restart_to_apply"
    assert update_server.zip_requests == 2  # truncated attempt + resumed attempt
    ranges = [headers.get("Range") for path, headers in update_server.requests if path == "/VISION.zip"]
    resumed = [r for r in ranges if r and r.startswith("bytes=") and r != "bytes=0-"]
    assert resumed, ranges  # resumed from the partial .part, not restarted
    assert (service.root / "versions" / "9.9.9" / "VISION.exe").is_file()

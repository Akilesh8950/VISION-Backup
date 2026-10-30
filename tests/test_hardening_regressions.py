from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from vision_app.app import build_services
from vision_app.config import AppConfig
from vision_app.extensions import connectors as connectors_module
from vision_app.extensions import mcp_config as mcp_module
from vision_app.extensions.connectors import ConnectorConfig, ConnectorConfigFile, save_connectors
from vision_app.extensions.mcp_config import McpConfigFile, McpServerConfig, save_mcp_config
from vision_app.paths import AppPaths
from vision_app.python_helper import run_python_script
from vision_app.server import download_llama as download_llama_module
from vision_app.server.download_llama import download_file, safe_extract_zip
from vision_app.subprocess_utils import BoundedProcessResult, run_bounded
from vision_app.tools import schemas, web_reader, windows_tools
from vision_app.updater.downloads import activate_pending_model
from vision_app.web_bridge import VisionWebApi

ROOT = Path(__file__).resolve().parents[1]


class DummyCredentials:
    def __init__(self):
        self.values = {"REMOTE_API_KEY": "remote-secret-value", "HF_TOKEN": "hf-secret-value"}

    def get(self, key):
        return self.values.get(key, "")

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


def make_api(tmp_path):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "workspace")
    services = build_services(paths)
    services.credentials = DummyCredentials()
    api = VisionWebApi(services)
    api._refresh_binary = lambda: None  # avoid a background filesystem probe in unit tests
    return api


def test_resilient_extension_loaders_keep_valid_records(tmp_path):
    mcp_path = tmp_path / "mcp.json"
    mcp_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "servers": [
                    {"id": "good", "display_name": "Good", "transport": "stdio", "enabled": False},
                    {"id": "bad", "display_name": "Bad", "transport": "stdio", "enabled": True},
                    {"id": "good", "display_name": "Duplicate", "transport": "stdio", "enabled": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded, errors = mcp_module.load_mcp_config_resilient(mcp_path)
    assert [server.id for server in loaded.servers] == ["good"]
    assert "bad" in errors and "good" in errors

    connector_path = tmp_path / "connectors.json"
    connector_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "connectors": [
                    {"id": "remote", "display_name": "Remote", "transport": "streamable_http", "enabled": False},
                    {"id": "broken", "display_name": "Broken", "transport": "streamable_http", "enabled": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded_connectors, connector_errors = connectors_module.load_connectors_resilient(connector_path)
    assert [connector.id for connector in loaded_connectors.connectors] == ["remote"]
    assert "broken" in connector_errors


def test_bridge_never_echoes_saved_remote_address_and_blank_keeps_it(tmp_path):
    api = make_api(tmp_path)
    api.config.remote_base_url = "https://private.example/v1?account=secret"

    payload = api._config_payload()
    encoded = json.dumps(payload)
    assert payload["remote_base_url"] == ""
    assert payload["remote_base_url_configured"] is True
    assert "private.example" not in encoded

    api.save_settings({"remote_base_url": ""})
    assert api.config.remote_base_url == "https://private.example/v1?account=secret"

    api.save_settings({"remote_base_url": "", "clear_remote_base_url": True})
    assert api.config.remote_base_url == ""


def test_bridge_extension_and_project_summaries_do_not_leak_private_details(tmp_path):
    api = make_api(tmp_path)
    server = McpServerConfig(
        id="private-tools",
        display_name="Private tools",
        transport="streamable_http",
        enabled=False,
        url="https://tools.example/private/path?tenant=hidden",
        env_plain={"TOKEN": "plain-secret-value"},
        headers_plain={"Authorization": "header-secret-value"},
        toolset_sha256="a" * 64,
    )
    save_mcp_config(api.paths.mcp_config, McpConfigFile(servers=[server]))
    connector = ConnectorConfig(
        id="connector-one",
        display_name="Connector one",
        transport="streamable_http",
        enabled=False,
        url="https://connector.example/secret/path",
        headers_plain={"X-Key": "connector-secret"},
    )
    save_connectors(api.paths.connectors_config, ConnectorConfigFile(connectors=[connector]))
    api.projects.create_project("Private project", tmp_path / "secret-root", "DO NOT SEND THESE INSTRUCTIONS")

    extensions = api._extensions_payload()
    encoded = json.dumps({"extensions": extensions, "projects": api._projects_payload()})
    assert "plain-secret-value" not in encoded
    assert "header-secret-value" not in encoded
    assert "connector-secret" not in encoded
    assert "/private/path" not in encoded
    assert "DO NOT SEND THESE INSTRUCTIONS" not in encoded
    assert "secret-root" not in encoded
    assert "dynamic_tools" not in extensions
    assert extensions["servers"][0]["trusted"] is True
    assert extensions["servers"][0]["destination_label"] == "tools.example"


def test_bridge_attachment_payload_omits_path_and_failed_prepare_keeps_attachment(tmp_path):
    api = make_api(tmp_path)
    state = api.get_state()
    binary = tmp_path / "binary.dat"
    binary.write_bytes(b"hello\x00world")
    api._attachments["bad"] = {
        "id": "bad",
        "name": binary.name,
        "path": str(binary),
        "size": binary.stat().st_size,
    }
    assert "path" not in api._attachments_payload()[0]
    with pytest.raises(RuntimeError, match="UTF-8 text file"):
        api._prepare_turn(
            {"conversation_id": state["current_conversation_id"], "text": "Use it", "attachment_ids": ["bad"]},
            regenerate=False,
        )
    assert "bad" in api._attachments


def test_bridge_server_payload_and_activity_are_sanitized(tmp_path):
    api = make_api(tmp_path)
    api.config.model_path = str(tmp_path / "private" / "model.gguf")
    api._server_binary = tmp_path / "private" / "llama-server.exe"
    payload = api._server_payload()
    assert payload["model_path"] == "model.gguf"
    assert payload["binary"] == "llama-server.exe"
    assert "local_base_url" not in payload
    assert "command" not in payload

    activity = api.get_activity()
    assert "audit_path" not in activity
    assert "feedback_path" not in activity


def test_bridge_open_url_validation_and_export_validation(tmp_path, monkeypatch):
    api = make_api(tmp_path)
    assert api.open_url("https://user:pass@example.com/path")["ok"] is False
    assert api.open_url("file:///C:/Windows/win.ini")["ok"] is False
    assert api.open_url("https://example.com:99999/path")["ok"] is False

    opened = []
    monkeypatch.setattr("vision_app.web_bridge.webbrowser.open", lambda url: opened.append(url) or True)
    assert api.open_url("https://example.com/path")["ok"] is True
    assert opened == ["https://example.com/path"]

    class Window:
        def create_file_dialog(self, *args, **kwargs):
            raise AssertionError("invalid exports must be rejected before opening a save dialog")

    api.window = Window()
    assert api.save_export({"filename": "x.bin", "encoding": "base64", "data": "not base64!"})["ok"] is False

    executable = tmp_path / "payload.exe"
    executable.write_bytes(b"MZ")
    assert api.open_source_path(str(executable))["ok"] is False


def test_qt_workers_always_emit_a_terminal_signal():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication

    from vision_app.chat.worker import ChatWorker
    from vision_app.ui.workers import FunctionWorker

    _app = QCoreApplication.instance() or QCoreApplication([])

    failures = []
    worker = FunctionWorker(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    worker.failed.connect(failures.append)
    worker.run()
    assert failures == ["boom"]

    slot_failures = []
    successful = FunctionWorker(lambda: 42)
    successful.finished.connect(lambda _value: (_ for _ in ()).throw(RuntimeError("slot broke")))
    successful.failed.connect(slot_failures.append)
    successful.run()
    assert slot_failures == ["slot broke"]

    class FakeController:
        callbacks = None

        async def run_turn(self, **_kwargs):
            raise RuntimeError("controller exploded")

        async def regenerate_last(self, **_kwargs):
            raise RuntimeError("controller exploded")

    finished = []
    chat = ChatWorker(FakeController())  # type: ignore[arg-type]
    chat.finished.connect(finished.append)
    chat.run()  # no request still gets a terminal result
    assert len(finished) == 1 and finished[-1].error

    assert chat.submit(conversation_id="c1", user_text="hi") is True
    chat.run()
    assert len(finished) == 2
    assert finished[-1].conversation_id == "c1"
    assert finished[-1].error == "controller exploded"
    assert chat.is_running is False

    # An aborted window close must be able to resume the same worker/thread.
    assert chat.submit(conversation_id="c2", user_text="queued") is True
    chat.prepare_shutdown()
    assert chat.submit(conversation_id="c3", user_text="rejected") is False
    chat.run()
    assert len(finished) == 3
    assert finished[-1].conversation_id == "c2"
    assert finished[-1].cancelled is True
    chat.resume()
    assert chat.submit(conversation_id="c4", user_text="again") is True
    chat.run()
    assert len(finished) == 4
    assert finished[-1].conversation_id == "c4"
    assert finished[-1].error == "controller exploded"


def test_web_reader_rejects_any_non_public_dns_answer(monkeypatch):
    def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80)),
        ]

    monkeypatch.setattr(web_reader.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(PermissionError):
        web_reader.validate_public_url("http://example.test/")


def test_web_reader_connects_to_the_validated_address(monkeypatch):
    seen_paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen_paths.append(self.path)
            if self.path == "/go":
                self.send_response(302)
                self.send_header("Location", "/final")
                self.end_headers()
                return
            body = b"<html><head><title>Pinned</title></head><body><p>Validated address</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]

        def fake_getaddrinfo(*_args, **_kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

        # The loopback test server stands in for one public address returned
        # by DNS. Rebinding protection is proven by the reader dialing this
        # exact pre-validated tuple for both the request and redirect.
        monkeypatch.setattr(web_reader.socket, "getaddrinfo", fake_getaddrinfo)
        monkeypatch.setattr(web_reader, "_ip_is_public", lambda _address: True)
        result = web_reader.read_url(schemas.ReadUrlArgs(url=f"http://example.test:{port}/go"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert seen_paths == ["/go", "/final"]
    assert result["title"] == "Pinned"
    assert "Validated address" in result["text"]
    assert result["final_url"].endswith("/final")


def test_extension_save_validates_direct_mutations_before_replacing_file(tmp_path):
    path = tmp_path / "mcp.json"
    data = McpConfigFile(
        servers=[
            McpServerConfig(
                id="local-tools",
                display_name="Local tools",
                transport="stdio",
                enabled=False,
                command="",
            )
        ]
    )
    save_mcp_config(path, data)
    original = path.read_bytes()

    # Pydantic does not validate this direct assignment by itself. The save
    # boundary must refuse to persist an enabled stdio server with no command.
    data.servers[0].enabled = True
    with pytest.raises(Exception, match="requires a command"):
        save_mcp_config(path, data)
    assert path.read_bytes() == original


def test_bridge_trust_is_fail_closed_when_a_sibling_record_is_malformed(tmp_path):
    api = make_api(tmp_path)
    api.paths.mcp_config.parent.mkdir(parents=True, exist_ok=True)
    api.paths.mcp_config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "servers": [
                    {"id": "good", "display_name": "Good", "transport": "stdio", "enabled": False},
                    {"id": "bad", "display_name": "Bad", "transport": "stdio", "enabled": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    original = api.paths.mcp_config.read_bytes()

    assert api._find_extension_record("mcp", "good").id == "good"
    result = api.trust_extension("mcp", "good", "a" * 64, ["lookup"])
    assert result["ok"] is False
    assert "needs repair" in result["message"]
    assert api.paths.mcp_config.read_bytes() == original


def test_frozen_python_helper_exit_codes_and_argv_restore(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["vision", "--original"])
    script = tmp_path / "script.py"
    script.write_text(
        "import sys\nprint(sys.argv[0].endswith('script.py'))\nprint('helper-output')\n",
        encoding="utf-8",
    )
    assert run_python_script(script) == 0
    assert "helper-output" in capsys.readouterr().out
    assert sys.argv == ["vision", "--original"]

    script.write_text("raise SystemExit(7)\n", encoding="utf-8")
    assert run_python_script(script) == 7

    script.write_text("raise RuntimeError('helper boom')\n", encoding="utf-8")
    assert run_python_script(script) == 1
    assert "helper boom" in capsys.readouterr().err

    assert run_python_script(tmp_path / "missing.py") == 2
    assert sys.argv == ["vision", "--original"]


def test_llama_archive_extraction_rejects_unsafe_windows_paths(tmp_path, monkeypatch):
    destination = tmp_path / "extract"
    for filename in (
        "../evil.txt",
        "folder/file.txt:stream",
        "folder/CON.txt",
        "folder/name.txt ",
        "folder/name.txt.",
        "folder./name.txt",
        "folder /name.txt",
        "COM1",
        "folder/lpt9.dat",
        "folder/aux",
        r"C:\evil.txt",
        r"folder\..\evil.txt",
        ".",
    ):
        archive = tmp_path / "archive.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(filename, b"nope")
        with pytest.raises(ValueError, match="Unsafe path"):
            safe_extract_zip(archive, destination)

    monkeypatch.setattr(download_llama_module, "MAX_EXTRACTED_BYTES", 3)
    archive = tmp_path / "oversize.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("runtime/llama-server.exe", b"1234")
    with pytest.raises(ValueError, match="expands beyond"):
        safe_extract_zip(archive, destination)

    monkeypatch.setattr(download_llama_module, "MAX_EXTRACTED_BYTES", 100)
    archive = tmp_path / "good.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("runtime/llama-server.exe", b"binary")
    safe_extract_zip(archive, destination)
    assert (destination / "runtime" / "llama-server.exe").read_bytes() == b"binary"


def test_llama_download_rejects_oversize_and_invalid_ranges(tmp_path, monkeypatch):
    class FakeResponse:
        def __init__(self, *, status_code=200, headers=None, chunks=()):
            self.status_code = status_code
            self.headers = headers or {}
            self.chunks = chunks

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self, _size):
            yield from self.chunks

    responses = []
    calls = []

    class FakeHttpx:
        @staticmethod
        def stream(_method, _url, *, headers, **_kwargs):
            calls.append(headers)
            return responses.pop(0)

    monkeypatch.setattr(download_llama_module, "httpx", FakeHttpx)
    monkeypatch.setattr(download_llama_module, "MAX_DOWNLOAD_BYTES", 5)
    destination = tmp_path / "llama.bin"
    part = destination.with_suffix(".bin.part")

    # A declared total over the cap deletes the stale partial file.
    part.write_bytes(b"abc")
    responses.append(FakeResponse(headers={"content-length": "6"}, chunks=(b"123456",)))
    with pytest.raises(RuntimeError, match="exceeds"):
        download_file("https://example.invalid/llama.bin", destination)
    assert not part.exists()
    assert not destination.exists()

    # The streamed byte count is authoritative when no length is supplied.
    responses.append(FakeResponse(chunks=(b"123", b"456")))
    with pytest.raises(RuntimeError, match="exceeds"):
        download_file("https://example.invalid/llama.bin", destination)
    assert not part.exists()
    assert not destination.exists()

    # An invalid Content-Range restarts cleanly instead of appending bytes
    # from an unverified range.
    part.write_bytes(b"ab")
    responses.extend(
        [
            FakeResponse(status_code=206, headers={"content-range": "bytes 2-1/5"}),
            FakeResponse(headers={"content-length": "5"}, chunks=(b"12345",)),
        ]
    )
    assert download_file("https://example.invalid/llama.bin", destination) == destination
    assert destination.read_bytes() == b"12345"
    assert calls[-2] == {"Range": "bytes=2-"}
    assert calls[-1] == {}


def test_bounded_subprocess_keeps_only_requested_head_or_tail_and_times_out():
    noisy = "import sys; sys.stdout.write('a' * 10000 + 'tail'); sys.stderr.write('head' + 'b' * 10000)"
    result = run_bounded(
        [sys.executable, "-c", noisy],
        timeout=5,
        stdout_chars=100,
        stderr_chars=100,
        stdout_mode="tail",
        stderr_mode="head",
    )
    assert result.returncode == 0
    assert len(result.stdout) == 100 and result.stdout.endswith("tail")
    assert len(result.stderr) == 100 and result.stderr.startswith("head")
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True

    with pytest.raises(subprocess.TimeoutExpired) as timeout:
        run_bounded(
            [sys.executable, "-c", "import sys,time; print('started', flush=True); time.sleep(30)"],
            timeout=0.2,
            stdout_chars=100,
        )
    assert "started" in str(timeout.value.output or "")


def test_gpu_status_parses_commas_and_skips_only_malformed_rows(monkeypatch):
    monkeypatch.setattr(windows_tools.shutil, "which", lambda _name: "/fake/nvidia-smi")
    monkeypatch.setattr(
        windows_tools,
        "run_bounded",
        lambda *_args, **_kwargs: BoundedProcessResult(
            returncode=0,
            stdout=(
                "NVIDIA RTX, Special Edition, 24576, 1024, 37\n"
                "Broken GPU, N/A, 1, 2\n"
                "Second GPU, 8192, 0, 0\n"
            ),
        ),
    )

    result = windows_tools._gpu_status()
    assert result["available"] is True
    assert result["devices"] == [
        {
            "name": "NVIDIA RTX, Special Edition",
            "vram_total_mb": 24576.0,
            "vram_used_mb": 1024.0,
            "utilization_percent": 37.0,
        },
        {
            "name": "Second GPU",
            "vram_total_mb": 8192.0,
            "vram_used_mb": 0.0,
            "utilization_percent": 0.0,
        },
    ]


def test_pending_model_activation_replaces_cleans_up_and_recovers_backup(tmp_path):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "workspace")
    paths.ensure()
    target = paths.model_file
    backup = target.with_suffix(".previous.gguf")
    pending = paths.models_dir / "pending" / target.name
    pending.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"old-model")
    pending.write_bytes(b"new-model")

    config = AppConfig.defaults(paths)
    config.pending_model_path = str(pending)
    config.pending_model_sha = "new-sha"
    config.pending_restart = True

    assert activate_pending_model(paths, config) is True
    assert target.read_bytes() == b"new-model"
    assert not pending.exists()
    assert not backup.exists()
    assert config.installed_model_sha == "new-sha"
    assert config.pending_restart is False

    # Simulate an interrupted later swap: current model was moved aside, but
    # the pending file disappeared before activation completed.
    target.replace(backup)
    config.pending_model_path = str(pending)
    config.pending_model_sha = "missing-sha"
    config.pending_restart = True
    assert activate_pending_model(paths, config) is False
    assert target.read_bytes() == b"new-model"
    assert not backup.exists()


def test_frontend_simple_settings_sidebar_and_release_protections_are_present():
    app = (ROOT / "app/frontend/src/App.jsx").read_text(encoding="utf-8")
    sidebar = (ROOT / "app/frontend/src/Sidebar.jsx").read_text(encoding="utf-8")
    library = (ROOT / "app/frontend/src/LibraryPanel.jsx").read_text(encoding="utf-8")
    styles = (ROOT / "app/frontend/src/styles.css").read_text(encoding="utf-8")
    bridge = (ROOT / "app/frontend/src/bridge.js").read_text(encoding="utf-8")
    widgets = (ROOT / "app/frontend/src/widgets.jsx").read_text(encoding="utf-8")
    vite = (ROOT / "app/frontend/vite.config.js").read_text(encoding="utf-8")
    build = (ROOT / "scripts/build.ps1").read_text(encoding="utf-8")

    for settings_entry in (
        "['general', 'spark', 'General', 'Appearance & startup']",
        "['voice', 'speaker', 'Voice', 'Read aloud & Live']",
        "['model', 'server', 'Model', 'Local & backup AI']",
        "['tools', 'puzzle', 'Tools', 'Skills & connections']",
        "['library', 'library', 'Library', 'Files & checkpoints']",
        "['privacy', 'shield', 'Privacy', 'Data & feedback']",
        "['updates', 'download', 'Updates', 'App, model & config']",
        "['about', 'info', 'About', 'Version & credits']",
    ):
        assert settings_entry in app
    assert "SETTINGS_SECTION_GROUPS" in app and "settings-tab-icon" in app
    assert "settings-nav-group-label" in app and "settings-nav-footer" in app
    assert "Vision control center" in app and "settings-tab-copy" in app
    assert "grid-template-columns: clamp(188px, calc(150px + 8dvw), 252px) minmax(0, 1fr)" in styles
    assert "grid-column: 1; grid-row: 1 / -1;" in styles
    # Read-aloud voice picker + JS-driven small-window rail (ARCHITECTURE §17).
    assert 'id="tts-voice-select"' in app and "from './voice.js'" in app
    assert "matchMedia('(max-width: 680px)')" in app and "effectiveCollapsed" in app
    assert "100dvh" in styles and "motion-reduced" in styles
    for label in ("Model & Server", "API & Connections"):
        assert label not in app
    assert "settingsTab === 'extensions'" not in app
    assert "settingsTab === 'connections'" not in app
    assert "storageGet" in app and "storageSet" in app
    assert "timestampMs" in app
    assert "vision.sidebarCollapsed" in app
    assert "vision.projectFilter" in app
    assert "vision.collapsedProjects" in app
    assert "Expand sidebar" in sidebar and "Collapse sidebar" in sidebar
    assert "logo-toggle" in sidebar
    assert "Pinned" in sidebar and "Move to project" in sidebar
    assert "double-click or press F2 to rename" in sidebar
    assert "ChatMenuItem" in sidebar and "Chat actions" in sidebar
    assert "Export as Markdown" in sidebar and "Danger zone" in sidebar
    assert "onContextMenu" in sidebar and "ArrowDown" in sidebar
    assert "aria-expanded={Boolean(menuOpen)}" in sidebar
    assert "virtual-spacer" in sidebar and "ROW_HEIGHTS" in sidebar
    assert "lazy(() => import('./LibraryPanel.jsx'))" in app
    assert "lazy(() => import('./widgets.jsx')" in app
    assert "Used in" in library and "Attach to message" in library
    assert "thumbnail" in library and "virtual-spacer" in library
    assert "--sidebar-w: clamp(260px, calc(193px + 4.65dvw), 300px)" in styles
    assert "--sidebar-c: clamp(44px, calc(18px + 10dvw), 64px)" in styles
    assert "transition: grid-template-columns 260ms" in styles
    assert "transition: transform 200ms" in styles
    assert "Saved address hidden" in app
    assert "earlyByType" in bridge
    assert "formula marker" in widgets
    assert "sourcemap: false" in vite and "minify: true" in vite
    assert "--optimize 1" in build and "--disable-windowed-traceback" in build
    assert not list((ROOT / "app/web").rglob("*.map"))

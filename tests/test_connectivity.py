"""Connectivity monitor, reconnect logic, and download failsafes."""

from __future__ import annotations

import socket
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from vision_app import connectivity
from vision_app.connectivity import (
    ConnectivityMonitor,
    ReconnectWaiter,
    internet_tool,
    is_network_error,
)
from vision_app.paths import AppPaths
from vision_app.server.download_llama import download_file
from vision_app.updater import downloads as downloads_module
from vision_app.updater.downloads import download_model


# ----------------------------------------------------------------------
# Error classification + monitor
# ----------------------------------------------------------------------
def test_network_error_classification() -> None:
    assert is_network_error(ConnectionError("connection reset"))
    assert is_network_error(TimeoutError("timed out"))
    assert is_network_error(socket.gaierror("dns failed"))
    assert is_network_error(httpx.ConnectError("refused"))
    assert not is_network_error(ValueError("schema invalid"))
    assert not is_network_error(RuntimeError("404 Entry Not Found"))

    class RepoNotFoundError(Exception):
        pass

    assert not is_network_error(RepoNotFoundError("gone"))


def test_monitor_caches_and_waits_for_reconnect() -> None:
    results = iter([False, False, True])
    monitor = ConnectivityMonitor(probe=lambda: next(results, True), fresh_seconds=60)
    assert monitor.cached_online() is None
    assert monitor.wait_until_online(timeout=5, poll_seconds=0.01) is True
    assert monitor.cached_online() is True


def test_waiter_online_failure_budget() -> None:
    monitor = ConnectivityMonitor(probe=lambda: True)
    waiter = ReconnectWaiter(
        monitor, max_online_attempts=3, base_delay_seconds=0, sleep=lambda _s: None
    )
    assert waiter.wait_after_failure() is True
    assert waiter.wait_after_failure() is True
    assert waiter.wait_after_failure() is False  # budget exhausted: give up loudly


def test_waiter_offline_waits_then_resumes() -> None:
    results = iter([False, True])
    monitor = ConnectivityMonitor(probe=lambda: next(results, True))
    waiter = ReconnectWaiter(monitor, poll_seconds=0.01)
    events: list[str] = []
    assert waiter.wait_after_failure(
        on_waiting=lambda: events.append("waiting"),
        on_resumed=lambda: events.append("resumed"),
    ) is True
    assert events == ["waiting", "resumed"]


# ----------------------------------------------------------------------
# internet_tool guard
# ----------------------------------------------------------------------
def test_internet_tool_fast_fails_when_known_offline(monkeypatch) -> None:
    monitor = ConnectivityMonitor(probe=lambda: False)
    monitor.note_failure()
    monkeypatch.setattr(connectivity, "_default_monitor", monitor)

    @internet_tool
    def tool() -> str:
        raise AssertionError("must not run while offline")

    with pytest.raises(RuntimeError, match="No internet"):
        tool()


def test_internet_tool_learns_from_connection_failure(monkeypatch) -> None:
    monitor = ConnectivityMonitor(probe=lambda: True)
    monkeypatch.setattr(connectivity, "_default_monitor", monitor)

    @internet_tool
    def tool() -> str:
        raise ConnectionError("connection aborted")

    with pytest.raises(RuntimeError, match="No internet"):
        tool()
    assert monitor.cached_online() is False


# ----------------------------------------------------------------------
# Model download: survives connection drops via hf resume retries
# ----------------------------------------------------------------------
def _fake_hf_module(monkeypatch, fn) -> None:
    module = types.ModuleType("huggingface_hub")
    module.hf_hub_download = fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)


def test_model_download_resumes_after_connection_drops(tmp_path, monkeypatch) -> None:
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    paths.ensure()
    monkeypatch.setattr(downloads_module, "ensure_disk_space", lambda *_a, **_k: 0)
    calls = {"count": 0}

    def flaky_download(*, repo_id, filename, revision, local_dir, token):
        calls["count"] += 1
        if calls["count"] <= 2:
            raise ConnectionError("connection reset by peer")
        target = Path(local_dir) / filename
        target.write_bytes(b"GGUFdata")
        return str(target)

    _fake_hf_module(monkeypatch, flaky_download)
    probe_results = iter([False, True, False, True])
    monitor = ConnectivityMonitor(probe=lambda: next(probe_results, True))
    monitor._fresh_seconds = 0.0  # every is_online() really probes in this test
    messages: list[str] = []

    result = download_model(
        paths, expected_size=8, monitor=monitor, progress=messages.append
    )

    assert result.read_bytes() == b"GGUFdata"
    assert calls["count"] == 3
    assert any("connection lost" in m.lower() for m in messages)
    assert any("resuming" in m.lower() for m in messages)


def test_model_download_non_network_error_fails_fast(tmp_path, monkeypatch) -> None:
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    paths.ensure()
    monkeypatch.setattr(downloads_module, "ensure_disk_space", lambda *_a, **_k: 0)
    calls = {"count": 0}

    def bad_download(**_kwargs):
        calls["count"] += 1
        raise ValueError("invalid repo id")

    _fake_hf_module(monkeypatch, bad_download)
    monitor = ConnectivityMonitor(probe=lambda: True)
    with pytest.raises(ValueError, match="invalid repo"):
        download_model(paths, expected_size=8, monitor=monitor)
    assert calls["count"] == 1


# ----------------------------------------------------------------------
# llama.cpp runtime download: Range resume after a mid-stream drop
# ----------------------------------------------------------------------
class _FlakyHandler(BaseHTTPRequestHandler):
    body = bytes(range(256)) * 6000  # ~1.5 MB
    requests_seen: ClassVar[list[str | None]] = []

    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        type(self).requests_seen.append(self.headers.get("Range"))
        body = type(self).body
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            start = int(range_header.removeprefix("bytes=").split("-", 1)[0] or 0)
            chunk = body[start:]
            self.send_response(206)
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
            self.end_headers()
            self.wfile.write(chunk)
            return
        # First response: deliver part of the body, then drop the line.
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body[:400_000])
        self.wfile.flush()
        import time as _time

        _time.sleep(0.2)
        self.close_connection = True
        self.connection.close()


def test_llama_download_resumes_after_mid_stream_drop(tmp_path) -> None:
    _FlakyHandler.requests_seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FlakyHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/llama.zip"
        destination = tmp_path / "llama.zip"
        monitor = ConnectivityMonitor(probe=lambda: True)
        result = download_file(url, destination, monitor=monitor)
        assert result.read_bytes() == _FlakyHandler.body
        resumed = [r for r in _FlakyHandler.requests_seen if r and r.startswith("bytes=")]
        assert resumed, _FlakyHandler.requests_seen
    finally:
        server.shutdown()
        server.server_close()


# ----------------------------------------------------------------------
# Model-facing network note
# ----------------------------------------------------------------------
def test_network_note_wording() -> None:
    from vision_app.chat.controller import ChatController

    controller = ChatController.__new__(ChatController)
    controller.connectivity_provider = lambda: False
    note = controller._network_note()
    assert "No internet" in note and "do not call them" in note

    controller.connectivity_provider = lambda: True
    assert "Internet is available" in controller._network_note()

    controller.connectivity_provider = lambda: None
    assert controller._network_note() == ""

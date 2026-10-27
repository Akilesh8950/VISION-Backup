import sys
import time

from vision_app.config import AppConfig
from vision_app.paths import AppPaths
from vision_app.server import discovery
from vision_app.server.discovery import find_llama_server, parse_build_number
from vision_app.server.download_llama import asset_names, detect_variant
from vision_app.server.health import HealthState
from vision_app.server.manager import LlamaServerManager, build_server_command


def test_frozen_command_shape():
    # The balanced profile is the frozen engine invocation: no tuning
    # flags, exactly the long-standing contract.
    config = AppConfig(local_port=8080, context_size=16384, gpu_layers=99, performance_profile="balanced")
    assert build_server_command("llama-server.exe", "model.gguf", config) == [
        "llama-server.exe", "-m", "model.gguf", "--host", "127.0.0.1", "--port", "8080",
        "-c", "16384", "-ngl", "99", "--jinja", "--flash-attn", "on",
    ]


def test_build_and_asset_selection():
    assert parse_build_number("llama b11819") == 11819
    assert parse_build_number("version: 0.7.0-dev (build 11819, commit 0693dbfb9)") == 11819
    assert parse_build_number("version: 11819") == 11819
    assert detect_variant(has_nvidia=True, machine="AMD64") == "cuda-12.4-x64"
    assert detect_variant(has_nvidia=False, machine="AMD64") == "vulkan-x64"
    assert len(asset_names("cuda-12.4-x64")) == 2


def test_find_llama_server_can_skip_unsupported_build(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path / "app", tmp_path / "workspace")
    paths.ensure()
    exe = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    unsupported = paths.bin_dir / "a" / exe
    supported = paths.bin_dir / "b" / exe
    unsupported.parent.mkdir()
    supported.parent.mkdir()
    unsupported.write_bytes(b"old")
    supported.write_bytes(b"new")
    monkeypatch.setattr(
        discovery,
        "detect_build",
        lambda candidate: 11814 if candidate == unsupported else 11819,
    )
    assert find_llama_server(paths, require_supported=True) == supported


def test_health_state_two_failure_rule():
    state = HealthState()
    assert state.record(False) == "failed_once"
    assert state.record(False) == "restart"
    assert state.record(False) == "failover"
    assert state.record(True) == "ok"


def test_manager_captures_child_logs():
    manager = LlamaServerManager()
    manager.start_command([sys.executable, "-u", "-c", "print('hello from fake server')"])
    deadline = time.time() + 5
    while time.time() < deadline and "hello from fake server" not in "\n".join(manager.logs()):
        time.sleep(0.05)
    assert "hello from fake server" in "\n".join(manager.logs())
    manager.stop()

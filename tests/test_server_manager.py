import sys
import time

from vision_app.config import AppConfig
from vision_app.server.discovery import parse_build_number
from vision_app.server.download_llama import asset_names, detect_variant
from vision_app.server.health import HealthState
from vision_app.server.manager import LlamaServerManager, build_server_command


def test_frozen_command_shape():
    config = AppConfig(local_port=8080, context_size=16384, gpu_layers=99)
    assert build_server_command("llama-server.exe", "model.gguf", config) == [
        "llama-server.exe", "-m", "model.gguf", "--host", "127.0.0.1", "--port", "8080",
        "-c", "16384", "-ngl", "99", "--jinja", "--flash-attn", "on",
    ]


def test_build_and_asset_selection():
    assert parse_build_number("llama b11819") == 11819
    assert detect_variant(has_nvidia=True, machine="AMD64") == "cuda-12.4-x64"
    assert detect_variant(has_nvidia=False, machine="AMD64") == "vulkan-x64"
    assert len(asset_names("cuda-12.4-x64")) == 2


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

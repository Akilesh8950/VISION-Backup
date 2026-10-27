import asyncio
import base64
import hashlib
import io
import struct
import threading
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from vision_app.chat.controller import ChatController
from vision_app.config import AppConfig, EndpointMode
from vision_app.history import HistoryStore
from vision_app.live import speech_to_text as stt
from vision_app.live.screen_capture import capture_screen_frame, decode_image_data_url
from vision_app.paths import AppPaths
from vision_app.server.manager import build_server_command
from vision_app.server.vision import resolve_mmproj, screen_vision_status
from vision_app.tools.context import ToolContext
from vision_app.tools.registry import ToolRegistry
from vision_app.tools.sandbox import PathSandbox
from vision_app.transport.endpoints import EndpointResolver
from vision_app.transport.schemas import ChatMessage
from vision_app.transport.sse import StreamAccumulator
from vision_app.web_bridge import VisionWebApi

JPEG_DATA_URL = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xd9").decode("ascii")


def _wav_bytes(*, seconds: float = 0.4, sample_rate: int = 16_000, amplitude: int = 4_000) -> bytes:
    frames = int(seconds * sample_rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack("<" + "h" * frames, *([amplitude, -amplitude] * ((frames + 1) // 2))[:frames]))
    return buffer.getvalue()


def test_live_config_defaults_and_clamps(tmp_path):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    paths.ensure()
    assert paths.stt_models_dir.is_dir()

    config = AppConfig.load(paths)
    assert config.stt_binary_path == ""
    assert config.stt_model_size == "base"
    assert config.stt_language == "auto"
    assert config.mmproj_path == ""

    clamped = AppConfig.model_validate({"stt_model_size": "huge", "stt_language": "not a language!"})
    assert clamped.stt_model_size == "base"
    assert clamped.stt_language == "auto"


def test_chat_message_image_wire_is_user_only_and_never_dumped():
    message = ChatMessage(role="user", content="What is this?", image_data_url=JPEG_DATA_URL)
    wire = message.to_wire()
    assert wire["content"][0] == {"type": "image_url", "image_url": {"url": JPEG_DATA_URL}}
    assert wire["content"][1] == {"type": "text", "text": "What is this?"}
    assert "image_data_url" not in message.model_dump()

    assistant = ChatMessage(role="assistant", content="Answer", image_data_url=JPEG_DATA_URL)
    assert assistant.to_wire()["content"] == "Answer"


def test_mmproj_resolution_is_conservative(tmp_path):
    model = tmp_path / "vision.gguf"
    model.write_bytes(b"model")
    config = AppConfig(performance_profile="balanced")

    assert resolve_mmproj(config, model) is None
    projector = tmp_path / "mmproj-vision.gguf"
    projector.write_bytes(b"projector")
    assert resolve_mmproj(config, model) == projector

    second = tmp_path / "other-mmproj.gguf"
    second.write_bytes(b"other")
    assert resolve_mmproj(config, model) is None  # never guess between two

    config.mmproj_path = str(projector)
    assert resolve_mmproj(config, model) == projector
    config.mmproj_path = str(tmp_path / "missing.gguf")
    assert resolve_mmproj(config, model) is None


def test_server_command_only_adds_mmproj_when_present(tmp_path):
    model = tmp_path / "vision.gguf"
    model.write_bytes(b"model")
    config = AppConfig(local_port=8080, context_size=16384, gpu_layers=99, performance_profile="balanced")

    # Today's text-only install: the frozen command is byte-for-byte as before.
    assert build_server_command("llama-server.exe", model, config) == [
        "llama-server.exe", "-m", str(model), "--host", "127.0.0.1", "--port", "8080",
        "-c", "16384", "-ngl", "99", "--jinja", "--flash-attn", "on",
    ]

    projector = tmp_path / "mmproj-vision.gguf"
    projector.write_bytes(b"projector")
    command = build_server_command("llama-server.exe", model, config)
    assert command[3:5] == ["--mmproj", str(projector)]

    status = screen_vision_status(config, model, server_running=True, server_command=command)
    assert status["available"] is True
    stale = screen_vision_status(config, model, server_running=True, server_command=["llama-server.exe"])
    assert stale["available"] is False
    assert "Restart" in stale["reason"]


def test_wav_validation_rejects_silence_and_wrong_shape():
    info = stt.validate_wav_bytes(_wav_bytes())
    assert info.sample_rate == 16_000
    assert info.peak == 4_000

    with pytest.raises(ValueError, match="didn't hear"):
        stt.validate_wav_bytes(_wav_bytes(amplitude=0))
    with pytest.raises(ValueError, match="16 kHz mono"):
        stt.validate_wav_bytes(_wav_bytes(sample_rate=8_000))
    with pytest.raises(ValueError, match="too short"):
        stt.validate_wav_bytes(_wav_bytes(seconds=0.05))


def test_whisper_command_and_transcript_cleanup(tmp_path):
    command = stt.build_whisper_command(
        tmp_path / "whisper-cli.exe",
        tmp_path / "ggml-base.bin",
        tmp_path / "clip.wav",
        language="ta",
        threads=3,
    )
    assert command[1:7] == ["-m", str(tmp_path / "ggml-base.bin"), "-f", str(tmp_path / "clip.wav"), "-l", "ta"]
    assert "-nt" in command and "-np" in command
    assert stt.clean_transcript("[00:00:00.000 --> 00:00:01.000]  Hello  there\n[BLANK_AUDIO]") == "Hello there"


def test_transcribe_uses_local_runner_and_always_deletes_temp_wav(tmp_path, monkeypatch):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    paths.ensure()
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"fake")
    model = stt.whisper_model_path(paths, "base")
    model.write_bytes(b"model")
    config = AppConfig(stt_binary_path=str(binary), stt_model_size="base", stt_language="en")
    monkeypatch.setattr(stt, "speech_to_text_status", lambda _paths, _config: {"available": True, "reason": ""})

    seen = {}

    def runner(command, **_kwargs):
        wav_path = Path(command[command.index("-f") + 1])
        seen["wav_path"] = wav_path
        seen["existed_during_run"] = wav_path.is_file()
        return SimpleNamespace(returncode=0, stdout="  Hello VISION  ", stderr="")

    assert stt.transcribe_wav_bytes(paths, config, _wav_bytes(), runner=runner) == "Hello VISION"
    assert seen["existed_during_run"] is True
    assert not seen["wav_path"].exists()


def test_ensure_speech_to_text_downloads_verified_engine_and_model(tmp_path, monkeypatch):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("Release/whisper-cli.exe", b"fake whisper")
    archive_bytes = archive_buffer.getvalue()
    model_bytes = b"fake ggml model"

    monkeypatch.setattr(stt, "WHISPER_ASSET_SIZE", len(archive_bytes))
    monkeypatch.setattr(stt, "WHISPER_ASSET_SHA256", hashlib.sha256(archive_bytes).hexdigest())
    spec = stt.WhisperModelSpec(
        "tiny", "ggml-tiny.bin", len(model_bytes), hashlib.sha256(model_bytes).hexdigest(), "Tiny"
    )
    monkeypatch.setattr(stt, "WHISPER_MODELS", {**stt.WHISPER_MODELS, "tiny": spec})

    def fake_download(_url, destination, progress=None, **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = archive_bytes if destination.suffix == ".zip" else model_bytes
        destination.write_bytes(payload)
        if progress:
            progress(destination.name, len(payload), len(payload))
        return destination

    monkeypatch.setattr(stt, "download_file", fake_download)
    result = stt.ensure_speech_to_text(paths, AppConfig(stt_model_size="tiny"))
    assert result["model_size"] == "tiny"
    assert Path(result["binary_path"]).name == "whisper-cli.exe"
    assert stt.whisper_model_path(paths, "tiny").read_bytes() == model_bytes


def test_screen_capture_downscales_in_memory_and_validates_frames():
    frame = capture_screen_frame(
        platform="win32",
        image_factory=lambda: Image.new("RGB", (1600, 900), color=(24, 80, 120)),
    )
    assert frame.width == 1280
    assert frame.height == 720
    assert frame.data_url.startswith("data:image/jpeg;base64,")
    assert decode_image_data_url(frame.data_url).startswith(b"\xff\xd8\xff")

    with pytest.raises(RuntimeError, match="Windows app"):
        capture_screen_frame(platform="linux")
    with pytest.raises(ValueError, match="valid JPEG"):
        decode_image_data_url("data:image/png;base64,AAAA")


class CapturingClient:
    def __init__(self, *, local_healthy: bool = True):
        self.local_healthy = local_healthy
        self.health_kinds: list[str] = []
        self.stream_calls: list[tuple[str, list[dict]]] = []

    async def health(self, endpoint):
        self.health_kinds.append(endpoint.kind)
        return endpoint.kind != "local" or self.local_healthy

    async def stream_chat(self, endpoint, messages, _tools, accumulator: StreamAccumulator):
        self.stream_calls.append((endpoint.kind, [message.to_wire() for message in messages]))
        for event in accumulator.feed_payload(
            {"choices": [{"delta": {"content": "I can answer now."}, "finish_reason": "stop"}]}
        ):
            yield event
        accumulator.done = True


def _controller(tmp_path, client):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    paths.ensure()
    history = HistoryStore(paths.history_db)
    conversation = history.create_conversation()
    config = AppConfig(
        endpoint_mode=EndpointMode.CLOUD,
        remote_base_url="https://backup.example.invalid/v1",
        performance_profile="balanced",
    )
    controller = ChatController(
        config=config,
        history=history,
        client=client,  # type: ignore[arg-type]
        endpoint_resolver=EndpointResolver(config),
        registry=ToolRegistry(),
        tool_context=ToolContext(paths=paths, history=history, sandbox=PathSandbox(paths.workspace)),
    )
    return history, conversation, controller


def test_image_turn_is_local_only_and_frame_is_not_persisted(tmp_path):
    client = CapturingClient()
    history, conversation, controller = _controller(tmp_path, client)
    result = asyncio.run(
        controller.run_turn(conversation, "What is on my screen?", tools=[], image_data_url=JPEG_DATA_URL)
    )
    assert result.assistant_text == "I can answer now."
    assert client.health_kinds == ["local"]  # cloud is not even probed
    kind, wire = client.stream_calls[0]
    assert kind == "local"
    user_wire = next(row for row in wire if row["role"] == "user")
    assert user_wire["content"][0]["image_url"]["url"] == JPEG_DATA_URL

    stored = history.list_messages(conversation)
    assert "one approved screen frame" in stored[0]["content"]
    assert "data:image" not in str(stored)
    assert all(not message.image_data_url for message in history.chat_messages(conversation))


def test_image_turn_never_fails_over_to_cloud(tmp_path):
    client = CapturingClient(local_healthy=False)
    _history, conversation, controller = _controller(tmp_path, client)
    result = asyncio.run(
        controller.run_turn(conversation, "What is on my screen?", tools=[], image_data_url=JPEG_DATA_URL)
    )
    assert result.incomplete is True
    assert client.health_kinds == ["local"]
    assert client.stream_calls == []


def _minimal_bridge(tmp_path, *, with_projector: bool = False):
    api = VisionWebApi.__new__(VisionWebApi)
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    paths.ensure()
    model = paths.model_file
    model.write_bytes(b"model")
    if with_projector:
        (paths.models_dir / "mmproj-vision.gguf").write_bytes(b"projector")
    api.paths = paths
    api.config = AppConfig(model_path=str(model), performance_profile="balanced")
    api.server_manager = SimpleNamespace(is_running=False, command=[])
    api._live_mini_window = None
    api._live_lock = threading.RLock()
    api._live_state = {
        "active": False, "status": "idle", "caption": "", "user_text": "", "assistant_text": "",
        "level": 0.0, "screen_mode": False, "frame_approved": False, "speech_output": True,
    }
    api._live_command = {"seq": 0, "action": ""}
    return api


def test_bridge_requires_preview_approval_and_real_vision_capability(tmp_path):
    api = _minimal_bridge(tmp_path)
    frame, error = api._validated_live_frame(
        {"live_image_data_url": JPEG_DATA_URL, "live_screen_approved": True}
    )
    assert frame == ""
    assert "text-only" in error

    rejected = api.send_message({"live_image_data_url": JPEG_DATA_URL, "live_screen_approved": True})
    assert rejected["accepted"] is False
    assert "text-only" in rejected["message"]

    vision_api = _minimal_bridge(tmp_path / "vision", with_projector=True)
    frame, error = vision_api._validated_live_frame({"live_image_data_url": JPEG_DATA_URL})
    assert frame == "" and "Preview and approve" in error
    frame, error = vision_api._validated_live_frame(
        {"live_image_data_url": JPEG_DATA_URL, "live_screen_approved": True}
    )
    assert frame == JPEG_DATA_URL and error == ""


def test_bridge_live_state_is_sanitized_and_contains_no_media(tmp_path):
    api = _minimal_bridge(tmp_path)
    result = api.set_live_state(
        {
            "active": True,
            "status": "not-a-status",
            "caption": "x" * 5000,
            "level": 99,
            "data_url": JPEG_DATA_URL,
            "audio_base64": "secret-audio",
        }
    )
    state = result["live_state"]
    assert state["status"] == "idle"
    assert len(state["caption"]) == 1200
    assert state["level"] == 1.0
    assert "data_url" not in state and "audio_base64" not in state


def test_frontend_live_safety_contract():
    root = Path(__file__).resolve().parents[1] / "app" / "frontend" / "src"
    panel = (root / "LivePanel.jsx").read_text(encoding="utf-8")
    live = (root / "live.js").read_text(encoding="utf-8")
    app = (root / "App.jsx").read_text(encoding="utf-8")
    assert "Hold to talk" in panel
    assert "Approve frame" in panel
    assert "SpeechRecognition" not in panel + live + app
    assert "getDisplayMedia" not in panel + live + app
    assert "download_speech_to_text" in panel + app
    assert "live_screen_approved" in app

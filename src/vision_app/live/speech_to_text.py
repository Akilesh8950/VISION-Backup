"""Local, offline speech-to-text for Vision Live (whisper.cpp).

Privacy contract:

* Transcription runs entirely on this PC with the official whisper.cpp
  ``whisper-cli`` binary and a GGML Whisper model.  Audio never leaves the
  machine and is never sent to a cloud speech service.
* The engine + model are downloaded only when the user clicks the download
  button in Settings.  Both downloads are pinned and SHA-256 verified
  (digest taken from the official GitHub release API / Hugging Face LFS
  metadata), resumable via the existing download helper, and the archive
  is extracted with the same path-traversal guard used for llama.cpp.
* A microphone clip is written to one temporary WAV solely because
  ``whisper-cli`` reads files; it is deleted in a ``finally`` block on
  every path.  Nothing is retained, logged, or added to telemetry.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..paths import AppPaths
from ..server.download_llama import Progress, download_file, safe_extract_zip
from ..subprocess_utils import run_bounded

WHISPER_TAG = "v1.8.4"
WHISPER_ASSET = "whisper-bin-x64.zip"
WHISPER_ASSET_SIZE = 4_078_768
WHISPER_ASSET_SHA256 = "74f973345cb52ef5ba3ec9e7e7af8e48cc8c71722d1528603b80588a11f82e3e"
WHISPER_ASSET_URL = (
    f"https://github.com/ggml-org/whisper.cpp/releases/download/{WHISPER_TAG}/{WHISPER_ASSET}"
)
WHISPER_MODEL_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

MAX_WAV_BYTES = 12_582_912  # 12 MiB hard cap on one push-to-talk clip
MAX_WAV_SECONDS = 120.0
MIN_WAV_SECONDS = 0.15


@dataclass(frozen=True)
class WhisperModelSpec:
    size: str
    filename: str
    bytes_total: int
    sha256: str
    label: str


WHISPER_MODELS: dict[str, WhisperModelSpec] = {
    "tiny": WhisperModelSpec(
        "tiny",
        "ggml-tiny.bin",
        77_691_713,
        "be07e048e1e28a0c6687329f79ab35f4729498a4bfd46d8b65d2f53ee029f6fe",
        "Tiny — fastest (78 MB)",
    ),
    "base": WhisperModelSpec(
        "base",
        "ggml-base.bin",
        147_951_465,
        "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
        "Base — balanced (148 MB)",
    ),
    "small": WhisperModelSpec(
        "small",
        "ggml-small.bin",
        487_601_967,
        "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
        "Small — most accurate (488 MB)",
    ),
}

_TRANSCRIBE_LOCK = threading.Lock()
_NOISE_TOKENS = {"[blank_audio]", "(silence)", "[silence]", "[inaudible]"}


def whisper_target_dir(paths: AppPaths) -> Path:
    return paths.bin_dir / f"whisper-{WHISPER_TAG}-x64"


def whisper_model_path(paths: AppPaths, model_size: str) -> Path:
    spec = WHISPER_MODELS.get(model_size) or WHISPER_MODELS["base"]
    return paths.stt_models_dir / spec.filename


def find_whisper_binary(paths: AppPaths | None, configured_path: str = "") -> Path | None:
    """Locate whisper-cli: explicit setting, managed download, then PATH."""

    configured = str(configured_path or "").strip()
    if configured and Path(configured).is_file():
        return Path(configured)
    if paths is not None:
        names = ("whisper-cli.exe", "whisper-cli")
        candidates: list[Path] = []
        for name in names:
            candidates.extend(paths.bin_dir.glob(f"whisper-*/**/{name}"))
        files = [candidate for candidate in candidates if candidate.is_file()]
        if files:
            files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            return files[0]
    for name in ("whisper-cli", "whisper-cli.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_file(path: Path, expected_size: int, expected_sha256: str) -> bool:
    try:
        return path.is_file() and path.stat().st_size == expected_size and _sha256(path) == expected_sha256
    except OSError:
        return False


def speech_to_text_status(paths: AppPaths, config: AppConfig) -> dict[str, Any]:
    """Public status for Settings / Vision Live.  Filenames only, no paths."""

    model_size = config.stt_model_size if config.stt_model_size in WHISPER_MODELS else "base"
    spec = WHISPER_MODELS[model_size]
    binary = find_whisper_binary(paths, config.stt_binary_path)
    model = whisper_model_path(paths, model_size)
    model_downloaded = model.is_file()
    platform_supported = sys.platform == "win32"
    available = bool(binary and model_downloaded and platform_supported)
    if available:
        reason = "Local speech-to-text is installed."
    elif not platform_supported:
        reason = "Vision Live speech-to-text is available in the Windows app."
    elif binary and not model_downloaded:
        reason = "Download the local voice model to finish setting up Vision Live."
    else:
        reason = "Download the local speech-to-text engine and voice model."
    return {
        "engine": "whisper.cpp",
        "available": available,
        "binary_downloaded": binary is not None,
        "binary_name": binary.name if binary else "",
        "model_downloaded": model_downloaded,
        "model_size": model_size,
        "model_label": spec.label,
        "model_bytes": spec.bytes_total,
        "language": config.stt_language,
        "platform_supported": platform_supported,
        "reason": reason,
        "models": [
            {"size": item.size, "label": item.label, "bytes": item.bytes_total}
            for item in WHISPER_MODELS.values()
        ],
    }


def ensure_speech_to_text(
    paths: AppPaths,
    config: AppConfig,
    progress: Progress | None = None,
    *,
    model_size: str | None = None,
) -> dict[str, Any]:
    """Download + verify whisper-cli and the selected model if missing."""

    selected = model_size if model_size in WHISPER_MODELS else config.stt_model_size
    if selected not in WHISPER_MODELS:
        selected = "base"
    spec = WHISPER_MODELS[selected]
    callback: Progress = progress or (lambda _stage, _done, _total: None)
    paths.ensure()
    paths.bin_dir.mkdir(parents=True, exist_ok=True)
    paths.stt_models_dir.mkdir(parents=True, exist_ok=True)

    binary = find_whisper_binary(paths, config.stt_binary_path)
    managed_target = whisper_target_dir(paths)
    if binary is None:
        archive = managed_target / WHISPER_ASSET
        managed_target.mkdir(parents=True, exist_ok=True)
        if archive.exists() and not _verified_file(archive, WHISPER_ASSET_SIZE, WHISPER_ASSET_SHA256):
            archive.unlink(missing_ok=True)
            archive.with_name(archive.name + ".part").unlink(missing_ok=True)
        if not archive.exists():
            download_file(WHISPER_ASSET_URL, archive, progress=callback)
            if not _verified_file(archive, WHISPER_ASSET_SIZE, WHISPER_ASSET_SHA256):
                archive.unlink(missing_ok=True)
                raise RuntimeError("The speech-to-text download failed its integrity check.")
        try:
            safe_extract_zip(archive, managed_target)
        except RuntimeError as exc:
            raise RuntimeError("The speech-to-text download could not be opened safely.") from exc
        binary = find_whisper_binary(paths, "")
    if binary is None:
        raise RuntimeError("Speech-to-text engine download did not produce whisper-cli.")
    if sys.platform != "win32":
        try:
            binary.chmod(0o755)
        except OSError:
            pass

    model = whisper_model_path(paths, selected)
    if model.exists() and not _verified_file(model, spec.bytes_total, spec.sha256):
        model.unlink(missing_ok=True)
        model.with_name(model.name + ".part").unlink(missing_ok=True)
    if not model.exists():
        download_file(f"{WHISPER_MODEL_BASE_URL}/{spec.filename}", model, progress=callback)
        if not _verified_file(model, spec.bytes_total, spec.sha256):
            model.unlink(missing_ok=True)
            raise RuntimeError("The voice model download failed its integrity check.")

    return {
        "ok": True,
        "binary_path": str(binary),
        "model_size": selected,
        "message": "Local speech-to-text is ready. Vision Live now works offline.",
    }


@dataclass(frozen=True)
class WavInfo:
    channels: int
    sample_width: int
    sample_rate: int
    frames: int
    duration_seconds: float
    peak: int


def validate_wav_bytes(data: bytes) -> WavInfo:
    """Accept only the exact 16 kHz mono PCM16 shape whisper.cpp expects."""

    if not data or len(data) > MAX_WAV_BYTES:
        raise ValueError("That voice clip is too large. Hold push-to-talk for a shorter message.")
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            info = WavInfo(
                channels=wav.getnchannels(),
                sample_width=wav.getsampwidth(),
                sample_rate=wav.getframerate(),
                frames=wav.getnframes(),
                duration_seconds=(wav.getnframes() / wav.getframerate()) if wav.getframerate() else 0.0,
                peak=0,
            )
            frames = wav.readframes(info.frames)
    except (wave.Error, EOFError, OSError) as exc:
        raise ValueError("That voice clip could not be read. Please try speaking again.") from exc
    if info.channels != 1 or info.sample_width != 2 or info.sample_rate != 16_000:
        raise ValueError("Voice clips must be 16 kHz mono audio. Please try again.")
    if info.duration_seconds < MIN_WAV_SECONDS:
        raise ValueError("That was too short — hold push-to-talk while you speak.")
    if info.duration_seconds > MAX_WAV_SECONDS:
        raise ValueError("That voice clip is too long. Please split it into shorter messages.")
    peak = 0
    usable = len(frames) - (len(frames) % 2)
    if usable:
        samples = struct.unpack(f"<{usable // 2}h", frames[:usable])
        peak = max((abs(sample) for sample in samples), default=0)
    if peak < 120:
        raise ValueError("I didn't hear anything. Check your microphone and try again.")
    return WavInfo(info.channels, info.sample_width, info.sample_rate, info.frames, info.duration_seconds, peak)


def build_whisper_command(
    binary: Path,
    model: Path,
    wav_path: Path,
    *,
    language: str = "auto",
    threads: int | None = None,
) -> list[str]:
    thread_count = max(1, min(8, int(threads or 0) or min(4, os.cpu_count() or 1)))
    clean_language = (language or "auto").strip().lower() or "auto"
    return [
        str(binary),
        "-m",
        str(model),
        "-f",
        str(wav_path),
        "-l",
        clean_language,
        "-nt",
        "-np",
        "-t",
        str(thread_count),
        "-bs",
        "1",
        "-bo",
        "1",
    ]


def clean_transcript(text: str) -> str:
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = re.sub(r"^\s*\[[0-9:.]+\s*-->[^\]]+\]\s*", "", raw).strip()
        if not line or line.lower() in _NOISE_TOKENS:
            continue
        lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()[:8000]


def transcribe_wav_bytes(
    paths: AppPaths,
    config: AppConfig,
    wav_bytes: bytes,
    *,
    language: str | None = None,
    runner=run_bounded,
) -> str:
    """Transcribe one clip locally.  The temp WAV is always deleted."""

    info = validate_wav_bytes(wav_bytes)
    status = speech_to_text_status(paths, config)
    if not status["available"]:
        raise RuntimeError(str(status["reason"]))
    binary = find_whisper_binary(paths, config.stt_binary_path)
    model = whisper_model_path(paths, config.stt_model_size)
    if binary is None or not model.is_file():
        raise RuntimeError("Local speech-to-text is not installed yet.")

    paths.stt_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="vision-live-", suffix=".wav", dir=paths.stt_dir, delete=False
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(wav_bytes)
    try:
        command = build_whisper_command(binary, model, temp_path, language=language or config.stt_language)
        timeout = max(30.0, min(180.0, info.duration_seconds * 3.0 + 20.0))
        with _TRANSCRIBE_LOCK:
            try:
                result = runner(command, timeout=timeout, stdout_chars=20_000, stderr_chars=4_000)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Speech-to-text took too long. Try a shorter message or the Tiny model.") from exc
            except OSError as exc:
                raise RuntimeError("The local speech-to-text engine could not start.") from exc
        if int(getattr(result, "returncode", 1)) != 0:
            raise RuntimeError("The local speech-to-text engine could not transcribe that clip.")
        text = clean_transcript(str(getattr(result, "stdout", "") or ""))
        if not text:
            raise ValueError("I couldn't make out any words. Please try again a little closer to the microphone.")
        return text
    finally:
        temp_path.unlink(missing_ok=True)

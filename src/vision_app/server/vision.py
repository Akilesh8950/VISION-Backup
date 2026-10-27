"""Optional multimodal projector (mmproj) resolution for llama-server.

The shipped VISION GGUF is text-only: its Hugging Face repo contains no
mmproj file, so by default nothing here changes the frozen server command.
When a future vision-capable VISION build ships a matching projector, the
user can place exactly one ``*mmproj*.gguf`` beside the active model (or
set it explicitly in Settings) and llama-server will be started with
``--mmproj``.  Auto-discovery deliberately refuses to guess when several
projectors are present: a projector only works with its own model, and
loading the wrong one can crash the server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import AppConfig


def resolve_mmproj(config: AppConfig, model_path: str | Path) -> Path | None:
    """Return the configured/discovered mmproj file, or None."""

    explicit = str(getattr(config, "mmproj_path", "") or "").strip()
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None
    model_dir = Path(model_path).expanduser().parent
    try:
        candidates = sorted(
            path
            for path in model_dir.glob("*.gguf")
            if "mmproj" in path.name.lower() and path.is_file()
        )
    except OSError:
        return None
    return candidates[0] if len(candidates) == 1 else None


def screen_vision_status(
    config: AppConfig,
    model_path: str | Path,
    *,
    server_running: bool = False,
    server_command: list[str] | None = None,
) -> dict[str, Any]:
    """Public, path-free status for the Vision Live UI."""

    mmproj = resolve_mmproj(config, model_path)
    if mmproj is None:
        return {
            "available": False,
            "configured": False,
            "mmproj_name": "",
            "reason": (
                "The installed VISION model is text-only (no matching "
                "mmproj vision file), so screenshots cannot be understood yet."
            ),
        }
    active = (not server_running) or "--mmproj" in list(server_command or [])
    return {
        "available": bool(active),
        "configured": True,
        "mmproj_name": mmproj.name,
        "reason": (
            "A vision projector is configured."
            if active
            else "Restart the local model so it loads the vision projector."
        ),
    }

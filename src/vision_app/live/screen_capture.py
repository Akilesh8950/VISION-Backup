"""One-frame, read-only screen capture for Vision Live.

Safety contract: this module grabs exactly one still frame of the primary
display when the user explicitly asks, downscales + JPEG-encodes it in
memory, and returns a data URL.  It never writes the frame to disk, never
streams continuous video, and has no ability to move the mouse, click, or
type.  The UI must preview the frame and get explicit approval before the
frame can be attached to a chat turn.
"""

from __future__ import annotations

import base64
import sys
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from typing import Any

MAX_FRAME_WIDTH = 1280
MAX_FRAME_BYTES = 2_000_000
JPEG_QUALITIES = (78, 66, 54)


@dataclass(frozen=True)
class ScreenFrame:
    data_url: str
    width: int
    height: int
    byte_count: int

    def payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "data_url": self.data_url,
            "width": self.width,
            "height": self.height,
            "bytes": self.byte_count,
        }


def decode_image_data_url(data_url: str, *, max_bytes: int = MAX_FRAME_BYTES) -> bytes:
    """Validate one approved frame before it may join a local model turn."""

    value = str(data_url or "")
    prefix = "data:image/jpeg;base64,"
    if not value.startswith(prefix):
        raise ValueError("The shared screen frame is not a valid JPEG frame.")
    encoded = value[len(prefix):]
    if not encoded or len(encoded) > ((max_bytes + 2) * 4 // 3) + 8:
        raise ValueError("The shared screen frame is too large.")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValueError("The shared screen frame is not valid image data.") from exc
    if len(decoded) > max_bytes or not decoded.startswith(b"\xff\xd8\xff"):
        raise ValueError("The shared screen frame is not a valid JPEG frame.")
    return decoded


def capture_screen_frame(
    *,
    platform: str | None = None,
    image_factory: Callable[[], Any] | None = None,
    max_width: int = MAX_FRAME_WIDTH,
) -> ScreenFrame:
    """Capture one downscaled primary-display frame as an in-memory JPEG."""

    current_platform = platform if platform is not None else sys.platform
    if image_factory is None and current_platform != "win32":
        raise RuntimeError("Screen sharing is available in the Windows app.")
    try:
        if image_factory is not None:
            image = image_factory()
        else:
            from PIL import ImageGrab

            image = ImageGrab.grab(all_screens=False)
    except Exception as exc:
        raise RuntimeError("VISION could not capture the screen. Windows may have blocked it.") from exc

    try:
        from PIL import Image

        rgb = image.convert("RGB")
        if rgb.width > max_width:
            ratio = max_width / float(rgb.width)
            rgb = rgb.resize((max_width, max(1, round(rgb.height * ratio))), Image.Resampling.LANCZOS)
        encoded = b""
        for quality in JPEG_QUALITIES:
            buffer = BytesIO()
            rgb.save(buffer, format="JPEG", quality=quality, optimize=True)
            encoded = buffer.getvalue()
            if len(encoded) <= MAX_FRAME_BYTES:
                break
        if not encoded or len(encoded) > MAX_FRAME_BYTES:
            raise RuntimeError("That screen frame was too large to share safely.")
        data_url = "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")
        return ScreenFrame(data_url=data_url, width=rgb.width, height=rgb.height, byte_count=len(encoded))
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("VISION could not prepare that screen frame.") from exc

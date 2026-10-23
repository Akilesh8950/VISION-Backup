from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatCommand:
    name: str
    argument: str


def parse_command(text: str) -> ChatCommand | None:
    stripped = text.strip()
    for prefix in ("/fix", "/teach"):
        if stripped == prefix or stripped.startswith(prefix + " "):
            return ChatCommand(prefix[1:], stripped[len(prefix):].strip())
    return None

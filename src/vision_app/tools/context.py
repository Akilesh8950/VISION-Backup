from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..history import HistoryStore
from ..paths import AppPaths
from .sandbox import PathSandbox

ConfirmCallback = Callable[[str, str, dict[str, Any]], bool]
TimerCallback = Callable[[float, str], None]
CheckpointCallback = Callable[[Path], None]


@dataclass
class ToolContext:
    paths: AppPaths
    history: HistoryStore
    sandbox: PathSandbox
    conversation_id: str = ""
    confirm: ConfirmCallback | None = None
    # Separate, explicit session-only opt-in.  Chat/Agent mode selection
    # never changes this flag; the UI owns it and it defaults to False on
    # every process start.
    auto_approve_python: bool = False
    # Agentic mode: registry-level "ask" prompts for safe builtin tools are
    # auto-approved.  Tool-level safety confirmations (outside-workspace
    # writes, launches, huge clipboard sets) still always ask.
    agent_auto_approve: bool = False
    timer_callback: TimerCallback | None = None
    checkpoint_callback: CheckpointCallback | None = None
    skill_loader: Callable[[str], dict[str, Any]] | None = None
    skill_resource_reader: Callable[[str, str], str] | None = None
    allow_writes_outside_workspace: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    timers: list[threading.Timer] = field(default_factory=list)

    def require_confirmation(self, title: str, message: str, details: dict[str, Any]) -> bool:
        if self.confirm is None:
            return False
        return bool(self.confirm(title, message, details))

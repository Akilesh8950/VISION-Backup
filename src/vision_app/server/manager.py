from __future__ import annotations

import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path

from ..config import AppConfig
from ..redaction import redact_text

LogCallback = Callable[[str], None]


def build_server_command(binary: str | Path, model_path: str | Path, config: AppConfig) -> list[str]:
    return [
        str(binary),
        "-m",
        str(model_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(config.local_port),
        "-c",
        str(config.context_size),
        "-ngl",
        str(config.gpu_layers),
        "--jinja",
        "--flash-attn",
        "on",
    ]


class LlamaServerManager:
    def __init__(self, max_log_lines: int = 2000):
        self.process: subprocess.Popen[str] | None = None
        self.command: list[str] = []
        self._logs: deque[str] = deque(maxlen=max_log_lines)
        self._lock = threading.Lock()
        self._callbacks: list[LogCallback] = []
        self._reader: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def exit_code(self) -> int | None:
        return None if self.process is None else self.process.poll()

    def on_log(self, callback: LogCallback) -> None:
        self._callbacks.append(callback)

    def _append_log(self, line: str) -> None:
        clean = redact_text(line.rstrip())
        with self._lock:
            self._logs.append(clean)
        for callback in list(self._callbacks):
            try:
                callback(clean)
            except Exception:
                pass

    def logs(self, limit: int | None = None) -> list[str]:
        with self._lock:
            values = list(self._logs)
        return values[-limit:] if limit else values

    def clear_logs(self) -> None:
        with self._lock:
            self._logs.clear()

    def start_command(self, command: list[str], cwd: str | Path | None = None) -> subprocess.Popen[str]:
        if self.is_running:
            return self.process  # type: ignore[return-value]
        self.command = list(command)
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            self.command,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        self._append_log("Started: " + " ".join(self.command))
        self._reader = threading.Thread(target=self._read_output, daemon=True, name="llama-server-log")
        self._reader.start()
        return self.process

    def _read_output(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                self._append_log(line)
        except Exception as exc:
            self._append_log(f"Log reader stopped: {exc}")
        code = process.poll()
        if code is not None:
            self._append_log(f"llama-server exited with code {code}")

    def start(self, binary: str | Path, model_path: str | Path, config: AppConfig) -> subprocess.Popen[str]:
        return self.start_command(build_server_command(binary, model_path, config), cwd=Path(binary).parent)

    def stop(self, timeout: float = 5.0) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)

    def restart(self, binary: str | Path, model_path: str | Path, config: AppConfig) -> subprocess.Popen[str]:
        self.stop()
        return self.start(binary, model_path, config)

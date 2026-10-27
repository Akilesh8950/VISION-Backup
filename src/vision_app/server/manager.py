from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from ..config import AppConfig
from ..redaction import redact_text
from .hardware import HardwareInfo, resolve_perf

LogCallback = Callable[[str], None]


def build_server_command(
    binary: str | Path,
    model_path: str | Path,
    config: AppConfig,
    hardware: HardwareInfo | None = None,
) -> list[str]:
    # The base invocation is the frozen contract.  Performance profiles
    # (Settings -> Model) may append tuning flags and, on low-end PCs,
    # shrink the context / GPU layers so the model fits comfortably;
    # the balanced profile appends nothing at all.
    perf = resolve_perf(
        config.performance_profile,
        config.context_size,
        config.gpu_layers,
        hardware,
    )
    return [
        str(binary),
        "-m",
        str(model_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(config.local_port),
        "-c",
        str(perf.context_size),
        "-ngl",
        str(perf.gpu_layers),
        "--jinja",
        "--flash-attn",
        "on",
        *perf.extra_args(),
    ]


class LlamaServerManager:
    def __init__(self, max_log_lines: int = 2000):
        self.process: subprocess.Popen[str] | None = None
        self.command: list[str] = []
        self._logs: deque[str] = deque(maxlen=max_log_lines)
        self._lock = threading.Lock()
        self._process_lock = threading.RLock()
        self._callbacks: list[LogCallback] = []
        self._reader: threading.Thread | None = None
        self._started_at: float | None = None
        self._last_started_at: float | None = None

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def exit_code(self) -> int | None:
        return None if self.process is None else self.process.poll()

    @property
    def started_at(self) -> float | None:
        """Monotonic start time for the current process, if any."""

        return self._started_at

    @property
    def last_started_at(self) -> float | None:
        return self._last_started_at

    def on_log(self, callback: LogCallback) -> None:
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def _append_log(self, line: str) -> None:
        clean = redact_text(line.rstrip())
        if len(clean) > 10_000:
            clean = clean[:10_000] + "… [log line truncated]"
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
        if limit is None or limit <= 0:
            return values
        return values[-limit:]

    def clear_logs(self) -> None:
        with self._lock:
            self._logs.clear()

    def _cleanup_reader(self, process: subprocess.Popen[str] | None, timeout: float = 1.0) -> None:
        reader = self._reader
        if reader is not None and reader.is_alive() and reader is not threading.current_thread():
            reader.join(timeout=timeout)
        if process is not None and process.stdout is not None:
            try:
                process.stdout.close()
            except Exception:
                pass
        if reader is not None and reader.is_alive() and reader is not threading.current_thread():
            reader.join(timeout=timeout)
        if reader is not None and not reader.is_alive() and self._reader is reader:
            self._reader = None

    def start_command(self, command: list[str], cwd: str | Path | None = None) -> subprocess.Popen[str]:
        with self._process_lock:
            if self.is_running and self.process is not None:
                return self.process
            if self.process is not None:
                self._cleanup_reader(self.process)
            self.command = list(command)
            creationflags = 0
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
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
            self.process = process
            self._started_at = time.monotonic()
            self._last_started_at = self._started_at
            self._append_log("Started: " + " ".join(self.command))
            reader = threading.Thread(
                target=self._read_output,
                args=(process,),
                daemon=True,
                name="llama-server-log",
            )
            self._reader = reader
            try:
                reader.start()
            except Exception:
                self._reader = None
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
                self._cleanup_reader(process)
                self._started_at = None
                raise
            return process

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        try:
            while True:
                # Read bounded chunks rather than iterating lines: a faulty
                # binary emitting megabytes without a newline must not make
                # the log reader allocate one unbounded Python string.
                line = process.stdout.readline(65_537)
                if not line:
                    break
                self._append_log(line)
        except Exception as exc:
            self._append_log(f"Log reader stopped: {exc}")
        finally:
            try:
                process.stdout.close()
            except Exception:
                pass
        code = process.poll()
        if code is not None:
            self._append_log(f"llama-server exited with code {code}")

    def start(self, binary: str | Path, model_path: str | Path, config: AppConfig) -> subprocess.Popen[str]:
        return self.start_command(build_server_command(binary, model_path, config), cwd=Path(binary).parent)

    def stop(self, timeout: float = 5.0) -> None:
        with self._process_lock:
            process = self.process
            self._started_at = None
            if process is None:
                return
            try:
                if process.poll() is None:
                    try:
                        process.terminate()
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                    try:
                        process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                        except (ProcessLookupError, PermissionError, OSError):
                            pass
                        try:
                            process.wait(timeout=timeout)
                        except subprocess.TimeoutExpired:
                            self._append_log("Timed out waiting for llama-server to stop")
            finally:
                self._cleanup_reader(process)

    def restart(self, binary: str | Path, model_path: str | Path, config: AppConfig) -> subprocess.Popen[str]:
        with self._process_lock:
            self.stop()
            return self.start(binary, model_path, config)

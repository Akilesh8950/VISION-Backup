from __future__ import annotations

import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class _BoundedBuffer:
    """Retain a bounded head or tail from a text stream."""

    def __init__(self, max_chars: int, *, mode: Literal["head", "tail"] = "tail") -> None:
        self._max_chars = max(0, int(max_chars))
        self._mode = mode
        self._chunks: deque[str] = deque()
        self._length = 0
        self.truncated = False

    def append(self, chunk: str) -> None:
        if not chunk:
            return
        if self._max_chars == 0:
            self.truncated = True
            return
        if self._mode == "head":
            remaining = self._max_chars - self._length
            if remaining <= 0:
                self.truncated = True
                return
            accepted = chunk[:remaining]
            self._chunks.append(accepted)
            self._length += len(accepted)
            if len(accepted) < len(chunk):
                self.truncated = True
            return
        if len(chunk) >= self._max_chars:
            if self._chunks or len(chunk) > self._max_chars:
                self.truncated = True
            self._chunks.clear()
            self._chunks.append(chunk[-self._max_chars :])
            self._length = self._max_chars
            return
        self._chunks.append(chunk)
        self._length += len(chunk)
        while self._length > self._max_chars and self._chunks:
            self.truncated = True
            excess = self._length - self._max_chars
            first = self._chunks[0]
            if len(first) <= excess:
                self._chunks.popleft()
                self._length -= len(first)
            else:
                self._chunks[0] = first[excess:]
                self._length -= excess

    def text(self) -> str:
        return "".join(self._chunks)


def _drain(stream: IO[str] | None, output: _BoundedBuffer) -> None:
    if stream is None:
        return
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            output.append(chunk)
    except (OSError, ValueError):
        # Broken/closed pipes are an ordinary child-process teardown race.
        return


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    """Kill an owned timed-out child and any descendants it spawned."""

    try:
        import psutil  # type: ignore
    except Exception:
        psutil = None  # type: ignore[assignment]

    if psutil is not None:
        try:
            root = psutil.Process(process.pid)
            descendants = root.children(recursive=True)
            for child in reversed(descendants):
                try:
                    child.kill()
                except (psutil.Error, OSError):
                    pass
            root.kill()
            try:
                psutil.wait_procs([*descendants, root], timeout=2)
            except (psutil.Error, OSError):
                pass
            return
        except (psutil.Error, OSError, ValueError):
            pass

    try:
        process.kill()
    except OSError:
        pass


def run_bounded(
    command: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float,
    stdout_chars: int = 20_000,
    stderr_chars: int = 20_000,
    stdout_mode: Literal["head", "tail"] = "tail",
    stderr_mode: Literal["head", "tail"] = "tail",
    creationflags: int = 0,
) -> BoundedProcessResult:
    """Run a no-shell child while bounding retained stdout/stderr.

    ``subprocess.run(..., capture_output=True)`` buffers every byte before a
    caller can truncate it. A noisy child can therefore exhaust memory even
    when the UI only needs the output tail. This helper drains both pipes
    concurrently and retains only the requested tails. Timeout semantics
    intentionally match ``subprocess.run`` by raising
    :class:`subprocess.TimeoutExpired` with the bounded output attached.
    """
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    stdout = _BoundedBuffer(stdout_chars, mode=stdout_mode)
    stderr = _BoundedBuffer(stderr_chars, mode=stderr_mode)
    readers = [
        threading.Thread(target=_drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=_drain, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    returncode: int | None
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_tree(process)
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            returncode = process.poll()
            if returncode is None:
                returncode = -1
    finally:
        for reader in readers:
            reader.join(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    if timed_out:
        raise subprocess.TimeoutExpired(command, timeout, output=stdout.text(), stderr=stderr.text())
    return BoundedProcessResult(
        returncode=int(returncode if returncode is not None else -1),
        stdout=stdout.text(),
        stderr=stderr.text(),
        stdout_truncated=stdout.truncated,
        stderr_truncated=stderr.truncated,
    )

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import BinaryIO, Self

from .paths import AppPaths


class SingleInstanceLock:
    """A best-effort, OS-released lock for one VISION data directory.

    The lock file itself may remain after a crash; the operating-system
    file lock does not.  That distinction is important: existence of the
    file is never treated as proof that another process is alive.
    """

    def __init__(self, lock_path: Path):
        self.lock_path = Path(lock_path)
        self._stream: BinaryIO | None = None

    @property
    def acquired(self) -> bool:
        return self._stream is not None

    def acquire(self) -> bool:
        if self._stream is not None:
            return True
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.lock_path.open("a+b")
        try:
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            stream.close()
            return False

        try:
            stream.seek(0)
            stream.truncate()
            stream.write(f"{os.getpid()}\n".encode("ascii"))
            stream.flush()
        except OSError:
            # The OS lock is the authority; recording the PID is diagnostic.
            pass
        self._stream = stream
        return True

    def release(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


def single_instance_lock_for(paths: AppPaths) -> SingleInstanceLock:
    return SingleInstanceLock(paths.app_data / "vision.lock")


__all__ = ["SingleInstanceLock", "single_instance_lock_for"]

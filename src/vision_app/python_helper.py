from __future__ import annotations

import os
import runpy
import sys
import traceback
from pathlib import Path

RUN_PYTHON_HELPER_ARG = "--vision-run-python"


def _ensure_standard_streams() -> list[object]:
    """Restore stdout/stderr in a windowed frozen process when possible.

    PyInstaller windowed executables may expose ``None`` for the standard
    text streams. When VISION launches itself as the ``run_python`` helper,
    the parent has already connected OS file descriptors 1 and 2 to pipes,
    so wrap duplicates of those descriptors instead of discarding output.
    """

    opened: list[object] = []
    for attribute, descriptor in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, attribute, None) is not None:
            continue
        try:
            stream = os.fdopen(
                os.dup(descriptor),
                "w",
                encoding="utf-8",
                errors="replace",
                closefd=True,
            )
        except OSError:
            continue
        setattr(sys, attribute, stream)
        opened.append(stream)
    return opened


def _exit_code(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    print(value, file=sys.stderr)
    return 1


def run_python_script(script: Path) -> int:
    script = Path(script).expanduser().resolve()
    opened = _ensure_standard_streams()
    old_argv = list(sys.argv)
    try:
        if not script.is_file():
            print(f"VISION Python helper: script not found: {script}", file=sys.stderr)
            return 2
        sys.argv = [str(script)]
        try:
            runpy.run_path(str(script), run_name="__main__")
            return 0
        except SystemExit as exc:
            return _exit_code(exc.code)
        except KeyboardInterrupt:
            traceback.print_exc()
            return 130
        except BaseException:
            traceback.print_exc()
            return 1
    finally:
        sys.argv = old_argv
        for stream in opened:
            try:
                stream.flush()  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                stream.close()  # type: ignore[attr-defined]
            except Exception:
                pass

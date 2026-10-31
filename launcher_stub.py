"""VISION outer launcher.

This file is compiled once as the stable outer ``VISION.exe``.  It uses only
the Python standard library and deliberately does not import the desktop
application.

Layout it manages::

    VISION.exe          <- this launcher
    current.json        <- {"version": "1.0.0", "previous": "0.9.0"}
    pending.json        <- written by the running app after verification
    versions/1.0.0/VISION.exe
    versions/1.1.0/VISION.exe
    downloads/...

The running app may write ``pending.json`` but must never rewrite
``current.json``.  This launcher changes the selection only after the child
process has exited.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LAUNCHER_VERSION = "1.0.0"
APP_EXE_NAME = "VISION.exe"
CRASH_WINDOW_SECONDS = 60.0
MAX_LAUNCHES = 6
_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z.-]+)?$"
)


def parse_version(value: str) -> tuple[tuple[int, int, int], tuple[str, ...]] | None:
    match = _VERSION_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    return ((int(match.group(1)), int(match.group(2)), int(match.group(3))), prerelease)


def version_key(value: str) -> tuple:
    parsed = parse_version(value)
    if parsed is None:
        return ((0, 0, 0), (0,), ())
    core, prerelease = parsed
    # A release outranks a prerelease.  Each prerelease identifier is made
    # comparable without mixing int/str comparison errors.
    identifiers = tuple((0, int(item), "") if item.isdigit() else (1, 0, item) for item in prerelease)
    return (core, (0 if prerelease else 1,), identifiers)


def read_json(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.read(1_000_001)
        if len(raw) > 1_000_000:
            return {}
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _valid_version(value: object) -> str:
    text = str(value or "").strip()
    return text if parse_version(text) is not None else ""


def version_exe(root: Path, version: str) -> Path:
    clean = _valid_version(version)
    if not clean:
        # A malformed launcher state file must never be able to turn a
        # version string such as "../../outside" into an executable path
        # outside the versioned install tree.
        return root / "versions" / "__invalid__" / APP_EXE_NAME
    return root / "versions" / clean / APP_EXE_NAME


def installed_versions(root: Path) -> list[str]:
    versions_dir = root / "versions"
    if not versions_dir.is_dir():
        return []
    found = [
        child.name
        for child in versions_dir.iterdir()
        if child.is_dir() and parse_version(child.name) is not None and (child / APP_EXE_NAME).is_file()
    ]
    return sorted(found, key=version_key)


def initial_selection(root: Path) -> tuple[str, str]:
    current_data = read_json(root / "current.json")
    current = _valid_version(current_data.get("version"))
    previous = _valid_version(current_data.get("previous"))
    installed = installed_versions(root)

    if not (current and version_exe(root, current).is_file()):
        current = previous if previous and version_exe(root, previous).is_file() else ""
    if not current and installed:
        current = installed[-1]
    if not current:
        raise FileNotFoundError("No installed VISION version was found")
    if previous == current or not (previous and version_exe(root, previous).is_file()):
        candidates = [version for version in installed if version != current]
        previous = candidates[-1] if candidates else ""
    return current, previous


def write_current(root: Path, current: str, previous: str) -> None:
    payload = read_json(root / "current.json")
    payload["version"] = current
    payload["previous"] = previous
    payload.setdefault("model_sha", "")
    payload.setdefault("last_checked", None)
    atomic_write_json(root / "current.json", payload)


def consume_pending(root: Path, current: str, previous: str) -> tuple[str, str] | None:
    pending_path = root / "pending.json"
    if not pending_path.is_file():
        return None
    pending = read_json(pending_path)
    next_version = _valid_version(pending.get("version"))
    # Whether valid or not, the file is a one-time handoff.  Removing an
    # invalid one prevents a launch loop; the currently selected version is
    # left untouched in that case.
    pending_path.unlink(missing_ok=True)
    if not next_version or not version_exe(root, next_version).is_file():
        return None
    next_previous = _valid_version(pending.get("previous")) or current
    if next_previous == next_version or not version_exe(root, next_previous).is_file():
        next_previous = current if current != next_version else previous
    write_current(root, next_version, next_previous)
    return next_version, next_previous


def prune_versions(root: Path, current: str, previous: str) -> None:
    keep = {current, previous}
    pending = read_json(root / "pending.json")
    if pending.get("version"):
        keep.add(str(pending["version"]))
    versions_dir = root / "versions"
    if not versions_dir.is_dir():
        return
    for child in versions_dir.iterdir():
        if child.is_dir() and parse_version(child.name) is not None and child.name not in keep:
            shutil.rmtree(child, ignore_errors=True)


def run_child(executable: Path, args: list[str], root: Path) -> tuple[int, float]:
    started = time.monotonic()
    env = os.environ.copy()
    env["VISION_LAUNCHER_ROOT"] = str(root)
    env["VISION_LAUNCHER_VERSION"] = LAUNCHER_VERSION
    completed = subprocess.run([str(executable), *args], cwd=str(root), env=env, check=False)
    return completed.returncode, time.monotonic() - started


SELFTEST_TIMEOUT_SECONDS = 180


def run_selftest(executable: Path, root: Path, version: str) -> bool:
    """Gate 4: run the freshly staged build's --selftest exactly once.

    Pass = exit code 0 and, when the build wrote result.json, ok is not
    false.  Any error (missing exe, timeout, non-zero exit) is a failure:
    the caller deletes the new folder and keeps the previous version.
    """
    version_dir = executable.parent
    result_path = version_dir / "result.json"
    result_path.unlink(missing_ok=True)
    env = os.environ.copy()
    env["VISION_LAUNCHER_ROOT"] = str(root)
    env["VISION_LAUNCHER_VERSION"] = LAUNCHER_VERSION
    try:
        completed = subprocess.run(
            [str(executable), "--selftest"],
            cwd=str(version_dir),
            env=env,
            check=False,
            timeout=SELFTEST_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    payload = read_json(result_path)
    return not (payload and payload.get("ok") is False)


def show_notice(root: Path, message: str) -> None:
    try:
        (root / "rollback-notice.txt").write_text(message + "\n", encoding="utf-8")
    except OSError:
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "VISION update rolled back", 0x40)
            return
        except Exception:
            pass
    try:
        print(message, file=sys.stderr)
    except Exception:
        # A windowed PyInstaller launcher may have no stderr stream.  The
        # rollback-notice.txt file above is the durable fallback.
        pass


def run_launcher(
    root: Path,
    args: list[str] | None = None,
    *,
    runner=run_child,
    notice=show_notice,
    selftest_runner=run_selftest,
) -> int:
    args = list(args or [])
    root = Path(root)
    current, previous = initial_selection(root)
    write_current(root, current, previous)
    state_path = root / "launcher-state.json"
    state = read_json(state_path)
    crashes = state.get("crashes") if isinstance(state.get("crashes"), dict) else {}
    last_code = 1

    for _launch in range(MAX_LAUNCHES):
        executable = version_exe(root, current)
        last_code, duration = runner(executable, args, root)

        # The child has exited, so consuming pending.json is now safe.
        applied = consume_pending(root, current, previous)
        if applied is not None:
            new_current, new_previous = applied
            # Gate 4: the new build must pass --selftest before its first
            # real launch.  On failure the folder is deleted, the previous
            # version stays selected, and the failure is recorded once.
            if not selftest_runner(version_exe(root, new_current), root, new_current):
                failed_version = new_current
                current, previous = new_previous, previous if previous != new_current else ""
                write_current(root, current, previous)
                shutil.rmtree(root / "versions" / failed_version, ignore_errors=True)
                state["last_selftest_failure"] = {"version": failed_version, "at": time.time()}
                if state.get("selftest_notice_for") != failed_version:
                    state["selftest_notice_for"] = failed_version
                    notice(
                        root,
                        f"VISION {failed_version} did not pass its startup check. "
                        f"VISION kept version {current}.",
                    )
                atomic_write_json(state_path, state)
                continue
            current, previous = applied
            continue

        quick_failure = last_code != 0 and duration < CRASH_WINDOW_SECONDS
        if not quick_failure:
            crashes.pop(current, None)
            state["crashes"] = crashes
            atomic_write_json(state_path, state)
            prune_versions(root, current, previous)
            return last_code

        count = int(crashes.get(current, 0) or 0) + 1
        crashes[current] = count
        state["crashes"] = crashes
        atomic_write_json(state_path, state)
        if count < 2:
            continue  # One retry distinguishes a transient exit from a loop.

        if previous and previous != current and version_exe(root, previous).is_file():
            bad_version = current
            current, previous = previous, bad_version
            write_current(root, current, previous)
            crashes.pop(bad_version, None)
            state["crashes"] = crashes
            if state.get("rollback_notice_for") != bad_version:
                state["rollback_notice_for"] = bad_version
                notice(
                    root,
                    f"VISION {bad_version} closed unexpectedly twice. "
                    f"VISION has rolled back to {current}.",
                )
            atomic_write_json(state_path, state)
            continue
        return last_code
    return last_code


def launcher_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = launcher_root()
    if len(args) >= 2 and args[0] == "--vision-launcher-root":
        root = Path(args[1]).expanduser().resolve()
        args = args[2:]
    if args and args[0] in {"--launcher-version", "--version"}:
        print(LAUNCHER_VERSION)
        return 0
    try:
        return run_launcher(root, args)
    except OSError as exc:
        show_notice(root, f"VISION could not start: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

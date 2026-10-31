from pathlib import Path

import pytest

from vision_app.tools.sandbox import PathSandbox, has_parent_reference, is_system_path


def test_relative_paths_resolve_inside_workspace(tmp_path):
    sandbox = PathSandbox(tmp_path)
    assert sandbox.resolve("notes/a.txt") == (tmp_path / "notes/a.txt").resolve()


def test_parent_escape_requires_confirmation(tmp_path):
    sandbox = PathSandbox(tmp_path / "ws")
    resolved = sandbox.resolve("../outside.txt")
    assert has_parent_reference("../outside.txt")
    assert sandbox.needs_confirmation("../outside.txt", resolved, action="write")


def test_system_paths_detected_and_write_blocked(tmp_path):
    sandbox = PathSandbox(tmp_path)
    assert is_system_path(r"C:\Windows\System32\config")
    assert is_system_path("/etc/passwd")
    with pytest.raises(PermissionError):
        sandbox.validate_write(Path(r"C:\Windows\file.txt"))


def test_executable_open_requires_confirmation(tmp_path):
    sandbox = PathSandbox(tmp_path)
    resolved = sandbox.resolve("tool.exe")
    assert sandbox.needs_confirmation("tool.exe", resolved, action="open")


def test_every_model_open_requires_confirmation_even_for_plain_text(tmp_path):
    sandbox = PathSandbox(tmp_path)
    resolved = sandbox.resolve("notes.txt")
    assert sandbox.needs_confirmation("notes.txt", resolved, action="open")


def test_shell_actionable_open_suffixes_require_confirmation(tmp_path):
    sandbox = PathSandbox(tmp_path)
    for name in (
        "payload.hta",
        "payload.jse",
        "payload.lnk",
        "payload.msc",
        "payload.reg",
        "payload.wsf",
        "macro.docm",
    ):
        resolved = sandbox.resolve(name)
        assert sandbox.needs_confirmation(name, resolved, action="open"), name

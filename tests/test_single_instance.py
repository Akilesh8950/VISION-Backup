from __future__ import annotations

from vision_app.paths import AppPaths
from vision_app.single_instance import SingleInstanceLock, single_instance_lock_for


def test_single_instance_lock_is_exclusive_and_reusable(tmp_path):
    path = tmp_path / "vision.lock"
    first = SingleInstanceLock(path)
    second = SingleInstanceLock(path)

    assert first.acquire() is True
    assert second.acquire() is False

    first.release()
    assert second.acquire() is True
    second.release()


def test_single_instance_lock_uses_app_data(tmp_path):
    paths = AppPaths(app_data=tmp_path / "data", workspace=tmp_path / "workspace")
    lock = single_instance_lock_for(paths)
    assert lock.lock_path == paths.app_data / "vision.lock"

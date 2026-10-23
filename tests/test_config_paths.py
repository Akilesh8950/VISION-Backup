from pathlib import Path

from vision_app.config import AppConfig
from vision_app.paths import AppPaths


def test_config_round_trip(tmp_path):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    config = AppConfig.load(paths)
    config.local_port = 9090
    config.save(paths)
    loaded = AppConfig.load(paths)
    assert loaded.local_port == 9090
    assert Path(loaded.model_path).name.endswith(".gguf")

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


def test_tts_and_motion_preferences_default_to_soothing(tmp_path):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    config = AppConfig.load(paths)  # old/absent config files get calm defaults
    assert config.tts_voice_uri == ""
    assert config.tts_voice_name == ""
    assert config.tts_style == "soothing"
    assert config.tts_rate == 0.92
    assert config.tts_pitch == 0.95
    assert config.tts_volume == 1.0
    assert config.tts_auto_read is False
    assert config.reduce_motion is False


def test_tts_preferences_clamp_instead_of_raising():
    # Foreign/stale values must never make the whole config unloadable.
    config = AppConfig.model_validate(
        {
            "tts_style": "opera",
            "tts_rate": 99,
            "tts_pitch": -3,
            "tts_volume": 7,
            "tts_voice_uri": "  " + "x" * 400,
        }
    )
    assert config.tts_style == "soothing"
    assert config.tts_rate == 1.5
    assert config.tts_pitch == 0.5
    assert config.tts_volume == 1.0
    assert len(config.tts_voice_uri) == 300

    junk = AppConfig.model_validate({"tts_rate": "loud", "tts_pitch": True, "tts_volume": None})
    assert junk.tts_rate == 0.92
    assert junk.tts_pitch == 0.95
    assert junk.tts_volume == 1.0


def test_tts_preferences_round_trip(tmp_path):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "ws")
    config = AppConfig.load(paths)
    config.tts_voice_uri = "Microsoft Aria Online (Natural)"
    config.tts_voice_name = "Microsoft Aria"
    config.tts_style = "custom"
    config.tts_rate = 0.85
    config.tts_pitch = 0.9
    config.tts_volume = 0.8
    config.tts_auto_read = True
    config.reduce_motion = True
    config.save(paths)
    loaded = AppConfig.load(paths)
    assert loaded.tts_voice_uri == "Microsoft Aria Online (Natural)"
    assert loaded.tts_style == "custom"
    assert loaded.tts_rate == 0.85
    assert loaded.tts_auto_read is True
    assert loaded.reduce_motion is True

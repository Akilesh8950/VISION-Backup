"""Performance profiles: hardware detection and server tuning.

Profiles are resolved from injected HardwareInfo so every assertion is
deterministic regardless of the machine running the tests.
"""

from __future__ import annotations

from vision_app.config import AppConfig
from vision_app.server.hardware import (
    HardwareInfo,
    effective_profile,
    recommended_profile,
    resolve_perf,
)
from vision_app.server.manager import build_server_command

WEAK_I3 = HardwareInfo(logical_cores=4, physical_cores=2, total_ram_gb=8.0)
TINY_RAM = HardwareInfo(logical_cores=4, physical_cores=2, total_ram_gb=4.0)
STRONG = HardwareInfo(logical_cores=16, physical_cores=8, total_ram_gb=32.0)
MID = HardwareInfo(logical_cores=8, physical_cores=4, total_ram_gb=16.0)
LOW_VRAM = HardwareInfo(
    logical_cores=16, physical_cores=8, total_ram_gb=32.0, gpu_vram_gb=4.0
)
HIGH_VRAM = HardwareInfo(
    logical_cores=16, physical_cores=8, total_ram_gb=32.0, gpu_vram_gb=12.0
)


def _value_after(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def test_recommended_profile_thresholds() -> None:
    assert recommended_profile(WEAK_I3) == "low_end"
    assert recommended_profile(TINY_RAM) == "low_end"
    assert recommended_profile(STRONG) == "balanced"
    assert recommended_profile(MID) == "balanced"
    # Unknown hardware (zeros) must not force low-end.
    assert recommended_profile(HardwareInfo()) == "balanced"
    assert recommended_profile(LOW_VRAM) == "low_end"
    assert recommended_profile(HIGH_VRAM) == "balanced"
    assert LOW_VRAM.as_dict()["gpu_vram_gb"] == 4.0
    # Explicit balanced remains an explicit user choice.
    assert resolve_perf("balanced", 16384, 99, LOW_VRAM).gpu_layers == 99


def test_auto_never_picks_performance() -> None:
    assert effective_profile("auto", STRONG) == "balanced"
    assert effective_profile("auto", WEAK_I3) == "low_end"
    assert effective_profile("performance", WEAK_I3) == "performance"
    assert effective_profile("nonsense", MID) == "balanced"


def test_balanced_profile_adds_no_flags() -> None:
    perf = resolve_perf("balanced", 16384, 99, STRONG)
    assert perf.context_size == 16384
    assert perf.gpu_layers == 99
    assert perf.extra_args() == []


def test_low_end_profile_fits_small_cpu_pc() -> None:
    perf = resolve_perf("low_end", 16384, 99, WEAK_I3)
    assert perf.context_size == 8192
    assert perf.gpu_layers == 0
    assert perf.threads == 2  # physical cores, not hyperthreads
    assert perf.cache_type_k == "q8_0"
    assert perf.cache_type_v == "q8_0"
    assert perf.parallel == 1


def test_low_end_tiny_ram_gets_4k_context() -> None:
    perf = resolve_perf("low_end", 16384, 99, TINY_RAM)
    assert perf.context_size == 4096


def test_low_end_never_grows_a_small_configured_context() -> None:
    perf = resolve_perf("low_end", 4096, 0, WEAK_I3)
    assert perf.context_size == 4096


def test_performance_profile_keeps_context_and_gpu() -> None:
    perf = resolve_perf("performance", 32768, 99, STRONG)
    assert perf.context_size == 32768
    assert perf.gpu_layers == 99
    assert perf.threads == 8
    assert perf.threads_batch == 16
    assert perf.batch == 1024


def test_build_command_low_end_on_weak_machine() -> None:
    config = AppConfig(local_port=8080, context_size=16384, gpu_layers=99, performance_profile="auto")
    command = build_server_command("llama-server.exe", "model.gguf", config, hardware=WEAK_I3)
    assert _value_after(command, "-c") == "8192"
    assert _value_after(command, "-ngl") == "0"
    assert _value_after(command, "-t") == "2"
    assert _value_after(command, "--cache-type-k") == "q8_0"
    assert "--jinja" in command
    assert _value_after(command, "--flash-attn") == "on"


def test_build_command_auto_on_strong_machine_matches_frozen_shape() -> None:
    config = AppConfig(local_port=8080, context_size=16384, gpu_layers=99, performance_profile="auto")
    command = build_server_command("llama-server.exe", "model.gguf", config, hardware=STRONG)
    assert command == [
        "llama-server.exe", "-m", "model.gguf", "--host", "127.0.0.1", "--port", "8080",
        "-c", "16384", "-ngl", "99", "--jinja", "--flash-attn", "on",
    ]


def test_config_profile_validation_is_forgiving() -> None:
    assert AppConfig(performance_profile="LOW-END").performance_profile == "low_end"
    assert AppConfig(performance_profile="cpu").performance_profile == "low_end"
    assert AppConfig(performance_profile="bogus").performance_profile == "auto"
    assert AppConfig(performance_profile=None).performance_profile == "auto"  # type: ignore[arg-type]

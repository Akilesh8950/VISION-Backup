"""Hardware detection and llama-server performance profiles.

One place decides how the local model server is tuned for the machine it
runs on.  Four profiles exist:

- ``auto``        - detect the hardware and pick ``low_end`` on clearly
                    constrained PCs, otherwise ``balanced``.  Auto never
                    picks ``performance``: it protects weak machines
                    without changing behaviour on strong ones.
- ``balanced``    - the exact default engine invocation VISION has
                    always shipped (no tuning flags).  Zero regression.
- ``performance`` - opt-in tuning for strong PCs: physical-core thread
                    counts and larger prompt batches.
- ``low_end``     - CPU-only PCs (e.g. an Intel i3 with 8 GB RAM):
                    smaller context, quantized KV cache, physical-core
                    threads, small batches, single slot.  The goal is
                    "fits in RAM and stays responsive", not speed -
                    a 9B model on a small CPU is usable, never fast.

Everything here is pure and injectable (pass a ``HardwareInfo``) so tests
are deterministic on any machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

PROFILES = ("auto", "balanced", "performance", "low_end")


@dataclass(frozen=True)
class HardwareInfo:
    logical_cores: int = 0
    physical_cores: int = 0
    total_ram_gb: float = 0.0
    gpu_vram_gb: float = 0.0
    gpu_free_vram_gb: float = 0.0
    gpu_name: str = ""

    def as_dict(self) -> dict[str, float | int | str]:
        return {
            "logical_cores": int(self.logical_cores or 0),
            "physical_cores": int(self.physical_cores or 0),
            "total_ram_gb": round(float(self.total_ram_gb or 0.0), 1),
            "gpu_vram_gb": round(float(self.gpu_vram_gb or 0.0), 1),
            "gpu_free_vram_gb": round(float(self.gpu_free_vram_gb or 0.0), 1),
            "gpu_name": str(self.gpu_name or ""),
        }


_cached_hardware: HardwareInfo | None = None


def _detect_nvidia_vram() -> tuple[float, float, str]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return 0.0, 0.0, ""
    try:
        completed = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0, 0.0, ""
    if completed.returncode != 0:
        return 0.0, 0.0, ""
    best: tuple[float, float, str] = (0.0, 0.0, "")
    for line in str(completed.stdout or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            total_mb = float(parts[-2])
            free_mb = float(parts[-1])
        except ValueError:
            continue
        candidate = (total_mb / 1024.0, free_mb / 1024.0, ", ".join(parts[:-2]))
        if candidate[0] > best[0]:
            best = candidate
    return best


def detect_hardware() -> HardwareInfo:
    """Best-effort hardware probe, cached after the first call.

    Never raises; unknowns stay 0.  Hardware does not change while the
    app runs, and this is called on the chat path (context budget), so
    the psutil probe runs once per process.
    """

    global _cached_hardware
    if _cached_hardware is not None:
        return _cached_hardware
    logical = 0
    physical = 0
    ram_gb = 0.0
    try:
        import psutil  # type: ignore

        logical = int(psutil.cpu_count(logical=True) or 0)
        physical = int(psutil.cpu_count(logical=False) or 0)
        ram_gb = float(psutil.virtual_memory().total) / float(1024**3)
    except Exception:
        logical = int(os.cpu_count() or 0)
    if logical <= 0:
        logical = int(os.cpu_count() or 1)
    if physical <= 0 or physical > logical:
        physical = max(1, logical // 2) if logical > 1 else logical
    gpu_vram_gb, gpu_free_vram_gb, gpu_name = _detect_nvidia_vram()
    _cached_hardware = HardwareInfo(
        logical_cores=logical,
        physical_cores=physical,
        total_ram_gb=ram_gb,
        gpu_vram_gb=gpu_vram_gb,
        gpu_free_vram_gb=gpu_free_vram_gb,
        gpu_name=gpu_name,
    )
    return _cached_hardware


def _physical(hw: HardwareInfo) -> int:
    if hw.physical_cores > 0:
        return hw.physical_cores
    logical = hw.logical_cores or 1
    return max(1, logical // 2) if logical > 1 else 1


def recommended_profile(hw: HardwareInfo) -> str:
    """Profile this hardware should use.  Only ever low_end/balanced."""

    constrained_ram = 0.0 < hw.total_ram_gb < 10.0
    constrained_cpu = 0 < hw.logical_cores <= 4
    # The Q4 weights alone are about 5.24 GiB.  Full GPU offload also needs
    # KV cache and compute buffers; a known-small discrete GPU is safer in
    # the CPU low-end profile under Auto.  Explicit profiles still win.
    constrained_vram = 0.0 < hw.gpu_vram_gb < 7.5
    return "low_end" if (constrained_ram or constrained_cpu or constrained_vram) else "balanced"


def effective_profile(profile: str, hw: HardwareInfo) -> str:
    normalized = str(profile or "auto").strip().lower()
    if normalized in {"balanced", "performance", "low_end"}:
        return normalized
    return recommended_profile(hw)


@dataclass(frozen=True)
class ServerPerf:
    profile: str  # effective profile, never "auto"
    context_size: int
    gpu_layers: int
    threads: int | None = None
    threads_batch: int | None = None
    batch: int | None = None
    ubatch: int | None = None
    cache_type_k: str | None = None
    cache_type_v: str | None = None
    parallel: int | None = None

    def extra_args(self) -> list[str]:
        """Additional llama-server flags after the fixed base command."""

        args: list[str] = []
        if self.threads:
            args += ["-t", str(self.threads)]
        if self.threads_batch:
            args += ["--threads-batch", str(self.threads_batch)]
        if self.batch:
            args += ["-b", str(self.batch)]
        if self.ubatch:
            args += ["-ub", str(self.ubatch)]
        if self.cache_type_k:
            args += ["--cache-type-k", self.cache_type_k]
        if self.cache_type_v:
            args += ["--cache-type-v", self.cache_type_v]
        if self.parallel:
            args += ["-np", str(self.parallel)]
        return args


def resolve_perf(
    profile: str,
    context_size: int,
    gpu_layers: int,
    hw: HardwareInfo | None = None,
) -> ServerPerf:
    hardware = hw if hw is not None else detect_hardware()
    effective = effective_profile(profile, hardware)
    context = max(1024, int(context_size or 16384))
    layers = max(0, int(gpu_layers or 0))

    if effective == "performance":
        physical = _physical(hardware)
        logical = hardware.logical_cores or physical
        return ServerPerf(
            profile=effective,
            context_size=context,
            gpu_layers=layers,
            threads=physical,
            threads_batch=max(physical, logical),
            batch=1024,
            ubatch=512,
        )
    if effective == "low_end":
        # KV cache is the memory that grows with context: cap the
        # context and quantize the cache (q8_0 roughly halves it) so an
        # 8 GB machine holds weights + cache without swapping.  Very
        # small RAM gets a 4k context for the same reason.
        context_cap = 4096 if 0.0 < hardware.total_ram_gb < 7.0 else 8192
        physical = _physical(hardware)
        return ServerPerf(
            profile=effective,
            context_size=min(context, context_cap),
            gpu_layers=0,
            threads=physical,
            threads_batch=physical,
            batch=256,
            ubatch=128,
            cache_type_k="q8_0",
            cache_type_v="q8_0",
            parallel=1,
        )
    return ServerPerf(profile="balanced", context_size=context, gpu_layers=layers)

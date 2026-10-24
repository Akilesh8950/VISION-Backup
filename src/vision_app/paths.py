from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .constants import GGUF_FILENAME

PORTABLE_MARKER_FILENAME = "VISION.portable"


@dataclass(frozen=True)
class AppPaths:
    app_data: Path
    workspace: Path
    installation_mode: str = "standard"
    installation_root: Path | None = None

    @property
    def is_portable(self) -> bool:
        return self.installation_mode == "portable"

    @property
    def config_file(self) -> Path:
        return self.app_data / "config.json"

    @property
    def history_db(self) -> Path:
        return self.app_data / "history.sqlite3"

    @property
    def logs_dir(self) -> Path:
        return self.app_data / "logs"

    @property
    def audit_log(self) -> Path:
        return self.logs_dir / "audit.jsonl"

    @property
    def app_log(self) -> Path:
        return self.logs_dir / "vision.log"

    @property
    def models_dir(self) -> Path:
        return self.app_data / "models"

    @property
    def model_file(self) -> Path:
        return self.models_dir / GGUF_FILENAME

    @property
    def bin_dir(self) -> Path:
        return self.app_data / "bin"

    @property
    def telemetry_dir(self) -> Path:
        return self.app_data / "telemetry"

    @property
    def needs_log(self) -> Path:
        return self.telemetry_dir / "needs.jsonl"

    @property
    def extensions_dir(self) -> Path:
        return self.app_data / "extensions"

    @property
    def skills_dir(self) -> Path:
        return self.app_data / "skills"

    @property
    def checkpoints_dir(self) -> Path:
        return self.app_data / "checkpoints"

    @property
    def mcp_config(self) -> Path:
        return self.extensions_dir / "mcp_servers.json"

    @property
    def connectors_config(self) -> Path:
        return self.extensions_dir / "connectors.json"

    def ensure(self) -> None:
        for directory in (
            self.app_data,
            self.workspace,
            self.logs_dir,
            self.models_dir,
            self.bin_dir,
            self.telemetry_dir,
            self.extensions_dir,
            self.skills_dir,
            self.checkpoints_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def portable_install_root() -> Path | None:
    """Return the portable root when running a frozen portable install.

    The Windows installer writes ``VISION.portable`` beside ``VISION.exe``
    only for portable mode. Source/development launches deliberately ignore
    the marker so tests and local runs keep their normal OS data locations.
    """

    if not getattr(sys, "frozen", False):
        return None
    exe_dir = Path(sys.executable).resolve().parent
    if (exe_dir / PORTABLE_MARKER_FILENAME).is_file():
        return exe_dir
    return None


def get_paths() -> AppPaths:
    frozen_root = (
        Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
    )
    override = os.environ.get("VISION_HOME")
    if override:
        root = Path(override).expanduser()
        return AppPaths(
            app_data=root,
            workspace=root / "workspace",
            installation_mode="custom",
            installation_root=frozen_root,
        )

    portable_root = portable_install_root()
    if portable_root is not None:
        return AppPaths(
            app_data=portable_root / "data",
            workspace=portable_root / "workspace",
            installation_mode="portable",
            installation_root=portable_root,
        )

    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData/Local")
        profile = Path(os.environ.get("USERPROFILE") or home)
        return AppPaths(
            app_data=local / "VISION",
            workspace=profile / "VISION/workspace",
            installation_root=frozen_root,
        )

    data_home = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
    return AppPaths(
        app_data=data_home / "VISION",
        workspace=home / "VISION/workspace",
        installation_root=frozen_root,
    )

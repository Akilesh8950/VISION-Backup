from __future__ import annotations

import re
import sys
from pathlib import Path

from vision_app import paths as paths_module

ROOT = Path(__file__).resolve().parents[1]


def _freeze_at(monkeypatch, install_dir: Path, *, marker: bool = True) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    exe = install_dir / "VISION.exe"
    exe.write_bytes(b"stub")
    if marker:
        (install_dir / paths_module.PORTABLE_MARKER_FILENAME).write_text(
            "portable", encoding="utf-8"
        )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.delenv("VISION_HOME", raising=False)


def test_frozen_portable_marker_keeps_data_and_workspace_beside_exe(
    tmp_path: Path, monkeypatch
) -> None:
    install_dir = tmp_path / "D_VISION"
    _freeze_at(monkeypatch, install_dir)

    assert paths_module.portable_install_root() == install_dir.resolve()
    paths = paths_module.get_paths()
    assert paths.app_data == install_dir.resolve() / "data"
    assert paths.workspace == install_dir.resolve() / "workspace"
    assert paths.models_dir == install_dir.resolve() / "data" / "models"
    assert paths.bin_dir == install_dir.resolve() / "data" / "bin"
    assert paths.installation_mode == "portable"
    assert paths.installation_root == install_dir.resolve()
    assert paths.is_portable is True


def test_frozen_versioned_launcher_portable_root_is_outer_folder(
    tmp_path: Path, monkeypatch
) -> None:
    outer = tmp_path / "VISION"
    version_dir = outer / "versions" / "1.0.0"
    version_dir.mkdir(parents=True)
    exe = version_dir / "VISION.exe"
    exe.write_bytes(b"app")
    (outer / "VISION.exe").write_bytes(b"launcher")
    (outer / paths_module.PORTABLE_MARKER_FILENAME).write_text("portable", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.delenv("VISION_HOME", raising=False)

    paths = paths_module.get_paths()

    assert paths_module.portable_install_root() == outer.resolve()
    assert paths.installation_root == outer.resolve()
    assert paths.app_data == outer.resolve() / "data"
    assert paths.workspace == outer.resolve() / "workspace"
    assert paths.updates_dir == outer.resolve() / "data" / "updates"


def test_vision_home_still_overrides_portable_marker(tmp_path: Path, monkeypatch) -> None:
    install_dir = tmp_path / "portable"
    _freeze_at(monkeypatch, install_dir)
    override = tmp_path / "override"
    monkeypatch.setenv("VISION_HOME", str(override))

    paths = paths_module.get_paths()
    assert paths.app_data == override
    assert paths.workspace == override / "workspace"
    assert paths.installation_mode == "custom"
    assert paths.installation_root == install_dir.resolve()
    assert paths.is_portable is False


def test_portable_marker_is_ignored_without_frozen_executable(
    tmp_path: Path, monkeypatch
) -> None:
    install_dir = tmp_path / "dev"
    _freeze_at(monkeypatch, install_dir)
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    assert paths_module.portable_install_root() is None


def test_frozen_standard_install_without_marker_is_not_portable(
    tmp_path: Path, monkeypatch
) -> None:
    install_dir = tmp_path / "standard"
    _freeze_at(monkeypatch, install_dir, marker=False)

    assert paths_module.portable_install_root() is None


def test_inno_setup_offers_scope_portable_mode_and_optimized_compression() -> None:
    script_path = ROOT / "installer" / "VISION.iss"
    script = script_path.read_text(encoding="utf-8-sig")

    assert script_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "AppId={{4053D7ED-FBE7-442F-9624-9ACCA45DA82C}" in script
    assert "ArchitecturesAllowed=x64compatible" in script
    assert "Compression=lzma2/ultra64" in script
    assert "SolidCompression=yes" in script
    assert "PrivilegesRequiredOverridesAllowed=commandline dialog" in script
    assert "CreateInputOptionPage" in script
    assert "IsAdminInstallMode" in script
    assert "VISION.portable" in script
    assert "UsePortableData" in script
    assert "UseStandardData" in script
    assert '{app}\\data' in script
    assert '{app}\\workspace' in script
    assert 'Excludes: "data\\*,workspace\\*,*.gguf' in script
    assert "dist\\installer-payload" in script
    assert "CurStepChanged" in script
    assert "ReadExistingCurrentVersion" in script
    assert 'Name: "{app}\\versions"' in script
    assert 'Name: "{app}\\downloads"' in script
    assert 'Type: filesandordirs; Name: "{app}\\versions"' in script
    assert "GetSpaceOnDisk64" in script
    assert "F3017226-FE2A-4295-8BDF-00C3A9A7E4C5" in script
    assert "runasoriginaluser" in script


def test_installer_build_wrapper_produces_checksums_and_refuses_dirty_dist() -> None:
    script = (ROOT / "scripts" / "build_installer.ps1").read_text(encoding="utf-8")

    assert "build.ps1" in script
    assert "JRSoftware.InnoSetup" in script
    assert "ISCC.exe" in script
    assert "Get-FileHash" in script
    assert "*.gguf" in script
    assert "VISION.portable" in script
    assert "Compress-Archive" in script
    assert "launcher_stub.py" in script
    assert 'versions\\$Version' in script
    assert '"current.json"' in script
    assert '/DDistDir=$PayloadDir' in script
    assert "VISION-Setup-$Version-x64.exe" in script


def test_portable_marker_template_documents_single_folder_layout() -> None:
    marker = (ROOT / "installer" / "VISION.portable").read_text(encoding="utf-8")

    assert "VISION — built by SPIRIT ORG" in marker
    assert ".\\data" in marker
    assert ".\\workspace" in marker
    assert "Windows Credential Manager" in marker


def test_installer_default_version_matches_app_and_windows_version_resource() -> None:
    init = (ROOT / "src" / "vision_app" / "__init__.py").read_text(encoding="utf-8")
    app_version = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)

    installer = (ROOT / "installer" / "VISION.iss").read_text(encoding="utf-8-sig")
    installer_version = re.search(r'#define AppVersion\s+"([^"]+)"', installer).group(1)

    version_info = (ROOT / "assets" / "version_info.txt").read_text(encoding="utf-8")
    file_version = re.search(r"StringStruct\('FileVersion',\s*'([^']+)'\)", version_info).group(1)

    assert installer_version == app_version
    assert file_version == app_version


def test_installation_tutorial_covers_recommended_flow_and_lifecycle() -> None:
    guide = (ROOT / "docs" / "INSTALLATION.md").read_text(encoding="utf-8")

    for expected in [
        "Install for current user only",
        "Portable folder (recommended)",
        "D:\\VISION",
        "12 GiB",
        "1. Download model",
        "2. Download llama-server",
        ".\\scripts\\build_installer.ps1",
        "Get-FileHash",
        "Moving or backing up a portable installation",
        "Updating",
        "Uninstalling",
        "Windows Credential Manager",
        "WebView2",
    ]:
        assert expected in guide

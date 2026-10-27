from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .skills import MAX_SKILL_MD_BYTES, SkillPackage, parse_skill_dir, parse_skill_text

MAX_ARCHIVE_BYTES = 30 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_FILES = 500
RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


@dataclass
class SkillImportPreview:
    package: SkillPackage
    staging_root: Path
    warnings: list[str]


def _is_zip(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(2) == b"PK"
    except OSError:
        return False


def _extract_archive(archive: Path, destination: Path) -> Path:
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Skill archive exceeds the 30 MB limit")
    if not zipfile.is_zipfile(archive):
        raise ValueError("Skill package is not a valid ZIP archive")
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    total = 0
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise ValueError("Skill archive contains too many files")
        seen: set[str] = set()
        for info in infos:
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Skill archive expands beyond the 100 MB limit")
            normalized = info.filename.replace("\\", "/")
            if normalized.startswith("/") or ":" in normalized.split("/")[0]:
                raise ValueError(f"Unsafe absolute path in skill archive: {info.filename}")
            parts = [part for part in normalized.split("/") if part not in ("", ".")]
            if any(part == ".." for part in parts):
                raise ValueError(f"Unsafe parent path in skill archive: {info.filename}")
            if any(Path(part).stem.lower() in RESERVED_NAMES for part in parts):
                raise ValueError(f"Reserved Windows filename in skill archive: {info.filename}")
            if ((getattr(info, "external_attr", 0) >> 16) & 0o170000) == 0o120000:
                raise ValueError("Symlinks are not allowed in skill archives")
            key = "/".join(parts).lower()
            if key in seen:
                raise ValueError(f"Duplicate path in skill archive: {info.filename}")
            seen.add(key)
            target = (destination / Path(*parts)).resolve() if parts else root
            if target != root and root not in target.parents:
                raise ValueError(f"Unsafe path in skill archive: {info.filename}")
        actual_total = 0
        for info in infos:
            normalized = info.filename.replace("\\", "/")
            parts = [part for part in normalized.split("/") if part not in ("", ".")]
            target = (destination / Path(*parts)).resolve() if parts else root
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    actual_total += len(chunk)
                    if actual_total > MAX_UNCOMPRESSED_BYTES:
                        raise ValueError("Skill archive expands beyond the 100 MB limit")
                    output.write(chunk)
    return destination


def _locate_skill_root(extracted: Path) -> Path:
    if (extracted / "SKILL.md").is_file():
        return extracted
    children = [child for child in extracted.iterdir() if child.is_dir()]
    candidates = [child for child in children if (child / "SKILL.md").is_file()]
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("Archive must contain SKILL.md at its root or in exactly one top-level skill folder")


def _convert_plain_markdown(text: str, name: str, description: str) -> str:
    if not name or not description:
        raise ValueError("A plain Markdown file needs a skill name and description before it can be converted")
    safe_description = " ".join(description.split())
    return f"---\nname: {name}\ndescription: {safe_description}\n---\n\n{text.lstrip()}"


def _validate_source_tree(source: Path) -> None:
    file_count = 0
    total_bytes = 0
    for current, dirs, names in os.walk(source):
        current_path = Path(current)
        for directory in dirs:
            if (current_path / directory).is_symlink():
                raise ValueError("Symlinks are not allowed in skill folder imports")
        for name in names:
            candidate = current_path / name
            if candidate.is_symlink():
                raise ValueError("Symlinks are not allowed in skill folder imports")
            file_count += 1
            if file_count > MAX_FILES:
                raise ValueError("Skill folder contains too many files")
            total_bytes += candidate.stat().st_size
            if total_bytes > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Skill folder exceeds the 100 MB limit")


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def preview_import(
    source: Path,
    staging_parent: Path,
    *,
    plain_name: str = "",
    plain_description: str = "",
) -> SkillImportPreview:
    source = source.expanduser()
    if not source.exists():
        raise FileNotFoundError(source)
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".skill-staging-", dir=str(staging_parent)))
    warnings: list[str] = []
    try:
        if source.is_dir():
            _validate_source_tree(source)
            skill_root = staging / "package"
            shutil.copytree(source, skill_root)
            package = parse_skill_dir(skill_root)
        elif source.suffix.lower() == ".md":
            if source.stat().st_size > MAX_SKILL_MD_BYTES:
                raise ValueError("Skill Markdown file exceeds the 2 MB limit")
            text = source.read_text(encoding="utf-8")
            try:
                package = parse_skill_text(text, staging)
            except Exception:
                text = _convert_plain_markdown(text, plain_name, plain_description)
                package = parse_skill_text(text, staging)
                warnings.append("Converted plain Markdown into a canonical SKILL.md package")
            skill_root = staging / package.name
            skill_root.mkdir()
            (skill_root / "SKILL.md").write_text(text, encoding="utf-8")
            package = parse_skill_dir(skill_root)
        elif source.suffix.lower() in {".zip", ".skill"}:
            if source.suffix.lower() == ".skill" and not _is_zip(source):
                raise ValueError(".skill file is not a ZIP container; its actual format is unsupported")
            extracted = _extract_archive(source, staging / "extracted")
            located = _locate_skill_root(extracted)
            package = parse_skill_dir(located)
            skill_root = staging / package.name
            shutil.copytree(located, skill_root)
            package = parse_skill_dir(skill_root)
        else:
            raise ValueError("Supported skill imports are a folder, .md, .zip, or .skill file")

        if package.scripts:
            warnings.append(f"Package contains {len(package.scripts)} script(s); none were executed")
        for relative in package.files:
            path = package.root / relative
            if path.suffix.lower() in {".exe", ".bat", ".cmd", ".ps1", ".sh"}:
                warnings.append(f"Executable file will not be runnable in VISION v1: {relative}")
        return SkillImportPreview(package=package, staging_root=staging, warnings=warnings)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def install_preview(preview: SkillImportPreview, skills_root: Path, *, overwrite: bool = False) -> Path:
    skills_root.mkdir(parents=True, exist_ok=True)
    destination = skills_root / preview.package.name
    destination_exists = destination.exists() or destination.is_symlink()
    if destination_exists and not overwrite:
        raise FileExistsError(f"Skill already installed: {preview.package.name}")
    if destination_exists:
        backup = skills_root / f".backup-{preview.package.name}"
        if backup.exists() or backup.is_symlink():
            _remove_path(backup)
        destination.replace(backup)
        try:
            shutil.copytree(preview.package.root, destination)
        except Exception:
            if destination.exists() or destination.is_symlink():
                _remove_path(destination)
            backup.replace(destination)
            raise
        _remove_path(backup)
    else:
        shutil.copytree(preview.package.root, destination)
    parse_skill_dir(destination)
    return destination


def import_skill(
    source: Path,
    skills_root: Path,
    *,
    overwrite: bool = False,
    plain_name: str = "",
    plain_description: str = "",
) -> Path:
    preview = preview_import(source, skills_root, plain_name=plain_name, plain_description=plain_description)
    try:
        return install_preview(preview, skills_root, overwrite=overwrite)
    finally:
        shutil.rmtree(preview.staging_root, ignore_errors=True)


def export_skill(skill_root: Path, destination: Path) -> Path:
    package = parse_skill_dir(skill_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for relative in package.files:
            zf.write(skill_root / relative, f"{package.name}/{relative}")
    return destination

import zipfile
from pathlib import Path

import pytest

from vision_app.extensions.skill_import import export_skill, import_skill
from vision_app.extensions.skills import SkillManager

SKILL = """---
name: demo-skill
description: Demonstrates a portable workflow. Use for demo tasks.
---

# Demo
Follow the steps.
"""


def write_skill(root: Path) -> Path:
    folder = root / "demo-skill"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL)
    (folder / "references").mkdir()
    (folder / "references/guide.md").write_text("Reference")
    return folder


def test_import_folder_and_zip_and_skill_alias(tmp_path):
    folder = write_skill(tmp_path / "src")
    installed = import_skill(folder, tmp_path / "skills1")
    assert (installed / "SKILL.md").exists()

    archive = tmp_path / "demo.zip"
    export_skill(installed, archive)
    installed2 = import_skill(archive, tmp_path / "skills2")
    assert installed2.name == "demo-skill"

    skill_file = tmp_path / "demo.skill"
    skill_file.write_bytes(archive.read_bytes())
    assert import_skill(skill_file, tmp_path / "skills3").name == "demo-skill"
    assert SkillManager(tmp_path / "skills3").get("demo-skill").files


def test_import_lone_md_and_plain_md_conversion(tmp_path):
    lone = tmp_path / "SKILL.md"
    lone.write_text(SKILL)
    assert import_skill(lone, tmp_path / "skills").name == "demo-skill"

    plain = tmp_path / "notes.md"
    plain.write_text("# Workflow\nDo the thing")
    converted = import_skill(
        plain, tmp_path / "skills2", plain_name="notes-workflow",
        plain_description="Executes the notes workflow. Use for note tasks.",
    )
    assert converted.name == "notes-workflow"


def test_zip_slip_rejected(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../SKILL.md", SKILL)
    with pytest.raises(ValueError):
        import_skill(archive, tmp_path / "skills")


def test_activation_metadata_then_body(tmp_path):
    folder = write_skill(tmp_path / "src")
    root = tmp_path / "skills"
    import_skill(folder, root)
    manager = SkillManager(root)
    assert "demo-skill" in manager.metadata_prompt()
    loaded = manager.load("demo-skill")
    assert "Follow the steps" in loaded["instructions"]

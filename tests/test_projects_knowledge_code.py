from vision_app.code_workspace.checkpoints import CheckpointManager
from vision_app.code_workspace.test_profiles import run_test_profile
from vision_app.knowledge.index import KnowledgeIndex
from vision_app.knowledge.retrieval import build_project_context
from vision_app.projects import ProjectStore
import sys


def test_project_knowledge_and_citations(tmp_path):
    db = tmp_path / "history.sqlite3"
    projects = ProjectStore(db)
    root = tmp_path / "project"
    project_id = projects.create_project("Docs", root, "Answer with citations")
    source = root / "guide.md"
    source.write_text("# Guide\nCoimbatore deployment uses port 8080 and a local model.")
    projects.add_source(project_id, source)
    index = KnowledgeIndex(db)
    assert index.index_file(project_id, source) == 1
    context, sources = build_project_context(index, project_id, "Which port does Coimbatore deployment use?")
    assert "[L1]" in context and "8080" in context
    assert sources[0]["locator"].startswith("lines")


def test_checkpoint_restore_and_user_test_profile(tmp_path):
    target = tmp_path / "code.py"
    target.write_text("old")
    manager = CheckpointManager(tmp_path / "checkpoints")
    checkpoint = manager.snapshot(target)
    target.write_text("new")
    manager.restore(checkpoint)
    assert target.read_text() == "old"

    result = run_test_profile({"program": sys.executable, "args": ["-c", "print('ok')"], "cwd": str(tmp_path)})
    assert result["exit_code"] == 0 and "ok" in result["stdout"]

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from vision_app.app import build_services
from vision_app.genui.factory import build_genui_widget
from vision_app.paths import AppPaths
from vision_app.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_genui_widgets_construct(qapp):
    samples = [
        {"kind": "table", "title": "T", "columns": ["A"], "rows": [["1"]]},
        {"kind": "chart", "title": "Squares", "chart_type": "bar", "labels": ["1", "2", "3", "4"], "values": [1, 4, 9, 16]},
        {"kind": "cards", "title": "Cards", "cards": [{"title": "CPU", "value": "10%"}]},
        {"kind": "timeline", "title": "Time", "events": [{"time": "Now", "title": "Event"}]},
        {"kind": "diff", "filename": "a.py", "before": "x=1", "after": "x=2"},
    ]
    widgets = [build_genui_widget(sample) for sample in samples]
    assert len(widgets) == 5
    for widget in widgets:
        widget.deleteLater()


def test_main_window_constructs(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_HOME", str(tmp_path / "vision-home"))
    paths = AppPaths(app_data=tmp_path / "vision-home", workspace=tmp_path / "vision-home/workspace")
    services = build_services(paths)
    services.config.first_run_completed = True
    window = MainWindow(services)
    assert window.windowTitle() == "VISION"
    assert window.conversations.count() >= 1
    window.close()
    qapp.processEvents()

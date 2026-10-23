from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from ..paths import AppPaths
from ..projects import ProjectStore


class ProjectsDialog(QDialog):
    changed = Signal()
    index_requested = Signal(str)
    run_profile_requested = Signal(str, str)

    def __init__(self, store: ProjectStore, paths: AppPaths, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Projects")
        self.resize(780, 620)
        self.store, self.paths = store, paths
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        for label, fn in (("New…", self._new), ("Rename…", self._rename), ("Archive", self._archive), ("Delete", self._delete)):
            button = QPushButton(label); button.clicked.connect(fn); row.addWidget(button)
        row.addStretch(); layout.addLayout(row)
        self.projects = QListWidget(self); self.projects.currentRowChanged.connect(self._load); layout.addWidget(self.projects)
        self.root_label = QLabel(""); layout.addWidget(self.root_label)
        layout.addWidget(QLabel("Project instructions"))
        self.instructions = QPlainTextEdit(self); layout.addWidget(self.instructions)
        save = QPushButton("Save instructions"); save.clicked.connect(self._save_instructions); layout.addWidget(save)

        layout.addWidget(QLabel("Knowledge sources"))
        self.sources = QListWidget(self); layout.addWidget(self.sources)
        source_row = QHBoxLayout()
        for label, fn in (("Add file…", self._add_file), ("Add folder…", self._add_folder), ("Remove source", self._remove_source), ("Index project", self._index)):
            button = QPushButton(label); button.clicked.connect(fn); source_row.addWidget(button)
        source_row.addStretch(); layout.addLayout(source_row)

        layout.addWidget(QLabel("User-defined test profiles (program + argv; never model-run)"))
        profile_row = QHBoxLayout()
        self.profile_name = QLineEdit(); self.profile_name.setPlaceholderText("Profile name")
        self.profile_program = QLineEdit(); self.profile_program.setPlaceholderText("Program, e.g. python")
        self.profile_args = QLineEdit(); self.profile_args.setPlaceholderText("Args, e.g. -m pytest -q")
        add = QPushButton("Save profile"); add.clicked.connect(self._save_profile)
        profile_row.addWidget(self.profile_name); profile_row.addWidget(self.profile_program); profile_row.addWidget(self.profile_args, 1); profile_row.addWidget(add)
        layout.addLayout(profile_row)
        self.profiles = QListWidget(self); layout.addWidget(self.profiles)
        run = QPushButton("Run selected profile"); run.clicked.connect(self._run_profile); layout.addWidget(run)
        self._refresh_projects()

    def _current_id(self) -> str:
        item = self.projects.currentItem()
        return str(item.data(32)) if item else ""

    def _refresh_projects(self) -> None:
        current = self._current_id(); self.projects.clear()
        selected = -1
        for index, project in enumerate(self.store.list_projects(include_archived=True)):
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(f"{project['name']} — {project['root_path']}" + (" (archived)" if project["archived"] else ""))
            item.setData(32, project["id"]); self.projects.addItem(item)
            if project["id"] == current: selected = index
        if self.projects.count(): self.projects.setCurrentRow(max(0, selected))

    def _load(self) -> None:
        project = self.store.get_project(self._current_id()) if self._current_id() else None
        if not project: return
        self.root_label.setText(str(project["root_path"])); self.instructions.setPlainText(project["instructions"])
        self.sources.clear()
        for source in self.store.list_sources(project["id"]):
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(f"{source['path']} · indexed: {source['last_indexed_at'] or 'never'} {source['last_error']}"); item.setData(32, source["id"]); self.sources.addItem(item)
        self.profiles.clear()
        for profile in self.store.list_test_profiles(project["id"]):
            self.profiles.addItem(f"{profile['name']}: {profile['program']} {' '.join(profile['args'])}")

    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "New project", "Name:")
        if not ok or not name.strip(): return
        existing = QMessageBox.question(self, "Project folder", "Use an existing folder? Choose No to create one under the VISION workspace.")
        if existing == QMessageBox.StandardButton.Yes:
            folder = QFileDialog.getExistingDirectory(self, "Choose project folder")
            if not folder: return
            root = Path(folder)
        else:
            root = self.store.default_root(self.paths.workspace, name)
        self.store.create_project(name.strip(), root)
        self._refresh_projects(); self.changed.emit()

    def _rename(self) -> None:
        project = self.store.get_project(self._current_id())
        if not project: return
        name, ok = QInputDialog.getText(self, "Rename project", "Name:", text=project["name"])
        if ok and name.strip(): self.store.update_project(project["id"], name=name.strip()); self._refresh_projects(); self.changed.emit()

    def _archive(self) -> None:
        if self._current_id(): self.store.update_project(self._current_id(), archived=True); self._refresh_projects(); self.changed.emit()

    def _delete(self) -> None:
        project_id = self._current_id()
        if project_id and QMessageBox.question(self, "Delete project?", "Delete project metadata? Files in the folder are not deleted.") == QMessageBox.StandardButton.Yes:
            self.store.delete_project(project_id); self._refresh_projects(); self.changed.emit()

    def _save_instructions(self) -> None:
        if self._current_id(): self.store.update_project(self._current_id(), instructions=self.instructions.toPlainText()); self.changed.emit()

    def _add_file(self) -> None:
        if not self._current_id(): return
        filename, _ = QFileDialog.getOpenFileName(self, "Add knowledge file")
        if filename: self.store.add_source(self._current_id(), Path(filename)); self._load(); self.changed.emit()

    def _add_folder(self) -> None:
        if not self._current_id(): return
        folder = QFileDialog.getExistingDirectory(self, "Add knowledge folder")
        if folder: self.store.add_source(self._current_id(), Path(folder), "folder"); self._load(); self.changed.emit()

    def _remove_source(self) -> None:
        item = self.sources.currentItem()
        if item: self.store.remove_source(str(item.data(32))); self._load(); self.changed.emit()

    def _index(self) -> None:
        if self._current_id(): self.index_requested.emit(self._current_id())

    def _save_profile(self) -> None:
        if not self._current_id() or not self.profile_name.text().strip() or not self.profile_program.text().strip(): return
        import shlex
        self.store.save_test_profile(self._current_id(), self.profile_name.text().strip(), self.profile_program.text().strip(), shlex.split(self.profile_args.text()), self.root_label.text())
        self._load(); self.changed.emit()

    def _run_profile(self) -> None:
        item = self.profiles.currentItem()
        if item and self._current_id(): self.run_profile_requested.emit(self._current_id(), item.text().split(":", 1)[0])

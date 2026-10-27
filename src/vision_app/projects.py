from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "project"


class ProjectStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.RLock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def _init_db(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS projects(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, root_path TEXT NOT NULL,
              instructions TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS project_extensions(
              project_id TEXT NOT NULL, kind TEXT NOT NULL, extension_id TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(project_id, kind, extension_id));
            CREATE TABLE IF NOT EXISTS project_sources(
              id TEXT PRIMARY KEY, project_id TEXT NOT NULL, path TEXT NOT NULL,
              kind TEXT NOT NULL DEFAULT 'file', enabled INTEGER NOT NULL DEFAULT 1,
              last_indexed_at TEXT, last_error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS project_test_profiles(
              project_id TEXT NOT NULL, name TEXT NOT NULL, program TEXT NOT NULL,
              args_json TEXT NOT NULL DEFAULT '[]', cwd TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
              PRIMARY KEY(project_id, name));
            """)

    @staticmethod
    def _table_exists(db: sqlite3.Connection, table: str) -> bool:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    @classmethod
    def _delete_knowledge(cls, db: sqlite3.Connection, project_id: str, source_path: str | None = None) -> None:
        if not cls._table_exists(db, "knowledge_chunks"):
            return
        rows = db.execute(
            "SELECT id, source_path FROM knowledge_chunks WHERE project_id=?", (project_id,)
        ).fetchall()
        selected: list[int] = []
        normalized_source = ""
        if source_path:
            normalized_source = str(Path(source_path).expanduser().resolve()).replace("\\", "/").casefold().rstrip("/")
        for row in rows:
            chunk_path = str(row[1]).replace("\\", "/").casefold()
            if not normalized_source or chunk_path == normalized_source or chunk_path.startswith(normalized_source + "/"):
                selected.append(int(row[0]))
        if cls._table_exists(db, "knowledge_fts"):
            for chunk_id in selected:
                db.execute("DELETE FROM knowledge_fts WHERE chunk_id=?", (chunk_id,))
        for chunk_id in selected:
            db.execute("DELETE FROM knowledge_chunks WHERE id=?", (chunk_id,))

    def create_project(self, name: str, root_path: Path, instructions: str = "") -> str:
        project_id = uuid.uuid4().hex
        ts = _now()
        root_path.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO projects(id,name,root_path,instructions,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (project_id, name.strip()[:120] or "Untitled project", str(root_path.resolve()), instructions, ts, ts),
            )
        return project_id

    def default_root(self, workspace: Path, name: str) -> Path:
        return workspace / "projects" / slugify(name)

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return dict(row) if row else None

    def list_projects(self, include_archived: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM projects"
        if not include_archived:
            query += " WHERE archived=0"
        query += " ORDER BY updated_at DESC"
        with self._lock, self._connect() as db:
            return [dict(row) for row in db.execute(query)]

    def update_project(self, project_id: str, **fields: Any) -> None:
        if not any(key in fields for key in ("name", "root_path", "instructions", "archived")):
            return
        with self._lock, self._connect() as db:
            if "name" in fields:
                db.execute("UPDATE projects SET name=? WHERE id=?", (fields["name"], project_id))
            if "root_path" in fields:
                db.execute("UPDATE projects SET root_path=? WHERE id=?", (fields["root_path"], project_id))
            if "instructions" in fields:
                db.execute("UPDATE projects SET instructions=? WHERE id=?", (fields["instructions"], project_id))
            if "archived" in fields:
                db.execute("UPDATE projects SET archived=? WHERE id=?", (int(fields["archived"]), project_id))
            db.execute("UPDATE projects SET updated_at=? WHERE id=?", (_now(), project_id))

    def delete_project(self, project_id: str) -> None:
        with self._lock, self._connect() as db:
            self._delete_knowledge(db, project_id)
            if self._table_exists(db, "conversations"):
                db.execute("UPDATE conversations SET project_id=NULL WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM projects WHERE id=?", (project_id,))
            db.execute("DELETE FROM project_sources WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM project_extensions WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM project_test_profiles WHERE project_id=?", (project_id,))

    def assign_conversation(self, conversation_id: str, project_id: str | None) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE conversations SET project_id=?, updated_at=? WHERE id=?", (project_id, _now(), conversation_id))

    def set_extension_enabled(self, project_id: str, kind: str, extension_id: str, enabled: bool) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO project_extensions VALUES(?,?,?,?) ON CONFLICT DO UPDATE SET enabled=excluded.enabled",
                (project_id, kind, extension_id, int(enabled)),
            )

    def enabled_extensions(self, project_id: str | None, kind: str) -> set[str]:
        if not project_id:
            return set()
        with self._lock, self._connect() as db:
            return {str(row[0]) for row in db.execute(
                "SELECT extension_id FROM project_extensions WHERE project_id=? AND kind=? AND enabled=1",
                (project_id, kind),
            )}

    def add_source(self, project_id: str, path: Path, kind: str = "file") -> str:
        source_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{project_id}:{path.resolve()}").hex
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO project_sources(id,project_id,path,kind,enabled) VALUES(?,?,?,?,1)",
                (source_id, project_id, str(path.expanduser().resolve()), kind),
            )
        return source_id

    def list_sources(self, project_id: str, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM project_sources WHERE project_id=?"
        if enabled_only:
            query += " AND enabled=1"
        with self._lock, self._connect() as db:
            return [dict(row) for row in db.execute(query, (project_id,))]

    def remove_source(self, source_id: str) -> None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT project_id, path FROM project_sources WHERE id=?", (source_id,)
            ).fetchone()
            if row is not None:
                self._delete_knowledge(db, str(row[0]), str(row[1]))
            db.execute("DELETE FROM project_sources WHERE id=?", (source_id,))

    def mark_source_indexed(self, source_id: str, error: str = "") -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE project_sources SET last_indexed_at=?, last_error=? WHERE id=?", (_now(), error[:500], source_id))

    def save_test_profile(self, project_id: str, name: str, program: str, args: list[str], cwd: str = "") -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO project_test_profiles VALUES(?,?,?,?,?,?)",
                (project_id, name[:80], program, json.dumps(args), cwd, _now()),
            )

    def list_test_profiles(self, project_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM project_test_profiles WHERE project_id=? ORDER BY name", (project_id,))]
        for row in rows:
            row["args"] = json.loads(row.pop("args_json") or "[]")
        return rows

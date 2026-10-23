from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .parsers import SUPPORTED_EXTENSIONS, is_secret_like, parse_file

SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "dist", "build", ".pytest_cache"}


def _chunk_text(text: str, max_chars: int = 1400, overlap: int = 120) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
            if boundary > start + 300:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return [chunk for chunk in chunks if chunk]


class KnowledgeIndex:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.RLock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def _init_db(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge_chunks(
              id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, source_path TEXT NOT NULL,
              file_hash TEXT NOT NULL, display_name TEXT NOT NULL, locator TEXT NOT NULL, text TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_knowledge_project_path ON knowledge_chunks(project_id, source_path);
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(text, chunk_id UNINDEXED, project_id UNINDEXED);
            """)

    @staticmethod
    def file_hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def remove_path(self, project_id: str, path: Path) -> None:
        source = str(path.resolve())
        with self._lock, self._connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM knowledge_chunks WHERE project_id=? AND source_path=?", (project_id, source))]
            for chunk_id in ids:
                db.execute("DELETE FROM knowledge_fts WHERE chunk_id=?", (chunk_id,))
            db.execute("DELETE FROM knowledge_chunks WHERE project_id=? AND source_path=?", (project_id, source))

    def index_file(self, project_id: str, path: Path, *, force: bool = False) -> int:
        path = path.expanduser().resolve()
        if is_secret_like(path):
            return 0
        digest = self.file_hash(path)
        with self._lock, self._connect() as db:
            existing = db.execute(
                "SELECT COUNT(*) FROM knowledge_chunks WHERE project_id=? AND source_path=? AND file_hash=?",
                (project_id, str(path), digest),
            ).fetchone()[0]
            if existing and not force:
                return int(existing)
        self.remove_path(project_id, path)
        count = 0
        with self._lock, self._connect() as db:
            for section in parse_file(path):
                for chunk in _chunk_text(section.text):
                    cursor = db.execute(
                        "INSERT INTO knowledge_chunks(project_id,source_path,file_hash,display_name,locator,text) VALUES(?,?,?,?,?,?)",
                        (project_id, str(path), digest, path.name, section.locator, chunk),
                    )
                    db.execute("INSERT INTO knowledge_fts(text,chunk_id,project_id) VALUES(?,?,?)", (chunk, int(cursor.lastrowid), project_id))
                    count += 1
        return count

    def index_path(self, project_id: str, path: Path) -> dict[str, Any]:
        files: list[Path] = []
        errors: list[str] = []
        if path.is_file():
            files = [path]
        elif path.is_dir():
            for current, dirs, names in os.walk(path):
                dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS)
                for name in sorted(names):
                    candidate = Path(current) / name
                    if candidate.suffix.lower() in SUPPORTED_EXTENSIONS and not is_secret_like(candidate):
                        files.append(candidate)
        else:
            raise FileNotFoundError(path)
        chunks = 0
        for file in files:
            try:
                chunks += self.index_file(project_id, file)
            except Exception as exc:
                errors.append(f"{file}: {exc}")
        return {"files": len(files), "chunks": chunks, "errors": errors}

    def search(self, project_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        terms = list(dict.fromkeys(re.findall(r"[A-Za-z0-9_]{2,}", query.lower())))
        if not terms:
            return []
        expression = " OR ".join(f'"{term}"' for term in terms[:12])
        with self._lock, self._connect() as db:
            rows = db.execute(
                """SELECT c.*, bm25(knowledge_fts) AS rank
                   FROM knowledge_fts JOIN knowledge_chunks c ON c.id=knowledge_fts.chunk_id
                   WHERE knowledge_fts MATCH ? AND knowledge_fts.project_id=?
                   ORDER BY rank LIMIT ?""",
                (expression, project_id, max(1, min(limit, 20))),
            ).fetchall()
        return [dict(row) for row in rows]

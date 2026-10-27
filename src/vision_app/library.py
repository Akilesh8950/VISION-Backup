from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_LIBRARY_FILE_BYTES = 25 * 1024 * 1024
MAX_LIBRARY_ITEMS = 1_000
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024
ATTACHMENT_MAX_BYTES = 256 * 1024

DOCUMENT_EXTENSIONS = {".pdf", ".docx"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".toml",
    ".yaml", ".yml", ".ini", ".cfg", ".csv", ".html", ".css", ".java", ".c",
    ".cpp", ".h", ".hpp", ".cs", ".go", ".rs", ".sql", ".xml", ".log",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".opus", ".flac", ".aac", ".m4a"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"}
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rs", ".sql", ".css", ".html", ".xml",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def kind_for_extension(extension: str) -> str:
    ext = extension.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in ARCHIVE_EXTENSIONS:
        return "archive"
    if ext in CODE_EXTENSIONS:
        return "code"
    if ext in {".pdf", ".docx", ".doc", ".odt", ".rtf"}:
        return "document"
    if ext in {".csv", ".tsv", ".xlsx", ".xls", ".ods"}:
        return "spreadsheet"
    if ext in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}:
        return "data"
    if ext in TEXT_EXTENSIONS:
        return "text"
    return "file"


def _clean_display_name(value: object, fallback: str = "Untitled file") -> str:
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", " ", name).strip()
    name = re.sub(r"\s+", " ", name)
    return (name[:160].rstrip(" .") or fallback)


class LibraryStore:
    """Persistent, app-managed copies of files uploaded by the user.

    The renderer receives metadata and small image previews only. Stored
    filenames are opaque IDs, so renaming an item changes its display name
    without ever exposing or moving a user-controlled filesystem path.
    """

    def __init__(self, db_path: Path, library_dir: Path):
        self.db_path = db_path
        self.library_dir = library_dir
        self._lock = threading.RLock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        library_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS library_items(
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    stored_name TEXT NOT NULL UNIQUE,
                    extension TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'file',
                    size INTEGER NOT NULL DEFAULT 0,
                    sha256 TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_library_items_sha
                    ON library_items(sha256, size);
                CREATE INDEX IF NOT EXISTS idx_library_items_updated
                    ON library_items(updated_at DESC);
                CREATE TABLE IF NOT EXISTS library_item_uses(
                    item_id TEXT NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
                    conversation_id TEXT NOT NULL,
                    used_at TEXT NOT NULL,
                    PRIMARY KEY(item_id, conversation_id)
                );
                CREATE INDEX IF NOT EXISTS idx_library_item_uses_item
                    ON library_item_uses(item_id);
                """
            )

    def _stored_path(self, stored_name: str) -> Path:
        candidate = (self.library_dir / Path(stored_name).name).resolve()
        root = self.library_dir.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("Library item path escapes the library folder")
        return candidate

    def _public_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        stored_name = str(data.pop("stored_name", ""))
        data.pop("sha256", None)
        size = int(data.get("size") or 0)
        extension = str(data.get("extension") or "")
        data["attachable"] = (
            (extension in TEXT_EXTENSIONS and size <= ATTACHMENT_MAX_BYTES)
            or (extension in DOCUMENT_EXTENSIONS and size <= 10_000_000)
        )
        data["thumbnail"] = ""
        if data.get("kind") == "image" and 0 < size <= MAX_THUMBNAIL_BYTES:
            try:
                path = self._stored_path(stored_name)
                with path.open("rb") as stream:
                    content = stream.read(MAX_THUMBNAIL_BYTES + 1)
                if len(content) <= MAX_THUMBNAIL_BYTES:
                    mime = str(data.get("mime_type") or "application/octet-stream")
                    data["thumbnail"] = (
                        f"data:{mime};base64," + base64.b64encode(content).decode("ascii")
                    )
            except (OSError, ValueError):
                data["thumbnail"] = ""
        return data

    def count(self) -> int:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT COUNT(*) FROM library_items").fetchone()
        return int(row[0]) if row else 0

    def list_items(self, limit: int = MAX_LIBRARY_ITEMS) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit or MAX_LIBRARY_ITEMS), MAX_LIBRARY_ITEMS))
        with self._lock, self._connect() as db:
            rows = db.execute(
                """
                SELECT i.*, COUNT(DISTINCT u.conversation_id) AS used_in_chats
                FROM library_items i
                LEFT JOIN library_item_uses u ON u.item_id=i.id
                GROUP BY i.id
                ORDER BY i.updated_at DESC, i.created_at DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        return [self._public_row(row) for row in rows]

    def get_item(self, item_id: str, *, public: bool = True) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                """
                SELECT i.*, COUNT(DISTINCT u.conversation_id) AS used_in_chats
                FROM library_items i
                LEFT JOIN library_item_uses u ON u.item_id=i.id
                WHERE i.id=?
                GROUP BY i.id
                """,
                (str(item_id),),
            ).fetchone()
        if row is None:
            return None
        return self._public_row(row) if public else dict(row)

    def item_path(self, item_id: str) -> Path | None:
        item = self.get_item(item_id, public=False)
        if not item:
            return None
        path = self._stored_path(str(item["stored_name"]))
        return path if path.is_file() else None

    def import_file(self, source: Path) -> dict[str, Any]:
        source = Path(source).expanduser()
        if not source.is_file():
            raise FileNotFoundError(source)

        # Deduplication below may make an already-known file usable even when
        # the library is at its item cap, so enforce that cap only after the
        # bounded copy pass has produced the digest.
        temporary = self.library_dir / f".incoming-{uuid.uuid4().hex}"
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as incoming, temporary.open("wb") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_LIBRARY_FILE_BYTES:
                        raise ValueError("File exceeds the 25 MB Item Library limit")
                    digest.update(chunk)
                    outgoing.write(chunk)
                outgoing.flush()

            extension = source.suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,12}", extension):
                extension = ""
            sha256 = digest.hexdigest()
            with self._lock, self._connect() as db:
                existing = db.execute(
                    "SELECT id, stored_name FROM library_items WHERE sha256=? AND size=? LIMIT 1",
                    (sha256, size),
                ).fetchone()
                if existing is not None:
                    existing_path = self._stored_path(str(existing["stored_name"]))
                    if existing_path.is_file():
                        db.execute(
                            "UPDATE library_items SET updated_at=? WHERE id=?",
                            (_now(), str(existing["id"])),
                        )
                        item_id = str(existing["id"])
                        temporary.unlink(missing_ok=True)
                        result = self.get_item(item_id)
                        if result is None:
                            raise RuntimeError("Library item disappeared during import")
                        return result
                    # A stale database row whose managed copy was removed is
                    # replaced atomically by the newly imported bytes.
                    db.execute("DELETE FROM library_items WHERE id=?", (str(existing["id"]),))

                count_row = db.execute("SELECT COUNT(*) FROM library_items").fetchone()
                if count_row and int(count_row[0]) >= MAX_LIBRARY_ITEMS:
                    raise ValueError(f"Item Library is limited to {MAX_LIBRARY_ITEMS} files")

                item_id = uuid.uuid4().hex
                stored_name = f"{item_id}{extension}"
                destination = self._stored_path(stored_name)
                temporary.replace(destination)
                timestamp = _now()
                name = _clean_display_name(source.name)
                mime_type = mimetypes.guess_type(source.name)[0] or ""
                try:
                    db.execute(
                        """
                        INSERT INTO library_items(
                            id,name,original_name,stored_name,extension,mime_type,kind,
                            size,sha256,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            item_id,
                            name,
                            name,
                            stored_name,
                            extension,
                            mime_type,
                            kind_for_extension(extension),
                            size,
                            sha256,
                            timestamp,
                            timestamp,
                        ),
                    )
                except BaseException:
                    destination.unlink(missing_ok=True)
                    raise
            result = self.get_item(item_id)
            if result is None:
                raise RuntimeError("Library item could not be loaded after import")
            return result
        finally:
            temporary.unlink(missing_ok=True)

    def rename_item(self, item_id: str, name: str) -> dict[str, Any]:
        clean = _clean_display_name(name, fallback="")
        if not clean:
            raise ValueError("Library item name cannot be empty")
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "UPDATE library_items SET name=?, updated_at=? WHERE id=?",
                (clean, _now(), str(item_id)),
            )
            if cursor.rowcount < 1:
                raise KeyError("Library item not found")
        result = self.get_item(item_id)
        if result is None:
            raise KeyError("Library item not found")
        return result

    def delete_item(self, item_id: str) -> bool:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT stored_name FROM library_items WHERE id=?", (str(item_id),)
            ).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM library_item_uses WHERE item_id=?", (str(item_id),))
            db.execute("DELETE FROM library_items WHERE id=?", (str(item_id),))
            stored_name = str(row["stored_name"])
        try:
            self._stored_path(stored_name).unlink(missing_ok=True)
        except (OSError, ValueError):
            # The database is the source of truth for visibility. A locked
            # orphan is preferable to resurrecting a deleted item in the UI.
            pass
        return True

    def record_use(self, item_id: str, conversation_id: str) -> None:
        if not item_id or not conversation_id:
            return
        with self._lock, self._connect() as db:
            exists = db.execute(
                "SELECT 1 FROM library_items WHERE id=?", (str(item_id),)
            ).fetchone()
            if exists is None:
                return
            db.execute(
                """
                INSERT INTO library_item_uses(item_id,conversation_id,used_at) VALUES(?,?,?)
                ON CONFLICT(item_id,conversation_id) DO UPDATE SET used_at=excluded.used_at
                """,
                (str(item_id), str(conversation_id), _now()),
            )

    def forget_conversation(self, conversation_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "DELETE FROM library_item_uses WHERE conversation_id=?",
                (str(conversation_id),),
            )

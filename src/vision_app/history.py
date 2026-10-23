from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .redaction import redact_text, redact_value
from .transport.schemas import ChatMessage, ToolCall


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HistoryStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.RLock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _init_db(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations(
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0,
                    pinned INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    reasoning TEXT NOT NULL DEFAULT '',
                    tool_calls_json TEXT,
                    tool_call_id TEXT,
                    incomplete INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
                CREATE TABLE IF NOT EXISTS memory_kv(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
                """
            )

    def create_conversation(self, title: str = "New chat", project_id: str | None = None) -> str:
        conversation_id = uuid.uuid4().hex
        ts = _now()
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO conversations(id, project_id, title, created_at, updated_at) VALUES(?,?,?,?,?)",
                (conversation_id, project_id, title[:120] or "New chat", ts, ts),
            )
        return conversation_id

    def list_conversations(self, include_archived: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM conversations"
        if not include_archived:
            query += " WHERE archived=0"
        query += " ORDER BY pinned DESC, updated_at DESC"
        with self._lock, self._connect() as db:
            return [dict(row) for row in db.execute(query)]

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                (title[:120], _now(), conversation_id),
            )

    def set_conversation_flags(
        self, conversation_id: str, *, pinned: bool | None = None, archived: bool | None = None
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        if pinned is not None:
            fields.append("pinned=?")
            values.append(int(pinned))
        if archived is not None:
            fields.append("archived=?")
            values.append(int(archived))
        if not fields:
            return
        values.extend([_now(), conversation_id])
        with self._lock, self._connect() as db:
            db.execute(
                f"UPDATE conversations SET {', '.join(fields)}, updated_at=? WHERE id=?", values
            )

    def delete_conversation(self, conversation_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str = "",
        *,
        reasoning: str = "",
        tool_calls: list[ToolCall] | None = None,
        tool_call_id: str | None = None,
        incomplete: bool = False,
    ) -> int:
        ts = _now()
        tool_json = None
        if tool_calls:
            tool_json = json.dumps(
                redact_value([call.model_dump() for call in tool_calls]), ensure_ascii=False
            )
        with self._lock, self._connect() as db:
            cursor = db.execute(
                """INSERT INTO messages(conversation_id, role, content, reasoning, tool_calls_json,
                   tool_call_id, incomplete, created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    conversation_id,
                    role,
                    redact_text(content),
                    redact_text(reasoning),
                    tool_json,
                    tool_call_id,
                    int(incomplete),
                    ts,
                ),
            )
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (ts, conversation_id))
            return int(cursor.lastrowid)

    def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)
            )]
        for row in rows:
            raw = row.pop("tool_calls_json", None)
            row["tool_calls"] = json.loads(raw) if raw else []
            row["incomplete"] = bool(row["incomplete"])
        return rows

    def chat_messages(self, conversation_id: str) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        for row in self.list_messages(conversation_id):
            calls = [ToolCall.model_validate(call) for call in row["tool_calls"]]
            messages.append(
                ChatMessage(
                    role=row["role"],
                    content=row["content"],
                    tool_calls=calls or None,
                    tool_call_id=row.get("tool_call_id"),
                )
            )
        return messages

    def last_exchange(self, conversation_id: str) -> tuple[str, str]:
        rows = self.list_messages(conversation_id)
        prompt = next((r["content"] for r in reversed(rows) if r["role"] == "user"), "")
        answer = next((r["content"] for r in reversed(rows) if r["role"] == "assistant"), "")
        return prompt, answer

    def memory_get(self, key: str) -> str | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT value FROM memory_kv WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def memory_set(self, key: str, value: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO memory_kv(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, redact_text(value), _now()),
            )

    def memory_delete(self, key: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM memory_kv WHERE key=?", (key,))

    def memory_list(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM memory_kv ORDER BY key")]

    def search_conversations(self, query: str) -> list[dict[str, Any]]:
        pattern = f"%{query}%"
        with self._lock, self._connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    """SELECT DISTINCT c.* FROM conversations c
                       LEFT JOIN messages m ON m.conversation_id=c.id
                       WHERE c.title LIKE ? OR m.content LIKE ?
                       ORDER BY c.updated_at DESC""",
                    (pattern, pattern),
                )
            ]

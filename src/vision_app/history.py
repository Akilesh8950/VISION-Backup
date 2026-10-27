from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .redaction import redact_text, redact_value
from .transport.schemas import ChatMessage, ToolCall

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


_TITLE_STOPWORDS = {
    "a", "about", "after", "all", "also", "an", "and", "any", "are", "as", "at",
    "be", "because", "been", "before", "being", "but", "by", "can", "could",
    "did", "do", "does", "for", "from", "had", "has", "have", "how", "i", "if",
    "in", "into", "is", "it", "its", "just", "me", "my", "of", "on", "or",
    "our", "please", "should", "so", "than", "that", "the", "their", "them",
    "then", "there", "these", "this", "to", "too", "under", "up", "us", "was",
    "we", "were", "what", "when", "where", "which", "who", "why", "will",
    "with", "would", "you", "your",
}
_TITLE_COMMAND_WORDS = {
    "analyze", "analyse", "answer", "build", "calculate", "check", "compare",
    "create", "describe", "design", "draw", "explain", "find", "fix", "generate",
    "give", "help", "improve", "list", "make", "open", "plan", "prepare",
    "review", "show", "solve", "summarize", "summarise", "tell", "write",
}
_TITLE_GENERIC_WORDS = {
    "answer", "below", "current", "details", "example", "following", "here",
    "information", "result", "results", "summary", "thing", "things", "value",
    "values",
}


def _title_words(text: str, *, command_words: bool = False) -> list[str]:
    cleaned = re.sub(r"```[\s\S]*?```", " ", str(text or ""))
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"[#>*_~|\[\](){}]", " ", cleaned)
    words: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’.-]*", cleaned):
        word = raw.strip("'.-")
        lowered = word.casefold()
        if not word or lowered in seen or lowered in _TITLE_STOPWORDS:
            continue
        if command_words and lowered in _TITLE_COMMAND_WORDS:
            continue
        if lowered in _TITLE_GENERIC_WORDS:
            continue
        seen.add(lowered)
        words.append(word)
    return words


def _title_case(word: str) -> str:
    if len(word) <= 5 and word.isupper() and any(char.isalpha() for char in word):
        return word
    if any(char.isupper() for char in word[1:]):
        return word
    return word[:1].upper() + word[1:]


def generate_conversation_title(prompt: str, answer: str = "") -> str:
    """Create a compact 3–5 word title from the first exchange.

    The user's wording wins because it best describes intent. Assistant text
    is only used to flesh out very short prompts such as "Weather in Paris?".
    A manually supplied title is never passed through this helper by callers.
    """

    words = _title_words(prompt, command_words=True)
    if len(words) < 3:
        seen = {word.casefold() for word in words}
        for word in _title_words(answer):
            if word.casefold() not in seen:
                words.append(word)
                seen.add(word.casefold())
            if len(words) >= 4:
                break
    if not words:
        fallback = " ".join(str(prompt or "").split()).strip()
        return fallback[:48] or "New chat"
    return " ".join(_title_case(word) for word in words[:5])[:120]


class HistoryStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.RLock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
            # The inner context preserves sqlite3's commit/rollback
            # semantics; the outer finally guarantees the handle is closed.
            with connection:
                yield connection
        finally:
            connection.close()

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
        if pinned is None and archived is None:
            return
        with self._lock, self._connect() as db:
            if pinned is not None:
                db.execute("UPDATE conversations SET pinned=? WHERE id=?", (int(pinned), conversation_id))
            if archived is not None:
                db.execute("UPDATE conversations SET archived=? WHERE id=?", (int(archived), conversation_id))
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (_now(), conversation_id))

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
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a message id")
            return int(cursor.lastrowid)

    def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)
            )]
        for row in rows:
            raw = row.pop("tool_calls_json", None)
            try:
                parsed = json.loads(raw) if raw else []
            except (TypeError, ValueError):
                parsed = []
            row["tool_calls"] = parsed if isinstance(parsed, list) else []
            row["incomplete"] = bool(row["incomplete"])
        return rows

    def chat_messages(self, conversation_id: str) -> list[ChatMessage]:
        parsed_messages: list[ChatMessage] = []
        skipped_roles = 0
        skipped_tool_calls = 0
        skipped_messages = 0
        for row in self.list_messages(conversation_id):
            if row.get("role") not in {"system", "user", "assistant", "tool"}:
                skipped_roles += 1
                continue
            calls: list[ToolCall] = []
            for raw_call in row.get("tool_calls") or []:
                try:
                    calls.append(ToolCall.model_validate(raw_call))
                except Exception:
                    skipped_tool_calls += 1
                    continue
            try:
                parsed_messages.append(
                    ChatMessage(
                        role=row["role"],
                        content=str(row.get("content") or ""),
                        tool_calls=calls or None,
                        tool_call_id=row.get("tool_call_id"),
                    )
                )
            except Exception:
                skipped_messages += 1
                continue
        if skipped_roles or skipped_tool_calls or skipped_messages:
            logger.warning(
                "Skipped malformed history while building model context: "
                "%d invalid role(s), %d invalid tool call(s), %d invalid message(s)",
                skipped_roles,
                skipped_tool_calls,
                skipped_messages,
            )

        # A process exit/cancellation can persist an assistant tool-call row
        # without all matching tool rows. Most chat APIs reject that shape
        # forever afterwards. Repair only the model-facing copy: synthesize
        # interrupted replies for missing calls and drop orphan tool rows;
        # the stored transcript remains an accurate record of what happened.
        sanitized: list[ChatMessage] = []
        pending: dict[str, ToolCall] = {}

        def flush_pending() -> None:
            for pending_call in list(pending.values()):
                sanitized.append(
                    ChatMessage(
                        role="tool",
                        content="Tool execution was interrupted before a result was recorded.",
                        tool_call_id=pending_call.id,
                        name=pending_call.function.name or None,
                    )
                )
            pending.clear()

        for message in parsed_messages:
            if message.role == "tool":
                if message.tool_call_id and message.tool_call_id in pending:
                    sanitized.append(message)
                    pending.pop(message.tool_call_id, None)
                continue
            if pending:
                flush_pending()
            if message.role == "assistant" and message.tool_calls:
                unique_calls: list[ToolCall] = []
                seen_ids: set[str] = set()
                for index, call in enumerate(message.tool_calls):
                    call_id = str(call.id or "").strip() or f"call_history_{len(sanitized)}_{index}"
                    if call_id in seen_ids:
                        call_id = f"{call_id}_{index}"
                    seen_ids.add(call_id)
                    unique_call = call if call.id == call_id else call.model_copy(update={"id": call_id})
                    unique_calls.append(unique_call)
                    pending[call_id] = unique_call
                message = message.model_copy(update={"tool_calls": unique_calls})
            sanitized.append(message)
        if pending:
            flush_pending()
        return sanitized

    def last_exchange(self, conversation_id: str) -> tuple[str, str]:
        rows = self.list_messages(conversation_id)
        prompt = next((r["content"] for r in reversed(rows) if r["role"] == "user"), "")
        answer = next((r["content"] for r in reversed(rows) if r["role"] == "assistant"), "")
        return prompt, answer

    def first_exchange(self, conversation_id: str) -> tuple[str, str]:
        rows = self.list_messages(conversation_id)
        prompt = ""
        answer = ""
        for row in rows:
            if not prompt and row.get("role") == "user":
                prompt = str(row.get("content") or "")
            elif prompt and row.get("role") == "assistant":
                answer = str(row.get("content") or "")
                break
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
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        with self._lock, self._connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    """SELECT DISTINCT c.* FROM conversations c
                       LEFT JOIN messages m ON m.conversation_id=c.id
                       WHERE c.title LIKE ? ESCAPE '\\' OR m.content LIKE ? ESCAPE '\\'
                       ORDER BY c.pinned DESC, c.updated_at DESC""",
                    (pattern, pattern),
                )
            ]

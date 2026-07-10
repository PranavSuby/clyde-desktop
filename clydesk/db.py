"""Chat persistence: typed message records (no magic-string prefixes)."""

import json

import aiosqlite

from .config import DB_PATH


def _connect():
    """Open a connection with foreign keys enforced (SQLite defaults them off,
    which silently allows orphaned messages after a chat is deleted)."""
    conn = aiosqlite.connect(DB_PATH)

    class _WithFK:
        async def __aenter__(self):
            self.db = await conn.__aenter__()
            await self.db.execute("PRAGMA foreign_keys = ON")
            return self.db

        async def __aexit__(self, *exc):
            return await conn.__aexit__(*exc)

    return _WithFK()

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text',   -- text | image_gen | user_image
    content TEXT NOT NULL,
    extra TEXT,                          -- JSON: {images:[], route:, tags:{}}
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
"""


async def init_db():
    import os
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with _connect() as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def create_chat(title: str) -> int:
    async with _connect() as db:
        cur = await db.execute("INSERT INTO chats (title) VALUES (?)", (title,))
        await db.commit()
        return cur.lastrowid


async def list_chats() -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, title, created_at FROM chats ORDER BY id DESC"
        )
        return [dict(r) for r in await cur.fetchall()]


async def rename_chat(chat_id: int, title: str):
    async with _connect() as db:
        await db.execute("UPDATE chats SET title=? WHERE id=?", (title, chat_id))
        await db.commit()


async def delete_chat(chat_id: int):
    async with _connect() as db:
        await db.execute("DELETE FROM chats WHERE id=?", (chat_id,))
        await db.commit()


async def search_chats(query: str, limit: int = 20) -> list[dict]:
    """Chats whose title or message content matches the query."""
    escaped = (query.replace("\\", "\\\\")
               .replace("%", "\\%").replace("_", "\\_"))
    like = f"%{escaped}%"
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT DISTINCT c.id, c.title FROM chats c
               LEFT JOIN messages m ON m.chat_id = c.id
               WHERE c.title LIKE ? ESCAPE '\\' OR m.content LIKE ? ESCAPE '\\'
               ORDER BY c.id DESC LIMIT ?""",
            (like, like, limit),
        )
        return [dict(r) for r in await cur.fetchall()]


async def save_message(chat_id: int, role: str, content: str,
                       kind: str = "text", extra: dict | None = None) -> int:
    async with _connect() as db:
        cur = await db.execute(
            "INSERT INTO messages (chat_id, role, kind, content, extra) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, kind, content, json.dumps(extra) if extra else None),
        )
        await db.commit()
        return cur.lastrowid


async def get_messages(chat_id: int) -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, role, kind, content, extra FROM messages "
            "WHERE chat_id=? ORDER BY id", (chat_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["extra"] = json.loads(r["extra"]) if r["extra"] else {}
    return rows

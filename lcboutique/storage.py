"""Baza de date a botului (SQLite): postările publicate și setările modificabile din mini-aplicație.

Fișierul stă în DATA_DIR (implicit ./data). Pe Railway, folderul trebuie să fie un Volume
(montat la /app/data), altfel se golește la fiecare redeploy.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Post:
    id: int
    created_at: float
    category: str
    product: dict  # câmpurile produsului (models.Product)
    price_lei: int
    text_message_id: int  # mesajul care conține textul (caption sau mesaj separat)
    is_caption: bool
    message_ids: list[int]  # toate mesajele postării (poze + text), pentru ștergere/mutare
    media_group_ids: list[str]
    media: list[dict]  # [{"kind": "photo", "file_id": "...", "preview_id": "..."}]
    thread_id: int | None
    calc: str = ""  # calculul prețului inițial (pentru proprietară)
    extra: dict = field(default_factory=dict)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    category TEXT NOT NULL,
    product TEXT NOT NULL,
    price_lei INTEGER NOT NULL,
    text_message_id INTEGER NOT NULL,
    is_caption INTEGER NOT NULL,
    message_ids TEXT NOT NULL,
    media_group_ids TEXT NOT NULL,
    media TEXT NOT NULL,
    thread_id INTEGER,
    calc TEXT NOT NULL DEFAULT '',
    extra TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS posts_text_message ON posts (text_message_id);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_JSON_FIELDS = ("product", "message_ids", "media_group_ids", "media", "extra")


class Storage:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.db.commit()

    # ---------- postări ----------

    def add_post(self, post: Post) -> Post:
        values = self._to_row(post)
        values.pop("id")
        cur = self.db.execute(
            f"INSERT INTO posts ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
            list(values.values()),
        )
        self.db.commit()
        post.id = cur.lastrowid
        return post

    def update_post(self, post: Post) -> None:
        values = self._to_row(post)
        post_id = values.pop("id")
        self.db.execute(
            f"UPDATE posts SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
            [*values.values(), post_id],
        )
        self.db.commit()

    def delete_post(self, post_id: int) -> None:
        self.db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        self.db.commit()

    def get_post(self, post_id: int) -> Post | None:
        row = self.db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        return self._from_row(row) if row else None

    def find_by_message(self, message_id: int, media_group_id: str | None = None) -> Post | None:
        row = self.db.execute("SELECT * FROM posts WHERE text_message_id = ?", (message_id,)).fetchone()
        if row:
            return self._from_row(row)
        # orice mesaj al postării (ex. altă poză din album)
        for row in self.db.execute(
            "SELECT * FROM posts WHERE message_ids LIKE ? OR (? != '' AND media_group_ids LIKE ?) "
            "ORDER BY id DESC LIMIT 20",
            (f"%{message_id}%", media_group_id or "", f'%"{media_group_id}"%'),
        ):
            post = self._from_row(row)
            if message_id in post.message_ids or (media_group_id and media_group_id in post.media_group_ids):
                return post
        return None

    def list_posts(self, query: str = "", category: str = "", limit: int = 50, offset: int = 0) -> list[Post]:
        sql, args = "SELECT * FROM posts WHERE 1=1", []
        if category:
            sql += " AND category = ?"
            args.append(category)
        if query:
            sql += " AND product LIKE ?"
            args.append(f"%{query}%")
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [self._from_row(r) for r in self.db.execute(sql, args)]

    # ---------- setări ----------

    def get_settings(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.db.execute("SELECT key, value FROM settings")}

    def set_setting(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        self.db.commit()

    # ---------- conversie ----------

    @staticmethod
    def _to_row(post: Post) -> dict:
        row = dict(post.__dict__)
        for key in _JSON_FIELDS:
            row[key] = json.dumps(row[key], ensure_ascii=False)
        row["is_caption"] = int(post.is_caption)
        return row

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Post:
        data = dict(row)
        for key in _JSON_FIELDS:
            data[key] = json.loads(data[key])
        data["is_caption"] = bool(data["is_caption"])
        return Post(**data)


def new_post(**kwargs) -> Post:
    return Post(id=0, created_at=time.time(), **kwargs)

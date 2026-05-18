from __future__ import annotations

import json
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterator
from typing import Iterable


@dataclass(frozen=True)
class Restaurant:
    id: int
    name: str
    category: str
    area: str | None
    tabelog_url: str | None
    google_maps_url: str | None
    comments: str
    keywords: list[str]
    source_channel_id: int
    source_message_id: int | None
    created_by: str
    created_at: str


class RestaurantDB:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self.session() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS restaurants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    area TEXT,
                    tabelog_url TEXT,
                    google_maps_url TEXT,
                    comments TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    source_channel_id INTEGER NOT NULL,
                    source_message_id INTEGER,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(name, tabelog_url)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_restaurants_category ON restaurants(category)"
            )

    def add_restaurant(
        self,
        *,
        name: str,
        category: str,
        area: str | None,
        tabelog_url: str | None,
        google_maps_url: str | None,
        comments: str,
        keywords: Iterable[str],
        source_channel_id: int,
        source_message_id: int | None,
        created_by: str,
    ) -> int:
        clean_keywords = sorted({k.strip().lower() for k in keywords if k.strip()})
        with self.session() as conn:
            cur = conn.execute(
                """
                INSERT OR REPLACE INTO restaurants (
                    id, name, category, area, tabelog_url, google_maps_url, comments,
                    keywords_json, source_channel_id, source_message_id, created_by
                )
                VALUES (
                    (SELECT id FROM restaurants WHERE name = ? AND COALESCE(tabelog_url, '') = COALESCE(?, '')),
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    name,
                    tabelog_url,
                    name,
                    category,
                    area,
                    tabelog_url,
                    google_maps_url,
                    comments,
                    json.dumps(clean_keywords, ensure_ascii=False),
                    source_channel_id,
                    source_message_id,
                    created_by,
                ),
            )
            return int(cur.lastrowid or conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def search(self, keyword: str, limit: int = 25) -> list[Restaurant]:
        key = keyword.strip().lower()
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT * FROM restaurants
                WHERE lower(name) LIKE ?
                   OR lower(category) LIKE ?
                   OR lower(area) LIKE ?
                   OR lower(comments) LIKE ?
                   OR lower(keywords_json) LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (f"%{key}%", f"%{key}%", f"%{key}%", f"%{key}%", f"%{key}%", limit),
            ).fetchall()
            if len(rows) < limit:
                existing_ids = {int(row["id"]) for row in rows}
                normalized_key = normalize_search_text(keyword)
                extra_rows = []
                for row in conn.execute(
                    "SELECT * FROM restaurants ORDER BY created_at DESC"
                ).fetchall():
                    if int(row["id"]) in existing_ids:
                        continue
                    if normalized_key in normalize_restaurant_row(row):
                        extra_rows.append(row)
                    if len(rows) + len(extra_rows) >= limit:
                        break
                rows = [*rows, *extra_rows]
        return [self._row_to_restaurant(row) for row in rows[:limit]]

    def get(self, restaurant_id: int) -> Restaurant | None:
        with self.session() as conn:
            row = conn.execute(
                "SELECT * FROM restaurants WHERE id = ?", (restaurant_id,)
            ).fetchone()
        return self._row_to_restaurant(row) if row else None

    def append_comment(
        self,
        *,
        restaurant_id: int,
        comment: str,
        created_by: str,
    ) -> Restaurant | None:
        restaurant = self.get(restaurant_id)
        if not restaurant:
            return None

        section = f"追加評論（{created_by}）：\n{comment.strip()}"
        base_comments = restaurant.comments.strip()
        if _is_basic_info_placeholder(base_comments):
            base_comments = ""
        comments = "\n\n".join(part for part in [base_comments, section] if part)
        with self.session() as conn:
            conn.execute(
                "UPDATE restaurants SET comments = ? WHERE id = ?",
                (comments, restaurant_id),
            )
        return self.get(restaurant_id)

    def cleanup_comment_placeholders(self) -> int:
        with self.session() as conn:
            rows = conn.execute("SELECT id, comments FROM restaurants").fetchall()
            changed = 0
            for row in rows:
                comments = str(row["comments"] or "").strip()
                cleaned = _remove_basic_info_placeholder(comments)
                if cleaned != comments:
                    conn.execute(
                        "UPDATE restaurants SET comments = ? WHERE id = ?",
                        (cleaned, row["id"]),
                    )
                    changed += 1
            return changed

    def all(self) -> list[Restaurant]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM restaurants ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_restaurant(row) for row in rows]

    @staticmethod
    def _row_to_restaurant(row: sqlite3.Row) -> Restaurant:
        return Restaurant(
            id=int(row["id"]),
            name=row["name"],
            category=row["category"],
            area=row["area"],
            tabelog_url=row["tabelog_url"],
            google_maps_url=row["google_maps_url"],
            comments=row["comments"],
            keywords=json.loads(row["keywords_json"]),
            source_channel_id=int(row["source_channel_id"]),
            source_message_id=row["source_message_id"],
            created_by=row["created_by"],
            created_at=row["created_at"],
        )


def _is_basic_info_placeholder(comments: str) -> bool:
    return comments in {
        "僅保存了基本餐廳資訊，無評論內容。",
        "僅保存了基本餐廳資訊，無評論內容",
    }


def _remove_basic_info_placeholder(comments: str) -> str:
    lines = comments.splitlines()
    while lines and _is_basic_info_placeholder(lines[0].strip()):
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines).strip()


def normalize_restaurant_row(row: sqlite3.Row) -> str:
    return normalize_search_text(
        " ".join(
            str(part or "")
            for part in [
                row["name"],
                row["category"],
                row["area"],
                row["comments"],
                row["keywords_json"],
            ]
        )
    )


def normalize_search_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(katakana_to_hiragana(char) for char in normalized)


def katakana_to_hiragana(char: str) -> str:
    code = ord(char)
    if 0x30A1 <= code <= 0x30F6:
        return chr(code - 0x60)
    return char

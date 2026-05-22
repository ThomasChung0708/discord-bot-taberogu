from __future__ import annotations

"""SQLite 資料存取層。

這個檔案只負責「資料怎麼存、怎麼查」：
- 建立 restaurants 資料表
- 新增或更新餐廳
- 搜尋餐廳
- 追加評論
- 做日文搜尋正規化，例如 カツ / かつ / ｶﾂ 視為同一種搜尋字

把資料庫邏輯集中在這裡，可以讓 bot.py 不需要知道 SQL 細節。
"""

import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterator
from typing import Iterable


@dataclass(frozen=True)
class Restaurant:
    """餐廳資料在 Python 裡的表示方式。

    dataclass 讓資料結構清楚、型別明確，也比直接傳 dict 更好理解。
    frozen=True 代表建立後不應該直接修改欄位；要修改資料就透過 RestaurantDB。
    """

    id: int
    name: str
    category: str
    area: str | None
    tabelog_url: str | None
    google_maps_url: str | None
    image_url: str | None
    comments: str
    keywords: list[str]
    tags: list[str]
    source_channel_id: int
    source_message_id: int | None
    created_by: str
    created_at: str
    lunch_budget_text: str | None = None
    lunch_budget_min: int | None = None
    lunch_budget_max: int | None = None
    dinner_budget_text: str | None = None
    dinner_budget_min: int | None = None
    dinner_budget_max: int | None = None
    price_updated_at: str | None = None


@dataclass(frozen=True)
class RestaurantComment:
    """Single comment attached to one restaurant."""

    id: int
    restaurant_id: int
    comment: str
    created_by: str
    created_at: str
    source_message_id: int | None = None


@dataclass(frozen=True)
class ChatMemoryMessage:
    """One non-bot Discord message saved as short-term recommendation memory."""

    message_id: int
    guild_id: int
    channel_id: int
    author_id: int
    author_name: str
    content: str
    created_at: str


class RestaurantDB:
    """包裝 SQLite 操作的類別。"""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._init()

    def connect(self) -> sqlite3.Connection:
        """建立 SQLite 連線，並讓查詢結果可以用 row["欄位名"] 讀取。"""
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """資料庫交易的 helper。

        with self.session() as conn:
            ...

        離開 with 區塊時會 commit 並關閉連線，避免忘記關資料庫。
        """

        conn = self.connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        """初始化資料表與索引。

        CREATE TABLE IF NOT EXISTS 代表如果資料表已存在就不重建，
        因此每次 bot 啟動都可以安全呼叫。
        """

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
                    image_url TEXT,
                    comments TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    source_channel_id INTEGER NOT NULL,
                    source_message_id INTEGER,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    lunch_budget_text TEXT,
                    lunch_budget_min INTEGER,
                    lunch_budget_max INTEGER,
                    dinner_budget_text TEXT,
                    dinner_budget_min INTEGER,
                    dinner_budget_max INTEGER,
                    price_updated_at TEXT,
                    UNIQUE(name, tabelog_url)
                )
                """
            )
            self._ensure_column(conn, "image_url", "TEXT")
            self._ensure_column(conn, "lunch_budget_text", "TEXT")
            self._ensure_column(conn, "lunch_budget_min", "INTEGER")
            self._ensure_column(conn, "lunch_budget_max", "INTEGER")
            self._ensure_column(conn, "dinner_budget_text", "TEXT")
            self._ensure_column(conn, "dinner_budget_min", "INTEGER")
            self._ensure_column(conn, "dinner_budget_max", "INTEGER")
            self._ensure_column(conn, "price_updated_at", "TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_restaurants_category ON restaurants(category)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS restaurant_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    restaurant_id INTEGER NOT NULL,
                    comment TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    source_message_id INTEGER,
                    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS restaurant_tags (
                    restaurant_id INTEGER NOT NULL,
                    tag TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (restaurant_id, tag),
                    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_restaurant_comments_restaurant_id ON restaurant_comments(restaurant_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_restaurant_tags_tag ON restaurant_tags(tag)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_memory_messages (
                    message_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    author_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_memory_channel_created
                ON chat_memory_messages(guild_id, channel_id, created_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_memory_summaries (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, channel_id)
                )
                """
            )
            self._migrate_comments_and_tags(conn)

    def _ensure_column(self, conn: sqlite3.Connection, name: str, definition: str) -> None:
        """舊 DB 升級用：缺欄位時自動 ALTER TABLE 加上。"""

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(restaurants)").fetchall()}
        if name not in columns:
            conn.execute(f"ALTER TABLE restaurants ADD COLUMN {name} {definition}")

    def _migrate_comments_and_tags(self, conn: sqlite3.Connection) -> None:
        """Backfill normalized comments and tags from the legacy columns."""

        rows = conn.execute(
            "SELECT id, comments, keywords_json, name, category, area FROM restaurants"
        ).fetchall()
        for row in rows:
            restaurant_id = int(row["id"])
            existing_comment = conn.execute(
                "SELECT 1 FROM restaurant_comments WHERE restaurant_id = ? LIMIT 1",
                (restaurant_id,),
            ).fetchone()
            comments = _remove_basic_info_placeholder(str(row["comments"] or "").strip())
            if comments and not existing_comment:
                conn.execute(
                    """
                    INSERT INTO restaurant_comments (restaurant_id, comment, created_by)
                    VALUES (?, ?, ?)
                    """,
                    (restaurant_id, comments, "Migration"),
                )

            try:
                keywords = json.loads(row["keywords_json"] or "[]")
            except json.JSONDecodeError:
                keywords = []
            tags = clean_keyword_list(
                keywords,
                name=row["name"],
                category=row["category"],
                area=row["area"],
            )
            for tag in tags:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO restaurant_tags (restaurant_id, tag)
                    VALUES (?, ?)
                    """,
                    (restaurant_id, tag),
                )

    def _append_comment_row(
        self,
        conn: sqlite3.Connection,
        *,
        restaurant_id: int,
        comment: str,
        created_by: str,
        source_message_id: int | None = None,
    ) -> None:
        """Insert one normalized comment row."""

        text = comment.strip()
        if not text:
            return
        conn.execute(
            """
            INSERT INTO restaurant_comments (
                restaurant_id, comment, created_by, source_message_id
            )
            VALUES (?, ?, ?, ?)
            """,
            (restaurant_id, text, created_by, source_message_id),
        )

    def _set_tags(
        self,
        conn: sqlite3.Connection,
        restaurant_id: int,
        tags: Iterable[str],
    ) -> None:
        """Replace normalized tags for one restaurant."""

        conn.execute("DELETE FROM restaurant_tags WHERE restaurant_id = ?", (restaurant_id,))
        for tag in clean_keyword_list(tags):
            conn.execute(
                """
                INSERT OR IGNORE INTO restaurant_tags (restaurant_id, tag)
                VALUES (?, ?)
                """,
                (restaurant_id, tag),
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
        image_url: str | None = None,
        lunch_budget_text: str | None = None,
        lunch_budget_min: int | None = None,
        lunch_budget_max: int | None = None,
        dinner_budget_text: str | None = None,
        dinner_budget_min: int | None = None,
        dinner_budget_max: int | None = None,
        price_updated_at: str | None = None,
    ) -> int:
        """新增或覆蓋同一家餐廳。

        這裡用 UNIQUE(name, tabelog_url) 避免同一間餐廳重複建立。
        如果 name + tabelog_url 已存在，INSERT OR REPLACE 會保留原本 id。
        """

        category = normalize_category(category)
        area = normalize_area(area)
        clean_keywords = clean_keyword_list(keywords, name=name, category=category, area=area)
        with self.session() as conn:
            cur = conn.execute(
                """
                INSERT OR REPLACE INTO restaurants (
                    id, name, category, area, tabelog_url, google_maps_url, image_url, comments,
                    keywords_json, source_channel_id, source_message_id, created_by,
                    lunch_budget_text, lunch_budget_min, lunch_budget_max,
                    dinner_budget_text, dinner_budget_min, dinner_budget_max, price_updated_at
                )
                VALUES (
                    (SELECT id FROM restaurants WHERE name = ? AND COALESCE(tabelog_url, '') = COALESCE(?, '')),
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    image_url.strip() if image_url and image_url.strip() else None,
                    comments,
                    json.dumps(clean_keywords, ensure_ascii=False),
                    source_channel_id,
                    source_message_id,
                    created_by,
                    lunch_budget_text,
                    lunch_budget_min,
                    lunch_budget_max,
                    dinner_budget_text,
                    dinner_budget_min,
                    dinner_budget_max,
                    price_updated_at,
                ),
            )
            restaurant_id = int(cur.lastrowid or conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            self._set_tags(conn, restaurant_id, clean_keywords)
            legacy_comments = _remove_basic_info_placeholder(comments.strip())
            if legacy_comments:
                self._append_comment_row(
                    conn,
                    restaurant_id=restaurant_id,
                    comment=legacy_comments,
                    created_by=created_by,
                    source_message_id=source_message_id,
                )
            return restaurant_id

    def search(self, keyword: str, limit: int = 25) -> list[Restaurant]:
        """搜尋餐廳。

        第一段用 SQLite LIKE 做快速搜尋。
        第二段在結果不足時，用 Python 做日文正規化搜尋：
        例如使用者搜尋「牛かつ」，也能命中資料中的「牛カツ」。
        """

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
                   OR EXISTS (
                       SELECT 1 FROM restaurant_comments
                       WHERE restaurant_comments.restaurant_id = restaurants.id
                         AND lower(restaurant_comments.comment) LIKE ?
                   )
                   OR EXISTS (
                       SELECT 1 FROM restaurant_tags
                       WHERE restaurant_tags.restaurant_id = restaurants.id
                         AND lower(restaurant_tags.tag) LIKE ?
                   )
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (
                    f"%{key}%",
                    f"%{key}%",
                    f"%{key}%",
                    f"%{key}%",
                    f"%{key}%",
                    f"%{key}%",
                    f"%{key}%",
                    limit,
                ),
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
                    if normalized_key in self._normalized_restaurant_text(conn, row):
                        extra_rows.append(row)
                    if len(rows) + len(extra_rows) >= limit:
                        break
                rows = [*rows, *extra_rows]
        return [self._row_to_restaurant(row) for row in rows[:limit]]

    def get(self, restaurant_id: int) -> Restaurant | None:
        """用 ID 取得單一餐廳。"""

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
        """把評論文字追加到某間餐廳。

        如果原本 comments 只是「僅保存基本資料」這種佔位文字，
        追加第一則評論時會先把佔位文字拿掉。
        """

        restaurant = self.get(restaurant_id)
        if not restaurant:
            return None

        section = f"追加評論（{created_by}）：\n{comment.strip()}"
        base_comments = restaurant.comments.strip()
        if _is_basic_info_placeholder(base_comments):
            base_comments = ""
        comments = "\n\n".join(part for part in [base_comments, section] if part)
        with self.session() as conn:
            self._append_comment_row(
                conn,
                restaurant_id=restaurant_id,
                comment=comment,
                created_by=created_by,
            )
            conn.execute(
                "UPDATE restaurants SET comments = ? WHERE id = ?",
                (comments, restaurant_id),
            )
        return self.get(restaurant_id)

    def update_restaurant(
        self,
        *,
        restaurant_id: int,
        name: str,
        category: str,
        area: str | None,
        tabelog_url: str | None,
        google_maps_url: str | None,
        comments: str,
        keywords: Iterable[str],
        image_url: str | None = None,
        lunch_budget_text: str | None = None,
        lunch_budget_min: int | None = None,
        lunch_budget_max: int | None = None,
        dinner_budget_text: str | None = None,
        dinner_budget_min: int | None = None,
        dinner_budget_max: int | None = None,
        price_updated_at: str | None = None,
    ) -> Restaurant | None:
        """更新後台表單送來的餐廳資料。

        管理後台會直接編輯餐廳欄位，因此這裡集中處理空白清理與
        keywords_json 的 JSON 轉換，避免 admin_app.py 裡混入 SQL 細節。
        """

        category = normalize_category(category)
        area = normalize_area(area)
        clean_keywords = clean_keyword_list(keywords, name=name, category=category, area=area)
        with self.session() as conn:
            cur = conn.execute(
                """
                UPDATE restaurants
                SET name = ?,
                    category = ?,
                    area = ?,
                    tabelog_url = ?,
                    google_maps_url = ?,
                    image_url = ?,
                    comments = ?,
                    keywords_json = ?,
                    lunch_budget_text = COALESCE(?, lunch_budget_text),
                    lunch_budget_min = COALESCE(?, lunch_budget_min),
                    lunch_budget_max = COALESCE(?, lunch_budget_max),
                    dinner_budget_text = COALESCE(?, dinner_budget_text),
                    dinner_budget_min = COALESCE(?, dinner_budget_min),
                    dinner_budget_max = COALESCE(?, dinner_budget_max),
                    price_updated_at = COALESCE(?, price_updated_at)
                WHERE id = ?
                """,
                (
                    name.strip(),
                    category,
                    area,
                    tabelog_url.strip() if tabelog_url and tabelog_url.strip() else None,
                    google_maps_url.strip() if google_maps_url and google_maps_url.strip() else None,
                    image_url.strip() if image_url and image_url.strip() else None,
                    comments.strip(),
                    json.dumps(clean_keywords, ensure_ascii=False),
                    lunch_budget_text.strip() if lunch_budget_text and lunch_budget_text.strip() else None,
                    lunch_budget_min,
                    lunch_budget_max,
                    dinner_budget_text.strip() if dinner_budget_text and dinner_budget_text.strip() else None,
                    dinner_budget_min,
                    dinner_budget_max,
                    price_updated_at,
                    restaurant_id,
                ),
            )
            if cur.rowcount == 0:
                return None
            self._set_tags(conn, restaurant_id, clean_keywords)
        return self.get(restaurant_id)

    def import_restaurant(
        self,
        *,
        restaurant_id: int | None,
        name: str,
        category: str,
        area: str | None,
        tabelog_url: str | None,
        google_maps_url: str | None,
        comments: str,
        keywords: Iterable[str],
        created_by: str = "Google Sheet",
        image_url: str | None = None,
        lunch_budget_text: str | None = None,
        lunch_budget_min: int | None = None,
        lunch_budget_max: int | None = None,
        dinner_budget_text: str | None = None,
        dinner_budget_min: int | None = None,
        dinner_budget_max: int | None = None,
        price_updated_at: str | None = None,
    ) -> int:
        """從 Google Sheet 匯入一間餐廳。

        和 add_restaurant 不同，這個方法允許保留 Sheet 裡的 id。
        如果 id 已存在，就更新該筆；如果 id 不存在，就用 Sheet 的 id 建立。
        這讓 Google Sheet 可以作為大量手動編輯後的回灌來源。
        """

        category = normalize_category(category)
        area = normalize_area(area)
        clean_keywords = clean_keyword_list(keywords, name=name, category=category, area=area)
        with self.session() as conn:
            if restaurant_id and self.get(restaurant_id):
                conn.execute(
                    """
                    UPDATE restaurants
                    SET name = ?,
                        category = ?,
                        area = ?,
                        tabelog_url = ?,
                        google_maps_url = ?,
                        image_url = ?,
                        comments = ?,
                        keywords_json = ?,
                        lunch_budget_text = ?,
                        lunch_budget_min = ?,
                        lunch_budget_max = ?,
                        dinner_budget_text = ?,
                        dinner_budget_min = ?,
                        dinner_budget_max = ?,
                        price_updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name.strip(),
                        category,
                        area,
                        tabelog_url.strip() if tabelog_url and tabelog_url.strip() else None,
                        google_maps_url.strip() if google_maps_url and google_maps_url.strip() else None,
                        image_url.strip() if image_url and image_url.strip() else None,
                        comments.strip(),
                        json.dumps(clean_keywords, ensure_ascii=False),
                        lunch_budget_text,
                        lunch_budget_min,
                        lunch_budget_max,
                        dinner_budget_text,
                        dinner_budget_min,
                        dinner_budget_max,
                        price_updated_at,
                        restaurant_id,
                    ),
                )
                self._set_tags(conn, restaurant_id, clean_keywords)
                return restaurant_id

            cur = conn.execute(
                """
                INSERT OR REPLACE INTO restaurants (
                    id, name, category, area, tabelog_url, google_maps_url, image_url, comments,
                    keywords_json, source_channel_id, source_message_id, created_by,
                    lunch_budget_text, lunch_budget_min, lunch_budget_max,
                    dinner_budget_text, dinner_budget_min, dinner_budget_max, price_updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    restaurant_id,
                    name.strip(),
                    category,
                    area,
                    tabelog_url.strip() if tabelog_url and tabelog_url.strip() else None,
                    google_maps_url.strip() if google_maps_url and google_maps_url.strip() else None,
                    image_url.strip() if image_url and image_url.strip() else None,
                    comments.strip(),
                    json.dumps(clean_keywords, ensure_ascii=False),
                    created_by,
                    lunch_budget_text,
                    lunch_budget_min,
                    lunch_budget_max,
                    dinner_budget_text,
                    dinner_budget_min,
                    dinner_budget_max,
                    price_updated_at,
                ),
            )
            new_id = int(restaurant_id or cur.lastrowid)
            self._set_tags(conn, new_id, clean_keywords)
            return new_id

    def update_price_info(
        self,
        *,
        restaurant_id: int,
        lunch_budget_text: str | None,
        lunch_budget_min: int | None,
        lunch_budget_max: int | None,
        dinner_budget_text: str | None,
        dinner_budget_min: int | None,
        dinner_budget_max: int | None,
        price_updated_at: str | None,
    ) -> Restaurant | None:
        """更新一間餐廳的食べログ價格資訊。"""

        with self.session() as conn:
            cur = conn.execute(
                """
                UPDATE restaurants
                SET lunch_budget_text = ?,
                    lunch_budget_min = ?,
                    lunch_budget_max = ?,
                    dinner_budget_text = ?,
                    dinner_budget_min = ?,
                    dinner_budget_max = ?,
                    price_updated_at = ?
                WHERE id = ?
                """,
                (
                    lunch_budget_text,
                    lunch_budget_min,
                    lunch_budget_max,
                    dinner_budget_text,
                    dinner_budget_min,
                    dinner_budget_max,
                    price_updated_at,
                    restaurant_id,
                ),
            )
            if cur.rowcount == 0:
                return None
        return self.get(restaurant_id)

    def restaurants_missing_prices(self, limit: int = 5) -> list[Restaurant]:
        """取得有食べログ URL、但還沒補價格的餐廳。"""

        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT * FROM restaurants
                WHERE tabelog_url IS NOT NULL
                  AND trim(tabelog_url) != ''
                  AND price_updated_at IS NULL
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_restaurant(row) for row in rows]

    def update_image_url(self, restaurant_id: int, image_url: str | None) -> Restaurant | None:
        """只更新一間餐廳的圖片 URL。"""

        with self.session() as conn:
            cur = conn.execute(
                "UPDATE restaurants SET image_url = ? WHERE id = ?",
                (image_url.strip() if image_url and image_url.strip() else None, restaurant_id),
            )
            if cur.rowcount == 0:
                return None
        return self.get(restaurant_id)

    def delete_restaurant(self, restaurant_id: int) -> bool:
        """刪除一間餐廳。管理後台刪錯資料時會用到。"""

        with self.session() as conn:
            conn.execute("DELETE FROM restaurant_comments WHERE restaurant_id = ?", (restaurant_id,))
            conn.execute("DELETE FROM restaurant_tags WHERE restaurant_id = ?", (restaurant_id,))
            cur = conn.execute("DELETE FROM restaurants WHERE id = ?", (restaurant_id,))
            return cur.rowcount > 0

    def comments_for(self, restaurant_id: int) -> list[RestaurantComment]:
        """Return normalized comments for one restaurant."""

        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT * FROM restaurant_comments
                WHERE restaurant_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (restaurant_id,),
            ).fetchall()
        return [self._row_to_comment(row) for row in rows]

    def tags_for(self, restaurant_id: int) -> list[str]:
        """Return normalized tags for one restaurant."""

        with self.session() as conn:
            return self._tags_for_conn(conn, restaurant_id)

    def all_tags(self) -> list[str]:
        """Return every tag currently used by saved restaurants."""

        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT tag FROM restaurant_tags
                WHERE trim(tag) != ''
                ORDER BY tag
                """
            ).fetchall()
        return [str(row["tag"]) for row in rows]

    def areas(self) -> list[str]:
        """取得目前所有地區，用於後台篩選選單。"""

        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT area
                FROM restaurants
                WHERE area IS NOT NULL AND trim(area) != ''
                ORDER BY area
                """
            ).fetchall()
        return [str(row["area"]) for row in rows]

    def categories(self) -> list[str]:
        """取得目前所有分類，用於後台篩選選單。"""

        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT category
                FROM restaurants
                WHERE trim(category) != ''
                ORDER BY category
                """
            ).fetchall()
        return [str(row["category"]) for row in rows]

    def cleanup_comment_placeholders(self) -> int:
        """清理舊資料中殘留的 comments 佔位文字。"""

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

    def cleanup_area_categories(self) -> int:
        """Normalize existing area/category values in saved rows."""

        changed = 0
        with self.session() as conn:
            rows = conn.execute("SELECT id, name, category, area, keywords_json FROM restaurants").fetchall()
            for row in rows:
                category = normalize_category(row["category"])
                area = normalize_area(row["area"])
                if category == row["category"] and area == row["area"]:
                    continue
                try:
                    keywords = json.loads(row["keywords_json"] or "[]")
                except json.JSONDecodeError:
                    keywords = []
                clean_keywords = clean_keyword_list(
                    keywords,
                    name=row["name"],
                    category=category,
                    area=area,
                )
                conn.execute(
                    """
                    UPDATE restaurants
                    SET category = ?, area = ?, keywords_json = ?
                    WHERE id = ?
                    """,
                    (category, area, json.dumps(clean_keywords, ensure_ascii=False), row["id"]),
                )
                self._set_tags(conn, int(row["id"]), clean_keywords)
                changed += 1
        return changed

    def record_chat_memory(
        self,
        *,
        guild_id: int,
        channel_id: int,
        author_id: int,
        author_name: str,
        message_id: int,
        content: str,
        created_at: str,
    ) -> bool:
        """Save one user message for recommendation memory."""

        text = content.strip()
        if not text:
            return False
        with self.session() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO chat_memory_messages (
                    message_id, guild_id, channel_id, author_id, author_name, content, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    guild_id,
                    channel_id,
                    author_id,
                    author_name.strip() or str(author_id),
                    text,
                    created_at,
                ),
            )
            return cur.rowcount > 0

    def chat_memory_count(self, *, guild_id: int, channel_id: int) -> int:
        """Return short-term memory row count for one Discord channel."""

        with self.session() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM chat_memory_messages
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
            ).fetchone()
        return int(row["total"] if row else 0)

    def old_chat_memory_messages(
        self,
        *,
        guild_id: int,
        channel_id: int,
        keep_latest: int,
        limit: int,
    ) -> list[ChatMemoryMessage]:
        """Return older messages that can be summarized and deleted."""

        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM chat_memory_messages
                WHERE guild_id = ?
                  AND channel_id = ?
                  AND message_id NOT IN (
                    SELECT message_id
                    FROM chat_memory_messages
                    WHERE guild_id = ? AND channel_id = ?
                    ORDER BY created_at DESC, message_id DESC
                    LIMIT ?
                  )
                ORDER BY created_at ASC, message_id ASC
                LIMIT ?
                """,
                (guild_id, channel_id, guild_id, channel_id, keep_latest, limit),
            ).fetchall()
        return [self._row_to_chat_memory_message(row) for row in rows]

    def delete_chat_memory_messages(self, message_ids: Iterable[int]) -> int:
        """Delete raw chat memory rows after they have been summarized."""

        ids = [int(message_id) for message_id in message_ids]
        if not ids:
            return 0
        with self.session() as conn:
            cur = conn.executemany(
                "DELETE FROM chat_memory_messages WHERE message_id = ?",
                [(message_id,) for message_id in ids],
            )
            return cur.rowcount if cur.rowcount is not None else 0

    def chat_memory_summary(self, *, guild_id: int, channel_id: int) -> str:
        """Return the long-term summary for one Discord channel."""

        with self.session() as conn:
            row = conn.execute(
                """
                SELECT summary
                FROM chat_memory_summaries
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
            ).fetchone()
        return str(row["summary"] or "") if row else ""

    def upsert_chat_memory_summary(
        self,
        *,
        guild_id: int,
        channel_id: int,
        summary: str,
    ) -> None:
        """Save or replace the long-term chat memory summary."""

        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO chat_memory_summaries (guild_id, channel_id, summary, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(guild_id, channel_id) DO UPDATE SET
                    summary = excluded.summary,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (guild_id, channel_id, summary.strip()),
            )

    def chat_memory_context(
        self,
        *,
        guild_id: int,
        channel_id: int,
        recent_limit: int = 30,
    ) -> str:
        """Build compact memory text for recommendation prompts."""

        with self.session() as conn:
            summary_row = conn.execute(
                """
                SELECT summary
                FROM chat_memory_summaries
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
            ).fetchone()
            rows = conn.execute(
                """
                SELECT *
                FROM chat_memory_messages
                WHERE guild_id = ? AND channel_id = ?
                ORDER BY created_at DESC, message_id DESC
                LIMIT ?
                """,
                (guild_id, channel_id, recent_limit),
            ).fetchall()

        parts: list[str] = []
        if summary_row and str(summary_row["summary"] or "").strip():
            parts.append(f"長期聊天偏好摘要：{str(summary_row['summary']).strip()}")
        recent = [
            f"{row['author_name']}: {str(row['content']).strip()}"
            for row in reversed(rows)
            if str(row["content"] or "").strip()
        ]
        if recent:
            parts.append("最近聊天：\n" + "\n".join(recent))
        return "\n\n".join(parts).strip()

    def all(self) -> list[Restaurant]:
        """取得全部餐廳，最新建立的排前面。"""

        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM restaurants ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_restaurant(row) for row in rows]

    def _row_to_restaurant(self, row: sqlite3.Row) -> Restaurant:
        """把 SQLite row 轉成 Restaurant dataclass。"""

        with self.session() as conn:
            tags = self._tags_for_conn(conn, int(row["id"]))
        return Restaurant(
            id=int(row["id"]),
            name=row["name"],
            category=row["category"],
            area=row["area"],
            tabelog_url=row["tabelog_url"],
            google_maps_url=row["google_maps_url"],
            image_url=row["image_url"],
            comments=row["comments"],
            keywords=json.loads(row["keywords_json"]),
            tags=tags,
            source_channel_id=int(row["source_channel_id"]),
            source_message_id=row["source_message_id"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            lunch_budget_text=row["lunch_budget_text"],
            lunch_budget_min=row["lunch_budget_min"],
            lunch_budget_max=row["lunch_budget_max"],
            dinner_budget_text=row["dinner_budget_text"],
            dinner_budget_min=row["dinner_budget_min"],
            dinner_budget_max=row["dinner_budget_max"],
            price_updated_at=row["price_updated_at"],
        )

    @staticmethod
    def _row_to_comment(row: sqlite3.Row) -> RestaurantComment:
        """Convert a SQLite comment row into a dataclass."""

        return RestaurantComment(
            id=int(row["id"]),
            restaurant_id=int(row["restaurant_id"]),
            comment=row["comment"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            source_message_id=row["source_message_id"],
        )

    @staticmethod
    def _row_to_chat_memory_message(row: sqlite3.Row) -> ChatMemoryMessage:
        """Convert a SQLite chat memory row into a dataclass."""

        return ChatMemoryMessage(
            message_id=int(row["message_id"]),
            guild_id=int(row["guild_id"]),
            channel_id=int(row["channel_id"]),
            author_id=int(row["author_id"]),
            author_name=str(row["author_name"]),
            content=str(row["content"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _tags_for_conn(conn: sqlite3.Connection, restaurant_id: int) -> list[str]:
        """Read tags with an existing connection."""

        rows = conn.execute(
            """
            SELECT tag FROM restaurant_tags
            WHERE restaurant_id = ?
            ORDER BY tag
            """,
            (restaurant_id,),
        ).fetchall()
        return [str(row["tag"]) for row in rows]

    def _normalized_restaurant_text(self, conn: sqlite3.Connection, row: sqlite3.Row) -> str:
        """Build the full searchable text for normalized Python-side search."""

        restaurant_id = int(row["id"])
        comment_rows = conn.execute(
            """
            SELECT comment FROM restaurant_comments
            WHERE restaurant_id = ?
            """,
            (restaurant_id,),
        ).fetchall()
        return normalize_search_text(
            " ".join(
                str(part or "")
                for part in [
                    row["name"],
                    row["category"],
                    row["area"],
                    row["comments"],
                    row["keywords_json"],
                    " ".join(self._tags_for_conn(conn, restaurant_id)),
                    " ".join(str(comment["comment"] or "") for comment in comment_rows),
                ]
            )
        )


def _is_basic_info_placeholder(comments: str) -> bool:
    """判斷 comments 是否只是沒有評論時的佔位文字。"""

    return comments in {
        "僅保存了基本餐廳資訊，無評論內容。",
        "僅保存了基本餐廳資訊，無評論內容",
    }


def _remove_basic_info_placeholder(comments: str) -> str:
    """從 comments 開頭移除舊版佔位文字。"""

    lines = comments.splitlines()
    while lines and _is_basic_info_placeholder(lines[0].strip()):
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines).strip()


def normalize_restaurant_row(row: sqlite3.Row) -> str:
    """把餐廳 row 的可搜尋欄位合併並正規化。"""

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
    """把搜尋文字正規化。

    NFKC：把半形片假名等字元轉成標準形式。
    casefold：比 lower 更適合做跨語言大小寫正規化。
    katakana_to_hiragana：把片假名轉平假名，提升日文搜尋命中率。
    """

    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(katakana_to_hiragana(char) for char in normalized)


def normalize_area(area: str | None) -> str | None:
    """Collapse long addresses into consistent station/neighborhood names."""

    if not area:
        return None
    text = unicodedata.normalize("NFKC", str(area)).strip()
    if not text:
        return None
    text = re.sub(r"\s+", "", text)
    aliases = [
        ("外神田", "秋葉原"),
        ("秋葉原", "秋葉原"),
        ("神保町", "神保町"),
        ("神田", "神田"),
        ("神谷町", "神谷町"),
        ("淡路町", "淡路町"),
        ("御茶ノ水", "御茶ノ水"),
        ("小川町", "小川町"),
        ("東京駅", "東京駅"),
        ("丸の内", "丸の内"),
        ("銀座", "銀座"),
        ("築地", "築地"),
        ("東銀座", "東銀座"),
        ("新宿三丁目", "新宿三丁目"),
        ("西武新宿", "西武新宿"),
        ("新大久保", "新大久保"),
        ("大久保", "大久保"),
        ("高田馬場", "高田馬場"),
        ("歌舞伎町", "新宿"),
        ("新宿", "新宿"),
        ("渋谷", "渋谷"),
        ("神泉", "神泉"),
        ("道玄坂", "渋谷"),
        ("代官山", "代官山"),
        ("恵比寿", "恵比寿"),
        ("原宿", "原宿"),
        ("表参道", "表参道"),
        ("池袋", "池袋"),
        ("東池袋", "東池袋"),
        ("西池袋", "池袋"),
        ("巣鴨", "巣鴨"),
        ("駒込", "駒込"),
        ("六本木", "六本木"),
        ("赤坂", "赤坂"),
        ("上野", "上野"),
        ("御徒町", "御徒町"),
        ("浅草", "浅草"),
        ("押上", "押上"),
        ("中野", "中野"),
        ("高円寺", "高円寺"),
        ("阿佐ヶ谷", "阿佐ヶ谷"),
        ("荻窪", "荻窪"),
        ("吉祥寺", "吉祥寺"),
        ("京王堀之内", "京王堀之内"),
        ("八王子", "八王子"),
        ("立川", "立川"),
        ("府中", "府中"),
        ("十条", "十条"),
        ("芦花公園", "芦花公園"),
        ("緑が丘", "緑が丘"),
        ("肥後橋", "肥後橋"),
        ("竹田", "竹田"),
        ("登戸", "登戸"),
        ("稲田堤", "稲田堤"),
        ("西川口", "西川口"),
        ("大宮", "大宮"),
        ("横浜", "横浜"),
        ("上星川", "上星川"),
    ]
    for needle, canonical in aliases:
        if needle in text:
            return canonical

    # Strip common Japanese address prefixes while keeping the local part.
    text = re.sub(r"^(東京都|神奈川県|埼玉県|千葉県)", "", text)
    city_only = re.fullmatch(r"(.+?市)", text)
    if city_only:
        return city_only.group(1).removesuffix("市")
    text = re.sub(r"^(.*?市)", "", text)
    text = re.sub(r"^(.*?[区町村])", "", text)
    return text or area.strip()


def normalize_category(category: str | None) -> str:
    """Collapse detailed food categories into cleaner filter groups."""

    text = unicodedata.normalize("NFKC", str(category or "")).strip()
    if not text:
        return "未分類"
    normalized = normalize_search_text(text)
    groups = [
        ("ラーメン", ["ラーメン", "拉麵", "拉面", "つけ麺", "沾麵", "沾面", "油そば", "まぜそば", "担々麺", "擔擔麺"]),
        ("肉料理", ["焼肉", "燒肉", "烧肉", "焼鳥", "ステーキ", "ホルモン", "肉"]),
        ("中華料理", ["中華", "台湾", "台灣", "飲茶", "点心", "點心", "餃子"]),
        ("日本料理", ["日本料理", "和食", "寿司", "おにぎり", "飯糰", "饭团", "そば", "うどん", "とんかつ", "豬排", "猪排", "カツ", "かつ", "かき氷", "刨冰"]),
        ("韓国料理", ["韓国"]),
        ("イタリアン", ["イタリア", "パスタ", "ピザ"]),
        ("カフェ・スイーツ", ["カフェ", "喫茶", "スイーツ", "ケーキ", "パン", "デザート"]),
        ("居酒屋・バー", ["居酒屋", "バー", "バル"]),
        ("カレー", ["カレー"]),
        ("朝食", ["早餐", "朝食"]),
    ]
    for canonical, values in groups:
        if any(normalize_search_text(value) in normalized for value in values):
            return canonical
    return text


def clean_keyword_list(
    keywords: Iterable[str],
    *,
    name: str | None = None,
    category: str | None = None,
    area: str | None = None,
) -> list[str]:
    """整理關鍵字，移除重複與被地區/分類涵蓋的項目。

    例如 area 是「市ヶ谷」，keywords 裡又有「市ヶ谷」時只保留一次。
    也會處理 カツ / かつ 這種日文正規化後相同的重複。
    """

    raw_parts: list[str] = []
    for value in [category, area, *keywords]:
        if not value:
            continue
        for part in str(value).replace("、", ",").replace("\n", ",").split(","):
            text = part.strip()
            if text:
                raw_parts.append(text)

    if name:
        normalized_area = normalize_search_text(area or "")
        for part in str(name).replace("、", ",").replace("\n", ",").split(","):
            text = part.strip()
            if not text:
                continue
            normalized_text = normalize_search_text(text)
            if normalized_area and normalized_text == normalized_area:
                continue
            raw_parts.append(text)

    cleaned: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        key = normalize_search_text(part)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(part)
    return cleaned


def katakana_to_hiragana(char: str) -> str:
    """單一字元的片假名轉平假名。"""

    code = ord(char)
    if 0x30A1 <= code <= 0x30F6:
        return chr(code - 0x60)
    return char

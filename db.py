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
    comments: str
    keywords: list[str]
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

    def _ensure_column(self, conn: sqlite3.Connection, name: str, definition: str) -> None:
        """舊 DB 升級用：缺欄位時自動 ALTER TABLE 加上。"""

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(restaurants)").fetchall()}
        if name not in columns:
            conn.execute(f"ALTER TABLE restaurants ADD COLUMN {name} {definition}")

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

        clean_keywords = clean_keyword_list(keywords, name=name, category=category, area=area)
        with self.session() as conn:
            cur = conn.execute(
                """
                INSERT OR REPLACE INTO restaurants (
                    id, name, category, area, tabelog_url, google_maps_url, comments,
                    keywords_json, source_channel_id, source_message_id, created_by,
                    lunch_budget_text, lunch_budget_min, lunch_budget_max,
                    dinner_budget_text, dinner_budget_min, dinner_budget_max, price_updated_at
                )
                VALUES (
                    (SELECT id FROM restaurants WHERE name = ? AND COALESCE(tabelog_url, '') = COALESCE(?, '')),
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    lunch_budget_text,
                    lunch_budget_min,
                    lunch_budget_max,
                    dinner_budget_text,
                    dinner_budget_min,
                    dinner_budget_max,
                    price_updated_at,
                ),
            )
            return int(cur.lastrowid or conn.execute("SELECT last_insert_rowid()").fetchone()[0])

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
    ) -> Restaurant | None:
        """更新後台表單送來的餐廳資料。

        管理後台會直接編輯餐廳欄位，因此這裡集中處理空白清理與
        keywords_json 的 JSON 轉換，避免 admin_app.py 裡混入 SQL 細節。
        """

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
                    comments = ?,
                    keywords_json = ?
                WHERE id = ?
                """,
                (
                    name.strip(),
                    category.strip(),
                    area.strip() if area and area.strip() else None,
                    tabelog_url.strip() if tabelog_url and tabelog_url.strip() else None,
                    google_maps_url.strip() if google_maps_url and google_maps_url.strip() else None,
                    comments.strip(),
                    json.dumps(clean_keywords, ensure_ascii=False),
                    restaurant_id,
                ),
            )
            if cur.rowcount == 0:
                return None
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
                        category.strip(),
                        area.strip() if area and area.strip() else None,
                        tabelog_url.strip() if tabelog_url and tabelog_url.strip() else None,
                        google_maps_url.strip() if google_maps_url and google_maps_url.strip() else None,
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
                return restaurant_id

            cur = conn.execute(
                """
                INSERT OR REPLACE INTO restaurants (
                    id, name, category, area, tabelog_url, google_maps_url, comments,
                    keywords_json, source_channel_id, source_message_id, created_by,
                    lunch_budget_text, lunch_budget_min, lunch_budget_max,
                    dinner_budget_text, dinner_budget_min, dinner_budget_max, price_updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    restaurant_id,
                    name.strip(),
                    category.strip(),
                    area.strip() if area and area.strip() else None,
                    tabelog_url.strip() if tabelog_url and tabelog_url.strip() else None,
                    google_maps_url.strip() if google_maps_url and google_maps_url.strip() else None,
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
            return int(restaurant_id or cur.lastrowid)

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

    def delete_restaurant(self, restaurant_id: int) -> bool:
        """刪除一間餐廳。管理後台刪錯資料時會用到。"""

        with self.session() as conn:
            cur = conn.execute("DELETE FROM restaurants WHERE id = ?", (restaurant_id,))
            return cur.rowcount > 0

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

    def all(self) -> list[Restaurant]:
        """取得全部餐廳，最新建立的排前面。"""

        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM restaurants ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_restaurant(row) for row in rows]

    @staticmethod
    def _row_to_restaurant(row: sqlite3.Row) -> Restaurant:
        """把 SQLite row 轉成 Restaurant dataclass。"""

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
            lunch_budget_text=row["lunch_budget_text"],
            lunch_budget_min=row["lunch_budget_min"],
            lunch_budget_max=row["lunch_budget_max"],
            dinner_budget_text=row["dinner_budget_text"],
            dinner_budget_min=row["dinner_budget_min"],
            dinner_budget_max=row["dinner_budget_max"],
            price_updated_at=row["price_updated_at"],
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

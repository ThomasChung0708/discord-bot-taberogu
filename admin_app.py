from __future__ import annotations

"""Discord 食べログ Bot 的管理後台。

這個檔案是一個獨立的 FastAPI 小網站，和 Discord bot 共用同一份
SQLite 資料庫。它的目標不是取代 Discord 操作，而是補上管理者常用功能：
- 查看所有餐廳
- 搜尋、分類、地區篩選
- 編輯餐廳資料
- 刪除錯誤餐廳
- 追加評論
- 一鍵同步 Google Sheet

啟動方式：
    uvicorn admin_app:app --host 127.0.0.1 --port 8000
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException, FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from db import Restaurant, RestaurantDB
from sheets_sync import read_restaurants_from_sheet, sync_restaurants_to_sheet


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_PATH_VALUE = os.getenv("DB_PATH", "restaurants.sqlite3")
DB_PATH = Path(DB_PATH_VALUE)
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_FILE_VALUE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
GOOGLE_SERVICE_ACCOUNT_FILE = (
    Path(GOOGLE_SERVICE_ACCOUNT_FILE_VALUE) if GOOGLE_SERVICE_ACCOUNT_FILE_VALUE else None
)
if GOOGLE_SERVICE_ACCOUNT_FILE and not GOOGLE_SERVICE_ACCOUNT_FILE.is_absolute():
    GOOGLE_SERVICE_ACCOUNT_FILE = BASE_DIR / GOOGLE_SERVICE_ACCOUNT_FILE
GOOGLE_SHEETS_WORKSHEET = os.getenv("GOOGLE_SHEETS_WORKSHEET", "restaurants").strip() or "restaurants"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
BACKUP_DIR_VALUE = os.getenv("BACKUP_DIR", "backups").strip() or "backups"
BACKUP_DIR = Path(BACKUP_DIR_VALUE)
if not BACKUP_DIR.is_absolute():
    BACKUP_DIR = BASE_DIR / BACKUP_DIR
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "14"))

db = RestaurantDB(str(DB_PATH))
app = FastAPI(title="Discord 食べログ Bot Admin")


class RestaurantPayload(BaseModel):
    """前端送來的餐廳編輯資料。"""

    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    area: str | None = None
    tabelog_url: str | None = None
    google_maps_url: str | None = None
    comments: str = ""
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    lunch_budget_text: str | None = None
    lunch_budget_min: int | None = None
    lunch_budget_max: int | None = None
    dinner_budget_text: str | None = None
    dinner_budget_min: int | None = None
    dinner_budget_max: int | None = None


class CommentPayload(BaseModel):
    """前端送來的追加評論資料。"""

    comment: str = Field(min_length=1)
    created_by: str = "Admin"


def comment_to_dict(comment) -> dict:
    """Convert a normalized comment row to JSON."""

    return {
        "id": comment.id,
        "restaurant_id": comment.restaurant_id,
        "comment": comment.comment,
        "created_by": comment.created_by,
        "created_at": comment.created_at,
        "source_message_id": comment.source_message_id,
    }


def restaurant_to_dict(restaurant: Restaurant) -> dict:
    """把 dataclass 轉成 JSON-friendly dict。"""

    return {
        "id": restaurant.id,
        "name": restaurant.name,
        "category": restaurant.category,
        "area": restaurant.area,
        "tabelog_url": restaurant.tabelog_url,
        "google_maps_url": restaurant.google_maps_url,
        "comments": restaurant.comments,
        "keywords": restaurant.keywords,
        "tags": restaurant.tags,
        "comment_items": [comment_to_dict(comment) for comment in db.comments_for(restaurant.id)],
        "source_channel_id": restaurant.source_channel_id,
        "source_message_id": restaurant.source_message_id,
        "created_by": restaurant.created_by,
        "created_at": restaurant.created_at,
        "lunch_budget_text": restaurant.lunch_budget_text,
        "lunch_budget_min": restaurant.lunch_budget_min,
        "lunch_budget_max": restaurant.lunch_budget_max,
        "dinner_budget_text": restaurant.dinner_budget_text,
        "dinner_budget_min": restaurant.dinner_budget_min,
        "dinner_budget_max": restaurant.dinner_budget_max,
        "price_updated_at": restaurant.price_updated_at,
    }


def require_admin(x_admin_password: str = Header(default="")) -> None:
    """保護會修改資料的 API。

    公開頁只能讀資料；編輯、刪除、匯入、同步都需要管理密碼。
    密碼放在 .env 的 ADMIN_PASSWORD，不寫進 GitHub。
    """

    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="尚未設定 ADMIN_PASSWORD")
    if x_admin_password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="管理密碼錯誤")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """公開只讀頁。"""

    return PUBLIC_HTML


@app.get("/admin", response_class=HTMLResponse)
def admin_index() -> str:
    """管理後台首頁。

    為了讓專案保持簡單，第一版先把 HTML/CSS/JS 放在同一個檔案。
    未來如果後台變大，再拆成 templates 與 static 檔案。
    """

    return ADMIN_HTML


@app.get("/api/admin/status")
def admin_status() -> dict:
    """讓前端知道管理密碼是否已設定。"""

    return {"password_configured": bool(ADMIN_PASSWORD)}


@app.post("/api/admin/check")
def admin_check(_: None = Depends(require_admin)) -> dict:
    """檢查管理密碼。"""

    return {"ok": True}


@app.get("/api/restaurants")
def list_restaurants(
    keyword: str = "",
    area: str = "",
    category: str = "",
) -> dict:
    """回傳餐廳列表，支援關鍵字、地區、分類篩選。"""

    restaurants = db.search(keyword, limit=200) if keyword.strip() else db.all()
    if area:
        restaurants = [restaurant for restaurant in restaurants if (restaurant.area or "") == area]
    if category:
        restaurants = [restaurant for restaurant in restaurants if restaurant.category == category]
    return {
        "restaurants": [restaurant_to_dict(restaurant) for restaurant in restaurants],
        "areas": db.areas(),
        "categories": db.categories(),
        "tags": db.all_tags(),
    }


@app.get("/api/restaurants/{restaurant_id}")
def get_restaurant(restaurant_id: int) -> dict:
    """取得單一餐廳。"""

    restaurant = db.get(restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    return restaurant_to_dict(restaurant)


@app.put("/api/restaurants/{restaurant_id}")
def update_restaurant(
    restaurant_id: int,
    payload: RestaurantPayload,
    _: None = Depends(require_admin),
) -> dict:
    """更新餐廳資料。"""

    restaurant = db.update_restaurant(
        restaurant_id=restaurant_id,
        name=payload.name,
        category=payload.category,
        area=payload.area,
        tabelog_url=payload.tabelog_url,
        google_maps_url=payload.google_maps_url,
        comments=payload.comments,
        keywords=payload.tags or payload.keywords,
        lunch_budget_text=payload.lunch_budget_text,
        lunch_budget_min=payload.lunch_budget_min,
        lunch_budget_max=payload.lunch_budget_max,
        dinner_budget_text=payload.dinner_budget_text,
        dinner_budget_min=payload.dinner_budget_min,
        dinner_budget_max=payload.dinner_budget_max,
        price_updated_at="manual",
    )
    if not restaurant:
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    return restaurant_to_dict(restaurant)


@app.delete("/api/restaurants/{restaurant_id}")
def delete_restaurant(
    restaurant_id: int,
    _: None = Depends(require_admin),
) -> dict:
    """刪除餐廳。"""

    if not db.delete_restaurant(restaurant_id):
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    return {"ok": True}


@app.post("/api/restaurants/{restaurant_id}/comments")
def append_comment(
    restaurant_id: int,
    payload: CommentPayload,
    _: None = Depends(require_admin),
) -> dict:
    """追加評論到指定餐廳。"""

    restaurant = db.append_comment(
        restaurant_id=restaurant_id,
        comment=payload.comment,
        created_by=payload.created_by,
    )
    if not restaurant:
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    return restaurant_to_dict(restaurant)


@app.post("/api/sync-sheet")
def sync_sheet(_: None = Depends(require_admin)) -> dict:
    """從後台手動同步 Google Sheet。"""

    if not GOOGLE_SHEETS_ID:
        raise HTTPException(status_code=400, detail="尚未設定 GOOGLE_SHEETS_ID")
    if not GOOGLE_SERVICE_ACCOUNT_FILE or not GOOGLE_SERVICE_ACCOUNT_FILE.exists():
        raise HTTPException(status_code=400, detail="找不到 Google service account JSON")

    count = sync_restaurants_to_sheet(
        restaurants=db.all(),
        spreadsheet_id=GOOGLE_SHEETS_ID,
        credentials_path=GOOGLE_SERVICE_ACCOUNT_FILE,
        worksheet_name=GOOGLE_SHEETS_WORKSHEET,
    )
    return {"ok": True, "count": count}


@app.post("/api/import-sheet")
def import_sheet(_: None = Depends(require_admin)) -> dict:
    """從 Google Sheet 匯入餐廳到 SQLite。"""

    if not GOOGLE_SHEETS_ID:
        raise HTTPException(status_code=400, detail="尚未設定 GOOGLE_SHEETS_ID")
    if not GOOGLE_SERVICE_ACCOUNT_FILE or not GOOGLE_SERVICE_ACCOUNT_FILE.exists():
        raise HTTPException(status_code=400, detail="找不到 Google service account JSON")

    rows = read_restaurants_from_sheet(
        spreadsheet_id=GOOGLE_SHEETS_ID,
        credentials_path=GOOGLE_SERVICE_ACCOUNT_FILE,
        worksheet_name=GOOGLE_SHEETS_WORKSHEET,
    )
    imported = 0
    skipped = 0
    for row in rows:
        name = row.get("name", "").strip()
        if not name:
            skipped += 1
            continue
        keywords = split_keywords(row.get("keywords", ""))
        category = row.get("category", "").strip() or "未分類"
        db.import_restaurant(
            restaurant_id=parse_optional_int(row.get("id", "")),
            name=name,
            category=category,
            area=row.get("area", "").strip() or None,
            google_maps_url=row.get("google_maps_url", "").strip() or None,
            tabelog_url=row.get("tabelog_url", "").strip() or None,
            comments=row.get("comments", "").strip(),
            keywords=keywords or [name, category],
            lunch_budget_text=row.get("lunch_budget", "").strip() or None,
            dinner_budget_text=row.get("dinner_budget", "").strip() or None,
        )
        imported += 1
    return {"ok": True, "imported": imported, "skipped": skipped}


@app.post("/api/backups")
def create_backup(_: None = Depends(require_admin)) -> dict:
    """Create a timestamped SQLite backup and rotate old backup files."""

    path = create_database_backup()
    return {"ok": True, "filename": path.name, "path": str(path), "count": len(list_backups())}


@app.get("/api/backups")
def backup_list(_: None = Depends(require_admin)) -> dict:
    """List available local database backups."""

    return {"backups": [backup.name for backup in list_backups()]}


@app.get("/api/backups/{filename}")
def download_backup(filename: str, _: None = Depends(require_admin)) -> FileResponse:
    """Download one local database backup."""

    backup = BACKUP_DIR / filename
    if backup.parent != BACKUP_DIR or not backup.exists() or backup.suffix != ".sqlite3":
        raise HTTPException(status_code=404, detail="找不到備份檔")
    return FileResponse(backup, filename=backup.name)


def create_database_backup() -> Path:
    """Use SQLite's backup API so the file is consistent while the app is running."""

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"restaurants_{timestamp}.sqlite3"
    source = sqlite3.connect(DB_PATH)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    rotate_backups()
    return backup_path


def list_backups() -> list[Path]:
    """Return backup files newest first."""

    if not BACKUP_DIR.exists():
        return []
    return sorted(BACKUP_DIR.glob("restaurants_*.sqlite3"), key=lambda path: path.stat().st_mtime, reverse=True)


def rotate_backups() -> None:
    """Keep only the newest configured number of backups."""

    for old_backup in list_backups()[BACKUP_KEEP:]:
        old_backup.unlink(missing_ok=True)


def parse_optional_int(value: str) -> int | None:
    """把 Sheet 裡的 id 轉成 int；空白或非數字就讓 SQLite 自動編號。"""

    text = str(value).strip()
    return int(text) if text.isdigit() else None


def split_keywords(value: str) -> list[str]:
    """支援逗號、日文頓號、換行分隔的關鍵字欄位。"""

    text = str(value).replace("、", ",").replace("\n", ",")
    return [part.strip() for part in text.split(",") if part.strip()]


PUBLIC_HTML = r"""
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>共享美食清單</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #657484;
      --line: #d9e0e7;
      --accent: #0f766e;
      --accent-soft: #dff3ef;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }

    header {
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      padding: 16px 22px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      position: sticky;
      top: 0;
      z-index: 5;
    }

    h1 { font-size: 20px; margin: 0; }
    a { color: var(--accent); text-decoration: none; }
    main {
      max-width: 1040px;
      margin: 0 auto;
      padding: 18px;
    }

    .toolbar {
      display: grid;
      grid-template-columns: 1fr 160px 160px;
      gap: 8px;
      margin-bottom: 14px;
    }

    input, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      font: inherit;
      background: #fff;
    }

    .summary {
      color: var(--muted);
      margin-bottom: 10px;
      font-size: 14px;
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 10px;
    }

    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }

    .card h2 {
      font-size: 17px;
      margin: 0 0 6px;
    }

    .meta, .comments {
      color: var(--muted);
      font-size: 14px;
    }

    .links {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }

    .links a {
      background: var(--accent-soft);
      border-radius: 6px;
      padding: 5px 8px;
      font-size: 13px;
    }

    .tags {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      margin: 8px 0;
    }

    .tag {
      background: #eef7f5;
      color: #0f766e;
      border: 1px solid #cbe7e2;
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 12px;
    }

    .about {
      margin-top: 22px;
      border-top: 1px solid var(--line);
      padding-top: 16px;
      color: var(--muted);
      font-size: 14px;
    }

    @media (max-width: 760px) {
      header { align-items: flex-start; flex-direction: column; }
      .toolbar { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>共享美食清單</h1>
    <a href="/admin">管理後台</a>
  </header>
  <main>
    <div class="toolbar">
      <input id="keyword" placeholder="搜尋店名、分類、地區、評論">
      <select id="area"><option value="">全部地區</option></select>
      <select id="category"><option value="">全部分類</option></select>
    </div>
    <div id="summary" class="summary"></div>
    <div id="grid" class="grid"></div>
    <section class="about">
      <strong>About this project</strong><br>
      Discord messages are saved into a SQLite restaurant database, then shown here through a FastAPI public page.
      The same data can be synced to Google Sheets and imported into Google My Maps. The bot runs on a GCP VM with systemd and HTTPS.
    </section>
  </main>

  <script>
    const state = { restaurants: [], areas: [], categories: [] };
    const $ = (id) => document.getElementById(id);

    function params() {
      const values = new URLSearchParams();
      if ($("keyword").value.trim()) values.set("keyword", $("keyword").value.trim());
      if ($("area").value) values.set("area", $("area").value);
      if ($("category").value) values.set("category", $("category").value);
      return values.toString();
    }

    async function loadRestaurants() {
      const response = await fetch(`/api/restaurants?${params()}`);
      const data = await response.json();
      state.restaurants = data.restaurants.sort((a, b) => a.id - b.id);
      state.areas = data.areas;
      state.categories = data.categories;
      renderFilters();
      renderCards();
    }

    function renderFilters() {
      fillSelect($("area"), "全部地區", state.areas);
      fillSelect($("category"), "全部分類", state.categories);
    }

    function fillSelect(select, label, values) {
      const current = select.value;
      select.innerHTML = `<option value="">${label}</option>`;
      values.forEach((value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      });
      if (values.includes(current)) select.value = current;
    }

    function renderCards() {
      $("summary").textContent = `目前顯示 ${state.restaurants.length} 間餐廳`;
      $("grid").innerHTML = "";
      state.restaurants.forEach((restaurant) => {
        const card = document.createElement("article");
        card.className = "card";
        card.innerHTML = `
          <h2>${escapeHtml(restaurant.name)}</h2>
          <div class="meta">ID ${restaurant.id} / ${escapeHtml(restaurant.category)} ${escapeHtml(restaurant.area || "")}</div>
          <div class="meta">${priceText(restaurant)}</div>
          <div class="tags">${tagHtml(restaurant.tags || restaurant.keywords || [])}</div>
          <div class="comments">${escapeHtml(shortText(restaurant.comments || ""))}</div>
          <div class="links">
            ${restaurant.google_maps_url ? `<a target="_blank" rel="noreferrer" href="${escapeAttr(restaurant.google_maps_url)}">Google Maps</a>` : ""}
            ${restaurant.tabelog_url ? `<a target="_blank" rel="noreferrer" href="${escapeAttr(restaurant.tabelog_url)}">食べログ</a>` : ""}
          </div>
        `;
        $("grid").appendChild(card);
      });
    }

    function shortText(value) {
      return value.length > 120 ? `${value.slice(0, 120)}...` : value;
    }

    function priceText(restaurant) {
      const parts = [];
      if (restaurant.lunch_budget_text) parts.push(`午餐 ${restaurant.lunch_budget_text}`);
      if (restaurant.dinner_budget_text) parts.push(`晚餐 ${restaurant.dinner_budget_text}`);
      return escapeHtml(parts.join(" / "));
    }

    function tagHtml(tags) {
      return tags.slice(0, 6).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (char) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      }[char]));
    }

    function escapeAttr(value) {
      return escapeHtml(value);
    }

    function debounce(fn, delay) {
      let timer = null;
      return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
      };
    }

    $("keyword").addEventListener("input", debounce(loadRestaurants, 250));
    $("area").addEventListener("change", loadRestaurants);
    $("category").addEventListener("change", loadRestaurants);
    loadRestaurants();
  </script>
</body>
</html>
"""


ADMIN_HTML = r"""
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>食べログ Bot 管理後台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #657484;
      --line: #d9e0e7;
      --accent: #0f766e;
      --accent-strong: #115e59;
      --danger: #b42318;
      --danger-bg: #fff0ed;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }

    header {
      position: sticky;
      top: 0;
      z-index: 10;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      padding: 14px 22px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }

    h1 {
      font-size: 18px;
      margin: 0;
      font-weight: 700;
    }

    main {
      display: grid;
      grid-template-columns: minmax(300px, 420px) minmax(0, 1fr);
      min-height: calc(100vh - 58px);
    }

    .sidebar {
      border-right: 1px solid var(--line);
      background: var(--panel);
      padding: 16px;
      overflow: auto;
    }

    .editor {
      padding: 18px 22px 28px;
      overflow: auto;
    }

    .toolbar {
      display: grid;
      grid-template-columns: 1fr 140px 140px;
      gap: 8px;
      margin-bottom: 12px;
    }

    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
      background: #fff;
      color: var(--text);
    }

    textarea {
      min-height: 150px;
      resize: vertical;
    }

    button {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 9px 12px;
      font: inherit;
      cursor: pointer;
    }

    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }

    button.primary:hover { background: var(--accent-strong); }

    .nav-link {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 11px;
      color: var(--accent);
      text-decoration: none;
      background: #fff;
    }

    button.danger {
      border-color: #fecaca;
      background: var(--danger-bg);
      color: var(--danger);
    }

    .actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }

    .restaurant-list {
      display: grid;
      gap: 8px;
    }

    .row {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fff;
      cursor: pointer;
    }

    .row.active {
      border-color: var(--accent);
      background: #dff3ef;
      box-shadow: 0 0 0 2px rgba(15, 118, 110, 0.16);
    }

    .row.active .row-title {
      color: var(--accent-strong);
    }

    .row.active .row-meta {
      color: #315f5b;
    }

    .row-title {
      font-weight: 700;
      margin-bottom: 4px;
    }

    .row-meta, .hint, .status {
      color: var(--muted);
      font-size: 13px;
    }

    .status {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin: 6px 0 10px;
    }

    .pager {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      white-space: nowrap;
    }

    .pager button {
      padding: 5px 8px;
      font-size: 12px;
    }

    .pager button:disabled {
      cursor: not-allowed;
      opacity: 0.45;
    }

    .form-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin: 14px 0;
    }

    label {
      display: grid;
      gap: 5px;
      font-size: 13px;
      color: var(--muted);
      font-weight: 600;
    }

    label span { color: var(--muted); }
    label.full { grid-column: 1 / -1; }

    .empty {
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 28px;
      text-align: center;
      color: var(--muted);
      background: #fff;
    }

    .message {
      margin-top: 10px;
      min-height: 22px;
      color: var(--accent-strong);
      font-size: 14px;
    }

    .login-panel {
      background: #f6f7f9;
      border-bottom: 1px solid var(--line);
      padding: 18px 22px;
    }

    .login-panel.hidden { display: none; }

    .login-box {
      max-width: 520px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }

    .login-box h2 {
      font-size: 17px;
      margin: 0 0 6px;
    }

    .login-box p {
      margin: 0 0 10px;
      color: var(--muted);
      font-size: 14px;
    }

    @media (max-width: 860px) {
      main { grid-template-columns: 1fr; }
      .sidebar { border-right: 0; border-bottom: 1px solid var(--line); }
      .toolbar { grid-template-columns: 1fr; }
      .form-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>食べログ Bot 管理後台</h1>
    <div class="actions">
      <a class="nav-link" href="/">公開頁</a>
      <button id="importSheet" class="primary">從 Google Sheet 匯入</button>
      <button id="syncSheet">DB 同步到 Sheet</button>
      <button id="reload">重新整理</button>
    </div>
  </header>

  <section id="loginPanel" class="login-panel">
    <div class="login-box">
      <h2>管理密碼</h2>
      <p>編輯、刪除、匯入與同步需要管理密碼。</p>
      <input id="adminPassword" type="password" placeholder="輸入 ADMIN_PASSWORD">
      <button id="loginButton" class="primary" type="button">進入管理後台</button>
      <div id="loginMessage" class="message"></div>
    </div>
  </section>

  <main>
    <section class="sidebar">
      <div class="toolbar">
        <input id="keyword" placeholder="搜尋店名、分類、地區、評論">
        <select id="area"><option value="">全部地區</option></select>
        <select id="category"><option value="">全部分類</option></select>
      </div>
      <div id="count" class="status"></div>
      <div id="restaurantList" class="restaurant-list"></div>
    </section>

    <section class="editor">
      <div id="empty" class="empty">從左邊選一間餐廳開始編輯。</div>
      <form id="editorForm" hidden>
        <div class="actions">
          <button class="primary" type="submit">儲存修改</button>
          <button class="danger" id="deleteRestaurant" type="button">刪除餐廳</button>
        </div>

        <div class="form-grid">
          <label>店名
            <input id="name" required>
          </label>
          <label>分類
            <input id="categoryInput" required>
          </label>
          <label>地區
            <input id="areaInput">
          </label>
          <label>關鍵字，用逗號分隔
            <input id="keywordsInput">
          </label>
          <label>午餐價格
            <input id="lunchBudget" placeholder="例：￥1,000～￥1,999">
          </label>
          <label>晚餐價格
            <input id="dinnerBudget" placeholder="例：￥2,000～￥2,999">
          </label>
          <label>午餐最低
            <input id="lunchMin" type="number" min="0" step="1">
          </label>
          <label>午餐最高
            <input id="lunchMax" type="number" min="0" step="1">
          </label>
          <label>晚餐最低
            <input id="dinnerMin" type="number" min="0" step="1">
          </label>
          <label>晚餐最高
            <input id="dinnerMax" type="number" min="0" step="1">
          </label>
          <label class="full">食べログ URL
            <input id="tabelogUrl">
          </label>
          <label class="full">Google Maps URL
            <input id="googleMapsUrl">
          </label>
          <label class="full">評論
            <textarea id="comments"></textarea>
          </label>
        </div>

        <label class="full">追加評論
          <textarea id="newComment" placeholder="輸入要追加的評論，不會覆蓋原本內容"></textarea>
        </label>
        <div class="actions" style="margin-top: 8px;">
          <button id="appendComment" type="button">追加評論</button>
        </div>
        <div id="message" class="message"></div>
      </form>
    </section>
  </main>

  <script>
    const state = {
      restaurants: [],
      selectedId: null,
      areas: [],
      categories: [],
      page: 1,
      pageSize: 10,
      adminPassword: localStorage.getItem("tabelogAdminPassword") || ""
    };

    const $ = (id) => document.getElementById(id);
    const backupButton = document.createElement("button");
    backupButton.id = "createBackup";
    backupButton.type = "button";
    backupButton.textContent = "立即備份 DB";
    $("reload").insertAdjacentElement("beforebegin", backupButton);
    const commentItems = document.createElement("div");
    commentItems.id = "commentItems";
    commentItems.className = "status";
    $("comments").closest("label").insertAdjacentElement("afterend", commentItems);

    function setMessage(text, isError = false) {
      $("message").textContent = text;
      $("message").style.color = isError ? "var(--danger)" : "var(--accent-strong)";
    }

    function setLoginMessage(text, isError = false) {
      $("loginMessage").textContent = text;
      $("loginMessage").style.color = isError ? "var(--danger)" : "var(--accent-strong)";
    }

    function adminHeaders() {
      return {
        "Content-Type": "application/json",
        "X-Admin-Password": state.adminPassword
      };
    }

    async function checkAdminPassword() {
      const response = await fetch("/api/admin/check", {
        method: "POST",
        headers: {"X-Admin-Password": state.adminPassword}
      });
      return response.ok;
    }

    async function initAdminAuth() {
      const status = await fetch("/api/admin/status").then((response) => response.json());
      if (!status.password_configured) {
        setLoginMessage("尚未設定 ADMIN_PASSWORD。請先在 .env 裡加入管理密碼。", true);
        return;
      }
      if (state.adminPassword && await checkAdminPassword()) {
        $("loginPanel").classList.add("hidden");
        return;
      }
      localStorage.removeItem("tabelogAdminPassword");
      state.adminPassword = "";
    }

    function keywordParams() {
      const params = new URLSearchParams();
      if ($("keyword").value.trim()) params.set("keyword", $("keyword").value.trim());
      if ($("area").value) params.set("area", $("area").value);
      if ($("category").value) params.set("category", $("category").value);
      return params.toString();
    }

    async function loadRestaurants() {
      const response = await fetch(`/api/restaurants?${keywordParams()}`);
      const data = await response.json();
      state.restaurants = data.restaurants.sort((a, b) => a.id - b.id);
      state.areas = data.areas;
      state.categories = data.categories;
      const maxPage = Math.max(1, Math.ceil(state.restaurants.length / state.pageSize));
      if (state.page > maxPage) state.page = maxPage;
      renderFilters();
      renderList();
      if (state.selectedId && !state.restaurants.some((item) => item.id === state.selectedId)) {
        clearEditor();
      }
    }

    function renderFilters() {
      fillSelect($("area"), "全部地區", state.areas);
      fillSelect($("category"), "全部分類", state.categories);
    }

    function fillSelect(select, label, values) {
      const current = select.value;
      select.innerHTML = `<option value="">${label}</option>`;
      values.forEach((value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      });
      if (values.includes(current)) select.value = current;
    }

    function renderList() {
      const total = state.restaurants.length;
      const maxPage = Math.max(1, Math.ceil(total / state.pageSize));
      const start = (state.page - 1) * state.pageSize;
      const pageItems = state.restaurants.slice(start, start + state.pageSize);

      $("count").innerHTML = `
        <span>目前顯示 ${total} 間餐廳，ID 由小到大排列</span>
        <span class="pager">
          <button id="prevListPage" type="button" ${state.page <= 1 ? "disabled" : ""}>上一頁</button>
          <span>${state.page} / ${maxPage}</span>
          <button id="nextListPage" type="button" ${state.page >= maxPage ? "disabled" : ""}>下一頁</button>
        </span>
      `;
      $("prevListPage").addEventListener("click", () => {
        state.page = Math.max(1, state.page - 1);
        renderList();
      });
      $("nextListPage").addEventListener("click", () => {
        state.page = Math.min(maxPage, state.page + 1);
        renderList();
      });

      $("restaurantList").innerHTML = "";
      pageItems.forEach((restaurant) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = `row ${restaurant.id === state.selectedId ? "active" : ""}`;
        row.innerHTML = `
          <div class="row-title">${escapeHtml(restaurant.name)}</div>
          <div class="row-meta">ID ${restaurant.id} / ${escapeHtml(restaurant.category)} ${escapeHtml(restaurant.area || "")}</div>
        `;
        row.addEventListener("click", () => selectRestaurant(restaurant.id));
        $("restaurantList").appendChild(row);
      });
    }

    async function selectRestaurant(id) {
      const response = await fetch(`/api/restaurants/${id}`);
      const restaurant = await response.json();
      state.selectedId = restaurant.id;
      $("empty").hidden = true;
      $("editorForm").hidden = false;
      $("name").value = restaurant.name || "";
      $("categoryInput").value = restaurant.category || "";
      $("areaInput").value = restaurant.area || "";
      $("keywordsInput").value = (restaurant.tags || restaurant.keywords || []).join(", ");
      $("lunchBudget").value = restaurant.lunch_budget_text || "";
      $("dinnerBudget").value = restaurant.dinner_budget_text || "";
      $("lunchMin").value = restaurant.lunch_budget_min || "";
      $("lunchMax").value = restaurant.lunch_budget_max || "";
      $("dinnerMin").value = restaurant.dinner_budget_min || "";
      $("dinnerMax").value = restaurant.dinner_budget_max || "";
      $("tabelogUrl").value = restaurant.tabelog_url || "";
      $("googleMapsUrl").value = restaurant.google_maps_url || "";
      $("comments").value = restaurant.comments || "";
      renderCommentItems(restaurant.comment_items || []);
      $("newComment").value = "";
      setMessage("");
      renderList();
    }

    function clearEditor() {
      state.selectedId = null;
      $("empty").hidden = false;
      $("editorForm").hidden = true;
      renderList();
    }

    function editorPayload() {
      return {
        name: $("name").value.trim(),
        category: $("categoryInput").value.trim(),
        area: $("areaInput").value.trim() || null,
        tabelog_url: $("tabelogUrl").value.trim() || null,
        google_maps_url: $("googleMapsUrl").value.trim() || null,
        comments: $("comments").value.trim(),
        keywords: $("keywordsInput").value.split(",").map((item) => item.trim()).filter(Boolean),
        tags: $("keywordsInput").value.split(",").map((item) => item.trim()).filter(Boolean),
        lunch_budget_text: $("lunchBudget").value.trim() || null,
        lunch_budget_min: numberOrNull($("lunchMin").value),
        lunch_budget_max: numberOrNull($("lunchMax").value),
        dinner_budget_text: $("dinnerBudget").value.trim() || null,
        dinner_budget_min: numberOrNull($("dinnerMin").value),
        dinner_budget_max: numberOrNull($("dinnerMax").value)
      };
    }

    function numberOrNull(value) {
      const text = String(value).trim();
      return text ? Number(text) : null;
    }

    function renderCommentItems(items) {
      if (!items.length) {
        $("commentItems").innerHTML = "目前沒有獨立評論紀錄。";
        return;
      }
      $("commentItems").innerHTML = items.map((item) => `
        <div style="border:1px solid var(--line); border-radius:6px; padding:8px; margin-bottom:6px; background:#fff;">
          <strong>${escapeHtml(item.created_by || "Unknown")}</strong>
          <span style="color:var(--muted);"> ${escapeHtml(item.created_at || "")}</span>
          <div>${escapeHtml(item.comment || "")}</div>
        </div>
      `).join("");
    }

    $("editorForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!state.selectedId) return;
      const response = await fetch(`/api/restaurants/${state.selectedId}`, {
        method: "PUT",
        headers: adminHeaders(),
        body: JSON.stringify(editorPayload())
      });
      if (!response.ok) {
        setMessage("儲存失敗，請確認欄位內容。", true);
        return;
      }
      setMessage("已儲存修改。");
      await loadRestaurants();
    });

    $("appendComment").addEventListener("click", async () => {
      if (!state.selectedId || !$("newComment").value.trim()) return;
      const response = await fetch(`/api/restaurants/${state.selectedId}/comments`, {
        method: "POST",
        headers: adminHeaders(),
        body: JSON.stringify({comment: $("newComment").value.trim(), created_by: "Admin"})
      });
      if (!response.ok) {
        setMessage("追加評論失敗。", true);
        return;
      }
      const restaurant = await response.json();
      $("comments").value = restaurant.comments || "";
      renderCommentItems(restaurant.comment_items || []);
      $("newComment").value = "";
      setMessage("已追加評論。");
    });

    backupButton.addEventListener("click", async () => {
      if (!confirm("要立即建立一份 SQLite 備份嗎？")) return;
      backupButton.disabled = true;
      backupButton.textContent = "備份中...";
      try {
        const response = await fetch("/api/backups", {
          method: "POST",
          headers: {"X-Admin-Password": state.adminPassword}
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "備份失敗");
        alert(`已建立備份：${data.filename}`);
      } catch (error) {
        alert(error.message);
      } finally {
        backupButton.disabled = false;
        backupButton.textContent = "立即備份 DB";
      }
    });

    $("deleteRestaurant").addEventListener("click", async () => {
      if (!state.selectedId) return;
      if (!confirm("確定要刪除這間餐廳嗎？這個動作不能復原。")) return;
      const response = await fetch(`/api/restaurants/${state.selectedId}`, {
        method: "DELETE",
        headers: {"X-Admin-Password": state.adminPassword}
      });
      if (!response.ok) {
        setMessage("刪除失敗。", true);
        return;
      }
      clearEditor();
      await loadRestaurants();
    });

    $("importSheet").addEventListener("click", async () => {
      if (!confirm("要把 Google Sheet 的資料匯入到本機資料庫嗎？同 ID 的餐廳會被 Sheet 內容更新。")) return;
      const button = $("importSheet");
      button.disabled = true;
      button.textContent = "匯入中...";
      try {
        const response = await fetch("/api/import-sheet", {
          method: "POST",
          headers: {"X-Admin-Password": state.adminPassword}
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "匯入失敗");
        alert(`已從 Google Sheet 匯入 ${data.imported} 筆，略過 ${data.skipped} 筆。`);
        clearEditor();
        await loadRestaurants();
      } catch (error) {
        alert(error.message);
      } finally {
        button.disabled = false;
        button.textContent = "從 Google Sheet 匯入";
      }
    });

    $("syncSheet").addEventListener("click", async () => {
      if (!confirm("這會用目前資料庫內容覆蓋 Google Sheet。確定要繼續嗎？")) return;
      const button = $("syncSheet");
      button.disabled = true;
      button.textContent = "同步中...";
      try {
        const response = await fetch("/api/sync-sheet", {
          method: "POST",
          headers: {"X-Admin-Password": state.adminPassword}
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "同步失敗");
        alert(`已同步 ${data.count} 筆餐廳到 Google Sheet。`);
      } catch (error) {
        alert(error.message);
      } finally {
        button.disabled = false;
        button.textContent = "DB 同步到 Sheet";
      }
    });

    $("reload").addEventListener("click", loadRestaurants);
    $("loginButton").addEventListener("click", async () => {
      state.adminPassword = $("adminPassword").value;
      if (!state.adminPassword) {
        setLoginMessage("請輸入管理密碼。", true);
        return;
      }
      if (!await checkAdminPassword()) {
        setLoginMessage("管理密碼錯誤。", true);
        return;
      }
      localStorage.setItem("tabelogAdminPassword", state.adminPassword);
      $("loginPanel").classList.add("hidden");
      setLoginMessage("");
    });

    $("keyword").addEventListener("input", debounce(() => {
      state.page = 1;
      loadRestaurants();
    }, 250));
    $("area").addEventListener("change", () => {
      state.page = 1;
      loadRestaurants();
    });
    $("category").addEventListener("change", () => {
      state.page = 1;
      loadRestaurants();
    });

    function debounce(fn, delay) {
      let timer = null;
      return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
      };
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }[char]));
    }

    initAdminAuth();
    loadRestaurants();
  </script>
</body>
</html>
"""

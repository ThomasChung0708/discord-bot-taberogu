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
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db import Restaurant, RestaurantDB
from extractor import fetch_tabelog_price_info
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
UPLOAD_DIR_VALUE = os.getenv("UPLOAD_DIR", "uploads").strip() or "uploads"
UPLOAD_DIR = Path(UPLOAD_DIR_VALUE)
if not UPLOAD_DIR.is_absolute():
    UPLOAD_DIR = BASE_DIR / UPLOAD_DIR
RESTAURANT_IMAGE_DIR = UPLOAD_DIR / "restaurants"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

db = RestaurantDB(str(DB_PATH))
app = FastAPI(title="Discord 食べログ Bot Admin")
RESTAURANT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


class RestaurantPayload(BaseModel):
    """前端送來的餐廳編輯資料。"""

    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    area: str | None = None
    tabelog_url: str | None = None
    google_maps_url: str | None = None
    image_url: str | None = None
    comments: str = ""
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    lunch_budget_text: str | None = None
    lunch_budget_min: int | None = None
    lunch_budget_max: int | None = None
    dinner_budget_text: str | None = None
    dinner_budget_min: int | None = None
    dinner_budget_max: int | None = None


class EnrichPricesPayload(BaseModel):
    limit: int = Field(default=5, ge=1, le=20)


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
        "image_url": restaurant.image_url,
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
        image_url=payload.image_url,
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


@app.post("/api/restaurants/{restaurant_id}/image")
async def upload_restaurant_image(
    restaurant_id: int,
    request: Request,
    _: None = Depends(require_admin),
) -> dict:
    """Upload a local image for one restaurant and store its public URL."""

    if not db.get(restaurant_id):
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    suffix = ALLOWED_IMAGE_TYPES.get(content_type)
    if not suffix:
        raise HTTPException(status_code=400, detail="只支援 JPG、PNG、WEBP、GIF 圖片")

    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="沒有收到圖片資料")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="圖片不能超過 5 MB")
    filename = f"restaurant-{restaurant_id}-{uuid.uuid4().hex}{suffix}"
    path = RESTAURANT_IMAGE_DIR / filename
    path.write_bytes(data)

    restaurant = db.update_image_url(restaurant_id, f"/uploads/restaurants/{filename}")
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
            image_url=row.get("image_url", "").strip() or None,
            comments=row.get("comments", "").strip(),
            keywords=keywords or [name, category],
            lunch_budget_text=row.get("lunch_budget", "").strip() or None,
            dinner_budget_text=row.get("dinner_budget", "").strip() or None,
        )
        imported += 1
    return {"ok": True, "imported": imported, "skipped": skipped}


@app.post("/api/cleanup-taxonomy")
def cleanup_taxonomy(_: None = Depends(require_admin)) -> dict:
    """Normalize existing area/category values."""

    changed = db.cleanup_area_categories()
    return {"ok": True, "changed": changed}


@app.post("/api/enrich-prices")
def enrich_prices(payload: EnrichPricesPayload, _: None = Depends(require_admin)) -> dict:
    """Fetch missing lunch/dinner prices from saved Tabelog URLs."""

    restaurants = db.restaurants_missing_prices(limit=payload.limit)
    updated: list[str] = []
    not_found: list[str] = []
    for restaurant in restaurants:
        price = fetch_tabelog_price_info(restaurant.tabelog_url)
        if not price.price_updated_at:
            not_found.append(restaurant.name)
            continue
        db.update_price_info(
            restaurant_id=restaurant.id,
            lunch_budget_text=price.lunch_budget_text,
            lunch_budget_min=price.lunch_budget_min,
            lunch_budget_max=price.lunch_budget_max,
            dinner_budget_text=price.dinner_budget_text,
            dinner_budget_min=price.dinner_budget_min,
            dinner_budget_max=price.dinner_budget_max,
            price_updated_at=price.price_updated_at,
        )
        updated.append(
            f"ID {restaurant.id}: {restaurant.name} "
            f"午餐 {price.lunch_budget_text or '-'} / 晚餐 {price.dinner_budget_text or '-'}"
        )

    return {
        "ok": True,
        "checked": len(restaurants),
        "updated": len(updated),
        "not_found": len(not_found),
        "updated_items": updated[:10],
        "not_found_items": not_found[:10],
    }


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
      color-scheme: light;
      --bg: #f8f3ee;
      --bg-band: #fff8f1;
      --panel: #fffdf9;
      --panel-strong: #ffffff;
      --text: #2d251f;
      --muted: #7b6d62;
      --line: #ead8c8;
      --line-strong: #d9b89d;
      --accent: #b45635;
      --accent-strong: #893d27;
      --accent-soft: #f6dfd1;
      --sage: #4f7864;
      --sage-soft: #e6f0e8;
      --shadow: 0 14px 34px rgba(97, 63, 40, 0.10);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        linear-gradient(180deg, var(--bg-band) 0, var(--bg) 280px, #f7f1eb 100%);
      color: var(--text);
      font-family: "Yu Gothic UI", "Hiragino Sans", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }

    header {
      background: rgba(255, 253, 249, 0.94);
      border-bottom: 1px solid rgba(234, 216, 200, 0.85);
      padding: 14px 24px;
      position: sticky;
      top: 0;
      z-index: 5;
      backdrop-filter: blur(16px);
    }

    .header-inner {
      max-width: 1160px;
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }

    .brand-mark {
      width: 34px;
      height: 34px;
      border-radius: 8px;
      background: var(--accent);
      color: #fff7ed;
      display: grid;
      place-items: center;
      font-weight: 800;
      box-shadow: 0 8px 18px rgba(180, 86, 53, 0.22);
    }

    h1 {
      font-size: 20px;
      margin: 0;
      letter-spacing: 0;
      line-height: 1.2;
    }

    .subtitle {
      margin-top: 2px;
      color: var(--muted);
      font-size: 12px;
    }

    a { color: var(--accent-strong); text-decoration: none; }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }

    .lang-switch {
      display: inline-flex;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: var(--panel-strong);
    }

    .lang-switch button {
      border: 0;
      border-right: 1px solid var(--line);
      background: transparent;
      color: var(--muted);
      padding: 7px 9px;
      font: inherit;
      cursor: pointer;
    }

    .lang-switch button:last-child { border-right: 0; }
    .lang-switch button.active {
      background: var(--accent);
      color: #fff8f1;
    }

    .nav-link {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 7px 10px;
      background: var(--panel-strong);
      color: var(--text);
      font-size: 14px;
    }

    main {
      max-width: 1160px;
      margin: 0 auto;
      padding: 24px 18px 36px;
    }

    .intro {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: end;
      gap: 18px;
      margin-bottom: 16px;
      padding: 8px 2px 4px;
    }

    .intro-title {
      margin: 0;
      font-size: 30px;
      line-height: 1.15;
      letter-spacing: 0;
    }

    .intro-copy {
      margin: 8px 0 0;
      color: var(--muted);
      max-width: 620px;
    }

    .count-pill {
      border: 1px solid var(--line-strong);
      background: var(--panel);
      color: var(--accent-strong);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 14px;
      white-space: nowrap;
    }

    .toolbar {
      display: grid;
      grid-template-columns: 1fr 160px 160px;
      gap: 10px;
      margin-bottom: 16px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 253, 249, 0.88);
      box-shadow: var(--shadow);
    }

    input, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 11px 12px;
      font: inherit;
      background: var(--panel-strong);
      color: var(--text);
      outline: none;
    }

    input:focus, select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(180, 86, 53, 0.15);
    }

    .summary {
      color: var(--muted);
      margin-bottom: 12px;
      font-size: 14px;
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 14px;
    }

    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      min-height: 218px;
      position: relative;
      box-shadow: 0 8px 22px rgba(85, 55, 33, 0.07);
      transition: transform 140ms ease, border-color 140ms ease, box-shadow 140ms ease;
    }

    .card:hover {
      transform: translateY(-2px);
      border-color: var(--line-strong);
      box-shadow: 0 16px 32px rgba(85, 55, 33, 0.12);
    }

    .card h2 {
      font-size: 17px;
      margin: 0 0 8px;
      line-height: 1.35;
    }

    .card.has-image h2 {
      padding-right: 104px;
    }

    .food-image {
      width: 88px;
      height: 88px;
      border: 1px solid var(--line);
      border-radius: 8px;
      object-fit: cover;
      position: absolute;
      top: 16px;
      right: 16px;
      background: #f2ebe2;
      box-shadow: 0 8px 18px rgba(57, 40, 28, 0.12);
    }

    .card.has-image .meta,
    .card.has-image .tags,
    .card.has-image .comments {
      padding-right: 96px;
    }

    .meta, .comments {
      color: var(--muted);
      font-size: 14px;
    }

    .meta {
      margin-bottom: 3px;
    }

    .comments {
      margin-top: 8px;
    }

    .links {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }

    .links a {
      background: var(--accent-soft);
      border: 1px solid #efc6b2;
      border-radius: 8px;
      padding: 6px 9px;
      font-size: 13px;
      color: var(--accent-strong);
      font-weight: 650;
    }

    .links a:first-child {
      background: var(--sage-soft);
      border-color: #c8dfcf;
      color: #315d48;
    }

    .tags {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 10px 0;
    }

    .tag {
      background: var(--sage-soft);
      color: #315d48;
      border: 1px solid #c8dfcf;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
    }

    @media (max-width: 760px) {
      header { padding: 12px 14px; }
      .header-inner { align-items: flex-start; flex-direction: column; }
      .intro { grid-template-columns: 1fr; }
      .intro-title { font-size: 25px; }
      .toolbar { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-inner">
      <div class="brand">
        <div class="brand-mark">食</div>
        <div>
          <h1 data-i18n="title">共享美食清單</h1>
          <div class="subtitle" data-i18n="subtitle">Discord 頻道裡收集的餐廳口袋名單</div>
        </div>
      </div>
      <div class="header-actions">
        <div class="lang-switch" aria-label="Language">
          <button id="langZh" type="button">中文</button>
          <button id="langJa" type="button">日本語</button>
        </div>
        <a class="nav-link" href="/admin" data-i18n="adminLink">管理後台</a>
      </div>
    </div>
  </header>
  <main>
    <section class="intro">
      <div>
        <h2 class="intro-title" data-i18n="introTitle">今天想去哪裡吃？</h2>
        <p class="intro-copy" data-i18n="introCopy">用店名、地區或料理類型快速翻找大家存下來的店。</p>
      </div>
      <div id="summary" class="count-pill"></div>
    </section>
    <div class="toolbar">
      <input id="keyword" data-i18n-placeholder="searchPlaceholder" placeholder="搜尋店名、分類、地區、評論">
      <select id="area"><option value="">全部地區</option></select>
      <select id="category"><option value="">全部分類</option></select>
    </div>
    <div id="grid" class="grid"></div>
  </main>

  <script>
    const translations = {
      zh: {
        title: "共享美食清單",
        subtitle: "Discord 頻道裡收集的餐廳口袋名單",
        introTitle: "今天想去哪裡吃？",
        introCopy: "用店名、地區或料理類型快速翻找大家存下來的店。",
        adminLink: "管理後台",
        searchPlaceholder: "搜尋店名、分類、地區、評論",
        allAreas: "全部地區",
        allCategories: "全部分類",
        summary: (count) => `目前顯示 ${count} 間餐廳`,
        lunch: "午餐",
        dinner: "晚餐"
      },
      ja: {
        title: "共有グルメリスト",
        subtitle: "Discord チャンネルで集めたお店リスト",
        introTitle: "今日はどこで食べる？",
        introCopy: "店名・エリア・料理ジャンルから、保存したお店をすぐに探せます。",
        adminLink: "管理画面",
        searchPlaceholder: "店名・分類・エリア・コメントを検索",
        allAreas: "すべてのエリア",
        allCategories: "すべての分類",
        summary: (count) => `${count} 件のレストランを表示中`,
        lunch: "ランチ",
        dinner: "ディナー"
      }
    };
    const savedLanguage = localStorage.getItem("tabelogLanguage");
    const state = { restaurants: [], areas: [], categories: [], language: savedLanguage || "zh" };
    const $ = (id) => document.getElementById(id);
    const t = (key, ...args) => {
      const value = translations[state.language][key] || translations.zh[key] || key;
      return typeof value === "function" ? value(...args) : value;
    };

    function applyLanguage() {
      document.documentElement.lang = state.language === "ja" ? "ja" : "zh-Hant";
      document.querySelectorAll("[data-i18n]").forEach((element) => {
        element.textContent = t(element.dataset.i18n);
      });
      document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
        element.placeholder = t(element.dataset.i18nPlaceholder);
      });
      $("langZh").classList.toggle("active", state.language === "zh");
      $("langJa").classList.toggle("active", state.language === "ja");
      renderFilters();
      renderCards();
    }

    function setLanguage(language) {
      state.language = language;
      localStorage.setItem("tabelogLanguage", language);
      applyLanguage();
    }

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
      fillSelect($("area"), t("allAreas"), state.areas);
      fillSelect($("category"), t("allCategories"), state.categories);
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
      $("summary").textContent = t("summary", state.restaurants.length);
      $("grid").innerHTML = "";
      state.restaurants.forEach((restaurant) => {
        const card = document.createElement("article");
        card.className = `card ${restaurant.image_url ? "has-image" : ""}`;
        card.innerHTML = `
          ${restaurant.image_url ? `<img class="food-image" src="${escapeAttr(restaurant.image_url)}" alt="${escapeAttr(restaurant.name)}" onerror="this.remove()">` : ""}
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
      if (restaurant.lunch_budget_text) parts.push(`${t("lunch")} ${restaurant.lunch_budget_text}`);
      if (restaurant.dinner_budget_text) parts.push(`${t("dinner")} ${restaurant.dinner_budget_text}`);
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
    $("langZh").addEventListener("click", () => setLanguage("zh"));
    $("langJa").addEventListener("click", () => setLanguage("ja"));
    applyLanguage();
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

    .lang-switch {
      display: inline-flex;
      border: 1px solid var(--line);
      border-radius: 6px;
      overflow: hidden;
      background: #fff;
    }

    .lang-switch button {
      border: 0;
      border-right: 1px solid var(--line);
      border-radius: 0;
      color: var(--muted);
      padding: 8px 10px;
    }

    .lang-switch button:last-child { border-right: 0; }
    .lang-switch button.active {
      background: var(--accent);
      color: #fff;
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

    .image-upload {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }

    .image-upload input[type="file"] {
      flex: 1;
      min-width: 220px;
    }

    .image-preview {
      width: 120px;
      height: 90px;
      border: 1px solid var(--line);
      border-radius: 8px;
      object-fit: cover;
      background: #edf5f3;
    }

    .image-preview[hidden] { display: none; }

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
    <h1 data-i18n="adminTitle">食べログ Bot 管理後台</h1>
    <div class="actions">
      <div class="lang-switch" aria-label="Language">
        <button id="adminLangZh" type="button">中文</button>
        <button id="adminLangJa" type="button">日本語</button>
      </div>
      <a class="nav-link" href="/" data-i18n="publicPage">公開頁</a>
      <button id="importSheet" class="primary" data-i18n="importSheet">從 Google Sheet 匯入</button>
      <button id="syncSheet" data-i18n="syncSheet">DB 同步到 Sheet</button>
      <button id="reload" data-i18n="reload">重新整理</button>
    </div>
  </header>

  <section id="loginPanel" class="login-panel">
    <div class="login-box">
      <h2 data-i18n="adminPasswordTitle">管理密碼</h2>
      <p data-i18n="adminPasswordHelp">編輯、刪除、匯入與同步需要管理密碼。</p>
      <input id="adminPassword" type="password" data-i18n-placeholder="adminPasswordPlaceholder" placeholder="輸入 ADMIN_PASSWORD">
      <button id="loginButton" class="primary" type="button" data-i18n="login">進入管理後台</button>
      <div id="loginMessage" class="message"></div>
    </div>
  </section>

  <main>
    <section class="sidebar">
      <div class="toolbar">
        <input id="keyword" data-i18n-placeholder="searchPlaceholder" placeholder="搜尋店名、分類、地區、評論">
        <select id="area"><option value="">全部地區</option></select>
        <select id="category"><option value="">全部分類</option></select>
      </div>
      <div id="count" class="status"></div>
      <div id="restaurantList" class="restaurant-list"></div>
    </section>

    <section class="editor">
      <div id="empty" class="empty" data-i18n="emptyEditor">從左邊選一間餐廳開始編輯。</div>
      <form id="editorForm" hidden>
        <div class="actions">
          <button class="primary" type="submit" data-i18n="saveChanges">儲存修改</button>
          <button class="danger" id="deleteRestaurant" type="button" data-i18n="deleteRestaurant">刪除餐廳</button>
        </div>

        <div class="form-grid">
          <label><span data-i18n="name">店名</span>
            <input id="name" required>
          </label>
          <label><span data-i18n="category">分類</span>
            <input id="categoryInput" required>
          </label>
          <label><span data-i18n="area">地區</span>
            <input id="areaInput">
          </label>
          <label><span data-i18n="keywords">關鍵字，用逗號分隔</span>
            <input id="keywordsInput">
          </label>
          <label><span data-i18n="lunchBudget">午餐價格</span>
            <input id="lunchBudget" data-i18n-placeholder="lunchBudgetPlaceholder" placeholder="例：￥1,000～￥1,999">
          </label>
          <label><span data-i18n="dinnerBudget">晚餐價格</span>
            <input id="dinnerBudget" data-i18n-placeholder="dinnerBudgetPlaceholder" placeholder="例：￥2,000～￥2,999">
          </label>
          <label><span data-i18n="lunchMin">午餐最低</span>
            <input id="lunchMin" type="number" min="0" step="1">
          </label>
          <label><span data-i18n="lunchMax">午餐最高</span>
            <input id="lunchMax" type="number" min="0" step="1">
          </label>
          <label><span data-i18n="dinnerMin">晚餐最低</span>
            <input id="dinnerMin" type="number" min="0" step="1">
          </label>
          <label><span data-i18n="dinnerMax">晚餐最高</span>
            <input id="dinnerMax" type="number" min="0" step="1">
          </label>
          <label class="full">食べログ URL
            <input id="tabelogUrl">
          </label>
          <label class="full">Google Maps URL
            <input id="googleMapsUrl">
          </label>
          <label class="full"><span data-i18n="imageUrl">圖片 URL</span>
            <input id="imageUrl">
          </label>
          <label class="full"><span data-i18n="uploadImage">上傳圖片</span>
            <div class="image-upload">
              <input id="imageFile" type="file" accept="image/jpeg,image/png,image/webp,image/gif">
              <button id="uploadImage" type="button" data-i18n="uploadImageButton">上傳圖片</button>
              <img id="imagePreview" class="image-preview" alt="" hidden>
            </div>
          </label>
          <label class="full"><span data-i18n="comments">評論</span>
            <textarea id="comments"></textarea>
          </label>
        </div>

        <label class="full"><span data-i18n="appendCommentTitle">追加評論</span>
          <textarea id="newComment" data-i18n-placeholder="appendCommentPlaceholder" placeholder="輸入要追加的評論，不會覆蓋原本內容"></textarea>
        </label>
        <div class="actions" style="margin-top: 8px;">
          <button id="appendComment" type="button" data-i18n="appendComment">追加評論</button>
        </div>
        <div id="message" class="message"></div>
      </form>
    </section>
  </main>

  <script>
    const translations = {
      zh: {
        adminTitle: "食べログ Bot 管理後台",
        publicPage: "公開頁",
        importSheet: "從 Google Sheet 匯入",
        syncSheet: "DB 同步到 Sheet",
        reload: "重新整理",
        createBackup: "立即備份 DB",
        cleanupTaxonomy: "整理地區/分類",
        enrichPrices: "補抓價格",
        adminPasswordTitle: "管理密碼",
        adminPasswordHelp: "編輯、刪除、匯入與同步需要管理密碼。",
        adminPasswordPlaceholder: "輸入 ADMIN_PASSWORD",
        login: "進入管理後台",
        searchPlaceholder: "搜尋店名、分類、地區、評論",
        allAreas: "全部地區",
        allCategories: "全部分類",
        emptyEditor: "從左邊選一間餐廳開始編輯。",
        saveChanges: "儲存修改",
        deleteRestaurant: "刪除餐廳",
        name: "店名",
        category: "分類",
        area: "地區",
        keywords: "關鍵字，用逗號分隔",
        lunchBudget: "午餐價格",
        dinnerBudget: "晚餐價格",
        lunchBudgetPlaceholder: "例：￥1,000～￥1,999",
        dinnerBudgetPlaceholder: "例：￥2,000～￥2,999",
        lunchMin: "午餐最低",
        lunchMax: "午餐最高",
        dinnerMin: "晚餐最低",
        dinnerMax: "晚餐最高",
        imageUrl: "圖片 URL",
        uploadImage: "上傳圖片",
        uploadImageButton: "上傳圖片",
        uploadingImage: "上傳中...",
        comments: "評論",
        appendCommentTitle: "追加評論",
        appendCommentPlaceholder: "輸入要追加的評論，不會覆蓋原本內容",
        appendComment: "追加評論",
        count: (total) => `目前顯示 ${total} 間餐廳，ID 由小到大排列`,
        prev: "上一頁",
        next: "下一頁",
        noComments: "目前沒有獨立評論紀錄。",
        passwordNotConfigured: "尚未設定 ADMIN_PASSWORD。請先在 .env 裡加入管理密碼。",
        saveFailed: "儲存失敗，請確認欄位內容。",
        saved: "已儲存修改。",
        appendFailed: "追加評論失敗。",
        appended: "已追加評論。",
        chooseImage: "請先選擇圖片。",
        imageUploaded: "已上傳圖片。",
        imageUploadFailed: "圖片上傳失敗。",
        cleanupConfirm: "要把既有資料的地區與分類整理成統一格式嗎？",
        cleaning: "整理中...",
        cleanupFailed: "整理失敗",
        cleanupDone: (count) => `已整理 ${count} 筆餐廳`,
        enrichConfirm: "要從食べログ慢慢補抓最多 5 筆缺少的價格嗎？",
        enriching: "補抓中...",
        enrichFailed: "補抓價格失敗",
        enrichDone: (updated, checked) => `已檢查 ${checked} 筆，更新 ${updated} 筆價格。`,
        backupConfirm: "要立即建立一份 SQLite 備份嗎？",
        backingUp: "備份中...",
        backupFailed: "備份失敗",
        backupDone: (filename) => `已建立備份：${filename}`,
        deleteConfirm: "確定要刪除這間餐廳嗎？這個動作不能復原。",
        deleteFailed: "刪除失敗。",
        importConfirm: "要把 Google Sheet 的資料匯入到本機資料庫嗎？同 ID 的餐廳會被 Sheet 內容更新。",
        importing: "匯入中...",
        importFailed: "匯入失敗",
        importDone: (imported, skipped) => `已從 Google Sheet 匯入 ${imported} 筆，略過 ${skipped} 筆。`,
        syncConfirm: "這會用目前資料庫內容覆蓋 Google Sheet。確定要繼續嗎？",
        syncing: "同步中...",
        syncFailed: "同步失敗",
        syncDone: (count) => `已同步 ${count} 筆餐廳到 Google Sheet。`,
        passwordRequired: "請輸入管理密碼。",
        passwordWrong: "管理密碼錯誤。"
      },
      ja: {
        adminTitle: "食べログ Bot 管理画面",
        publicPage: "公開ページ",
        importSheet: "Google Sheet から取り込み",
        syncSheet: "DB を Sheet に同期",
        reload: "再読み込み",
        createBackup: "DB を今すぐバックアップ",
        cleanupTaxonomy: "エリア/分類を整理",
        enrichPrices: "価格を補完",
        adminPasswordTitle: "管理パスワード",
        adminPasswordHelp: "編集・削除・取り込み・同期には管理パスワードが必要です。",
        adminPasswordPlaceholder: "ADMIN_PASSWORD を入力",
        login: "管理画面に入る",
        searchPlaceholder: "店名・分類・エリア・コメントを検索",
        allAreas: "すべてのエリア",
        allCategories: "すべての分類",
        emptyEditor: "左側からレストランを選択して編集を開始します。",
        saveChanges: "変更を保存",
        deleteRestaurant: "レストランを削除",
        name: "店名",
        category: "分類",
        area: "エリア",
        keywords: "キーワード（カンマ区切り）",
        lunchBudget: "ランチ価格",
        dinnerBudget: "ディナー価格",
        lunchBudgetPlaceholder: "例：￥1,000～￥1,999",
        dinnerBudgetPlaceholder: "例：￥2,000～￥2,999",
        lunchMin: "ランチ最低額",
        lunchMax: "ランチ最高額",
        dinnerMin: "ディナー最低額",
        dinnerMax: "ディナー最高額",
        imageUrl: "画像 URL",
        uploadImage: "画像をアップロード",
        uploadImageButton: "画像をアップロード",
        uploadingImage: "アップロード中...",
        comments: "コメント",
        appendCommentTitle: "コメントを追加",
        appendCommentPlaceholder: "追加するコメントを入力します。既存の内容は上書きしません",
        appendComment: "コメントを追加",
        count: (total) => `${total} 件のレストランを表示中（ID 昇順）`,
        prev: "前へ",
        next: "次へ",
        noComments: "独立したコメント記録はまだありません。",
        passwordNotConfigured: "ADMIN_PASSWORD が未設定です。.env に管理パスワードを追加してください。",
        saveFailed: "保存に失敗しました。入力内容を確認してください。",
        saved: "変更を保存しました。",
        appendFailed: "コメントの追加に失敗しました。",
        appended: "コメントを追加しました。",
        chooseImage: "先に画像を選択してください。",
        imageUploaded: "画像をアップロードしました。",
        imageUploadFailed: "画像のアップロードに失敗しました。",
        cleanupConfirm: "既存データのエリアと分類を統一形式に整理しますか？",
        cleaning: "整理中...",
        cleanupFailed: "整理に失敗しました",
        cleanupDone: (count) => `${count} 件のレストランを整理しました`,
        enrichConfirm: "食べログから価格未入力の店を最大 5 件補完しますか？",
        enriching: "補完中...",
        enrichFailed: "価格補完に失敗しました",
        enrichDone: (updated, checked) => `${checked} 件を確認し、${updated} 件の価格を更新しました。`,
        backupConfirm: "SQLite バックアップを今すぐ作成しますか？",
        backingUp: "バックアップ中...",
        backupFailed: "バックアップに失敗しました",
        backupDone: (filename) => `バックアップを作成しました：${filename}`,
        deleteConfirm: "このレストランを削除しますか？この操作は元に戻せません。",
        deleteFailed: "削除に失敗しました。",
        importConfirm: "Google Sheet のデータをローカル DB に取り込みますか？同じ ID のレストランは Sheet の内容で更新されます。",
        importing: "取り込み中...",
        importFailed: "取り込みに失敗しました",
        importDone: (imported, skipped) => `Google Sheet から ${imported} 件取り込み、${skipped} 件スキップしました。`,
        syncConfirm: "現在の DB 内容で Google Sheet を上書きします。続行しますか？",
        syncing: "同期中...",
        syncFailed: "同期に失敗しました",
        syncDone: (count) => `${count} 件のレストランを Google Sheet に同期しました。`,
        passwordRequired: "管理パスワードを入力してください。",
        passwordWrong: "管理パスワードが違います。"
      }
    };
    const savedLanguage = localStorage.getItem("tabelogLanguage");
    const state = {
      restaurants: [],
      selectedId: null,
      areas: [],
      categories: [],
      page: 1,
      pageSize: 10,
      language: savedLanguage || "zh",
      adminPassword: localStorage.getItem("tabelogAdminPassword") || ""
    };

    const $ = (id) => document.getElementById(id);
    const t = (key, ...args) => {
      const value = translations[state.language][key] || translations.zh[key] || key;
      return typeof value === "function" ? value(...args) : value;
    };

    function applyLanguage() {
      document.documentElement.lang = state.language === "ja" ? "ja" : "zh-Hant";
      document.querySelectorAll("[data-i18n]").forEach((element) => {
        element.textContent = t(element.dataset.i18n);
      });
      document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
        element.placeholder = t(element.dataset.i18nPlaceholder);
      });
      $("adminLangZh").classList.toggle("active", state.language === "zh");
      $("adminLangJa").classList.toggle("active", state.language === "ja");
      backupButton.textContent = t("createBackup");
      cleanupButton.textContent = t("cleanupTaxonomy");
      enrichButton.textContent = t("enrichPrices");
      renderFilters();
      renderList();
      if (!$("editorForm").hidden && state.selectedId) {
        setMessage($("message").textContent);
      }
    }

    function setLanguage(language) {
      state.language = language;
      localStorage.setItem("tabelogLanguage", language);
      applyLanguage();
    }

    const backupButton = document.createElement("button");
    backupButton.id = "createBackup";
    backupButton.type = "button";
    backupButton.textContent = t("createBackup");
    $("reload").insertAdjacentElement("beforebegin", backupButton);
    const cleanupButton = document.createElement("button");
    cleanupButton.id = "cleanupTaxonomy";
    cleanupButton.type = "button";
    cleanupButton.textContent = t("cleanupTaxonomy");
    backupButton.insertAdjacentElement("beforebegin", cleanupButton);
    const enrichButton = document.createElement("button");
    enrichButton.id = "enrichPrices";
    enrichButton.type = "button";
    enrichButton.textContent = t("enrichPrices");
    cleanupButton.insertAdjacentElement("beforebegin", enrichButton);
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
        setLoginMessage(t("passwordNotConfigured"), true);
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
      fillSelect($("area"), t("allAreas"), state.areas);
      fillSelect($("category"), t("allCategories"), state.categories);
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
        <span>${escapeHtml(t("count", total))}</span>
        <span class="pager">
          <button id="prevListPage" type="button" ${state.page <= 1 ? "disabled" : ""}>${escapeHtml(t("prev"))}</button>
          <span>${state.page} / ${maxPage}</span>
          <button id="nextListPage" type="button" ${state.page >= maxPage ? "disabled" : ""}>${escapeHtml(t("next"))}</button>
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
      $("imageUrl").value = restaurant.image_url || "";
      $("imageFile").value = "";
      renderImagePreview(restaurant.image_url || "");
      $("comments").value = restaurant.comments || "";
      renderCommentItems(restaurant.comment_items || []);
      $("newComment").value = "";
      setMessage("");
      renderList();
    }

    function renderImagePreview(url) {
      if (!url) {
        $("imagePreview").hidden = true;
        $("imagePreview").removeAttribute("src");
        return;
      }
      $("imagePreview").src = url;
      $("imagePreview").hidden = false;
    }

    function clearEditor() {
      state.selectedId = null;
      $("empty").hidden = false;
      $("editorForm").hidden = true;
      $("imageFile").value = "";
      renderImagePreview("");
      renderList();
    }

    function editorPayload() {
      return {
        name: $("name").value.trim(),
        category: $("categoryInput").value.trim(),
        area: $("areaInput").value.trim() || null,
        tabelog_url: $("tabelogUrl").value.trim() || null,
        google_maps_url: $("googleMapsUrl").value.trim() || null,
        image_url: $("imageUrl").value.trim() || null,
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
        $("commentItems").innerHTML = escapeHtml(t("noComments"));
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
        setMessage(t("saveFailed"), true);
        return;
      }
      setMessage(t("saved"));
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
        setMessage(t("appendFailed"), true);
        return;
      }
      const restaurant = await response.json();
      $("comments").value = restaurant.comments || "";
      renderCommentItems(restaurant.comment_items || []);
      $("newComment").value = "";
      setMessage(t("appended"));
    });

    $("imageUrl").addEventListener("input", () => {
      renderImagePreview($("imageUrl").value.trim());
    });

    $("uploadImage").addEventListener("click", async () => {
      if (!state.selectedId) return;
      const file = $("imageFile").files[0];
      if (!file) {
        setMessage(t("chooseImage"), true);
        return;
      }
      const button = $("uploadImage");
      button.disabled = true;
      button.textContent = t("uploadingImage");
      try {
        const data = await file.arrayBuffer();
        const response = await fetch(`/api/restaurants/${state.selectedId}/image`, {
          method: "POST",
          headers: {
            "X-Admin-Password": state.adminPassword,
            "Content-Type": file.type
          },
          body: data
        });
        const restaurant = await response.json();
        if (!response.ok) throw new Error(restaurant.detail || t("imageUploadFailed"));
        $("imageUrl").value = restaurant.image_url || "";
        $("imageFile").value = "";
        renderImagePreview(restaurant.image_url || "");
        setMessage(t("imageUploaded"));
        await loadRestaurants();
      } catch (error) {
        setMessage(error.message, true);
      } finally {
        button.disabled = false;
        button.textContent = t("uploadImageButton");
      }
    });

    cleanupButton.addEventListener("click", async () => {
      if (!confirm(t("cleanupConfirm"))) return;
      cleanupButton.disabled = true;
      cleanupButton.textContent = t("cleaning");
      try {
        const response = await fetch("/api/cleanup-taxonomy", {
          method: "POST",
          headers: {"X-Admin-Password": state.adminPassword}
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || t("cleanupFailed"));
        alert(t("cleanupDone", data.changed));
        await loadRestaurants();
      } catch (error) {
        alert(error.message);
      } finally {
        cleanupButton.disabled = false;
        cleanupButton.textContent = t("cleanupTaxonomy");
      }
    });

    enrichButton.addEventListener("click", async () => {
      if (!confirm(t("enrichConfirm"))) return;
      enrichButton.disabled = true;
      enrichButton.textContent = t("enriching");
      try {
        const response = await fetch("/api/enrich-prices", {
          method: "POST",
          headers: adminHeaders(),
          body: JSON.stringify({limit: 5})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || t("enrichFailed"));
        alert(t("enrichDone", data.updated, data.checked));
        await loadRestaurants();
      } catch (error) {
        alert(error.message);
      } finally {
        enrichButton.disabled = false;
        enrichButton.textContent = t("enrichPrices");
      }
    });

    backupButton.addEventListener("click", async () => {
      if (!confirm(t("backupConfirm"))) return;
      backupButton.disabled = true;
      backupButton.textContent = t("backingUp");
      try {
        const response = await fetch("/api/backups", {
          method: "POST",
          headers: {"X-Admin-Password": state.adminPassword}
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || t("backupFailed"));
        alert(t("backupDone", data.filename));
      } catch (error) {
        alert(error.message);
      } finally {
        backupButton.disabled = false;
        backupButton.textContent = t("createBackup");
      }
    });

    $("deleteRestaurant").addEventListener("click", async () => {
      if (!state.selectedId) return;
      if (!confirm(t("deleteConfirm"))) return;
      const response = await fetch(`/api/restaurants/${state.selectedId}`, {
        method: "DELETE",
        headers: {"X-Admin-Password": state.adminPassword}
      });
      if (!response.ok) {
        setMessage(t("deleteFailed"), true);
        return;
      }
      clearEditor();
      await loadRestaurants();
    });

    $("importSheet").addEventListener("click", async () => {
      if (!confirm(t("importConfirm"))) return;
      const button = $("importSheet");
      button.disabled = true;
      button.textContent = t("importing");
      try {
        const response = await fetch("/api/import-sheet", {
          method: "POST",
          headers: {"X-Admin-Password": state.adminPassword}
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || t("importFailed"));
        alert(t("importDone", data.imported, data.skipped));
        clearEditor();
        await loadRestaurants();
      } catch (error) {
        alert(error.message);
      } finally {
        button.disabled = false;
        button.textContent = t("importSheet");
      }
    });

    $("syncSheet").addEventListener("click", async () => {
      if (!confirm(t("syncConfirm"))) return;
      const button = $("syncSheet");
      button.disabled = true;
      button.textContent = t("syncing");
      try {
        const response = await fetch("/api/sync-sheet", {
          method: "POST",
          headers: {"X-Admin-Password": state.adminPassword}
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || t("syncFailed"));
        alert(t("syncDone", data.count));
      } catch (error) {
        alert(error.message);
      } finally {
        button.disabled = false;
        button.textContent = t("syncSheet");
      }
    });

    $("reload").addEventListener("click", loadRestaurants);
    $("loginButton").addEventListener("click", async () => {
      state.adminPassword = $("adminPassword").value;
      if (!state.adminPassword) {
        setLoginMessage(t("passwordRequired"), true);
        return;
      }
      if (!await checkAdminPassword()) {
        setLoginMessage(t("passwordWrong"), true);
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
    $("adminLangZh").addEventListener("click", () => setLanguage("zh"));
    $("adminLangJa").addEventListener("click", () => setLanguage("ja"));

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

    applyLanguage();
    initAdminAuth();
    loadRestaurants();
  </script>
</body>
</html>
"""

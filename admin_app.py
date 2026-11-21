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
import hashlib
import hmac
import ipaddress
import logging
import math
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from db import Restaurant, RestaurantDB
from extractor import fetch_tabelog_business_hours, fetch_tabelog_price_info
from sheets_sync import read_restaurants_from_sheet, sync_restaurants_to_sheet
from url_utils import sanitize_url, validate_url


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
GOOGLE_MY_MAPS_URL = os.getenv("GOOGLE_MY_MAPS_URL", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_hex(32)
    logging.getLogger(__name__).warning("未設定 SESSION_SECRET，使用暫時金鑰；重啟後管理登入將失效。")
SESSION_COOKIE = "tabelog_admin_session"
SESSION_TTL = 12 * 60 * 60
LOGIN_WINDOW = 15 * 60
LOGIN_MAX_FAILURES = 5
login_attempts: dict[str, tuple[list[float], float]] = {}
login_lock = threading.Lock()
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
app = FastAPI(title="Discord 食べログ Bot Admin", docs_url=None, redoc_url=None, openapi_url=None)
RESTAURANT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def uploads_nosniff(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/uploads/", "/static/")):
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


class RestaurantPayload(BaseModel):
    """前端送來的餐廳編輯資料。"""

    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    area: str | None = None
    tabelog_url: str | None = None
    google_maps_url: str | None = None
    image_url: str | None = None
    recommended_by: str | None = None
    comments: str = ""
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    lunch_budget_text: str | None = None
    lunch_budget_min: int | None = None
    lunch_budget_max: int | None = None
    dinner_budget_text: str | None = None
    dinner_budget_min: int | None = None
    dinner_budget_max: int | None = None
    business_hours_text: str | None = None

    @field_validator("tabelog_url", "google_maps_url")
    @classmethod
    def validate_link(cls, value: str | None) -> str | None:
        return validate_url(value)

    @field_validator("image_url")
    @classmethod
    def validate_image(cls, value: str | None) -> str | None:
        return validate_url(value, image=True)


class EnrichPricesPayload(BaseModel):
    limit: int = Field(default=5, ge=1, le=20)


class CommentPayload(BaseModel):
    """前端送來的追加評論資料。"""

    comment: str = Field(min_length=1)
    created_by: str = "Admin"


def comment_to_dict(comment, *, internal: bool = False) -> dict:
    """Convert a normalized comment row to JSON."""

    result = {
        "id": comment.id,
        "restaurant_id": comment.restaurant_id,
        "comment": comment.comment,
        "created_by": comment.created_by,
        "created_at": comment.created_at,
        "source_message_id": comment.source_message_id,
    }
    return result if internal else public_fields(result)


def restaurant_to_dict(restaurant: Restaurant, *, internal: bool = False) -> dict:
    """把 dataclass 轉成 JSON-friendly dict。"""

    result = {
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
        "comment_items": [comment_to_dict(comment, internal=internal) for comment in db.comments_for(restaurant.id)],
        "source_channel_id": restaurant.source_channel_id,
        "source_message_id": restaurant.source_message_id,
        "created_by": restaurant.created_by,
        "recommended_by": restaurant.recommended_by,
        "created_at": restaurant.created_at,
        "lunch_budget_text": restaurant.lunch_budget_text,
        "lunch_budget_min": restaurant.lunch_budget_min,
        "lunch_budget_max": restaurant.lunch_budget_max,
        "dinner_budget_text": restaurant.dinner_budget_text,
        "dinner_budget_min": restaurant.dinner_budget_min,
        "dinner_budget_max": restaurant.dinner_budget_max,
        "price_updated_at": restaurant.price_updated_at,
        "business_hours_text": restaurant.business_hours_text,
        "business_hours_updated_at": restaurant.business_hours_updated_at,
    }
    return result if internal else public_fields(result)


def public_fields(data: dict) -> dict:
    return {key: value for key, value in data.items()
            if key not in {"source_channel_id", "source_message_id", "created_by"}}


def session_signature(payload: str) -> str:
    # Changing the admin password also invalidates existing sessions.
    key = hmac.new(SESSION_SECRET.encode(), ADMIN_PASSWORD.encode(), hashlib.sha256).digest()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def is_admin_authenticated(request: Request) -> bool:
    if not ADMIN_PASSWORD:
        return False
    token = request.cookies.get(SESSION_COOKIE, "")
    try:
        expires, nonce, signature = token.split(".")
        payload = f"{expires}.{nonce}"
        now = time.time()
        return (now < int(expires) <= now + SESSION_TTL
                and len(nonce) == 64
                and hmac.compare_digest(signature, session_signature(payload)))
    except (ValueError, TypeError, OverflowError):
        return False


def require_admin(request: Request) -> None:
    """保護會修改資料的 API。

    公開頁只能讀資料；編輯、刪除、匯入、同步都需要管理密碼。
    密碼放在 .env 的 ADMIN_PASSWORD，不寫進 GitHub。
    """

    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="尚未設定 ADMIN_PASSWORD")
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=401, detail="請先登入管理後台，或登入已過期。")


class LoginPayload(BaseModel):
    password: str


def is_loopback_peer(request: Request) -> bool:
    try:
        return bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
    except ValueError:
        return False


def login_client_ip(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    # Only trust forwarded addresses from a local reverse proxy; use its last hop.
    if is_loopback_peer(request) and request.headers.get("x-forwarded-for"):
        forwarded = request.headers["x-forwarded-for"].split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return host


@app.post("/api/admin/login")
def admin_login(payload: LoginPayload, request: Request, response: Response) -> dict:
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="尚未設定 ADMIN_PASSWORD")
    now = time.monotonic()
    ip = login_client_ip(request)
    with login_lock:
        for key, (failures, until) in list(login_attempts.items()):
            if until <= now and (not failures or failures[-1] <= now - LOGIN_WINDOW):
                del login_attempts[key]
        failures, until = login_attempts.get(ip, ([], 0))
        if until > now:
            raise HTTPException(status_code=429, detail="登入失敗次數過多，請於 15 分鐘後再試。",
                                headers={"Retry-After": str(math.ceil(until - now))})
        failures = [attempt for attempt in failures if attempt > now - LOGIN_WINDOW]
        if not hmac.compare_digest(payload.password.encode(), ADMIN_PASSWORD.encode()):
            failures.append(now)
            until = now + LOGIN_WINDOW if len(failures) >= LOGIN_MAX_FAILURES else 0
            login_attempts[ip] = (failures, until)
            if until:
                raise HTTPException(status_code=429, detail="登入失敗次數過多，請於 15 分鐘後再試。",
                                    headers={"Retry-After": str(LOGIN_WINDOW)})
            raise HTTPException(status_code=401, detail="管理密碼錯誤")
        login_attempts.pop(ip, None)
    signed_payload = f"{int(time.time()) + SESSION_TTL}.{secrets.token_hex(32)}"
    response.set_cookie(SESSION_COOKIE, f"{signed_payload}.{session_signature(signed_payload)}",
                        max_age=SESSION_TTL, httponly=True, samesite="strict", path="/",
                        secure=request.url.scheme == "https" or
                        (is_loopback_peer(request) and
                         request.headers.get("x-forwarded-proto", "").lower() == "https"))
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """公開只讀頁。"""

    return PUBLIC_HTML


@app.get("/map", response_class=HTMLResponse)
def map_index() -> str:
    """公開地圖頁。"""

    return MAP_HTML.replace("__MY_MAPS_EMBED_URL__", my_maps_embed_url(GOOGLE_MY_MAPS_URL))


@app.get("/admin", response_class=HTMLResponse)
def admin_index(request: Request) -> HTMLResponse:
    """管理後台首頁。

    HTML 從 templates 載入，CSS 與 JavaScript 由 static 提供。
    """

    return HTMLResponse(ADMIN_HTML if is_admin_authenticated(request) else ADMIN_LOGIN_HTML,
                        headers={"Cache-Control": "no-store"})


@app.get("/api/admin/status")
def admin_status(request: Request, response: Response) -> dict:
    """讓前端知道管理密碼是否已設定。"""

    response.headers["Cache-Control"] = "no-store"
    return {"password_configured": bool(ADMIN_PASSWORD), "authenticated": is_admin_authenticated(request)}


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

    return restaurant_list(keyword, area, category)


def restaurant_list(keyword: str, area: str, category: str, *, internal: bool = False) -> dict:
    """Share list filters while keeping internal serialization explicit."""
    restaurants = db.search(keyword, limit=200) if keyword.strip() else db.all()
    if area:
        restaurants = [restaurant for restaurant in restaurants if (restaurant.area or "") == area]
    if category:
        restaurants = [restaurant for restaurant in restaurants if restaurant.category == category]
    return {
        "restaurants": [restaurant_to_dict(restaurant, internal=internal) for restaurant in restaurants],
        "areas": db.areas(),
        "categories": db.categories(),
        "tags": db.all_tags(),
    }


@app.get("/api/admin/restaurants")
def admin_list_restaurants(response: Response, keyword: str = "", area: str = "", category: str = "",
                           _: None = Depends(require_admin)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return restaurant_list(keyword, area, category, internal=True)


@app.get("/api/admin/restaurants/{restaurant_id}")
def admin_get_restaurant(restaurant_id: int, response: Response,
                         _: None = Depends(require_admin)) -> dict:
    restaurant = db.get(restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    response.headers["Cache-Control"] = "no-store"
    return restaurant_to_dict(restaurant, internal=True)


@app.get("/api/restaurants/{restaurant_id}")
def get_restaurant(restaurant_id: int) -> dict:
    """取得單一餐廳。"""

    restaurant = db.get(restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    return restaurant_to_dict(restaurant)


@app.get("/api/restaurants/{restaurant_id}/comments")
def list_restaurant_comments(restaurant_id: int) -> dict:
    """取得單一餐廳的正式評論列。"""

    if not db.get(restaurant_id):
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    return {
        "comments": [comment_to_dict(comment) for comment in db.comments_for(restaurant_id)]
    }


@app.put("/api/restaurants/{restaurant_id}")
def update_restaurant(
    restaurant_id: int,
    payload: RestaurantPayload,
    _: None = Depends(require_admin),
) -> dict:
    """更新餐廳資料。"""

    previous = db.get(restaurant_id)
    restaurant = db.update_restaurant(
        restaurant_id=restaurant_id,
        name=payload.name,
        category=payload.category,
        area=payload.area,
        tabelog_url=payload.tabelog_url,
        google_maps_url=payload.google_maps_url,
        image_url=payload.image_url,
        recommended_by=payload.recommended_by,
        comments=payload.comments,
        keywords=payload.tags or payload.keywords,
        lunch_budget_text=payload.lunch_budget_text,
        lunch_budget_min=payload.lunch_budget_min,
        lunch_budget_max=payload.lunch_budget_max,
        dinner_budget_text=payload.dinner_budget_text,
        dinner_budget_min=payload.dinner_budget_min,
        dinner_budget_max=payload.dinner_budget_max,
        business_hours_text=payload.business_hours_text,
        business_hours_updated_at="manual" if payload.business_hours_text else None,
        price_updated_at="manual",
    )
    if not restaurant:
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    if previous and previous.image_url != restaurant.image_url:
        cleanup_local_image(previous.image_url)
    return restaurant_to_dict(restaurant, internal=True)


@app.delete("/api/restaurants/{restaurant_id}")
def delete_restaurant(
    restaurant_id: int,
    _: None = Depends(require_admin),
) -> dict:
    """刪除餐廳。"""

    restaurant = db.get(restaurant_id)
    if not restaurant or not db.delete_restaurant(restaurant_id):
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    cleanup_local_image(restaurant.image_url)
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
    return restaurant_to_dict(restaurant, internal=True)


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
    length = request.headers.get("content-length")
    if length is not None:
        try:
            size = int(length)
        except ValueError:
            raise HTTPException(status_code=400, detail="無效的 Content-Length")
        if size < 0:
            raise HTTPException(status_code=400, detail="無效的 Content-Length")
        if size > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="圖片不能超過 5 MB")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="圖片不能超過 5 MB")
        data.extend(chunk)
    if not data:
        raise HTTPException(status_code=400, detail="沒有收到圖片資料")
    detected_type = detect_image_type(data)
    if not detected_type or (content_type.startswith("image/") and content_type != detected_type):
        raise HTTPException(status_code=400, detail="圖片內容或格式不符，只支援 JPG、PNG、WEBP、GIF")
    suffix = ALLOWED_IMAGE_TYPES[detected_type]
    filename = f"restaurant-{restaurant_id}-{uuid.uuid4().hex}{suffix}"
    path = RESTAURANT_IMAGE_DIR / filename
    path.write_bytes(data)

    try:
        previous = db.get(restaurant_id)
        if not previous:
            raise HTTPException(status_code=404, detail="找不到這間餐廳")
        restaurant = db.update_image_url(restaurant_id, f"/uploads/restaurants/{filename}")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if not restaurant:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    cleanup_local_image(previous.image_url)
    return restaurant_to_dict(restaurant, internal=True)


def detect_image_type(data: bytes | bytearray) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def cleanup_local_image(url: str | None) -> None:
    """Remove only safe, unreferenced uploads contained in the image directory."""
    safe = sanitize_url(url, image=True)
    if not safe or not safe.startswith("/uploads/restaurants/"):
        return
    with db.session() as conn:
        if conn.execute("SELECT 1 FROM restaurants WHERE image_url = ? LIMIT 1", (safe,)).fetchone():
            return
    root = RESTAURANT_IMAGE_DIR.resolve()
    path = root / safe.rsplit("/", 1)[-1]
    if not path.resolve().is_relative_to(root):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logging.getLogger(__name__).warning("無法清除舊的餐廳圖片：%s", path)


@app.post("/api/sync-sheet")
def sync_sheet(_: None = Depends(require_admin)) -> dict:
    """從後台手動同步 Google Sheet。"""

    if not GOOGLE_SHEETS_ID:
        raise HTTPException(status_code=400, detail="尚未設定 GOOGLE_SHEETS_ID")
    if not GOOGLE_SERVICE_ACCOUNT_FILE or not GOOGLE_SERVICE_ACCOUNT_FILE.exists():
        raise HTTPException(status_code=400, detail="找不到 Google service account JSON")

    result = sync_restaurants_to_sheet(
        restaurants=db.all(),
        spreadsheet_id=GOOGLE_SHEETS_ID,
        credentials_path=GOOGLE_SERVICE_ACCOUNT_FILE,
        worksheet_name=GOOGLE_SHEETS_WORKSHEET,
    )
    return {
        "ok": True,
        "count": result.count,
        "verified_count": result.verified_count,
        "max_id": result.max_id,
        "worksheet_name": result.worksheet_name,
        "worksheet_url": result.worksheet_url,
    }


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
            google_maps_url=sanitize_url(row.get("google_maps_url")),
            tabelog_url=sanitize_url(row.get("tabelog_url")),
            image_url=sanitize_url(row.get("image_url"), image=True),
            recommended_by=row.get("recommended_by", "").strip() or None,
            comments=row.get("comments", "").strip(),
            keywords=keywords or [name, category],
            lunch_budget_text=row.get("lunch_budget", "").strip() or None,
            dinner_budget_text=row.get("dinner_budget", "").strip() or None,
            business_hours_text=row.get("business_hours", "").strip() or None,
            business_hours_updated_at="google-sheet" if row.get("business_hours", "").strip() else None,
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


@app.post("/api/enrich-hours")
def enrich_hours(payload: EnrichPricesPayload, _: None = Depends(require_admin)) -> dict:
    """Fetch missing business hours from saved Tabelog URLs."""

    restaurants = db.restaurants_missing_business_hours(limit=payload.limit)
    updated: list[str] = []
    not_found: list[str] = []
    for restaurant in restaurants:
        hours = fetch_tabelog_business_hours(restaurant.tabelog_url)
        if not hours.business_hours_updated_at:
            not_found.append(restaurant.name)
            continue
        db.update_business_hours(
            restaurant_id=restaurant.id,
            business_hours_text=hours.business_hours_text,
            business_hours_updated_at=hours.business_hours_updated_at,
        )
        first_line = (hours.business_hours_text or "").splitlines()[0] if hours.business_hours_text else "-"
        updated.append(f"ID {restaurant.id}: {restaurant.name} {first_line}")

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
    source = sqlite3.connect(DB_PATH, timeout=15.0)
    source.execute("PRAGMA busy_timeout = 15000")
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


def my_maps_embed_url(url: str) -> str:
    """Convert a Google My Maps edit/share URL into an embeddable URL."""

    parsed = urlparse(url.strip())
    if not parsed.netloc:
        return ""
    query = parse_qs(parsed.query)
    mid = (query.get("mid") or [""])[0].strip()
    if not mid and "/maps/d/" in parsed.path:
        parts = [part for part in parsed.path.split("/") if part]
        if "d" in parts:
            index = parts.index("d")
            if index + 1 < len(parts):
                mid = parts[index + 1]
    if not mid:
        return ""
    return "https://www.google.com/maps/d/embed?" + urlencode({"mid": mid, "ehbc": "2E312F"})


ADMIN_LOGIN_HTML = (BASE_DIR / "templates" / "login.html").read_text(encoding="utf-8")


PUBLIC_HTML = (BASE_DIR / "templates" / "public.html").read_text(encoding="utf-8")


MAP_HTML = (BASE_DIR / "templates" / "map.html").read_text(encoding="utf-8")


ADMIN_HTML = (BASE_DIR / "templates" / "admin.html").read_text(encoding="utf-8")

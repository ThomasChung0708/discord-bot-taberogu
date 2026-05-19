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
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
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


class CommentPayload(BaseModel):
    """前端送來的追加評論資料。"""

    comment: str = Field(min_length=1)
    created_by: str = "Admin"


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
        "source_channel_id": restaurant.source_channel_id,
        "source_message_id": restaurant.source_message_id,
        "created_by": restaurant.created_by,
        "created_at": restaurant.created_at,
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """管理後台首頁。

    為了讓專案保持簡單，第一版先把 HTML/CSS/JS 放在同一個檔案。
    未來如果後台變大，再拆成 templates 與 static 檔案。
    """

    return ADMIN_HTML


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
    }


@app.get("/api/restaurants/{restaurant_id}")
def get_restaurant(restaurant_id: int) -> dict:
    """取得單一餐廳。"""

    restaurant = db.get(restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    return restaurant_to_dict(restaurant)


@app.put("/api/restaurants/{restaurant_id}")
def update_restaurant(restaurant_id: int, payload: RestaurantPayload) -> dict:
    """更新餐廳資料。"""

    restaurant = db.update_restaurant(
        restaurant_id=restaurant_id,
        name=payload.name,
        category=payload.category,
        area=payload.area,
        tabelog_url=payload.tabelog_url,
        google_maps_url=payload.google_maps_url,
        comments=payload.comments,
        keywords=payload.keywords,
    )
    if not restaurant:
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    return restaurant_to_dict(restaurant)


@app.delete("/api/restaurants/{restaurant_id}")
def delete_restaurant(restaurant_id: int) -> dict:
    """刪除餐廳。"""

    if not db.delete_restaurant(restaurant_id):
        raise HTTPException(status_code=404, detail="找不到這間餐廳")
    return {"ok": True}


@app.post("/api/restaurants/{restaurant_id}/comments")
def append_comment(restaurant_id: int, payload: CommentPayload) -> dict:
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
def sync_sheet() -> dict:
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
def import_sheet() -> dict:
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
        )
        imported += 1
    return {"ok": True, "imported": imported, "skipped": skipped}


def parse_optional_int(value: str) -> int | None:
    """把 Sheet 裡的 id 轉成 int；空白或非數字就讓 SQLite 自動編號。"""

    text = str(value).strip()
    return int(text) if text.isdigit() else None


def split_keywords(value: str) -> list[str]:
    """支援逗號、日文頓號、換行分隔的關鍵字欄位。"""

    text = str(value).replace("、", ",").replace("\n", ",")
    return [part.strip() for part in text.split(",") if part.strip()]


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
      <button id="importSheet" class="primary">從 Google Sheet 匯入</button>
      <button id="syncSheet">DB 同步到 Sheet</button>
      <button id="reload">重新整理</button>
    </div>
  </header>

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
      pageSize: 10
    };

    const $ = (id) => document.getElementById(id);

    function setMessage(text, isError = false) {
      $("message").textContent = text;
      $("message").style.color = isError ? "var(--danger)" : "var(--accent-strong)";
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
      $("keywordsInput").value = (restaurant.keywords || []).join(", ");
      $("tabelogUrl").value = restaurant.tabelog_url || "";
      $("googleMapsUrl").value = restaurant.google_maps_url || "";
      $("comments").value = restaurant.comments || "";
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
        keywords: $("keywordsInput").value.split(",").map((item) => item.trim()).filter(Boolean)
      };
    }

    $("editorForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!state.selectedId) return;
      const response = await fetch(`/api/restaurants/${state.selectedId}`, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
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
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({comment: $("newComment").value.trim(), created_by: "Admin"})
      });
      if (!response.ok) {
        setMessage("追加評論失敗。", true);
        return;
      }
      const restaurant = await response.json();
      $("comments").value = restaurant.comments || "";
      $("newComment").value = "";
      setMessage("已追加評論。");
    });

    $("deleteRestaurant").addEventListener("click", async () => {
      if (!state.selectedId) return;
      if (!confirm("確定要刪除這間餐廳嗎？這個動作不能復原。")) return;
      const response = await fetch(`/api/restaurants/${state.selectedId}`, {method: "DELETE"});
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
        const response = await fetch("/api/import-sheet", {method: "POST"});
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
        const response = await fetch("/api/sync-sheet", {method: "POST"});
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

    loadRestaurants();
  </script>
</body>
</html>
"""

from __future__ import annotations

"""Google Sheets 同步模組。

這個檔案只處理「把餐廳資料寫進 Google Sheet」。
bot.py 不直接碰 Google Sheets API，這樣出錯時比較好定位：
- bot.py 負責 Discord 指令
- db.py 負責 SQLite
- sheets_sync.py 負責 Google Sheets API
"""

from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from db import Restaurant


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Google Sheet 第一列欄位名稱。
# My Maps 匯入時會看到這些欄位，通常用 name + area 定位，name 當標記名稱。
SHEET_HEADERS = [
    "id",
    "name",
    "category",
    "area",
    "image_url",
    "google_maps_url",
    "tabelog_url",
    "comments",
    "recommended_by",
    "business_hours",
    "keywords",
    "lunch_budget",
    "dinner_budget",
]


def sync_restaurants_to_sheet(
    *,
    restaurants: list[Restaurant],
    spreadsheet_id: str,
    credentials_path: Path,
    worksheet_name: str = "restaurants",
) -> int:
    """把餐廳清單完整同步到 Google Sheet。

    目前策略是「整張覆寫」：
    1. 確認 worksheet 存在
    2. 清空 A:H
    3. 寫入 header + 全部餐廳

    好處是資料庫和 Sheet 會保持一致，不需要處理逐筆新增/刪除的同步狀態。
    """

    # service account JSON 是 bot 寫入 Google Sheet 的身分證明。
    # 這個 JSON 不可以上傳 GitHub，只能放在本機或 VM。
    credentials = Credentials.from_service_account_file(
        str(credentials_path),
        scopes=SCOPES,
    )
    service = build("sheets", "v4", credentials=credentials)

    values = [SHEET_HEADERS]
    for restaurant in restaurants:
        # Google Sheets API 期待的是二維陣列：
        # 外層 list = 多列，內層 list = 單列的多個欄位。
        values.append(
            [
                restaurant.id,
                restaurant.name,
                restaurant.category,
                restaurant.area or "",
                restaurant.image_url or "",
                restaurant.google_maps_url or "",
                restaurant.tabelog_url or "",
                restaurant.comments,
                restaurant.recommended_by or "",
                restaurant.business_hours_text or "",
                ", ".join(restaurant.keywords),
                restaurant.lunch_budget_text or "",
                restaurant.dinner_budget_text or "",
            ]
        )

    sheet = service.spreadsheets()
    ensure_worksheet(sheet, spreadsheet_id, worksheet_name)
    range_name = f"{worksheet_name}!A:M"

    # clear + update 比 append 更適合這個專案：
    # append 會一直往下加，容易產生重複資料。
    sheet.values().clear(
        spreadsheetId=spreadsheet_id,
        range=range_name,
    ).execute()
    sheet.values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{worksheet_name}!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()
    return len(restaurants)


def read_restaurants_from_sheet(
    *,
    spreadsheet_id: str,
    credentials_path: Path,
    worksheet_name: str = "restaurants",
) -> list[dict[str, str]]:
    """從 Google Sheet 讀回餐廳資料。

    Sheet 第一列必須是欄位名稱，例如 id/name/category/area。
    回傳時會把每一列轉成 dict，讓 admin_app.py 可以再寫回 SQLite。
    """

    credentials = Credentials.from_service_account_file(
        str(credentials_path),
        scopes=SCOPES,
    )
    service = build("sheets", "v4", credentials=credentials)
    sheet = service.spreadsheets()
    ensure_worksheet(sheet, spreadsheet_id, worksheet_name)

    result = sheet.values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{worksheet_name}!A:M",
    ).execute()
    values = result.get("values", [])
    if not values:
        return []

    headers = [str(header).strip() for header in values[0]]
    rows: list[dict[str, str]] = []
    for value_row in values[1:]:
        if not any(str(value).strip() for value in value_row):
            continue
        row = {
            header: str(value_row[index]).strip() if index < len(value_row) else ""
            for index, header in enumerate(headers)
        }
        rows.append(row)
    return rows


def ensure_worksheet(sheet_resource, spreadsheet_id: str, worksheet_name: str) -> None:
    """確認指定 worksheet 存在；沒有就自動建立。"""

    metadata = sheet_resource.get(spreadsheetId=spreadsheet_id).execute()
    existing_titles = {
        item["properties"]["title"]
        for item in metadata.get("sheets", [])
        if "properties" in item
    }
    if worksheet_name in existing_titles:
        return

    sheet_resource.batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "addSheet": {
                        "properties": {
                            "title": worksheet_name,
                        }
                    }
                }
            ]
        },
    ).execute()

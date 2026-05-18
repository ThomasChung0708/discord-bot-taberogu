from __future__ import annotations

from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from db import Restaurant


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_HEADERS = [
    "id",
    "name",
    "category",
    "area",
    "google_maps_url",
    "tabelog_url",
    "comments",
    "keywords",
]


def sync_restaurants_to_sheet(
    *,
    restaurants: list[Restaurant],
    spreadsheet_id: str,
    credentials_path: Path,
    worksheet_name: str = "restaurants",
) -> int:
    credentials = Credentials.from_service_account_file(
        str(credentials_path),
        scopes=SCOPES,
    )
    service = build("sheets", "v4", credentials=credentials)

    values = [SHEET_HEADERS]
    for restaurant in restaurants:
        values.append(
            [
                restaurant.id,
                restaurant.name,
                restaurant.category,
                restaurant.area or "",
                restaurant.google_maps_url or "",
                restaurant.tabelog_url or "",
                restaurant.comments,
                ", ".join(restaurant.keywords),
            ]
        )

    sheet = service.spreadsheets()
    ensure_worksheet(sheet, spreadsheet_id, worksheet_name)
    range_name = f"{worksheet_name}!A:H"
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


def ensure_worksheet(sheet_resource, spreadsheet_id: str, worksheet_name: str) -> None:
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

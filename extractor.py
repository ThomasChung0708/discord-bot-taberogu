from __future__ import annotations

"""餐廳資訊抽取模組。

bot.py 會把 Discord 訊息整理成 MessageSnippet，交給這個模組。
這個模組再做三件事：
1. 從文字中找食べログ網址
2. 如果有食べログ網址，嘗試抓頁面標題補足店名
3. 呼叫 OpenAI，把訊息整理成結構化餐廳資料
"""

import json
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from openai import OpenAI


TABELOG_RE = re.compile(r"https?://(?:[a-z0-9-]+\.)?tabelog\.com/[^\s)>]+", re.I)


@dataclass(frozen=True)
class MessageSnippet:
    """送進 AI 前的簡化版 Discord 訊息。"""

    author: str
    content: str
    attachment_urls: list[str]


@dataclass(frozen=True)
class ExtractionResult:
    """AI 抽取後回傳給 bot.py 的餐廳資料。"""

    name: str | None
    category: str
    area: str | None
    tabelog_url: str | None
    google_maps_url: str | None
    comments: str
    keywords: list[str]
    reason: str


def find_tabelog_url(text: str) -> str | None:
    """用正規表示式找出第一個食べログ網址。"""

    match = TABELOG_RE.search(text)
    return match.group(0) if match else None


def fetch_tabelog_title(url: str | None) -> str | None:
    """讀取食べログ頁面標題。

    這不是必要步驟；如果網路失敗或網站擋住請求，就回傳 None。
    bot 仍會把原本 Discord 訊息交給 AI 判斷。
    """

    if not url:
        return None
    try:
        resp = requests.get(
            url,
            timeout=3,
            headers={"User-Agent": "Mozilla/5.0 restaurant-memory-bot/0.1"},
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        return str(og_title["content"]).strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return None


def google_maps_search_url(name: str, area: str | None = None) -> str:
    """產生 Google Maps 搜尋連結。

    這不是精準座標，而是用店名 + 地區讓 Google Maps 自己搜尋。
    My Maps 匯入時也可以用 name + area 做定位。
    """

    query = f"{name} {area or ''}".strip()
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"


def extract_restaurant(
    *,
    client: OpenAI,
    model: str,
    messages: list[MessageSnippet],
) -> ExtractionResult:
    """從多則訊息抽取一間餐廳資料。

    目前設計是「指定訊息」或「指定訊息 + 被回覆的原訊息」抽一間餐廳。
    如果未來要一次抽多間餐廳，這裡的回傳型別就要改成 list[ExtractionResult]。
    """

    plain_messages = []
    all_text = []
    for msg in messages:
        attachment_note = ""
        if msg.attachment_urls:
            attachment_note = f" attachments={msg.attachment_urls}"
        line = f"{msg.author}: {msg.content}{attachment_note}".strip()
        plain_messages.append(line)
        all_text.append(msg.content)

    combined = "\n".join(all_text)
    tabelog_url = find_tabelog_url(combined)
    tabelog_title = fetch_tabelog_title(tabelog_url)

    prompt = {
        "messages": plain_messages,
        "tabelog_url": tabelog_url,
        "tabelog_title": tabelog_title,
        "rule": "Extract restaurant information only. Do not judge whether the restaurant is good or recommended. If a restaurant name or Tabelog title is available, return it.",
    }

    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        max_tokens=500,
        timeout=20,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract restaurant memory from Discord chat. "
                    "Return compact JSON with keys: name(string|null), "
                    "category(string), area(string|null), comments(string), keywords(array of strings), "
                    "reason(string). Use Traditional Chinese for comments/reason. "
                    "Do not reject restaurants because there is no review or recommendation. "
                    "If there is no review text, comments can briefly say that only basic restaurant info was saved. "
                    "Category should be short, e.g. 拉麵, 燒肉, 咖啡, 居酒屋."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
    )

    raw = response.choices[0].message.content or "{}"
    data = json.loads(raw)
    name = _clean_optional(data.get("name"))
    area = _clean_optional(data.get("area"))
    category = str(data.get("category") or "未分類").strip()
    google_url = google_maps_search_url(name, area) if name else None

    return ExtractionResult(
        name=name,
        category=category,
        area=area,
        tabelog_url=tabelog_url,
        google_maps_url=google_url,
        comments=str(data.get("comments") or "").strip(),
        keywords=[str(k).strip() for k in data.get("keywords", []) if str(k).strip()],
        reason=str(data.get("reason") or "").strip(),
    )


def _clean_optional(value: object) -> str | None:
    """把 AI 回傳的欄位整理成 None 或非空字串。"""

    if value is None:
        return None
    text = str(value).strip()
    return text or None

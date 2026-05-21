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
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup
from openai import OpenAI


TABELOG_RE = re.compile(r"https?://(?:[a-z0-9-]+\.)?tabelog\.com/[^\s)>]+", re.I)
GOOGLE_MAPS_RE = re.compile(
    r"https?://(?:www\.)?(?:google\.[^\s)>]+/maps|maps\.app\.goo\.gl|goo\.gl/maps)/[^\s)>]+",
    re.I,
)


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
    image_url: str | None
    comments: str
    keywords: list[str]
    reason: str
    lunch_budget_text: str | None = None
    lunch_budget_min: int | None = None
    lunch_budget_max: int | None = None
    dinner_budget_text: str | None = None
    dinner_budget_min: int | None = None
    dinner_budget_max: int | None = None
    price_updated_at: str | None = None


@dataclass(frozen=True)
class PriceInfo:
    """食べログ頁面抓到的價格資訊。"""

    lunch_budget_text: str | None = None
    lunch_budget_min: int | None = None
    lunch_budget_max: int | None = None
    dinner_budget_text: str | None = None
    dinner_budget_min: int | None = None
    dinner_budget_max: int | None = None
    price_updated_at: str | None = None


def find_tabelog_url(text: str) -> str | None:
    """用正規表示式找出第一個食べログ網址。"""

    match = TABELOG_RE.search(text)
    return match.group(0) if match else None


def find_google_maps_url(text: str) -> str | None:
    """用正規表示式找出第一個 Google Maps 網址。"""

    match = GOOGLE_MAPS_RE.search(text)
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


def fetch_tabelog_image_url(url: str | None) -> str | None:
    """Best-effort fetch of the restaurant preview image from Tabelog."""

    if not url:
        return None
    try:
        resp = requests.get(
            url,
            timeout=5,
            headers={
                "User-Agent": "Mozilla/5.0 restaurant-memory-bot/0.1",
                "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
            },
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for selector in [
        {"property": "og:image"},
        {"name": "twitter:image"},
    ]:
        image = soup.find("meta", selector)
        if image and image.get("content"):
            return str(image["content"]).strip()
    return None


def fetch_tabelog_price_info(url: str | None) -> PriceInfo:
    """從食べログ店家頁抓午餐/晚餐預算。

    食べログ頁面可能改版或擋請求，所以這個函式採取 best effort：
    找不到價格就回傳空 PriceInfo，不讓保存餐廳失敗。
    """

    if not url:
        return PriceInfo()
    try:
        resp = requests.get(
            url,
            timeout=6,
            headers={
                "User-Agent": "Mozilla/5.0 restaurant-memory-bot/0.1",
                "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
            },
        )
        resp.raise_for_status()
    except requests.RequestException:
        return PriceInfo()

    soup = BeautifulSoup(resp.text, "html.parser")
    page_text = soup.get_text("\n", strip=True)
    dinner_text = find_price_near_label(page_text, ["夜", "ディナー", "Dinner"])
    lunch_text = find_price_near_label(page_text, ["昼", "ランチ", "Lunch"])

    if not dinner_text or not lunch_text:
        prices = PRICE_RE.findall(page_text)
        if prices:
            dinner_text = dinner_text or prices[0]
        if len(prices) > 1:
            lunch_text = lunch_text or prices[1]

    lunch_min, lunch_max = parse_budget_range(lunch_text)
    dinner_min, dinner_max = parse_budget_range(dinner_text)
    updated_at = None
    if lunch_text or dinner_text:
        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return PriceInfo(
        lunch_budget_text=lunch_text,
        lunch_budget_min=lunch_min,
        lunch_budget_max=lunch_max,
        dinner_budget_text=dinner_text,
        dinner_budget_min=dinner_min,
        dinner_budget_max=dinner_max,
        price_updated_at=updated_at,
    )


PRICE_RE = re.compile(r"￥\s*[\d,]+\s*未満|￥\s*[\d,]+(?:\s*[～〜-]\s*￥?\s*[\d,]+)?")


def find_price_near_label(text: str, labels: list[str]) -> str | None:
    """在頁面文字中找靠近指定標籤的價格。"""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not any(label in line for label in labels):
            continue
        window = "\n".join(lines[index : index + 8])
        match = PRICE_RE.search(window)
        if match:
            return normalize_price_text(match.group(0))
    return None


def parse_budget_range(text: str | None) -> tuple[int | None, int | None]:
    """把 '￥1,000～￥1,999' 解析成 (1000, 1999)。"""

    if not text:
        return None, None
    numbers = [int(value.replace(",", "")) for value in re.findall(r"\d[\d,]*", text)]
    if "未満" in text and numbers:
        return 0, numbers[0] - 1
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    if len(numbers) == 1:
        return numbers[0], None
    return None, None


def normalize_price_text(text: str) -> str:
    """統一價格文字格式。"""

    return re.sub(r"\s+", "", text).replace("〜", "～")


def resolve_google_maps_url(url: str | None) -> str | None:
    """把 Google Maps 短網址展開。

    maps.app.goo.gl 這類短網址本身看不出店名，因此先用 HEAD/GET
    跟隨跳轉，再從最後網址解析 /maps/place/店名。
    """

    if not url:
        return None
    try:
        resp = requests.get(
            url,
            timeout=5,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 restaurant-memory-bot/0.1"},
        )
        return resp.url or url
    except requests.RequestException:
        return url


def place_name_from_google_maps_url(url: str | None) -> str | None:
    """從 Google Maps URL 解析可能的店名。

    支援常見格式：
    - /maps/place/店名/...
    - /maps/search/?api=1&query=店名
    - ?q=店名
    """

    if not url:
        return None
    resolved_url = resolve_google_maps_url(url)
    if not resolved_url:
        return None

    parsed = urlparse(resolved_url)
    query = parse_qs(parsed.query)
    for key in ("query", "q"):
        values = query.get(key)
        if values and values[0].strip():
            return cleanup_place_name(values[0])

    marker = "/maps/place/"
    if marker in parsed.path:
        place_part = parsed.path.split(marker, 1)[1].split("/", 1)[0]
        return cleanup_place_name(unquote(place_part.replace("+", " ")))
    return None


def cleanup_place_name(value: str) -> str | None:
    """清理 Google Maps URL 裡解析出的店名。"""

    text = unquote(value).replace("+", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text or None


def find_tabelog_url_by_search(name: str | None, area: str | None = None) -> str | None:
    """用店名反查食べログ店家頁。

    優先走使用者期待的流程：
    1. 從 Google Maps 得到店名
    2. 直接打食べログ站內搜尋
    3. 從站內搜尋結果挑最像的店家頁

    如果站內搜尋失敗，才退回搜尋引擎備援。

    找不到時回傳 None，不影響餐廳保存。
    """

    if not name:
        return None
    return find_tabelog_url_by_site_search(name, area) or find_tabelog_url_by_web_search(name, area)


def find_tabelog_url_by_site_search(name: str, area: str | None = None) -> str | None:
    """直接使用食べログ站內搜尋找店家頁。"""

    query = tabelog_search_query(name, area)
    try:
        resp = requests.get(
            "https://tabelog.com/rstLst/",
            params={"sw": query, "SrtT": "trend"},
            timeout=8,
            headers={
                "User-Agent": "Mozilla/5.0 restaurant-memory-bot/0.1",
                "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
            },
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    return best_tabelog_candidate(resp.text, name=name, area=area)


def find_tabelog_url_by_web_search(name: str, area: str | None = None) -> str | None:
    """搜尋引擎備援：站內搜尋找不到時才使用。"""

    query = f"site:tabelog.com {tabelog_search_query(name, area)}".strip()
    try:
        resp = requests.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0 restaurant-memory-bot/0.1"},
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for anchor in soup.find_all("a", href=True):
        candidate = extract_tabelog_result_url(str(anchor["href"]))
        if candidate:
            return candidate
    return None


def tabelog_search_query(name: str, area: str | None = None) -> str:
    """組合食べログ搜尋關鍵字，避免店名和地區重複。"""

    clean_name = cleanup_place_name(name) or name
    clean_area = cleanup_place_name(area or "") or ""
    if clean_area and normalize_match_text(clean_area) not in normalize_match_text(clean_name):
        return f"{clean_name} {clean_area}".strip()
    return clean_name.strip()


def best_tabelog_candidate(html: str, *, name: str, area: str | None) -> str | None:
    """從食べログ搜尋結果 HTML 中挑最像目標店家的 URL。"""

    soup = BeautifulSoup(html, "html.parser")
    scored_candidates: list[tuple[float, str]] = []
    for anchor in soup.find_all("a", href=True):
        candidate = extract_tabelog_result_url(str(anchor["href"]))
        if not candidate:
            continue
        text = anchor.get_text(" ", strip=True)
        score = tabelog_candidate_score(text, name=name, area=area)
        scored_candidates.append((score, candidate))

    if not scored_candidates:
        return None
    scored_candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best_url = scored_candidates[0]
    return best_url if best_score >= 0.35 else None


def tabelog_candidate_score(candidate_text: str, *, name: str, area: str | None) -> float:
    """用簡單相似度幫食べログ搜尋結果排序。

    搜尋結果的 HTML class 名稱容易變動，所以不要依賴特定 CSS class。
    只用連結文字和目標店名/地區做粗略排序，夠穩也好維護。
    """

    candidate = normalize_match_text(candidate_text)
    target_name = normalize_match_text(name)
    target_area = normalize_match_text(area or "")
    if not candidate or not target_name:
        return 0.0

    score = SequenceMatcher(None, candidate, target_name).ratio()
    if target_name in candidate:
        score += 0.5
    if target_area and target_area in candidate:
        score += 0.15
    return score


def normalize_match_text(value: str) -> str:
    """比對搜尋結果時用的輕量正規化。"""

    text = cleanup_place_name(value) or ""
    return re.sub(r"[\s　・･|｜/／\\()（）【】\[\]-]+", "", text).casefold()


def extract_tabelog_result_url(url: str) -> str | None:
    """從搜尋結果連結中取出食べログ店家 URL。"""

    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com"):
        redirected = parse_qs(parsed.query).get("uddg")
        if redirected:
            url = redirected[0]
            parsed = urlparse(url)

    if "tabelog.com" not in parsed.netloc:
        return None
    if any(skip in parsed.path for skip in ("/rstLst/", "/help/", "/sitemap/")):
        return None
    if not re.search(r"/[A-Z]\d{4,}/[A-Z]\d{6,}/\d+/?", parsed.path):
        return None
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


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
    attachment_image_url = first_image_url(messages)
    tabelog_url = find_tabelog_url(combined)
    google_maps_url = find_google_maps_url(combined)
    google_maps_place_name = place_name_from_google_maps_url(google_maps_url)
    tabelog_title = fetch_tabelog_title(tabelog_url)

    prompt = {
        "messages": plain_messages,
        "tabelog_url": tabelog_url,
        "tabelog_title": tabelog_title,
        "google_maps_url": google_maps_url,
        "google_maps_place_name": google_maps_place_name,
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
    if not name:
        name = google_maps_place_name
    area = _clean_optional(data.get("area"))
    category = str(data.get("category") or "未分類").strip()
    if not tabelog_url:
        tabelog_url = find_tabelog_url_by_search(name, area)
    google_url = google_maps_url or (google_maps_search_url(name, area) if name else None)
    price_info = fetch_tabelog_price_info(tabelog_url)
    image_url = attachment_image_url or fetch_tabelog_image_url(tabelog_url)

    return ExtractionResult(
        name=name,
        category=category,
        area=area,
        tabelog_url=tabelog_url,
        google_maps_url=google_url,
        image_url=image_url,
        comments=str(data.get("comments") or "").strip(),
        keywords=[str(k).strip() for k in data.get("keywords", []) if str(k).strip()],
        reason=str(data.get("reason") or "").strip(),
        lunch_budget_text=price_info.lunch_budget_text,
        lunch_budget_min=price_info.lunch_budget_min,
        lunch_budget_max=price_info.lunch_budget_max,
        dinner_budget_text=price_info.dinner_budget_text,
        dinner_budget_min=price_info.dinner_budget_min,
        dinner_budget_max=price_info.dinner_budget_max,
        price_updated_at=price_info.price_updated_at,
    )


def _clean_optional(value: object) -> str | None:
    """把 AI 回傳的欄位整理成 None 或非空字串。"""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def first_image_url(messages: list[MessageSnippet]) -> str | None:
    """Use the first Discord image attachment as the restaurant card image."""

    image_extensions = (".jpg", ".jpeg", ".png", ".webp", ".gif")
    for message in messages:
        for url in message.attachment_urls:
            clean_url = str(url).split("?", 1)[0].lower()
            if clean_url.endswith(image_extensions):
                return str(url)
    return None

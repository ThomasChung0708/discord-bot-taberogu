from __future__ import annotations

"""Discord bot 的主程式。

這個檔案負責「接收 Discord 事件」與「把使用者操作導向正確功能」：
- 右鍵訊息保存餐廳資訊
- 右鍵評論追加到指定餐廳
- @bot 關鍵字搜尋餐廳
- @bot 更新地圖，把 SQLite 資料同步到 Google Sheets
- slash command 的輔助功能，例如 /list_restaurants、/export_map_csv

可以把 bot.py 想成 MVC 架構裡的 Controller：
它不直接處理資料庫細節，也不直接寫 Google Sheets，而是呼叫 db.py、
extractor.py、sheets_sync.py 這些模組完成真正工作。
"""

import csv
import asyncio
import datetime as dt
import io
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv
from googleapiclient.errors import HttpError
from openai import OpenAI

from db import ChatMemoryMessage, Restaurant, RestaurantDB, normalize_search_text
from extractor import MessageSnippet, extract_restaurant, fetch_tabelog_price_info
from sheets_sync import sync_restaurants_to_sheet


# 所有本機檔案路徑都以 bot.py 所在資料夾為基準。
# 這樣不管你在 Windows、VM，或從哪個工作目錄啟動 bot，
# 都會讀到同一份 .env、restaurants.sqlite3、service-account.json。
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# .env 是部署時放機密資料的地方；這些值不要 commit 到 GitHub。
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DB_PATH_VALUE = os.getenv("DB_PATH", "restaurants.sqlite3")
DB_PATH = Path(DB_PATH_VALUE)
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_FILE_VALUE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
GOOGLE_SERVICE_ACCOUNT_FILE = Path(GOOGLE_SERVICE_ACCOUNT_FILE_VALUE) if GOOGLE_SERVICE_ACCOUNT_FILE_VALUE else None
if GOOGLE_SERVICE_ACCOUNT_FILE and not GOOGLE_SERVICE_ACCOUNT_FILE.is_absolute():
    GOOGLE_SERVICE_ACCOUNT_FILE = BASE_DIR / GOOGLE_SERVICE_ACCOUNT_FILE
GOOGLE_SHEETS_WORKSHEET = os.getenv("GOOGLE_SHEETS_WORKSHEET", "restaurants").strip() or "restaurants"
GOOGLE_MY_MAPS_URL = os.getenv("GOOGLE_MY_MAPS_URL", "").strip()
PUBLIC_WEB_URL = os.getenv("PUBLIC_WEB_URL", "").strip()
CHAT_MEMORY_ENABLED = os.getenv("CHAT_MEMORY_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
CHAT_MEMORY_RAW_LIMIT = int(os.getenv("CHAT_MEMORY_RAW_LIMIT", "100"))
CHAT_MEMORY_SUMMARY_BATCH = int(os.getenv("CHAT_MEMORY_SUMMARY_BATCH", "50"))
CHAT_MEMORY_MAX_MESSAGE_CHARS = int(os.getenv("CHAT_MEMORY_MAX_MESSAGE_CHARS", "800"))

# 建立共用物件：
# - db 負責所有 SQLite 操作
# - openai_client 負責呼叫 OpenAI API
# - MESSAGE_ID_RE 用來從 Discord 訊息連結中抓 message id
db = RestaurantDB(str(DB_PATH))
db.cleanup_comment_placeholders()
openai_client = OpenAI()
MESSAGE_ID_RE = re.compile(r"(\d{17,25})")

# message_content 是讓 bot 可以讀到一般訊息文字的 intent。
# @bot 拉麵、@bot 更新地圖 這類功能都需要它。
intents = discord.Intents.default()
intents.message_content = True


class RestaurantSelect(discord.ui.Select):
    """查詢結果用的餐廳下拉選單。

    目前主要保留給 slash command 的選擇式查詢使用。
    Discord 下拉選單最多只能有 25 個選項，所以這裡也只取前 25 筆。
    """

    def __init__(self, restaurants: list[Restaurant]) -> None:
        options = [
            discord.SelectOption(
                label=r.name[:100],
                description=f"{r.category} {r.area or ''}".strip()[:100],
                value=str(r.id),
            )
            for r in restaurants[:25]
        ]
        super().__init__(
            placeholder="選擇餐廳",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        restaurant = db.get(int(self.values[0]))
        if not restaurant:
            await interaction.response.send_message("找不到這間餐廳，可能資料被刪除了。", ephemeral=True)
            return
        await interaction.response.send_message(embed=restaurant_embed(restaurant), ephemeral=True)


class RestaurantSelectView(discord.ui.View):
    """把 RestaurantSelect 包成 Discord View。

    View 是 Discord UI 元件的容器；Select、Button 都要放在 View 裡。
    """

    def __init__(self, restaurants: list[Restaurant]) -> None:
        super().__init__(timeout=180)
        self.add_item(RestaurantSelect(restaurants))


class SearchResultsView(discord.ui.View):
    """搜尋結果分頁。

    一次只顯示一間餐廳，避免「拉麵」這種關鍵字命中很多店時洗版。
    使用者可以按「上一頁 / 下一頁」在同一則訊息裡切換結果。
    """

    def __init__(self, keyword: str, restaurants: list[Restaurant]) -> None:
        super().__init__(timeout=180)
        self.keyword = keyword
        self.restaurants = restaurants
        self.index = 0
        self.update_buttons()

    def current_embed(self) -> discord.Embed:
        """把目前 index 指到的餐廳轉成 Discord embed。"""
        embed = restaurant_embed(self.restaurants[self.index])
        embed.set_footer(text=f"{self.keyword} 搜尋結果 {self.index + 1} / {len(self.restaurants)}")
        return embed

    def update_buttons(self) -> None:
        """根據目前頁數決定上一頁/下一頁按鈕能不能按。"""
        self.previous_page.disabled = self.index <= 0
        self.next_page.disabled = self.index >= len(self.restaurants) - 1

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary)
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.index = max(0, self.index - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.primary)
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.index = min(len(self.restaurants) - 1, self.index + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)


class AreaSelect(discord.ui.Select):
    """先用關鍵字搜尋，再讓使用者選地區。

    例如 @bot 拉麵 找到很多間時，先分成「府中」「池袋」「西國立」等地區，
    使用者選完地區後才進入 SearchResultsView 分頁。
    """

    def __init__(self, keyword: str, restaurants: list[Restaurant]) -> None:
        self.keyword = keyword
        self.restaurants = restaurants
        areas = sorted({(restaurant.area or "未分類地區").strip() for restaurant in restaurants})
        options = [
            discord.SelectOption(
                label=area[:100],
                description=f"{sum(1 for restaurant in restaurants if (restaurant.area or '未分類地區').strip() == area)} 間",
                value=area,
            )
            for area in areas[:25]
        ]
        super().__init__(
            placeholder="選擇地區",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """使用者選地區後，過濾出該地區餐廳並改成分頁結果。"""
        area = self.values[0]
        restaurants = [
            restaurant
            for restaurant in self.restaurants
            if (restaurant.area or "未分類地區").strip() == area
        ]
        view = SearchResultsView(f"{self.keyword} / {area}", restaurants)
        await interaction.response.edit_message(
            content=f"找到 {len(restaurants)} 筆「{self.keyword}」在「{area}」的餐廳：",
            embed=view.current_embed(),
            view=view,
        )


class AreaSelectView(discord.ui.View):
    """地區下拉選單的 View 容器。"""

    def __init__(self, keyword: str, restaurants: list[Restaurant]) -> None:
        super().__init__(timeout=180)
        self.add_item(AreaSelect(keyword, restaurants))


class CommentRestaurantSelect(discord.ui.Select):
    """把單則 Discord 評論追加到某間餐廳的選單。

    使用者右鍵一則評論 → 保存為餐廳評論 → bot 顯示這個選單。
    選中餐廳後，callback 會呼叫 db.append_comment 寫入 SQLite。
    """

    def __init__(self, restaurants: list[Restaurant], comment: str, created_by: str) -> None:
        self.comment = comment
        self.created_by = created_by
        options = [
            discord.SelectOption(
                label=r.name[:100],
                description=f"ID {r.id} / {r.category} {r.area or ''}".strip()[:100],
                value=str(r.id),
            )
            for r in restaurants[:25]
        ]
        super().__init__(
            placeholder="選擇要追加評論的餐廳",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        restaurant = db.append_comment(
            restaurant_id=int(self.values[0]),
            comment=self.comment,
            created_by=self.created_by,
        )
        if not restaurant:
            await interaction.response.send_message("找不到這間餐廳，可能資料被刪除了。", ephemeral=True)
            return
        await interaction.response.edit_message(content="已追加評論。", view=None)
        await interaction.followup.send(
            "已把這則訊息追加成餐廳評論。",
            embed=restaurant_embed(restaurant),
            ephemeral=False,
        )


class CommentRestaurantSelectView(discord.ui.View):
    """評論追加選單的 View 容器。"""

    def __init__(self, restaurants: list[Restaurant], comment: str, created_by: str) -> None:
        super().__init__(timeout=180)
        self.add_item(CommentRestaurantSelect(restaurants, comment, created_by))


class RestaurantBot(discord.Client):
    """自訂 Discord Client。

    discord.Client 負責和 Discord Gateway 保持連線。
    CommandTree 負責 slash command 和右鍵應用程式選單。
    """

    def __init__(self) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        commands = await self.tree.sync()
        print(f"Synced {len(commands)} slash commands: {', '.join(command.name for command in commands)}")


client = RestaurantBot()


@client.event
async def on_ready() -> None:
    """Discord 連線成功時觸發。"""
    print(f"Logged in as {client.user}")


@client.event
async def on_message(message: discord.Message) -> None:
    """處理一般訊息。

    這裡只處理「有提到 bot」的訊息：
    - @bot 更新地圖：同步 Google Sheet
    - @bot 地圖：回 My Maps 連結
    - @bot 拉麵：用「拉麵」搜尋餐廳
    """

    if message.author.bot or not client.user:
        return
    if client.user not in message.mentions:
        await remember_chat_message(message)
        return

    keyword = re.sub(rf"<@!?{client.user.id}>", "", message.content).strip()
    if not keyword:
        await message.reply("請在提到我後面加上關鍵字，例如：@食べログBOT 拉麵", mention_author=False)
        return

    recommendation_request = parse_recommendation_request(keyword)
    if recommendation_request is not None:
        await send_recommendations(message, recommendation_request)
        return
    keyword = cleanup_search_keyword(keyword)
    if not keyword:
        await message.reply("請在提到我後面加上關鍵字，例如：@食べログBOT 拉麵", mention_author=False)
        return
    recommendation_request = parse_recommendation_request(keyword)
    if recommendation_request is not None:
        await send_recommendations(message, recommendation_request)
        return
    await send_search_results(message, keyword)


async def remember_chat_message(message: discord.Message) -> None:
    """Store non-bot channel messages as lightweight recommendation memory."""

    if not CHAT_MEMORY_ENABLED or not message.guild:
        return
    content = message.clean_content.strip() if hasattr(message, "clean_content") else message.content.strip()
    if not content:
        return
    content = re.sub(r"\s+", " ", content)
    if len(content) > CHAT_MEMORY_MAX_MESSAGE_CHARS:
        content = content[:CHAT_MEMORY_MAX_MESSAGE_CHARS].rstrip() + "..."

    inserted = db.record_chat_memory(
        guild_id=message.guild.id,
        channel_id=message.channel.id,
        author_id=message.author.id,
        author_name=message.author.display_name,
        message_id=message.id,
        content=content,
        created_at=message.created_at.isoformat(),
    )
    if not inserted:
        return

    count = db.chat_memory_count(guild_id=message.guild.id, channel_id=message.channel.id)
    if count > CHAT_MEMORY_RAW_LIMIT + CHAT_MEMORY_SUMMARY_BATCH:
        asyncio.create_task(compact_chat_memory(message.guild.id, message.channel.id))


async def compact_chat_memory(guild_id: int, channel_id: int) -> None:
    """Summarize old raw chat memory so the DB keeps recent detail and compact history."""

    old_messages = db.old_chat_memory_messages(
        guild_id=guild_id,
        channel_id=channel_id,
        keep_latest=CHAT_MEMORY_RAW_LIMIT,
        limit=CHAT_MEMORY_SUMMARY_BATCH,
    )
    if not old_messages:
        return
    existing_summary = db.chat_memory_summary(guild_id=guild_id, channel_id=channel_id)
    try:
        summary = await asyncio.to_thread(
            summarize_chat_memory,
            existing_summary,
            old_messages,
        )
    except Exception as exc:
        print(f"chat memory summarization failed: {exc}")
        return
    if not summary:
        return
    db.upsert_chat_memory_summary(guild_id=guild_id, channel_id=channel_id, summary=summary)
    db.delete_chat_memory_messages(message.message_id for message in old_messages)


def summarize_chat_memory(existing_summary: str, messages: list[ChatMemoryMessage]) -> str:
    """Use AI to merge older chat messages into a compact food-preference summary."""

    transcript = "\n".join(
        f"{message.author_name}: {message.content}"
        for message in messages
        if message.content.strip()
    )
    response = openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "你在整理 Discord 美食聊天記憶。請只保留和餐廳推薦有關的偏好："
                    "地區、料理類型、預算、口味、喜歡/不喜歡、用餐場景、常提到的店。"
                    "不要保存無關閒聊、敏感個資、完整對話。請用繁體中文，200 字以內。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "existing_summary": existing_summary,
                        "new_messages": transcript,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        temperature=0.1,
        max_tokens=300,
        timeout=20,
    )
    return (response.choices[0].message.content or "").strip()


@client.tree.context_menu(name="保存餐廳資訊")
async def save_restaurant_from_message(
    interaction: discord.Interaction,
    message: discord.Message,
) -> None:
    """右鍵訊息選單：從指定訊息抽取餐廳資訊並保存。"""

    await interaction.response.defer(thinking=True, ephemeral=True)

    snippets = await snippets_for_target_message(message)
    await save_extracted_restaurant(
        interaction=interaction,
        snippets=snippets,
        source_message_id=message.id,
    )


@client.tree.context_menu(name="保存為餐廳評論")
async def save_comment_from_message(
    interaction: discord.Interaction,
    message: discord.Message,
) -> None:
    """右鍵訊息選單：把指定訊息當作評論追加到某間餐廳。"""

    comment = format_comment_messages([message])
    if not comment:
        await interaction.response.send_message(
            "這則訊息沒有可保存的文字或附件。",
            ephemeral=True,
        )
        return

    restaurants = db.all()
    if not restaurants:
        await interaction.response.send_message(
            "目前還沒有餐廳資料，請先保存餐廳資訊。",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        "要把這則評論追加到哪間餐廳？",
        view=CommentRestaurantSelectView(restaurants, comment, interaction.user.display_name),
        ephemeral=True,
    )


@client.tree.command(name="save_restaurant", description="請改用訊息右鍵選單保存指定餐廳資訊")
async def save_restaurant(interaction: discord.Interaction) -> None:
    """保留舊 slash command，提醒使用者改用右鍵選單。"""

    await interaction.response.send_message(
        "現在請對要保存的那則訊息按右鍵或長按，選「應用程式」→「保存餐廳資訊」。"
        "這樣我只會讀取你指定的那則訊息，不會再往上抓最近幾則。",
        ephemeral=True,
    )


async def save_extracted_restaurant(
    *,
    interaction: discord.Interaction,
    snippets: list[MessageSnippet],
    source_message_id: int | None,
) -> None:
    """共用的餐廳保存流程。

    右鍵保存餐廳時會先把 Discord message 轉成 MessageSnippet，
    再進到這裡呼叫 OpenAI 抽取餐廳欄位，最後寫進 SQLite。
    """

    if not snippets:
        await interaction.followup.send(
            "這則訊息沒有可讀取的文字或附件，我先不存。",
            ephemeral=True,
        )
        return

    try:
        result = extract_restaurant(client=openai_client, model=OPENAI_MODEL, messages=snippets)
    except TimeoutError:
        await interaction.followup.send(
            "處理逾時了。食べログ或 AI 回應太慢，請稍後再試一次。",
            ephemeral=True,
        )
        return
    except Exception as exc:
        print(f"save_restaurant failed: {exc}")
        await interaction.followup.send(
            "剛剛處理失敗了。請確認 OpenAI API key、網路連線，或稍後再試一次。",
            ephemeral=True,
        )
        return

    if not result.name:
        await interaction.followup.send(
            f"這段我先不存：{result.reason or '找不到餐廳名稱或食べログ資訊。'}",
            ephemeral=True,
        )
        return

    restaurant_id = db.add_restaurant(
        name=result.name,
        category=result.category,
        area=result.area,
        tabelog_url=result.tabelog_url,
        google_maps_url=result.google_maps_url,
        comments=result.comments,
        keywords=result.keywords + [result.category],
        source_channel_id=interaction.channel_id or 0,
        source_message_id=source_message_id,
        created_by=interaction.user.display_name,
        image_url=result.image_url,
        lunch_budget_text=result.lunch_budget_text,
        lunch_budget_min=result.lunch_budget_min,
        lunch_budget_max=result.lunch_budget_max,
        dinner_budget_text=result.dinner_budget_text,
        dinner_budget_min=result.dinner_budget_min,
        dinner_budget_max=result.dinner_budget_max,
        price_updated_at=result.price_updated_at,
    )

    restaurant = db.get(restaurant_id)
    await interaction.followup.send(
        f"已儲存這間餐廳。餐廳 ID：{restaurant_id}",
        embed=restaurant_embed(restaurant) if restaurant else None,
        ephemeral=False,
    )


async def snippets_for_target_message(message: discord.Message) -> list[MessageSnippet]:
    """把指定 Discord 訊息轉成 AI 可讀的片段。

    如果這則訊息是「回覆」別人的訊息，會連同被回覆的原訊息一起送給 AI。
    這能支援「這間好吃」回覆一則食べログ連結的情境。
    """

    messages: list[discord.Message] = []
    referenced = await fetch_referenced_message(message)
    if referenced and not referenced.author.bot:
        messages.append(referenced)
    if not message.author.bot:
        messages.append(message)
    return [message_to_snippet(msg) for msg in messages]


async def fetch_referenced_message(message: discord.Message) -> discord.Message | None:
    """取得 Discord 回覆訊息的原始 message。"""

    reference = message.reference
    if not reference or not reference.message_id:
        return None
    if isinstance(reference.resolved, discord.Message):
        return reference.resolved
    channel = message.channel
    if hasattr(channel, "fetch_message"):
        try:
            return await channel.fetch_message(reference.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    return None


def message_to_snippet(message: discord.Message) -> MessageSnippet:
    """把 discord.Message 轉成 extractor.py 使用的簡單資料結構。"""

    return MessageSnippet(
        author=message.author.display_name,
        content=message.content,
        attachment_urls=[attachment.url for attachment in message.attachments],
    )


@client.tree.command(name="find_restaurant", description="用關鍵字查詢已儲存餐廳")
@app_commands.describe(keyword="例如：拉麵、咖啡、澀谷、家系")
async def find_restaurant(interaction: discord.Interaction, keyword: str) -> None:
    """slash command 搜尋餐廳。"""

    restaurants = db.search(keyword)
    if not restaurants:
        await interaction.response.send_message(f"目前沒有找到「{keyword}」。", ephemeral=True)
        return

    areas = unique_areas(restaurants)
    if len(areas) > 1:
        await interaction.response.send_message(
            f"找到 {len(restaurants)} 筆「{keyword}」相關餐廳，請先選擇地區：",
            view=AreaSelectView(keyword, restaurants),
            ephemeral=True,
        )
        return

    view = SearchResultsView(keyword, restaurants)
    await interaction.response.send_message(
        f"找到 {len(restaurants)} 筆「{keyword}」相關餐廳：",
        embed=view.current_embed(),
        view=view,
        ephemeral=True,
    )

@client.tree.command(name="recommend", description="從資料庫餐廳中推薦符合條件的店")
@app_commands.describe(request="例如：新宿 家系 濃厚 1500，或：池袋 豬排")
async def recommend(interaction: discord.Interaction, request: str) -> None:
    """Recommend restaurants from the saved database only."""

    await send_recommendation_interaction(interaction, request)


@client.tree.command(name="recommend_restaurant", description="從資料庫餐廳中推薦符合條件的店")
@app_commands.describe(request="例如：新宿 家系 濃厚 1500，或：池袋 豬排")
async def recommend_restaurant(interaction: discord.Interaction, request: str) -> None:
    """Longer alias for the recommendation command."""

    await send_recommendation_interaction(interaction, request)


@client.tree.command(name="recommed", description="推薦餐廳指令的拼字容錯")
@app_commands.describe(request="例如：新宿 家系 濃厚 1500，或：池袋 豬排")
async def recommed(interaction: discord.Interaction, request: str) -> None:
    """Typo-tolerant alias for /recommend."""

    await send_recommendation_interaction(interaction, request)


async def send_recommendation_interaction(interaction: discord.Interaction, request: str) -> None:
    """Handle all slash-command recommendation aliases."""

    await interaction.response.defer(thinking=True, ephemeral=False)
    memory_context = ""
    if interaction.guild_id and interaction.channel_id:
        memory_context = db.chat_memory_context(
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
        )
    content = build_recommendation_response(request, memory_context=memory_context)
    await interaction.followup.send(content, ephemeral=False)


def parse_recommendation_request(keyword: str) -> str | None:
    """Return the recommendation request part for @bot mention messages."""

    for text in recommendation_request_lines(keyword):
        normalized = normalize_search_text(text)
        for trigger in recommendation_trigger_prefixes():
            normalized_trigger = normalize_search_text(trigger)
            if normalized == normalized_trigger:
                return ""
            if normalized.startswith(normalized_trigger):
                rest = text[len(trigger) :].strip()
                rest = strip_recommendation_leading_words(rest)
                if rest:
                    return rest
        for trigger in recommendation_inline_triggers():
            index = normalized.find(normalize_search_text(trigger))
            if index <= 0:
                continue
            rest = text[index + len(trigger) :].strip()
            rest = strip_recommendation_leading_words(rest)
            if rest:
                return rest
    return None


def recommendation_request_lines(keyword: str) -> list[str]:
    """Split a mention message into meaningful request lines."""

    return [line.strip() for line in keyword.splitlines() if line.strip()]


def recommendation_trigger_prefixes() -> list[str]:
    """Recommendation command words, built with code points for encoding safety."""

    return [
        chr(0x627E),  # 找
        chr(0x63A8) + chr(0x85A6),  # 推薦
        chr(0x5E2E) + chr(0x6211) + chr(0x627E),  # 帮我找
        chr(0x5E6B) + chr(0x6211) + chr(0x627E),  # 幫我找
        chr(0x5E2E) + chr(0x6211) + chr(0x63A8) + chr(0x8350),  # 帮我推荐
        chr(0x5E6B) + chr(0x6211) + chr(0x63A8) + chr(0x85A6),  # 幫我推薦
        "recommend",
    ]


def recommendation_inline_triggers() -> list[str]:
    """Words that may appear inside a casual sentence, such as '我想找...'."""

    return [
        chr(0x627E),  # 找
        chr(0x63A8) + chr(0x85A6),  # 推薦
        "recommend",
    ]


def strip_recommendation_leading_words(text: str) -> str:
    """Remove filler words after the trigger word."""

    filler_words = [
        chr(0x5728),  # 在
        chr(0x5230),  # 到
        chr(0x5403),  # 吃
        chr(0x60F3) + chr(0x5403),  # 想吃
    ]
    result = text.strip()
    changed = True
    while changed and result:
        changed = False
        for word in filler_words:
            if result.startswith(word):
                result = result[len(word) :].strip()
                changed = True
    return result


async def send_recommendations(target: discord.Message, request: str) -> None:
    """Reply to a mention-based recommendation request."""

    if not request:
        await target.reply(
            "想找什麼類型呢？例如：`@食べログBOT 找 新宿 家系 濃厚`",
            mention_author=False,
        )
        return
    status = await target.reply("正在從已儲存的餐廳裡挑候選...", mention_author=False)
    memory_context = ""
    if target.guild:
        memory_context = db.chat_memory_context(
            guild_id=target.guild.id,
            channel_id=target.channel.id,
        )
    response = build_recommendation_response(request, memory_context=memory_context)
    await status.edit(content=response)


def build_recommendation_response(request: str, memory_context: str = "") -> str:
    """Build a recommendation message without inventing restaurants."""

    intent = infer_recommendation_intent(request, memory_context=memory_context)
    candidates = recommendation_candidates(
        request,
        memory_context=memory_context,
        intent=intent,
        limit=10,
    )
    if not candidates:
        understood = intent.get("summary") or "、".join(intent_search_terms(intent)) or request
        return (
            f"目前沒有找到符合「{understood}」的已儲存餐廳。\n"
            "可以先試試比較短的關鍵字，例如：`@食べログBOT 拉麵` 或 `@食べログBOT 找 新宿 拉麵`。"
        )

    ranked = rank_recommendations_with_ai(
        request,
        candidates,
        memory_context=memory_context,
        intent=intent,
    )
    understood = intent.get("summary") or request
    lines = [f"我理解成「{understood}」，從 DB 裡幫你挑了 {len(ranked)} 間候選："]
    for index, (restaurant, reason) in enumerate(ranked, start=1):
        price = recommendation_price_text(restaurant)
        lines.append(
            "\n".join(
                part
                for part in [
                    f"{index}. ID {restaurant.id}｜{restaurant.name}",
                    f"   {restaurant.category} / {restaurant.area or '地區未填'}",
                    f"   理由：{reason}",
                    f"   價格：{price}" if price else "",
                    f"   食べログ：{restaurant.tabelog_url}" if restaurant.tabelog_url else "",
                    f"   Google Maps：{restaurant.google_maps_url}" if restaurant.google_maps_url else "",
                ]
                if part
            )
        )
    return trim_discord_message("\n\n".join(lines))


def recommendation_candidates(
    request: str,
    memory_context: str = "",
    intent: dict[str, object] | None = None,
    limit: int = 10,
) -> list[Restaurant]:
    """Score saved restaurants against a natural language request."""

    restaurants = db.all()
    scored: list[tuple[int, int, Restaurant]] = []
    intent = intent or infer_recommendation_intent(request, memory_context=memory_context)
    budget = recommendation_budget(intent, request)
    tokens = recommendation_tokens(request)
    intent_tokens = recommendation_tokens(" ".join(intent_search_terms(intent)))
    memory_tokens = recommendation_tokens(memory_context)[:25] if memory_context else []
    for restaurant in restaurants:
        score = score_restaurant_for_request(restaurant, tokens, budget)
        if intent_tokens:
            score += score_restaurant_for_request(restaurant, intent_tokens, budget)
        exclude_tokens = recommendation_tokens(" ".join(list_from_intent(intent, "exclude_terms")))
        if exclude_tokens and score_restaurant_for_request(restaurant, exclude_tokens, None) > 0:
            continue
        if memory_tokens:
            memory_score = score_restaurant_for_request(restaurant, memory_tokens, None)
            if score > 0:
                score += min(3, memory_score)
            elif len(tokens) <= 1 and memory_score > 0:
                score = min(3, memory_score)
        if score > 0:
            scored.append((score, -restaurant.id, restaurant))
    scored.sort(reverse=True)
    return [restaurant for _, _, restaurant in scored[:limit]]


def recommendation_tokens(request: str) -> list[str]:
    """Split a request into normalized searchable tokens."""

    text = cleanup_search_keyword(request)
    text = re.sub(r"[、，。！？!?/｜|]+", " ", text)
    stop_words = {
        "找",
        "推薦",
        "帮我",
        "幫我",
        "有沒有",
        "有没有",
        "可以",
        "嗎",
        "么",
        "現在",
        "現在人",
        "人在",
        "想吃",
        "最好吃",
        "之類",
        "之類的",
        "餐廳",
        "店",
        "附近",
    }
    normalized_stops = {normalize_search_text(word) for word in stop_words}
    normalized_stops.update(
        normalize_search_text(word)
        for word in [
            "我",
            "人在",
            "想要",
            "想吃",
            "請問",
            "這邊",
            "有沒有",
            "推薦",
            "一下",
            "會去",
        ]
    )
    normalized_text = normalize_search_text(text)
    tokens: list[str] = []
    for raw in text.split():
        raw = raw.strip()
        if not raw:
            continue
        normalized = normalize_search_text(raw)
        if not normalized or normalized in normalized_stops:
            continue
        if normalized.isdigit():
            continue
        tokens.append(normalized)
        tokens.extend(recommendation_synonyms(normalized))
    for term in known_recommendation_terms():
        if term and term in normalized_text and term not in tokens:
            tokens.append(term)
    for alias in recommendation_synonyms_for_text(normalized_text):
        if alias not in tokens:
            tokens.append(alias)
    if not tokens:
        fallback = normalize_search_text(text)
        if fallback:
            tokens.append(fallback)
    return tokens


def infer_recommendation_intent(request: str, memory_context: str = "") -> dict[str, object]:
    """Turn a natural sentence into DB search intent before candidate lookup."""

    known = recommendation_known_values()
    fallback = rule_based_recommendation_intent(request)
    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是餐廳推薦 bot 的查詢理解器。請先理解使用者真正想吃/想做什麼，"
                        "再產生適合拿去 SQLite 餐廳 DB 搜尋的詞。"
                        "DB 欄位包含店名、地區、分類、keywords、tags、comments、午餐/晚餐價格。"
                        "請回 JSON object，格式："
                        "{\"summary\":\"使用者意圖一句話\","
                        "\"area_terms\":[\"地區詞\"],"
                        "\"food_terms\":[\"料理/店型詞\"],"
                        "\"scene_terms\":[\"場景/需求詞\"],"
                        "\"exclude_terms\":[\"不要的詞\"],"
                        "\"budget\":數字或null,"
                        "\"expanded_terms\":[\"可搜尋同義詞\"]}。"
                        "例：'人在橫濱 想要喝到吐' => area_terms 橫濱, food/scene 包含 居酒屋、酒、飲み、バル。"
                        "例：'新宿 燒烤' => area_terms 新宿, food_terms 包含 燒肉、焼肉、焼鳥、串燒。"
                        "只能產生搜尋詞，不要推薦不存在的店。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": request,
                            "chat_memory": memory_context[:1200],
                            "known_db_values": known,
                            "fallback_hint": fallback,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=500,
            response_format={"type": "json_object"},
            timeout=20,
        )
        data = json.loads(response.choices[0].message.content or "{}")
        return merge_recommendation_intents(fallback, data)
    except Exception as exc:
        print(f"recommendation intent inference failed: {exc}")
        return fallback


def recommendation_known_values(limit: int = 120) -> dict[str, list[str]]:
    """Expose known DB vocabulary to the AI intent parser."""

    areas: list[str] = []
    categories: list[str] = []
    tags: list[str] = []
    seen_area: set[str] = set()
    seen_category: set[str] = set()
    seen_tag: set[str] = set()
    for restaurant in db.all():
        if restaurant.area and restaurant.area not in seen_area:
            seen_area.add(restaurant.area)
            areas.append(restaurant.area)
        if restaurant.category and restaurant.category not in seen_category:
            seen_category.add(restaurant.category)
            categories.append(restaurant.category)
        for tag in [*restaurant.tags, *restaurant.keywords]:
            if tag and tag not in seen_tag:
                seen_tag.add(tag)
                tags.append(tag)
        if len(areas) + len(categories) + len(tags) >= limit:
            break
    return {
        "areas": areas[:40],
        "categories": categories[:30],
        "tags": tags[:50],
    }


def rule_based_recommendation_intent(request: str) -> dict[str, object]:
    """Local fallback intent expansion for common casual phrases."""

    terms = recommendation_tokens(request)
    normalized = normalize_search_text(request)
    area_terms: list[str] = []
    food_terms: list[str] = []
    scene_terms: list[str] = []
    expanded_terms: list[str] = []

    known = recommendation_known_values()
    for area in known["areas"]:
        if normalize_search_text(area) in normalized:
            area_terms.append(area)
    area_aliases = [
        ("橫濱", ["横浜", "橫濱"]),
        ("横浜", ["横浜", "橫濱"]),
        ("澀谷", ["渋谷", "澀谷"]),
        ("渋谷", ["渋谷", "澀谷"]),
    ]
    for needle, values in area_aliases:
        if normalize_search_text(needle) in normalized:
            area_terms.extend(values)

    if any(word in request for word in ["喝到吐", "喝酒", "小酌", "飲み", "酒"]):
        food_terms.extend(["居酒屋", "バル", "焼鳥", "串燒", "串焼き"])
        scene_terms.extend(["喝酒", "聚餐", "下班"])
        expanded_terms.extend(["酒", "飲み", "居酒屋", "バル"])
    if any(word in request for word in ["燒烤", "烧烤", "烤肉", "串燒", "串烧"]):
        food_terms.extend(["燒肉", "焼肉", "焼鳥", "串燒", "串焼き", "ホルモン"])
        expanded_terms.extend(["燒肉", "焼肉", "焼鳥", "串"])
    if any(word in request for word in ["下班", "仕事帰り", "晚餐", "晚上"]):
        scene_terms.extend(["晚餐", "下班", "喝酒"])
    if any(word in request for word in ["便宜", "省錢", "安い", "便宜一點"]):
        scene_terms.extend(["便宜", "平價"])
    if any(word in request for word in ["一個人", "自己", "ひとり", "單人"]):
        scene_terms.extend(["一個人", "吧台", "快速"])

    return {
        "summary": request,
        "area_terms": unique_texts(area_terms),
        "food_terms": unique_texts(food_terms),
        "scene_terms": unique_texts(scene_terms),
        "exclude_terms": [],
        "budget": parse_first_int(request),
        "expanded_terms": unique_texts([*terms, *expanded_terms]),
    }


def merge_recommendation_intents(
    fallback: dict[str, object],
    inferred: dict[str, object],
) -> dict[str, object]:
    """Merge AI intent with deterministic fallback terms."""

    merged = dict(fallback)
    for key in ["area_terms", "food_terms", "scene_terms", "exclude_terms", "expanded_terms"]:
        merged[key] = unique_texts([
            *list_from_intent(fallback, key),
            *list_from_intent(inferred, key),
        ])
    summary = str(inferred.get("summary") or fallback.get("summary") or "").strip()
    if summary:
        merged["summary"] = summary
    budget = inferred.get("budget", fallback.get("budget"))
    merged["budget"] = budget if isinstance(budget, int) else fallback.get("budget")
    return merged


def intent_search_terms(intent: dict[str, object]) -> list[str]:
    """Flatten structured recommendation intent into searchable text terms."""

    terms: list[str] = []
    for key in ["area_terms", "food_terms", "scene_terms", "expanded_terms"]:
        terms.extend(list_from_intent(intent, key))
    return unique_texts(terms)


def list_from_intent(intent: dict[str, object], key: str) -> list[str]:
    """Read a list-like value from an intent dict safely."""

    value = intent.get(key)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def recommendation_budget(intent: dict[str, object], request: str) -> int | None:
    """Pick parsed AI budget first, then fallback to first number in text."""

    budget = intent.get("budget")
    if isinstance(budget, int):
        return budget
    return parse_first_int(request)


def unique_texts(values: Iterable[str]) -> list[str]:
    """Deduplicate text while keeping order."""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        key = normalize_search_text(text)
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def known_recommendation_terms() -> list[str]:
    """Collect saved areas/categories/keywords so natural sentences can match them."""

    terms: list[str] = []
    seen: set[str] = set()
    for restaurant in db.all():
        for value in [restaurant.area, restaurant.category, *restaurant.keywords, *restaurant.tags]:
            normalized = normalize_search_text(value or "")
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            terms.append(normalized)
    return terms


def recommendation_synonyms(token: str) -> list[str]:
    """Map common Chinese/Taiwanese food words to Japanese DB keywords."""

    groups = [
        ("豬排", ["とんかつ", "かつ", "カツ", "炸豬排"]),
        ("猪排", ["とんかつ", "かつ", "カツ", "炸猪排"]),
        ("拉麵", ["ラーメン", "らーめん", "ramen"]),
        ("拉面", ["ラーメン", "らーめん", "ramen"]),
        ("沾麵", ["つけ麺", "つけめん"]),
        ("沾面", ["つけ麺", "つけめん"]),
        ("燒肉", ["焼肉", "やきにく", "yakiniku"]),
        ("烧肉", ["焼肉", "やきにく", "yakiniku"]),
        ("壽司", ["寿司", "すし", "sushi"]),
        ("寿司", ["寿司", "すし", "sushi"]),
        ("咖哩", ["カレー", "curry"]),
        ("咖喱", ["カレー", "curry"]),
        ("居酒屋", ["居酒屋", "izakaya"]),
    ]
    aliases: list[str] = []
    for source, values in groups:
        normalized_source = normalize_search_text(source)
        normalized_values = [normalize_search_text(value) for value in values]
        if token == normalized_source or token in normalized_values:
            aliases.extend([normalized_source, *normalized_values])
    return [alias for alias in aliases if alias != token]


def recommendation_synonyms_for_text(normalized_text: str) -> list[str]:
    """Find synonym tokens inside a sentence that was not split by spaces."""

    aliases: list[str] = []
    seed_words = [
        "豬排",
        "猪排",
        "拉麵",
        "拉面",
        "沾麵",
        "沾面",
        "燒肉",
        "烧肉",
        "壽司",
        "寿司",
        "咖哩",
        "咖喱",
        "居酒屋",
    ]
    for word in seed_words:
        normalized_word = normalize_search_text(word)
        if normalized_word in normalized_text:
            aliases.append(normalized_word)
            aliases.extend(recommendation_synonyms(normalized_word))
    return aliases


def score_restaurant_for_request(
    restaurant: Restaurant,
    tokens: list[str],
    budget: int | None,
) -> int:
    """Give higher points to area/category/keyword matches than comment matches."""

    fields = {
        "name": normalize_search_text(restaurant.name),
        "category": normalize_search_text(restaurant.category),
        "area": normalize_search_text(restaurant.area or ""),
        "keywords": normalize_search_text(" ".join(restaurant.keywords)),
        "tags": normalize_search_text(" ".join(restaurant.tags)),
        "comments": normalize_search_text(restaurant.comments or ""),
    }
    score = 0
    for token in tokens:
        if token in fields["area"]:
            score += 5
        if token in fields["category"]:
            score += 4
        if token in fields["keywords"]:
            score += 4
        if token in fields["tags"]:
            score += 5
        if token in fields["name"]:
            score += 3
        if token in fields["comments"]:
            score += 2
    if budget and restaurant_matches_budget(restaurant, budget):
        score += 2
    return score


def restaurant_matches_budget(restaurant: Restaurant, budget: int) -> bool:
    """Return True when known lunch/dinner max price is within the requested budget."""

    known_ranges = [
        (restaurant.lunch_budget_min, restaurant.lunch_budget_max),
        (restaurant.dinner_budget_min, restaurant.dinner_budget_max),
    ]
    for minimum, maximum in known_ranges:
        if maximum and maximum <= budget:
            return True
        if minimum and not maximum and minimum <= budget:
            return True
    return False


def rank_recommendations_with_ai(
    request: str,
    candidates: list[Restaurant],
    memory_context: str = "",
    intent: dict[str, object] | None = None,
) -> list[tuple[Restaurant, str]]:
    """Ask AI to rank only the provided candidates, with a local fallback."""

    candidate_by_id = {restaurant.id: restaurant for restaurant in candidates}
    payload = [
        {
            "id": restaurant.id,
            "name": restaurant.name,
            "category": restaurant.category,
            "area": restaurant.area,
            "keywords": restaurant.keywords,
            "tags": restaurant.tags,
            "comments": (restaurant.comments or "")[:500],
            "lunch_budget": restaurant.lunch_budget_text,
            "dinner_budget": restaurant.dinner_budget_text,
        }
        for restaurant in candidates
    ]
    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是餐廳推薦助手。只能從使用者提供的 candidates 中推薦，"
                        "絕對不能新增或猜測不存在的餐廳。請用繁體中文回覆 JSON，"
                        "格式為 {\"recommendations\":[{\"id\":數字,\"reason\":\"一句理由\"}]}，最多 3 間。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": request,
                            "interpreted_intent": intent or {},
                            "chat_memory": memory_context[:2000],
                            "candidates": payload,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=500,
            response_format={"type": "json_object"},
            timeout=20,
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        ranked: list[tuple[Restaurant, str]] = []
        for item in data.get("recommendations", [])[:3]:
            restaurant = candidate_by_id.get(int(item.get("id", 0)))
            if not restaurant:
                continue
            reason = str(item.get("reason") or fallback_recommendation_reason(restaurant)).strip()
            ranked.append((restaurant, reason[:160]))
        if ranked:
            return ranked
    except Exception as exc:
        print(f"recommendation AI ranking failed: {exc}")

    return [
        (restaurant, fallback_recommendation_reason(restaurant))
        for restaurant in candidates[:3]
    ]


def fallback_recommendation_reason(restaurant: Restaurant) -> str:
    """Simple non-AI reason used when the API is unavailable."""

    details = [restaurant.category]
    if restaurant.area:
        details.append(restaurant.area)
    if restaurant.tags or restaurant.keywords:
        details.append("、".join((restaurant.tags or restaurant.keywords)[:3]))
    return "、".join(part for part in details if part) or "和你的條件有關聯"


def recommendation_price_text(restaurant: Restaurant) -> str:
    """Format price information for recommendation results."""

    parts = []
    if restaurant.lunch_budget_text:
        parts.append(f"午餐 {restaurant.lunch_budget_text}")
    if restaurant.dinner_budget_text:
        parts.append(f"晚餐 {restaurant.dinner_budget_text}")
    return " / ".join(parts)


def trim_discord_message(content: str, limit: int = 1900) -> str:
    """Keep normal message replies under Discord's 2000-character limit."""

    if len(content) <= limit:
        return content
    return content[: limit - 20].rstrip() + "\n...（結果太長，先省略後面）"


async def send_search_results(
    target: discord.Message,
    keyword: str,
) -> None:
    """@bot 關鍵字搜尋共用流程。"""

    recommendation_request = parse_recommendation_request(keyword)
    if recommendation_request is not None:
        await send_recommendations(target, recommendation_request)
        return

    restaurants = db.search(keyword)
    if not restaurants:
        await target.reply(f"目前沒有找到「{keyword}」。", mention_author=False)
        return

    areas = unique_areas(restaurants)
    if len(areas) > 1:
        await target.reply(
            f"找到 {len(restaurants)} 筆「{keyword}」相關餐廳，請先選擇地區：",
            view=AreaSelectView(keyword, restaurants),
            mention_author=False,
        )
        return

    view = SearchResultsView(keyword, restaurants)
    await target.reply(
        f"找到 {len(restaurants)} 筆「{keyword}」相關餐廳：",
        embed=view.current_embed(),
        view=view,
        mention_author=False,
    )


async def list_restaurants(interaction: discord.Interaction) -> None:
    """列出目前資料庫裡的餐廳與 ID，方便除錯與手動操作。"""

    restaurants = db.all()
    if not restaurants:
        await interaction.response.send_message(
            "目前資料庫裡沒有餐廳。請先對餐廳訊息使用「保存餐廳資訊」。",
            ephemeral=True,
        )
        return

    lines = [
        f"ID {r.id}: {r.name}（{r.category} {r.area or ''}）".strip()
        for r in restaurants[:25]
    ]
    await interaction.response.send_message(
        "目前已儲存的餐廳：\n" + "\n".join(lines),
        ephemeral=True,
    )


@app_commands.describe(
    restaurant_id="要編輯的餐廳 ID",
    name="新的店名，留空代表不修改",
    category="新的分類，留空代表不修改",
    area="新的地區，留空代表不修改",
    tabelog_url="新的食べログ URL，留空代表不修改",
    google_maps_url="新的 Google Maps URL，留空代表不修改",
    comments="新的評論內容，留空代表不修改",
    keywords="新的關鍵字，用逗號分隔，留空代表不修改",
)
async def edit_restaurant(
    interaction: discord.Interaction,
    restaurant_id: int,
    name: str | None = None,
    category: str | None = None,
    area: str | None = None,
    tabelog_url: str | None = None,
    google_maps_url: str | None = None,
    comments: str | None = None,
    keywords: str | None = None,
) -> None:
    """Discord slash command：編輯餐廳。

    Discord 的 slash command 不適合一次放很長的表單，
    所以這裡採用「只填想改的欄位」的方式。
    """

    restaurant = db.get(restaurant_id)
    if not restaurant:
        await interaction.response.send_message(
            f"找不到 ID {restaurant_id} 的餐廳。",
            ephemeral=True,
        )
        return

    updated = db.update_restaurant(
        restaurant_id=restaurant_id,
        name=name if name is not None else restaurant.name,
        category=category if category is not None else restaurant.category,
        area=area if area is not None else restaurant.area,
        tabelog_url=tabelog_url if tabelog_url is not None else restaurant.tabelog_url,
        google_maps_url=google_maps_url if google_maps_url is not None else restaurant.google_maps_url,
        comments=comments if comments is not None else restaurant.comments,
        keywords=parse_keywords(keywords) if keywords is not None else restaurant.keywords,
        image_url=restaurant.image_url,
    )
    await interaction.response.send_message(
        "已更新餐廳資料。",
        embed=restaurant_embed(updated) if updated else None,
        ephemeral=False,
    )


@app_commands.describe(
    restaurant_id="要刪除的餐廳 ID",
    confirm="安全確認：請選 True 才會真的刪除",
)
async def delete_restaurant(
    interaction: discord.Interaction,
    restaurant_id: int,
    confirm: bool = False,
) -> None:
    """Discord slash command：刪除餐廳。

    為了避免手滑，必須把 confirm 設成 True 才會真的刪除。
    """

    restaurant = db.get(restaurant_id)
    if not restaurant:
        await interaction.response.send_message(
            f"找不到 ID {restaurant_id} 的餐廳。",
            ephemeral=True,
        )
        return
    if not confirm:
        await interaction.response.send_message(
            f"你準備刪除 ID {restaurant.id}: {restaurant.name}。"
            "如果確定要刪除，請把 confirm 設成 True 再執行一次。",
            ephemeral=True,
        )
        return

    db.delete_restaurant(restaurant_id)
    await interaction.response.send_message(
        f"已刪除 ID {restaurant.id}: {restaurant.name}。",
        ephemeral=False,
    )


@app_commands.describe(
    restaurant="餐廳 ID 或關鍵字。若關鍵字找到多筆，請改用餐廳 ID",
    start_message="開始訊息的 ID 或訊息連結",
    end_message="結束訊息的 ID 或訊息連結",
)
async def add_comment(
    interaction: discord.Interaction,
    restaurant: str,
    start_message: str,
    end_message: str,
) -> None:
    """用開始/結束訊息連結，把一段 Discord 訊息追加成評論。"""

    await interaction.response.defer(thinking=True, ephemeral=True)

    target = resolve_restaurant(restaurant)
    if isinstance(target, str):
        await interaction.followup.send(target, ephemeral=True)
        return

    if not interaction.channel or not hasattr(interaction.channel, "fetch_message"):
        await interaction.followup.send("這個頻道不能讀取指定訊息。", ephemeral=True)
        return

    start_id = parse_message_id(start_message)
    end_id = parse_message_id(end_message)
    if not start_id or not end_id:
        await interaction.followup.send(
            "請貼開始和結束訊息的 ID 或訊息連結。",
            ephemeral=True,
        )
        return

    try:
        start = await interaction.channel.fetch_message(start_id)
        end = await interaction.channel.fetch_message(end_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        await interaction.followup.send(
            "讀不到其中一則訊息。請確認訊息在同一個頻道，且 bot 有讀取訊息歷史權限。",
            ephemeral=True,
        )
        return

    messages = await messages_between(interaction.channel, start, end)
    comment = format_comment_messages(messages)
    if not comment:
        await interaction.followup.send("這個區間沒有可保存的文字或附件。", ephemeral=True)
        return

    updated = db.append_comment(
        restaurant_id=target.id,
        comment=comment,
        created_by=interaction.user.display_name,
    )
    await interaction.followup.send(
        "已追加評論到這間餐廳。",
        embed=restaurant_embed(updated) if updated else None,
        ephemeral=False,
    )


async def export_map_csv(interaction: discord.Interaction) -> None:
    """把餐廳資料匯出成 CSV，作為 My Maps 的手動備援方案。"""

    restaurants = db.all()
    if not restaurants:
        await interaction.response.send_message("目前還沒有餐廳可以匯出。", ephemeral=True)
        return

    text_file = io.StringIO()
    writer = csv.writer(text_file)
    writer.writerow(
        [
            "name",
            "category",
            "area",
            "image_url",
            "google_maps_url",
            "tabelog_url",
            "comments",
            "keywords",
        ]
    )
    for restaurant in restaurants:
        writer.writerow(
            [
                restaurant.name,
                restaurant.category,
                restaurant.area or "",
                restaurant.image_url or "",
                restaurant.google_maps_url or "",
                restaurant.tabelog_url or "",
                restaurant.comments,
                ", ".join(restaurant.keywords),
            ]
        )

    csv_bytes = text_file.getvalue().encode("utf-8-sig")
    file = discord.File(io.BytesIO(csv_bytes), filename="restaurants_for_google_maps.csv")
    await interaction.response.send_message(
        "已匯出 CSV。可以匯入 Google My Maps，或放到 Google Sheets 當共享清單。",
        file=file,
        ephemeral=True,
    )


async def sync_google_sheet(interaction: discord.Interaction) -> None:
    """slash command：同步 SQLite 餐廳資料到 Google Sheet。"""

    await interaction.response.defer(thinking=True, ephemeral=True)
    await sync_google_sheet_to_discord(interaction)


async def backup_db(interaction: discord.Interaction) -> None:
    """把目前 SQLite 資料庫備份成檔案傳回 Discord。

    這個指令適合定期把 VM 上的主資料庫下載回本機保存。
    使用 SQLite backup API，而不是直接複製檔案，避免 bot 正在寫入時拿到不完整檔案。
    """

    await interaction.response.defer(thinking=True, ephemeral=True)
    if not DB_PATH.exists():
        await interaction.followup.send("找不到資料庫檔案，還沒有可備份的資料。", ephemeral=True)
        return

    backup_path = create_database_backup()
    filename = f"restaurants_backup_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S')}.sqlite3"
    try:
        await interaction.followup.send(
            "這是目前餐廳資料庫備份檔。",
            file=discord.File(str(backup_path), filename=filename),
            ephemeral=True,
        )
    finally:
        backup_path.unlink(missing_ok=True)


@app_commands.describe(limit="這次最多補幾間，建議 5。最大 20")
async def enrich_prices(interaction: discord.Interaction, limit: int = 5) -> None:
    """從已保存的食べログ URL 補抓價格資訊。"""

    await interaction.response.defer(thinking=True, ephemeral=True)
    result = enrich_prices_data(limit)
    await interaction.followup.send(result, ephemeral=True)


async def enrich_prices_from_message(message: discord.Message, keyword: str) -> None:
    """@bot 補價格：用一般訊息觸發價格補抓。

    Slash command 可能被 Discord 快取拖延，一般訊息入口可以立刻使用。
    """

    limit = parse_first_int(keyword) or 5
    status_message = await message.reply("正在補抓食べログ價格...", mention_author=False)
    result = enrich_prices_data(limit)
    await status_message.edit(content=result[:1900])


def enrich_prices_data(limit: int = 5) -> str:
    """補抓食べログ價格並回傳摘要文字。"""

    safe_limit = min(max(limit, 1), 20)
    restaurants = db.restaurants_missing_prices(limit=safe_limit)
    if not restaurants:
        return "目前沒有需要補價格的餐廳，或餐廳沒有食べログ網址。"

    updated = []
    not_found = []
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

    lines = [
        f"這次檢查 {len(restaurants)} 間。",
        f"成功更新：{len(updated)}",
        f"找不到價格：{len(not_found)}",
    ]
    if updated:
        lines.append("\n更新成功：")
        lines.extend(updated[:10])
    if not_found:
        lines.append("\n找不到價格：")
        lines.extend(not_found[:10])
    return "\n".join(lines)


async def sync_google_sheet_from_message(message: discord.Message) -> None:
    """@bot 更新地圖：從一般訊息觸發 Google Sheet 同步。"""

    status_message = await message.reply("正在同步 Google Sheet...", mention_author=False)
    result = sync_google_sheet_data()
    await status_message.edit(content=result)


async def send_map_url(message: discord.Message) -> None:
    """@bot 地圖：回覆 My Maps 分享網址。"""

    if not GOOGLE_MY_MAPS_URL:
        await message.reply(
            "還沒有設定 My Maps 網址。請先在 .env 加上 GOOGLE_MY_MAPS_URL。",
            mention_author=False,
        )
        return
    await message.reply(
        f"餐廳地圖在這裡：\n{GOOGLE_MY_MAPS_URL}",
        mention_author=False,
    )


async def web(interaction: discord.Interaction) -> None:
    """slash command：顯示公開餐廳網頁。"""

    if not PUBLIC_WEB_URL:
        await interaction.response.send_message(
            "還沒有設定公開網頁網址。請先在 .env 加上 PUBLIC_WEB_URL。",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        f"餐廳公開網頁在這裡：\n{PUBLIC_WEB_URL}",
        ephemeral=False,
    )


async def send_public_web_url(message: discord.Message) -> None:
    """@bot 網頁：回覆公開餐廳網頁。"""

    if not PUBLIC_WEB_URL:
        await message.reply(
            "還沒有設定公開網頁網址。請先在 .env 加上 PUBLIC_WEB_URL。",
            mention_author=False,
        )
        return
    await message.reply(
        f"餐廳公開網頁在這裡：\n{PUBLIC_WEB_URL}",
        mention_author=False,
    )


async def sync_google_sheet_to_discord(interaction: discord.Interaction) -> None:
    """slash command 的同步回覆包裝。"""

    result = sync_google_sheet_data()
    await interaction.followup.send(result, ephemeral=False)


def sync_google_sheet_data() -> str:
    """實際執行 Google Sheet 同步，並把結果整理成可回覆 Discord 的文字。"""

    if not GOOGLE_SHEETS_ID:
        return "尚未設定 GOOGLE_SHEETS_ID。請先在 .env 填入 Google Sheet ID。"
    if not GOOGLE_SERVICE_ACCOUNT_FILE or not GOOGLE_SERVICE_ACCOUNT_FILE.exists():
        return "找不到 Google service account JSON。請確認 .env 的 GOOGLE_SERVICE_ACCOUNT_FILE。"

    restaurants = db.all()
    if not restaurants:
        return "目前沒有餐廳可以同步。"

    try:
        count = sync_restaurants_to_sheet(
            restaurants=restaurants,
            spreadsheet_id=GOOGLE_SHEETS_ID,
            credentials_path=GOOGLE_SERVICE_ACCOUNT_FILE,
            worksheet_name=GOOGLE_SHEETS_WORKSHEET,
        )
    except HttpError as exc:
        status = getattr(exc, "status_code", None) or getattr(getattr(exc, "resp", None), "status", None)
        print(f"sync_google_sheet failed: {exc}")
        if status == 404:
            return (
                "同步 Google Sheet 失敗：找不到這張 Sheet，或 service account 沒有存取權。"
                "請確認 Sheet ID 正確，並把 service account 加到 Sheet 共用名單且設為編輯者。"
            )
        if status == 403:
            return (
                "同步 Google Sheet 失敗：權限不足或 API 未啟用。"
                "請確認 Google Sheets API 已啟用，且 service account 是 Sheet 編輯者。"
            )
        return f"同步 Google Sheet 失敗：Google API 回傳 HTTP {status or '錯誤'}。"
    except Exception as exc:
        print(f"sync_google_sheet failed: {exc}")
        return "同步 Google Sheet 失敗。請確認 Sheet ID、service account 權限與 JSON 檔。"

    return f"已同步 {count} 筆餐廳到 Google Sheet。My Maps 讀取這張表後會看到最新資料。"


def create_database_backup() -> Path:
    """建立 SQLite 備份檔並回傳暫存路徑。"""

    fd, temp_name = tempfile.mkstemp(prefix="restaurants_backup_", suffix=".sqlite3")
    os.close(fd)
    backup_path = Path(temp_name)
    source = sqlite3.connect(DB_PATH)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return backup_path


def restaurant_embed(restaurant: Restaurant) -> discord.Embed:
    """把 Restaurant 轉成 Discord embed 卡片。"""

    embed = discord.Embed(
        title=restaurant.name,
        description=restaurant.comments,
        color=discord.Color.green(),
    )
    embed.add_field(name="ID", value=str(restaurant.id), inline=True)
    embed.add_field(name="分類", value=restaurant.category, inline=True)
    if restaurant.area:
        embed.add_field(name="地區", value=restaurant.area, inline=True)
    if restaurant.tabelog_url:
        embed.add_field(name="食べログ", value=restaurant.tabelog_url, inline=False)
    if restaurant.google_maps_url:
        embed.add_field(name="Google Maps", value=restaurant.google_maps_url, inline=False)
    thumbnail_url = discord_embed_image_url(restaurant.image_url)
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    price_lines = []
    if restaurant.lunch_budget_text:
        price_lines.append(f"午餐：{restaurant.lunch_budget_text}")
    if restaurant.dinner_budget_text:
        price_lines.append(f"晚餐：{restaurant.dinner_budget_text}")
    if price_lines:
        embed.add_field(name="價格", value="\n".join(price_lines), inline=False)
    if restaurant.keywords:
        embed.add_field(name="關鍵字", value=", ".join(restaurant.keywords), inline=False)
    if restaurant.tags:
        embed.add_field(name="Tags", value=", ".join(restaurant.tags), inline=False)
    return embed


def discord_embed_image_url(image_url: str | None) -> str | None:
    """Return a fully qualified image URL acceptable for Discord embeds."""

    if not image_url:
        return None
    url = image_url.strip()
    if url.startswith(("http://", "https://")):
        return url
    if not url.startswith("/") or not PUBLIC_WEB_URL:
        return None
    return f"{PUBLIC_WEB_URL.rstrip('/')}{url}"


def unique_areas(restaurants: list[Restaurant]) -> list[str]:
    """從餐廳列表中取出不重複地區。"""

    return sorted({(restaurant.area or "未分類地區").strip() for restaurant in restaurants})


def resolve_restaurant(value: str) -> Restaurant | str:
    """把使用者輸入的餐廳 ID 或關鍵字解析成 Restaurant。"""

    text = value.strip()
    if text.isdigit():
        restaurant = db.get(int(text))
        return restaurant or f"找不到 ID {text} 的餐廳。"

    matches = db.search(text)
    if not matches:
        return f"找不到「{text}」相關餐廳。"
    if len(matches) == 1:
        return matches[0]

    choices = "\n".join(f"ID {r.id}: {r.name}（{r.category} {r.area or ''}）" for r in matches[:10])
    return f"「{text}」找到多筆，請用餐廳 ID 再試一次：\n{choices}"


def parse_message_id(value: str) -> int | None:
    """從 Discord 訊息 ID 或訊息連結中抓出 message id。"""

    matches = MESSAGE_ID_RE.findall(value)
    return int(matches[-1]) if matches else None


def parse_keywords(value: str | None) -> list[str]:
    """把逗號、日文頓號、換行分隔的關鍵字轉成 list。"""

    if not value:
        return []
    text = value.replace("、", ",").replace("\n", ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def is_enrich_prices_message(keyword: str) -> bool:
    """判斷 @bot 訊息是不是補抓價格。"""

    normalized = keyword.strip().lower().replace("　", " ")
    normalized = re.sub(r"[：:，,。.!！?？]+", " ", normalized).strip()
    return normalized.startswith(("補價格", "補抓價格", "更新價格", "enrich_prices", "enrich prices"))


def parse_first_int(value: str) -> int | None:
    """從文字中抓第一個整數，例如「補價格 5」會得到 5。"""

    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


def cleanup_search_keyword(keyword: str) -> str:
    """把自然語句清成適合搜尋 DB 的關鍵字。

    例如：
    - 新宿 拉麵推薦 -> 新宿 拉麵
    - 渋谷想吃家系有推薦嗎 -> 渋谷 家系
    """

    text = keyword.strip()
    replacements = [
        "有推薦嗎",
        "推薦一下",
        "推薦",
        "想吃",
        "我想吃",
        "現在人在",
        "人在",
        "附近",
        "嗎",
        "?",
        "？",
    ]
    for old in replacements:
        text = text.replace(old, " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def messages_between(
    channel: discord.abc.Messageable,
    first: discord.Message,
    second: discord.Message,
) -> list[discord.Message]:
    """讀取兩則訊息之間的 Discord 訊息。"""

    older, newer = sorted([first, second], key=lambda msg: msg.created_at)
    messages = [older]
    async for msg in channel.history(
        limit=50,
        after=older.created_at,
        before=newer.created_at,
        oldest_first=True,
    ):
        messages.append(msg)
    if newer.id != older.id:
        messages.append(newer)
    return [msg for msg in messages if not msg.author.bot]


def format_comment_messages(messages: list[discord.Message]) -> str:
    """把多則 Discord 訊息整理成適合存入 comments 欄位的文字。"""

    lines: list[str] = []
    for msg in messages:
        parts = []
        if msg.content.strip():
            parts.append(msg.content.strip())
        if msg.attachments:
            parts.extend(attachment.url for attachment in msg.attachments)
        if parts:
            lines.append(f"{msg.author.display_name}: {' '.join(parts)}")
    return "\n".join(lines)


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)

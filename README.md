# Discord 餐廳記憶 Bot

這是一個 Discord bot 雛形，用來把大家在頻道裡貼的餐廳照片、食べログ網址、聊天評價整理成可查詢的餐廳資料庫。

## 功能

- 對指定訊息按右鍵或長按，選「應用程式」→「保存餐廳資訊」：只讀取那一則訊息，判斷餐廳名稱、分類、地區、大家的評論。
- 如果指定訊息是回覆別人的訊息，bot 也會一起讀取被回覆的原訊息。
- 只要能找到餐廳名稱或食べログ資訊，就會存進 SQLite；評論可以之後手動追加。
- 對指定評論按右鍵或長按，選「應用程式」→「保存為餐廳評論」：選擇餐廳後，直接把那一則訊息追加成評論。
- `/add_comment`：指定餐廳與開始/結束訊息連結，把那段 Discord 原文追加成餐廳評論。
- `/find_restaurant 拉麵`：用關鍵字查詢，出現餐廳選單。
- 選餐廳後會顯示食べログ網址、Google Maps 連結、當時 Discord 的評論。
- `/export_map_csv`：匯出 CSV，可匯入 Google My Maps 或 Google Sheets。
- `/list_restaurant`：列出目前已儲存的餐廳與ID
 

## 設定

1. 複製 `.env.example` 成 `.env`
2. 填入：

```env
DISCORD_TOKEN=你的 Discord bot token
OPENAI_API_KEY=你的 OpenAI API key
OPENAI_MODEL=gpt-4.1-mini
DB_PATH=restaurants.sqlite3
GOOGLE_SHEETS_ID=你的 Google Sheet ID
GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json
GOOGLE_SHEETS_WORKSHEET=restaurants
GOOGLE_MY_MAPS_URL=你的 Google My Maps 分享網址
```

3. 安裝套件：

```bash
pip install -r requirements.txt
```

4. 啟動：

```bash
python bot.py
```

## Discord 權限

Discord Developer Portal 裡需要開：

- Message Content Intent
- bot 權限：Read Message History、Send Messages、Use Slash Commands、Embed Links

## 手動追加評論

1. 對開始訊息按右鍵，選「複製訊息連結」。
2. 對結束訊息按右鍵，選「複製訊息連結」。
3. 在 Discord 輸入：

```text
/add_comment restaurant:餐廳ID或關鍵字 start_message:開始訊息連結 end_message:結束訊息連結
```

如果關鍵字找到多間餐廳，bot 會列出餐廳 ID。下一次把 `restaurant` 改成那個 ID 即可。

## 關於共享地圖

目前每筆資料會自動產生 Google Maps 搜尋連結，也可以用 `/export_map_csv` 匯出後匯入 Google My Maps。真正直接寫入 Google My Maps 沒有穩定官方 API，建議下一步做其中一種：

- 匯出 CSV，再匯入 Google My Maps。
- 寫入 Google Sheets，讓 My Maps 讀取同一份表格。
- 改用自己架一個 Leaflet/Mapbox 小網頁地圖。

這版先把資料結構留好，之後可以再加 Google Sheets 同步。

## Google Sheets 同步

建議用 Google Sheets 當 My Maps 的資料來源。bot 收到 `/sync_google_sheet` 後會把目前 SQLite 裡的餐廳整張同步到 Sheet。

1. 在 Google Cloud 建立 service account，下載 JSON 金鑰。
2. 把 JSON 放到專案資料夾，例如 `service-account.json`。
3. 建立一張 Google Sheet，複製網址中的 Sheet ID。
4. 把 service account 的 email 加到那張 Google Sheet 的共用名單，權限設為編輯者。
5. 在 `.env` 填入：

```env
GOOGLE_SHEETS_ID=你的 Google Sheet ID
GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json
GOOGLE_SHEETS_WORKSHEET=restaurants
```

6. Discord 輸入 `/sync_google_sheet`。
7. 到 Google My Maps 匯入該 Google Sheet，選 `name`、`area` 或 `google_maps_url` 作為定位資料。

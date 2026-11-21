# Discord 餐廳記憶 Bot

這是一個 Discord bot 雛形，用來把大家在頻道裡貼的餐廳照片、食べログ網址、聊天評價整理成可查詢的餐廳資料庫。

## 功能

- 對指定訊息按右鍵或長按，選「應用程式」→「保存餐廳資訊」：只讀取那一則訊息，判斷餐廳名稱、分類、地區、大家的評論。
- 如果指定訊息是回覆別人的訊息，bot 也會一起讀取被回覆的原訊息。
- 只要能找到餐廳名稱或食べログ資訊，就會存進 SQLite；評論可以之後手動追加。
- 對指定評論按右鍵或長按，選「應用程式」→「保存為餐廳評論」：選擇餐廳後，直接把那一則訊息追加成評論。
- `/add_comment`：指定餐廳與開始/結束訊息連結，把那段 Discord 原文追加成餐廳評論。
- `/edit_restaurant`：用餐廳 ID 編輯店名、分類、地區、連結、評論、關鍵字。
- `/delete_restaurant`：用餐廳 ID 刪除錯誤餐廳，必須把 `confirm` 設成 `True` 才會刪除。
- `/backup_db`：把目前 VM 上的餐廳資料庫備份成 SQLite 檔案傳回 Discord。
- `/enrich_prices`：從既有食べログ網址慢慢補抓午餐/晚餐價格。
- `/find_restaurant 拉麵`：用關鍵字查詢，出現餐廳選單。
- 選餐廳後會顯示食べログ網址、Google Maps 連結、當時 Discord 的評論。
- `/export_map_csv`：匯出 CSV，可匯入 Google My Maps 或 Google Sheets。
- `/list_restaurants`：列出目前已儲存的餐廳與 ID
- 如果訊息裡是 Google Maps 連結，bot 會嘗試從網址解析店名，再反查食べログ店家網址。
- `@bot 網頁` 或 `/web`：顯示公開餐廳網頁網址。
 

## 設定

1. 複製 `.env.example` 成 `.env`
2. 填入：

```env
DISCORD_TOKEN=你的 Discord bot token
OPENAI_API_KEY=你的 OpenAI API key
OPENAI_MODEL=gpt-6-luna
OPENAI_REASONING_EFFORT=low
DB_PATH=restaurants.sqlite3
GOOGLE_SHEETS_ID=你的 Google Sheet ID
GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json
GOOGLE_SHEETS_WORKSHEET=restaurants
GOOGLE_MY_MAPS_URL=你的 Google My Maps 分享網址
ADMIN_PASSWORD=管理後台密碼
SESSION_SECRET=自行產生並保存的隨機金鑰
PUBLIC_WEB_URL=https://discord-bottaberogudb.org/
PUBLIC_MAP_URL=https://discord-bottaberogudb.org/map
AUTO_SAVE_RESTAURANT_LINKS=true
ALLOWED_TEXT_CHANNEL_NAMES=食べログ
ALLOWED_TEXT_CHANNEL_IDS=
```

預設模型為 `gpt-6-luna`，繼續使用 Chat Completions。`OPENAI_REASONING_EFFORT` 未設定、留白或無效時，預設使用 `low`；無效值會記錄警告，但不輸出原值。可選 `none`、`low`、`medium`、`high`、`xhigh`、`max`，`none` 仍可明確指定。使用推理時會省略 temperature；模型拒絕選用參數時，保留逐項移除、重試與每模型快取機制。輸出額度包含推理 token，若出現空回覆請增加 `max_completion_tokens`。上線前請以代表性請求評估品質、延遲與成本。

`ALLOWED_TEXT_CHANNEL_NAMES` 會限制 bot 只在指定文字頻道讀取一般訊息、記憶聊天、自動保存餐廳，以及右鍵保存餐廳/評論。預設只在 `#食べログ` 生效。若想用頻道 ID 控制，可以填 `ALLOWED_TEXT_CHANNEL_IDS`，多個值用逗號分隔。

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

`/edit_restaurant`、`/delete_restaurant`、`/sync_google_sheet`、`/backup_db` 僅能在伺服器內使用，執行者必須有「管理伺服器」（Manage Server / `manage_guild`）或「管理員」權限。除了 Discord 的預設指令權限外，程式也會在執行時檢查。餐廳指令仍受 `ALLOWED_TEXT_CHANNEL_NAMES` / `ALLOWED_TEXT_CHANNEL_IDS` 的頻道限制。

## 手動追加評論

1. 對開始訊息按右鍵，選「複製訊息連結」。
2. 對結束訊息按右鍵，選「複製訊息連結」。
3. 在 Discord 輸入：

```text
/add_comment restaurant:餐廳ID或關鍵字 start_message:開始訊息連結 end_message:結束訊息連結
```

如果關鍵字找到多間餐廳，bot 會列出餐廳 ID。下一次把 `restaurant` 改成那個 ID 即可。

## 編輯與刪除餐廳

編輯餐廳時，只需要填想修改的欄位，留空的欄位會保持原本資料。

```text
/edit_restaurant restaurant_id:1 area:秋葉原 category:燒肉
```

刪除餐廳時，為了避免手滑，必須把 `confirm` 設成 `True`：

```text
/delete_restaurant restaurant_id:1 confirm:True
```

## 備份資料庫

VM 上的 `restaurants.sqlite3` 是 bot 真正在使用的主資料庫。可以定期在 Discord 執行：

```text
/backup_db
```

bot 會把目前資料庫備份成 `.sqlite3` 檔案傳給執行指令的人。建議重要更新後下載保存一份。

## 補抓食べログ價格

如果餐廳已經有食べログ網址，可以慢慢把午餐/晚餐價格補進 DB：

```text
/enrich_prices limit:5
```

建議一次補 5 間左右，避免一次對食べログ發太多請求。之後保存新餐廳時，如果有食べログ網址，bot 也會嘗試自動抓價格。

## 補抓食べログ營業時間

如果餐廳已經有食べログ網址，可以在管理後台按「補抓營業時間」慢慢把營業時間補進 DB。

建議一次補 5 間左右。食べログ頁面可能改版或擋請求，所以抓不到時不會覆蓋既有資料，可以之後再試或從管理後台手動填入。

## Google Maps 反查食べログ

保存餐廳資訊時，如果訊息裡沒有食べログ連結、但有 Google Maps 連結，bot 會嘗試：

1. 從 Google Maps URL 解析店名。
2. 用店名與地區搜尋食べログ。
3. 找到像店家頁的結果後，存入 `tabelog_url`。

這不是 Google 或食べログ官方 API，所以可能會有找不到或找到錯誤候選的情況。找不到時仍會保存餐廳基本資料，之後可以用 `/edit_restaurant` 或管理後台補上正確食べログ網址。

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

## 管理後台

`admin_app.py` 會讀取同一份 `restaurants.sqlite3`，並提供兩個入口：

- `/`：公開只讀頁，朋友可以查看和搜尋餐廳，但不能修改資料。
- `/admin`：管理後台，需要 `ADMIN_PASSWORD` 才能編輯、刪除、匯入、同步。

公開清單與地圖不提供後台連結，管理者請自行開啟 `/admin`。未登入時只會收到登入頁，登入成功後才提供完整編輯介面。密碼不儲存在瀏覽器；後台改用有效期 12 小時的 HMAC-SHA256 簽章 cookie（`HttpOnly`、`SameSite=Strict`），並提供「登出」按鈕。HTTPS（含本機反向代理的 `X-Forwarded-Proto: https`）會設定 `Secure`。舊的 `X-Admin-Password` header 已不支援。

正式環境請設定固定且保密的 `SESSION_SECRET`，例如執行 `python -c "import secrets; print(secrets.token_hex(32))"` 產生。未設定時會警告並為每個程序產生暫時金鑰，重啟後登入失效；多個 worker 必須共用同一個金鑰。登出會立即清除瀏覽器 cookie；輪替 `SESSION_SECRET` 或 `ADMIN_PASSWORD` 會使所有先前簽發的 session cookie 失效。

登入失敗達 5 次會鎖定該 IP 15 分鐘；成功登入會清除該 IP 的失敗紀錄。只有來自本機反向代理的 `X-Forwarded-For` 才會用最後一個位址判定 IP。失敗紀錄保存在各程序記憶體；目前的單一 worker 部署適用，多 worker 部署應另設共用限流。API 文件路由 `/docs`、`/redoc`、`/openapi.json` 已停用。公開 API 不回傳 Discord 來源頻道、來源訊息或建立者欄位；推薦者仍公開顯示。

可以做的事：

- 查看所有餐廳
- 搜尋餐廳
- 用地區和分類篩選
- 編輯店名、分類、地區、連結、關鍵字、評論
- 手動編輯午餐/晚餐價格與價格範圍
- 刪除錯誤餐廳
- 追加評論
- 從 Google Sheet 匯入餐廳到 SQLite
- 同步 Google Sheet

本機啟動：

Windows 可以直接雙擊：

```text
start_admin.bat
```

它會開啟後台服務並自動打開瀏覽器。使用後台時請保持那個黑色視窗開著，關掉視窗後後台就會停止。

或手動輸入：

```bash
uvicorn admin_app:app --host 127.0.0.1 --port 8000
```

然後打開：

```text
http://127.0.0.1:8000/admin
```

公開只讀頁：

```text
http://127.0.0.1:8000
```

VM 正式部署必須透過 Caddy 提供 HTTPS，admin service 維持監聽 `127.0.0.1:8000`，不要對外開放 TCP 8000。朋友使用 https://discord-bottaberogudb.org/ 與 https://discord-bottaberogudb.org/map；管理者使用 https://discord-bottaberogudb.org/admin。以上本機網址僅供開發測試。

新 VM 的完整復原步驟見 [GCP VM 部署手冊](deploy/README_GCP_VM.md)，包含固定 IP、Cloudflare DNS、swap、Windows 資料搬移、systemd 與 Caddy。**只做 git clone 不會還原 `.env`、`service-account.json`、`restaurants.sqlite3`、`uploads/`**，必須另行搬回這些資料。更新時執行 git pull 後，必須重新啟動 `discord-tabelog-bot` 與 `discord-tabelog-admin`，讓分離的模板與靜態資源一起更新；不要覆寫上述非 git 資料。

注意同步方向：

- 「從 Google Sheet 匯入」：Google Sheet → SQLite，適合你在 Sheet 手動新增很多資料後使用。
- 「DB 同步到 Sheet」：SQLite → Google Sheet，會用目前資料庫內容覆蓋 Sheet。

# GCP VM 復原與部署手冊（Ubuntu 24.04 LTS Minimal）

本手冊針對**已存在的新 VM**；舊 VM 與資料已刪除，不需再建立 VM。以下是交給操作員依序執行的步驟，不代表 IP、DNS 或部署已完成。

| 項目 | 現況／目標 |
| --- | --- |
| Project | lilith（`lilith-496215`） |
| VM / zone | `tabelog-bot` / `us-west1-b`（region `us-west1`） |
| 規格 | e2-micro，1 GB RAM，Ubuntu 24.04 LTS Minimal x86/64，30 GB standard persistent disk |
| 目前外部 IPv4 | `8.235.87.216`，仍為 ephemeral；須保留成 `tabelog-ip` |
| 網域 | `discord-bottaberogudb.org`，DNS 已由 Cloudflare 管理 |
| 公開路徑 | Internet → Caddy TCP 80/443 → `127.0.0.1:8000` → admin app |

除標示 Cloud Shell 或 Windows PowerShell 外，`bash` 指令皆在操作員自己的 VM SSH 終端執行；互動提示須填入實際使用者、路徑或已發布版本。不要把 token、密碼、私鑰貼到工單、聊天或 git。

## 1. 先固定 IP、修正 DNS 與檢查防火牆

1. 在 Google Cloud Console 選 `lilith-496215` → VPC network → IP addresses，找到已連到 `tabelog-bot` 的 **8.235.87.216**，將 ephemeral 改為 static，命名 **tabelog-ip**。保留目前 IP，不要另外分配一個新地址。也可在已登入且有權限的 **Cloud Shell（Bash）** 執行以下指令；Console 與 CLI 擇一：

   ```bash
   gcloud compute addresses create tabelog-ip --project=lilith-496215 --region=us-west1 --addresses=8.235.87.216
   gcloud compute addresses describe tabelog-ip --project=lilith-496215 --region=us-west1 --format='yaml(address,status,users)'
   ```

   確認 address 是 `8.235.87.216`、status 是 `IN_USE`，users 指向既有 VM。已保留時只執行 describe，不要重建。操作依據：[Google 官方保留／提升 IP 文件](https://docs.cloud.google.com/vpc/docs/reserve-static-external-ip-address#promote_ephemeral_ip)。

   費用提醒：將使用中的 ephemeral 提升為 static 不增加兩者之間的費率差，但不能保證「VM 運行時 static 免費」；現行標準 VM 的使用中外部 IPv4 仍按時計費，優惠／抵免以帳單為準，見 [Google 官方 IP 定價](https://cloud.google.com/vpc/network-pricing#ipaddress)。

2. 在 Cloudflare 更新 apex（`@`）A record：舊 IP `35.252.238.61`（已廢棄，不可使用）改為 `8.235.87.216`。本手冊預設僅使用 apex；若有使用 `www`，同步更新其 A record，或讓 CNAME 指向 apex，並依第 8 節加入 Caddyfile。移除指向舊主機的衝突 A／AAAA 記錄。
3. 初次簽發與驗證時，將相關記錄設為 **DNS only（灰雲）**，等 DNS 傳播與 Caddy 憑證成功後再考慮代理。若之後啟用 Cloudflare 代理，SSL/TLS 使用 **Full (strict)**，避免 Flexible 導致 HTTP 迴圈；代理開啟後公開 DNS 會顯示 Cloudflare IP，應在控制台確認 origin A record。
4. HTTP / HTTPS ingress tags 已允許，重建 VPC 時也已允許 SSH。仍須核對 VM tags 與實際生效規則：對外僅允許 TCP **22 / 80 / 443**（22 儘量限操作員來源）；**不要開放 TCP 8000，也不要放行所有 TCP**。若有 UFW 等主機防火牆，同樣只放行這三個埠，先確保 SSH 不會被阻擋。後端 8000 僅用於 loopback。

## 2. Ubuntu Minimal 套件與時區

Minimal 不保證有編輯器、GPG、DNS 查詢或編譯工具；`build-essential`、`python3-dev` 供缺少 wheel 時編譯 Python 相依套件，`tzdata` 提供時區資料。

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip python3-dev build-essential curl ca-certificates gnupg nano openssh-client dnsutils iproute2 util-linux tzdata
sudo timedatectl set-timezone Asia/Tokyo
timedatectl
date --iso-8601=seconds
```

確認時區為 `Asia/Tokyo`，時間同步正常；這讓 journal 顯示與備份檔名的本機時間符合操作員的 JST。

## 3. 先配置磁碟 swap（1 GB RAM 必做）

e2-micro 只有 1 GB RAM；安裝相依套件與同時執行 Caddy、bot、admin 容易 OOM。使用 **2G swap file**（磁碟不足時可用 1G），不以 zram 作為唯一方案。先看可用空間與既有 swap：

```bash
df -h /
free -h
sudo swapon --show
```

若已存在足夠 swap，跳過建立段落。以下只適用於尚無 `/swapfile`、且剩餘磁碟足夠時；使用子 shell，失敗即停止，不覆寫既有檔案或重複追加 fstab。

```bash
(
    set -eu
    if sudo test -e /swapfile || sudo test -L /swapfile; then
        echo '/swapfile 已存在，請先檢查；本段未修改它。' >&2
        exit 1
    fi
    sudo install -m 600 /dev/null /swapfile
    # fallocate 不支援時，以 dd 配置實際磁碟區塊；1G 可改成 1G / count=1024。
    sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    if ! grep -Eq '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab; then
        printf '/swapfile none swap sw 0 0\n' | sudo tee -a /etc/fstab
    fi
)
sudo swapon --show
free -h
```

確認約 2 GiB Swap 且 `/etc/fstab` 有一筆對應記錄；swap 可降低 OOM 機率，但不等於增加實體 RAM。

## 4. 系統使用者、clone 與 venv

`discordbot` 不提供互動登入；以目前有 sudo 的操作員安裝。使用已審核、包含本輪部署檔案的遠端分支或 tag；本輪工作尚未 commit / push 時，遠端不會自動包含這些修改。

```bash
id discordbot >/dev/null 2>&1 || sudo useradd --system --create-home --shell /usr/sbin/nologin discordbot
sudo install -d -o discordbot -g discordbot -m 755 /opt/discord-tabelog-bot
read -r -p '已發布的分支或 tag：' DeployRef
sudo -u discordbot git clone --branch "$DeployRef" https://github.com/ThomasChung0708/discord-bot-taberogu.git /opt/discord-tabelog-bot
cd /opt/discord-tabelog-bot
sudo -u discordbot python3 -m venv .venv
sudo -u discordbot .venv/bin/python -m pip install --upgrade pip
sudo -u discordbot .venv/bin/python -m pip install -r requirements.txt
```

Ubuntu 24.04 的系統 Python 由 apt 管理，請只在 venv 安裝專案依賴，不用 `sudo pip` 或 `--break-system-packages`。若 clone 目錄已有內容，先確認狀態，勿用強制清除來解決。

## 5. **Clone alone is not enough：只 clone 不會還原資料**

**舊 VM 已消失，無法再從舊 VM 取回資料。git 只提供程式與靜態資源；下列四項必須從 Windows PC 原專案搬回，確認完成前不要啟動服務。** 來源位於 `D:\Projects\ChatGPT\discord bot …`；名稱尾段未確認，請在下方提示輸入電腦上實際完整路徑，不要把省略號當作資料夾名稱。

| 必搬資料 | 用途與完整性 |
| --- | --- |
| `.env` | Discord / OpenAI / Sheets 等既有設定及密鑰；禁止 commit，搬完需按第 6 節調整 VM 設定。 |
| `service-account.json` | Google Sheets service account 私鑰，缺少會無法同步；禁止 commit。 |
| `restaurants.sqlite3` | 真正的餐廳、評論與圖片路徑資料；若有 `restaurants.sqlite3-wal` / `restaurants.sqlite3-shm` 也一併搬移。 |
| `uploads/` | 約 53 張餐廳圖片、約 40 MB；保留子目錄與檔名（例如 `restaurants/`），不要變成 `uploads/uploads/`。 |

### 5a. Windows PowerShell 上傳範例（由操作員執行）

先停止 Windows 上的 bot **與 admin 及所有其他 SQLite 寫入程序**，直到整份資料傳輸完成；不要在仍寫入時分別拷貝 DB 和 WAL。若不能停機，先用 SQLite backup API 產生一致的獨立備份，將該備份以 `restaurants.sqlite3` 名稱上傳，不能混入原 DB 的 sidecar。以下範例採停機後連同現存 sidecar 搬移。

Windows 需安裝 OpenSSH Client，使用已設好登入金鑰的操作員帳號（不是 `discordbot`）。先由 GCP Console 核對新 VM SSH host key；不要關閉 host key 驗證。這些是日後操作員執行的傳輸範例，本輪文件工作不執行 SSH 或 scp。

```powershell
$Source = Read-Host 'Windows 原專案完整路徑（D:\Projects\ChatGPT\discord bot 開頭）'
$VmUser = Read-Host '可登入新 VM 且有 sudo 的 Linux 使用者'
$Destination = "${VmUser}@8.235.87.216"
$DataFiles = @('.env', 'service-account.json', 'restaurants.sqlite3') | ForEach-Object {
    $File = Join-Path $Source $_
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { throw "缺少必要檔案：$_" }
    $File
}
$UploadDir = Join-Path $Source 'uploads'
if (-not (Test-Path -LiteralPath $UploadDir -PathType Container)) { throw '缺少 uploads 資料夾' }
foreach ($Sidecar in @('restaurants.sqlite3-wal', 'restaurants.sqlite3-shm')) {
    $File = Join-Path $Source $Sidecar
    if (Test-Path -LiteralPath $File -PathType Leaf) { $DataFiles += $File }
}
ssh $Destination 'install -d -m 700 "$HOME/tabelog-restore"'
if ($LASTEXITCODE -ne 0) { throw '建立受保護暫存目錄失敗' }
scp @DataFiles "${Destination}:tabelog-restore/"
if ($LASTEXITCODE -ne 0) { throw '資料檔傳輸失敗' }
scp -r $UploadDir "${Destination}:tabelog-restore/"
if ($LASTEXITCODE -ne 0) { throw '圖片傳輸失敗' }
Get-ChildItem -LiteralPath $UploadDir -Recurse -File | Measure-Object -Property Length -Sum
```

### 5b. VM 上安置資料與收緊權限

用同一位操作員登入，確認四項皆在自己的 `~/tabelog-restore/` 中。這是全新安裝流程；若服務已啟動，先停止 bot、admin 與 backup timer，並等備份工作結束，勿覆寫運行中的 DB。

```bash
(
    set -eu
    RestoreDir="$HOME/tabelog-restore"
    AppDir=/opt/discord-tabelog-bot
    for File in .env service-account.json restaurants.sqlite3; do
        sudo install -o discordbot -g discordbot -m 600 "$RestoreDir/$File" "$AppDir/$File"
    done
    for File in restaurants.sqlite3-wal restaurants.sqlite3-shm; do
        if test -f "$RestoreDir/$File"; then
            sudo install -o discordbot -g discordbot -m 600 "$RestoreDir/$File" "$AppDir/$File"
        fi
    done
    sudo install -d -o discordbot -g discordbot -m 700 "$AppDir/uploads" "$AppDir/backups"
    sudo cp -a "$RestoreDir/uploads/." "$AppDir/uploads/"
    sudo chown -R discordbot:discordbot "$AppDir/uploads"
    sudo chmod -R u+rwX,go-rwx "$AppDir/uploads"
    sudo stat -c '%U:%G %a %n' "$AppDir/.env" "$AppDir/service-account.json" "$AppDir/restaurants.sqlite3"
    sudo find "$AppDir/uploads" -type f | wc -l
    sudo du -sh "$AppDir/uploads"
)
```

核對 Windows 與 VM 的圖片數量、大小及目錄結構；約 53 張／40 MB 僅為預期量級。`.env`、JSON 私鑰、DB 的 owner 應是 `discordbot:discordbot`、mode `600`，不要用 `chmod 644` 或 `777` 處理密鑰。Caddy 透過 app 取得圖片，不需要直接讀取這些檔案。驗證完成後妥善清除操作員暫存副本；保留 Windows 原件與另存的安全備份。

## 6. 在 VM 編輯 .env（不要輸出真實值）

以版本中的 [`.env.example`](../.env.example) 核對既有設定，不要用範例覆蓋已搬來的 `.env`，也不要用 `cat .env` 分享設定。透過編輯器修改：

```bash
cd /opt/discord-tabelog-bot
sudo -u discordbot nano .env
```

VM 必須有以下設定；表內密鑰文字只是用途說明，不是可以直接使用的值。

| 設定 | VM 應使用的內容與原因 |
| --- | --- |
| `SESSION_SECRET` | 首次設定時由操作員在私人終端執行下方產生器，填入產生的值並持續保存。重啟／更新勿重生；輪替會讓既有 session 失效。 |
| `ADMIN_PASSWORD` | 使用密碼管理器產生的長隨機密碼，建議至少 24 字元；不要用短至 9 字元的密碼。 |
| `OPENAI_MODEL=gpt-6-luna` | 與本專案指定模型一致。 |
| `OPENAI_REASONING_EFFORT=low` | 本專案預設的推理設定；未設定或留白亦使用 `low`，仍可明確指定 `none`。 |
| `PUBLIC_WEB_URL=https://discord-bottaberogudb.org/` | Discord `/web` 與網頁分享入口。 |
| `PUBLIC_MAP_URL=https://discord-bottaberogudb.org/map` | 公開地圖入口。 |

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

以上產生器只交由操作員執行，輸出不可貼到 git 或聊天。其他 keys 依 `.env.example` 保留／填妥既有值；檢查 Windows 絕對路徑是否需改成 VM 路徑，例如 `DB_PATH=restaurants.sqlite3`、`GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json`。不要同時啟動 Windows 與 VM 的同一個 Discord bot。

## 7. 安裝 bot、admin、backup 的 systemd units

admin 單元**已經**使用 `--host 127.0.0.1 --port 8000`，不需改成對外監聽。bot 和 admin 由程式載入工作目錄的 `.env`；備份單元透過 `EnvironmentFile` 載入同一份設定。

```bash
cd /opt/discord-tabelog-bot
sudo install -m 644 deploy/systemd/discord-tabelog-bot.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/discord-tabelog-admin.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/discord-tabelog-backup.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/discord-tabelog-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now discord-tabelog-bot.service discord-tabelog-admin.service
sudo systemctl start discord-tabelog-backup.service
sudo journalctl -u discord-tabelog-backup.service -n 40 --no-pager
sudo systemctl enable --now discord-tabelog-backup.timer
```

備份使用 SQLite backup API 產生一致副本，預設保留最近 14 份於 `/opt/discord-tabelog-bot/backups/`。它只備份 DB，**不包含圖片、.env 或私鑰**；定期將 DB 與其他資料安全備份到 VM 以外，避免刪 VM 再次失去全部資料。

timer 使用 `OnCalendar=*-*-* 04:30:00 Asia/Tokyo` 並保留 `Persistent=true`，即使系統時區仍是 UTC，也會每天 **04:30 JST** 執行（UTC 前一天 19:30）；停機錯過後可能於開機時補跑。確認下一次排程：

```bash
systemd-analyze calendar '*-*-* 04:30:00 Asia/Tokyo'
systemctl list-timers --all discord-tabelog-backup.timer
systemctl show discord-tabelog-backup.timer -p NextElapseUSecRealtime -p LastTriggerUSec
```

## 8. 必須透過 Caddy 提供公開 HTTPS

截至本次核對，[Caddy 官方 Ubuntu apt 安裝文件](https://caddyserver.com/docs/install#debian-ubuntu-raspbian)仍以 **Cloudsmith stable repository** 為安裝方式。第 2 節已安裝 `gnupg` / `ca-certificates`；以下讀取權限只套用公開套件金鑰與來源清單，不能套用 `.env` 或 service-account 私鑰。

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

套件會建立並啟動 caddy systemd service。專案提供 [deploy/Caddyfile](Caddyfile)：

```caddyfile
discord-bottaberogudb.org {
    reverse_proxy 127.0.0.1:8000
}
```

確認 DNS 已指到新 IP，80/443 可抵達後安裝、驗證並重載設定；Caddy 自動取得、續期憑證並將 HTTP 轉 HTTPS，不需另裝 certbot。

```bash
cd /opt/discord-tabelog-bot
sudo install -m 644 deploy/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
sudo journalctl -u caddy -n 60 --no-pager
```

若真的使用 `www`，先完成它的 DNS，再將站台第一行改成 `discord-bottaberogudb.org, www.discord-bottaberogudb.org {`，重新 validate / reload；未使用時不必增加。

[Caddy 的預設代理標頭](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#defaults)會設定 `X-Forwarded-Proto`；不要移除它。app 只在 **loopback peer** 才信任 forwarded proto，HTTPS 登入 cookie 因此可設為 Secure。保留單元中的 loopback 綁定與代理信任範圍，不要將信任範圍擴到所有來源。

通過下一節的 HTTPS 驗證後，核對第 6 節的 `PUBLIC_WEB_URL` / `PUBLIC_MAP_URL`，若剛修改需重啟 bot 與 admin，讓 Discord 分享連結更新：

```bash
sudo systemctl restart discord-tabelog-bot.service discord-tabelog-admin.service
```

對外僅使用 80/443。朋友分享 **https://discord-bottaberogudb.org/**、地圖 **https://discord-bottaberogudb.org/map**；管理者自行開 **https://discord-bottaberogudb.org/admin**。不要分享 `:8000` 入口。

## 9. 上線驗證清單

### 服務、journal 與排程

```bash
sudo systemctl status discord-tabelog-bot.service discord-tabelog-admin.service discord-tabelog-backup.service discord-tabelog-backup.timer caddy.service --no-pager
sudo journalctl -u discord-tabelog-bot.service -u discord-tabelog-admin.service -u discord-tabelog-backup.service -u caddy.service -n 100 --no-pager
sudo ss -lntp
systemctl list-timers --all discord-tabelog-backup.timer
sudo ls -lh /opt/discord-tabelog-bot/backups
```

- bot、admin、caddy 應為 active (running)，timer 為 active (waiting)。backup.service 是 oneshot，成功完成後 `inactive (dead)` / `status=0/SUCCESS` 正常，不會持續 running。
- `ss` 應看到 admin 只在 `127.0.0.1:8000`；Caddy 的公開監聽為 80/443。將 SSH 與應用監聽對照第 1 節 GCP／主機防火牆，確認 8000 無對外規則。
- 備份應產生非空 `.sqlite3`，NEXT 是 04:30 JST；日誌不應有讀取檔案權限或 OOM 問題。

### VM 本機與正式 HTTPS 分別測試

以下 loopback curl **只在 VM 內診斷**；localhost 成功而 HTTPS 失敗時，檢查 DNS、Caddy journal 與 80/443，不要開放 8000。

```bash
curl --fail --silent --show-error -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
curl --fail --silent --show-error -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/admin
dig +short A discord-bottaberogudb.org
curl --fail --silent --show-error -o /dev/null -w '%{http_code}\n' https://discord-bottaberogudb.org/
curl --fail --silent --show-error -o /dev/null -w '%{http_code}\n' https://discord-bottaberogudb.org/admin
curl --fail --silent --show-error -o /dev/null -w '%{http_code}\n' https://discord-bottaberogudb.org/map
curl --fail --silent --show-error -o /dev/null -w '%{http_code}\n' https://discord-bottaberogudb.org/static/css/tokens.css
curl --fail --silent --show-error -o /dev/null -w '%{http_code}\n' https://discord-bottaberogudb.org/static/js/public.js
```

- DNS only 時 A 應回 `8.235.87.216`；用外部瀏覽器再測一次上述 HTTPS 頁面，憑證有效且無混合內容錯誤，勿用 `curl -k` 掩蓋 TLS 問題。
- 用無痕視窗開 `/admin`，應出現登入頁，未登入不能編輯；使用長密碼登入後核對 cookie 的 Secure / HttpOnly。不要把密碼放進 curl 命令或 shell history。
- 首頁與 `/map` 能顯示既有餐廳，CSS、JS、favicon 正常載入。點開餐廳核對圖片；在瀏覽器 Network 確認實際 `/uploads/restaurants/...` 圖片 URL 回 200，不以 `/uploads/` 目錄本身是否列出內容判斷。
- Discord `/web` 顯示 HTTPS 網域，既有餐廳數量與 Windows 原資料一致。若有缺檔，先停止服務釐清，不要用空 DB 取代來源。

## 10. 日後 git pull 與更新

先確保已有 VM 外的安全資料備份。`.env`、`service-account.json`、`restaurants.sqlite3`（含存在的 WAL/SHM）與 `uploads/` 是運行資料，**不在 git 版本內容中，絕不可覆寫**；不要使用 `git clean -fdx`、重建目錄或重新複製 `.env.example` 來更新。

```bash
cd /opt/discord-tabelog-bot
sudo -u discordbot git status --short
sudo -u discordbot git pull --ff-only
sudo -u discordbot .venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart discord-tabelog-bot.service discord-tabelog-admin.service
sudo systemctl status discord-tabelog-bot.service discord-tabelog-admin.service --no-pager
```

**每次 pull 後都重新啟動 bot 與 admin**；模板／靜態檔案已分離，僅重啟 bot 不足以讓 admin 完整載入更新。若 unit 檔有更新，重做第 7 節 install / daemon-reload，並執行 `sudo systemctl restart discord-tabelog-backup.timer` 套用新排程；若 Caddyfile 有更新，重做第 8 節 install / validate / reload。最後重跑第 9 節檢查，包含 NEXT、登入頁、圖片與靜態資源。

# GCP VM 部署筆記

這個 bot 適合用 Ubuntu VM + systemd 24 小時執行。

## 1. VM 上安裝套件

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

## 2. 建立執行使用者與專案資料夾

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin discordbot
sudo mkdir -p /opt/discord-tabelog-bot
sudo chown -R discordbot:discordbot /opt/discord-tabelog-bot
```

## 3. 下載程式

```bash
sudo -u discordbot git clone YOUR_GITHUB_REPO_URL /opt/discord-tabelog-bot
cd /opt/discord-tabelog-bot
sudo -u discordbot python3 -m venv .venv
sudo -u discordbot .venv/bin/pip install -r requirements.txt
```

## 4. 放入機密檔

在 VM 的 `/opt/discord-tabelog-bot/.env` 放入正式設定。

也把 Google service account JSON 放到：

```text
/opt/discord-tabelog-bot/service-account.json
```

這兩個檔案不要 commit 到 GitHub。

## 5. 安裝 systemd service

```bash
sudo cp deploy/systemd/discord-tabelog-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable discord-tabelog-bot
sudo systemctl start discord-tabelog-bot
```

## 6. 查看狀態與 log

```bash
sudo systemctl status discord-tabelog-bot
sudo journalctl -u discord-tabelog-bot -f
```

## 更新程式

```bash
cd /opt/discord-tabelog-bot
sudo -u discordbot git pull
sudo -u discordbot .venv/bin/pip install -r requirements.txt
sudo systemctl restart discord-tabelog-bot
```

## 讓朋友查看餐廳資料

`admin_app.py` 有兩個頁面：

- 公開只讀頁：`http://VM外部IP:8000/`
- 管理後台：`http://VM外部IP:8000/admin`

公開頁可以給朋友查看、搜尋、篩選餐廳。管理後台需要 `.env` 的 `ADMIN_PASSWORD` 才能編輯、刪除、匯入或同步資料。

### 1. 在 `.env` 加管理密碼

```bash
cd /opt/discord-tabelog-bot
sudo nano .env
```

加入：

```env
ADMIN_PASSWORD=你自己的管理密碼
```

### 2. 安裝 web 後台服務

```bash
cd /opt/discord-tabelog-bot
sudo cp deploy/systemd/discord-tabelog-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable discord-tabelog-admin
sudo systemctl start discord-tabelog-admin
```

查看狀態：

```bash
sudo systemctl status discord-tabelog-admin
sudo journalctl -u discord-tabelog-admin -f
```

### 3. 開啟 GCP 防火牆

到 Google Cloud Console：

1. VPC 網路
2. 防火牆
3. 建立防火牆規則
4. 方向：Ingress
5. 目標：你的 VM，或全部 VM
6. 來源 IPv4 範圍：`0.0.0.0/0`
7. TCP：`8000`

開好後，朋友可以打開：

```text
http://VM外部IP:8000/
```

如果你之前的 VM 外部 IP 沒變，就是：

```text
http://35.252.238.61:8000/
```

注意：這是 HTTP，不是 HTTPS。公開查看頁可以先這樣測試；如果要長期公開，建議下一步加 Nginx + HTTPS。

## HTTPS 公開方式

正式 HTTPS 建議準備一個網域，例如：

```text
food.example.com
```

把網域的 DNS A record 指到 VM 外部 IP：

```text
35.252.238.61
```

接著可以用 Caddy 自動申請 HTTPS 憑證。

### 1. 安裝 Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

### 2. 讓 admin web 只聽本機

如果使用 Caddy 對外公開，建議把 `/etc/systemd/system/discord-tabelog-admin.service` 裡的：

```text
--host 0.0.0.0 --port 8000
```

改成：

```text
--host 127.0.0.1 --port 8000
```

然後：

```bash
sudo systemctl daemon-reload
sudo systemctl restart discord-tabelog-admin
```

### 3. 設定 Caddyfile

```bash
sudo nano /etc/caddy/Caddyfile
```

範例：

```text
food.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

重啟：

```bash
sudo systemctl reload caddy
```

之後公開頁會變成：

```text
https://food.example.com/
```

管理頁：

```text
https://food.example.com/admin
```

最後記得把 `.env` 改成：

```env
PUBLIC_WEB_URL=https://food.example.com/
```

再重啟 bot：

```bash
sudo systemctl restart discord-tabelog-bot
```

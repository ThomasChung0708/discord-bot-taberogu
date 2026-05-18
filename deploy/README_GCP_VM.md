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

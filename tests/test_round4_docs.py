"""Offline deployment-document regressions; never read live data or contact a VM."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "discord-bottaberogudb.org"
NEW_IP = "8.235.87.216"
OLD_IP = "35.252.238.61"
DOCUMENTS = ("deploy/README_GCP_VM.md", "README.md", ".env.example", "deploy/Caddyfile")


def deployment_issues(source):
    """Allow loopback diagnostics and explicit prohibitions, reject live exposure."""
    issues = []
    # Normalize continued shell commands so firewall rules cannot hide a port
    # on the following line. This only inspects text, never executes commands.
    source = re.sub(r"(?<!`)[\\`]\r?\n\s*", " ", source)
    in_code = False
    for number, raw in enumerate(source.splitlines(), 1):
        if raw.lstrip().startswith("```"):
            in_code = not in_code
            continue
        line = raw.replace("`", "").replace("**", "")
        if OLD_IP in line:
            migration_note = (
                not in_code
                and line.count(OLD_IP) == 1
                and f"舊 IP {OLD_IP}（已廢棄，不可使用）改為 {NEW_IP}" in line
            )
            if not migration_note:
                issues.append(f"line {number}: obsolete live address")

        without_loopback_urls = re.sub(
            r"https?://127\.0\.0\.1:8000\b", "LOOPBACK", line, flags=re.IGNORECASE,
        )
        if re.search(r"https?://[^\s<>\"']*:8000\b", without_loopback_urls, re.IGNORECASE):
            issues.append(f"line {number}: public port-8000 URL")

        # Mentions of Caddy's internal destination are not public exposure.
        line = re.sub(r"(?:https?://)?127\.0\.0\.1:8000\b", "LOOPBACK", line)
        if not re.search(r"\b8000\b", line):
            continue
        # Prohibitions are prose only. An executable firewall command must fail
        # even if somebody places a reassuring comment on the same line.
        if re.search(
            r"\bufw\s+(?:allow|insert)\b.*\b8000\b"
            r"|\bfirewall-rules\s+(?:create|update)\b.*(?:--allow|--rules).*\b8000\b"
            r"|\b(?:iptables|nft)\b.*(?:dport|dports).*\b8000\b.*\b(?:ACCEPT|accept)\b",
            line, re.IGNORECASE,
        ):
            issues.append(f"line {number}: firewall command exposes 8000")
        if re.search(r"--host\s+(?:0\.0\.0\.0|\[?::\]?)\s+.*--port\s+8000\b", line):
            issues.append(f"line {number}: wildcard backend bind")
        for sentence in re.split(r"[。；;]|(?<=[.!?])\s+", line):
            if not re.search(r"\b8000\b", sentence):
                continue
            prohibited = re.search(
                r"不要|不得|不可|禁止|勿|不應|never|do not|don't|must not", sentence, re.IGNORECASE,
            )
            if not in_code and prohibited:
                continue
            if re.search(
                r"(?:開放|放行|分享|公開|open|allow|share|expose)\s*[^。；;]{0,40}\b8000\b"
                r"|\b8000\b[^。；;]{0,40}(?:對外開放|公開網址|public URL)"
                r"|TCP\s*[:：]\s*8000\b",
                sentence, re.IGNORECASE,
            ):
                issues.append(f"line {number}: public/firewall port-8000 instruction")
    return issues


class Round4DocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = {name: (ROOT / name).read_text(encoding="utf-8") for name in DOCUMENTS}
        cls.playbook = cls.documents["deploy/README_GCP_VM.md"]

    def test_no_live_old_ip_or_public_backend_in_any_deployment_document(self):
        for name, source in self.documents.items():
            with self.subTest(document=name):
                self.assertEqual(deployment_issues(source), [])

    def test_guard_rejects_unsafe_instructions(self):
        examples = (
            f"DNS A record: {OLD_IP}",
            f"```text\n{OLD_IP}\n```",
            f"Share http://{NEW_IP}:8000/ with friends.",
            "http://VM外部IP:8000/admin",
            "PUBLIC_WEB_URL=http://your_vm_external_ip:8000/",
            "https://discord-bottaberogudb.org:8000/",
            "sudo ufw allow 8000/tcp # do not expose other ports",
            "gcloud compute firewall-rules create admin " + "\\\n" + "    --allow=tcp:8000",
            "sudo iptables -A INPUT -p tcp --dport 8000 -j ACCEPT",
            "開放 GCP 防火牆 TCP 8000",
            "TCP：8000",
            "Open port 8000 to the Internet.",
            "uvicorn admin_app:app --host 0.0.0.0 --port 8000",
        )
        for source in examples:
            with self.subTest(source=source):
                self.assertTrue(deployment_issues(source))

    def test_guard_allows_local_development_and_migration_warning(self):
        examples = (
            "curl http://127.0.0.1:8000/",
            "reverse_proxy 127.0.0.1:8000",
            "uvicorn admin_app:app --host 127.0.0.1 --port 8000",
            "不要開放 TCP 8000，也不要分享 :8000 入口。",
            f"舊 IP `{OLD_IP}`（已廢棄，不可使用）改為 `{NEW_IP}`。",
        )
        for source in examples:
            with self.subTest(source=source):
                self.assertEqual(deployment_issues(source), [])

    def test_existing_vm_and_network_preconditions_are_documented(self):
        for required in (
            NEW_IP, DOMAIN, "lilith-496215", "tabelog-bot", "us-west1-b",
            "e2-micro", "Ubuntu 24.04 LTS Minimal", "tabelog-ip", "ephemeral",
            "Cloudflare", "22 / 80 / 443", "www", "DNS only",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.playbook)

    def test_minimal_packages_user_venv_and_timezone(self):
        for required in (
            "git", "python3", "python3-venv", "python3-pip", "curl", "ca-certificates", "gnupg",
            "sudo timedatectl set-timezone Asia/Tokyo", "discordbot", "/opt/discord-tabelog-bot",
            "git clone", "python3 -m venv", "pip install -r requirements.txt",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.playbook)

    def test_data_restore_checklist_and_permissions(self):
        for required in (
            "Clone alone is not enough", "Windows", "PowerShell", r"D:\Projects\ChatGPT\discord bot",
            ".env", "service-account.json", "restaurants.sqlite3", "uploads/",
            "restaurants.sqlite3-wal", "restaurants.sqlite3-shm", "SQLite backup API",
            "先停止 Windows", "scp", "53", "40 MB", "discordbot:discordbot", "-m 600",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.playbook)

    def test_production_environment_settings(self):
        for required in (
            "SESSION_SECRET", "ADMIN_PASSWORD", "OPENAI_MODEL=gpt-6-luna",
            "OPENAI_REASONING_EFFORT=low", f"PUBLIC_WEB_URL=https://{DOMAIN}/",
            f"PUBLIC_MAP_URL=https://{DOMAIN}/map",
        ):
            for name in ("deploy/README_GCP_VM.md", ".env.example"):
                with self.subTest(document=name, required=required):
                    self.assertIn(required, self.documents[name])
        self.assertIn('python3 -c "import secrets; print(secrets.token_hex(32))"', self.playbook)
        self.assertIn("重啟／更新勿重生", self.playbook)
        self.assertIn("至少 24 字元", self.playbook)
        self.assertIn(".env.example", self.playbook)

    def test_disk_swap_instructions(self):
        for required in ("swap", "2G", "fallocate", "dd if=/dev/zero", "chmod 600 /swapfile",
                         "mkswap", "swapon", "/etc/fstab", "test -e /swapfile"):
            with self.subTest(required=required):
                self.assertIn(required, self.playbook)

    def test_units_and_explicit_jst_backup_schedule(self):
        timer = (ROOT / "deploy/systemd/discord-tabelog-backup.timer").read_text(encoding="utf-8")
        self.assertRegex(timer, r"(?m)^OnCalendar=\*-\*-\* 04:30:00 Asia/Tokyo$")
        self.assertRegex(timer, r"(?m)^Persistent=true$")
        admin = (ROOT / "deploy/systemd/discord-tabelog-admin.service").read_text(encoding="utf-8")
        self.assertIn("--host 127.0.0.1 --port 8000", admin)
        self.assertNotIn("0.0.0.0", admin)
        for unit in ("bot.service", "admin.service", "backup.service", "backup.timer"):
            self.assertIn(f"discord-tabelog-{unit}", self.playbook)
        self.assertIn("systemd-analyze calendar", self.playbook)
        self.assertIn("systemctl list-timers", self.playbook)

    def test_caddy_and_verification(self):
        caddyfile = self.documents["deploy/Caddyfile"]
        self.assertRegex(caddyfile, rf"(?m)^{re.escape(DOMAIN)}\s*\{{$")
        self.assertIn("reverse_proxy 127.0.0.1:8000", caddyfile)
        for required in (
            "Caddy", "https://caddyserver.com/docs/install", "Cloudsmith", "apt install -y caddy",
            "caddy validate", "systemctl reload caddy", "X-Forwarded-Proto", "loopback peer",
            "systemctl status", "journalctl", "curl", "http://127.0.0.1:8000/",
            f"https://{DOMAIN}/", f"https://{DOMAIN}/admin", f"https://{DOMAIN}/map",
            "/static/", "/uploads/restaurants/", "登入頁", "oneshot",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.playbook)

    def test_updates_restart_both_processes_and_preserve_data(self):
        restart = "systemctl restart discord-tabelog-bot.service discord-tabelog-admin.service"
        self.assertIn(restart, self.playbook.split("## 10.", 1)[1])
        self.assertIn("git pull --ff-only", self.playbook)
        self.assertIn("絕不可覆寫", self.playbook)
        readme = self.documents["README.md"]
        self.assertIn("deploy/README_GCP_VM.md", readme)
        self.assertIn("discord-tabelog-bot", readme)
        self.assertIn("discord-tabelog-admin", readme)
        self.assertIn("http://127.0.0.1:8000/admin", readme)


if __name__ == "__main__":
    unittest.main()

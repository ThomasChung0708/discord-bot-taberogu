"""Round 1 regressions; isolated local DBs, no Discord/network connections."""

import asyncio
import importlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from db import RestaurantDB
from extractor import ExtractionResult, MessageSnippet
from url_utils import validate_url


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {"created_by", "source_channel_id", "source_message_id"}


class Round1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.env = patch.dict(os.environ, {
            "DB_PATH": str(cls.root / "startup.sqlite3"),
            "UPLOAD_DIR": str(cls.root / "uploads"),
            "BACKUP_DIR": str(cls.root / "backups"),
            "ADMIN_PASSWORD": "test-管理密碼",
            "SESSION_SECRET": "test-secret-" * 8,
            "DISCORD_TOKEN": "offline-test-token",
            "OPENAI_API_KEY": "offline-test-key",
        })
        cls.env.start()
        cls.dotenv = patch("dotenv.load_dotenv")
        cls.dotenv.start()
        # Imports register commands but do not call client.run or setup_hook.
        cls.previous_modules = {name: sys.modules.pop(name, None) for name in ("bot", "admin_app")}
        cls.admin = importlib.import_module("admin_app")
        cls.bot = importlib.import_module("bot")

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.bot.client.close())
        for name, module in cls.previous_modules.items():
            sys.modules.pop(name, None)
            if module is not None:
                sys.modules[name] = module
        cls.env.stop()
        cls.dotenv.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.case_dir = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.case_dir.cleanup)
        self.path = Path(self.case_dir.name)
        self.db = RestaurantDB(str(self.path / "test.sqlite3"))
        self.images = self.path / "restaurants"
        self.images.mkdir()
        for name, value in {"db": self.db, "DB_PATH": self.db.path,
                            "RESTAURANT_IMAGE_DIR": self.images,
                            "BACKUP_DIR": self.path / "backups"}.items():
            mocked = patch.object(self.admin, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.admin.login_attempts.clear()
        self.client = TestClient(self.admin.app)
        self.addCleanup(self.client.close)
        self.id = self.add_restaurant()

    def add_restaurant(self, **overrides):
        values = dict(name="測試餐廳", category="拉麵", area="新宿",
                      tabelog_url="https://tabelog.com/example", google_maps_url="https://maps.google.com/",
                      comments="好吃", keywords=["濃厚"], source_channel_id=123,
                      source_message_id=456, created_by="private-user", recommended_by="推薦者")
        values.update(overrides)
        return self.db.add_restaurant(**values)

    def login(self, **kwargs):
        response = self.client.post("/api/admin/login", json={"password": self.admin.ADMIN_PASSWORD}, **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def assert_public(self, value):
        if isinstance(value, dict):
            self.assertFalse(PRIVATE.intersection(value))
            for child in value.values():
                self.assert_public(child)
        elif isinstance(value, list):
            for child in value:
                self.assert_public(child)

    def test_commands_registered(self):
        expected = {"list_restaurants", "edit_restaurant", "delete_restaurant", "add_comment",
                    "export_map_csv", "sync_google_sheet", "backup_db", "save_restaurant",
                    "find_restaurant", "recommend", "recommend_restaurant", "recommed", "reset",
                    "enrich_prices", "map", "web", "保存餐廳資訊", "保存為餐廳評論"}
        commands = self.bot.client.tree.get_commands()
        self.assertEqual({command.name for command in commands}, expected)
        self.assertLess(len(commands), 100)
        for name in ("delete_restaurant", "edit_restaurant", "sync_google_sheet", "backup_db"):
            command = self.bot.client.tree.get_command(name)
            self.assertTrue(command.guild_only)
            self.assertTrue(command.default_permissions.manage_guild)

    def test_privileged_commands_runtime_permissions(self):
        for name in ("delete_restaurant", "edit_restaurant", "sync_google_sheet", "backup_db"):
            for guild, permissions in [(None, SimpleNamespace(manage_guild=True, administrator=True)),
                                       (object(), SimpleNamespace(manage_guild=False, administrator=False)),
                                       (object(), None)]:
                interaction = SimpleNamespace(guild=guild, user=SimpleNamespace(guild_permissions=permissions),
                                              response=SimpleNamespace(send_message=AsyncMock()))
                args = {"restaurant_id": self.id} if name in {"edit_restaurant", "delete_restaurant"} else {}
                asyncio.run(self.bot.client.tree.get_command(name).callback(interaction, **args))
                interaction.response.send_message.assert_awaited_once()
                self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])
        for permissions in [SimpleNamespace(manage_guild=True, administrator=False),
                            SimpleNamespace(manage_guild=False, administrator=True)]:
            interaction = SimpleNamespace(guild=object(), user=SimpleNamespace(guild_permissions=permissions))
            self.assertFalse(asyncio.run(self.bot.reject_non_admin_interaction(interaction)))

    def test_discord_edit_rejects_unsafe_urls(self):
        interaction = SimpleNamespace(guild=object(), user=SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_guild=True, administrator=False)),
            response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(self.bot, "reject_disallowed_interaction_channel", AsyncMock(return_value=False)):
            for field in ("tabelog_url", "google_maps_url"):
                interaction.response.send_message.reset_mock()
                asyncio.run(self.bot.edit_restaurant.callback(interaction, self.id, **{field: "javascript:alert(1)"}))
                self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])

    def test_discord_edit_preserves_omitted_comments_and_accepts_exact_text(self):
        interaction = SimpleNamespace(guild=object(), user=SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_guild=True, administrator=False)),
            response=SimpleNamespace(send_message=AsyncMock()))
        original = self.db.get(self.id).comments
        original_items = self.db.comments_for(self.id)
        with patch.object(self.bot, "db", self.db), patch.object(
                self.bot, "reject_disallowed_interaction_channel", AsyncMock(return_value=False)):
            for value in (None, '  更新原文\n\n  ', ''):
                with self.subTest(comments=value):
                    asyncio.run(self.bot.edit_restaurant.callback(
                        interaction, self.id, name='修改後餐廳', comments=value))
                    self.assertEqual(self.db.get(self.id).comments, original if value is None else value)
                    self.assertEqual(self.db.comments_for(self.id), original_items)

    def test_docs_and_public_navigation(self):
        for url in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(url).status_code, 404)
        for url in ("/", "/map"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotRegex(response.text, r'href\s*=\s*[\"\x27]/admin')

    def test_login_editor_logout_and_header_rejection(self):
        response = self.client.get("/admin")
        self.assertIn('id="loginForm"', response.text)
        self.assertNotIn('id="editorForm"', response.text)
        self.assertNotIn("editorPayload", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/api/admin/status").json(),
                         {"password_configured": True, "authenticated": False})
        self.assertEqual(self.client.get("/api/admin/restaurants", headers={
            "X-Admin-Password": self.admin.ADMIN_PASSWORD.encode().hex()}).status_code, 401)
        # Test an exact ASCII password header as well, avoiding HTTP header encoding restrictions.
        with patch.object(self.admin, "ADMIN_PASSWORD", "test-password"):
            self.assertEqual(self.client.post("/api/admin/check", headers={
                "X-Admin-Password": "test-password"}).status_code, 401)
        cookie = self.login().headers["set-cookie"]
        for flag in ("HttpOnly", "SameSite=strict", "Path=/", "Max-Age=43200"):
            self.assertIn(flag, cookie)
        self.assertTrue(self.client.get("/api/admin/status").json()["authenticated"])
        editor = self.client.get("/admin").text
        self.assertIn('id="editorForm"', editor)
        self.assertIn('id="logoutButton"', editor)
        self.assertNotIn("X-Admin-Password", editor)
        self.assertNotIn("tabelogAdminPassword", editor)
        script = self.client.get("/static/js/admin.js")
        self.assertEqual(script.status_code, 200)
        self.assertNotIn("X-Admin-Password", script.text)
        self.assertNotIn("tabelogAdminPassword", script.text)
        self.assertIn("/api/admin/restaurants", script.text)
        self.assertEqual(self.client.post("/api/admin/logout").status_code, 200)
        self.assertFalse(self.client.get("/api/admin/status").json()["authenticated"])
        self.assertIn('id="loginForm"', self.client.get("/admin").text)

    def test_session_tampering_expiry_secure_and_missing_password(self):
        self.assertNotIn("Secure", self.login(headers={"X-Forwarded-Proto": "https"}).headers["set-cookie"])
        self.client.cookies.clear()
        for token in ("bad", "1.x.bad", "9" * 5000 + ".x.bad"):
            self.client.cookies.set(self.admin.SESSION_COOKIE, token)
            self.assertEqual(self.client.get("/api/admin/restaurants").status_code, 401)
        expires = str(int(time.time()) - 1)
        payload = f"{expires}.{'a' * 64}"
        self.client.cookies.set(self.admin.SESSION_COOKIE, payload + "." + self.admin.session_signature(payload))
        self.assertEqual(self.client.get("/api/admin/restaurants").status_code, 401)
        self.client.cookies.clear()
        self.login()
        token = self.client.cookies.get(self.admin.SESSION_COOKIE)
        self.client.cookies.clear()
        self.client.cookies.set(self.admin.SESSION_COOKIE, token[:-1] + ("0" if token[-1] != "0" else "1"))
        self.assertEqual(self.client.get("/api/admin/restaurants").status_code, 401)
        with patch.object(self.admin, "ADMIN_PASSWORD", ""):
            self.assertEqual(self.client.get("/api/admin/restaurants").status_code, 403)
            self.assertEqual(self.client.post("/api/admin/login", json={"password": ""}).status_code, 403)

    def test_login_rate_limit_reset_and_proxy_ip(self):
        for _ in range(4):
            self.assertEqual(self.client.post("/api/admin/login", json={"password": "bad"}).status_code, 401)
        self.login()
        self.assertEqual(self.admin.login_attempts, {})
        for _ in range(5):
            response = self.client.post("/api/admin/login", json={"password": "bad"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "900")
        self.assertEqual(self.client.post("/api/admin/login", json={"password": self.admin.ADMIN_PASSWORD}).status_code, 429)
        with TestClient(self.admin.app, client=("127.0.0.1", 1234)) as other:
            headers = {"X-Forwarded-For": "192.0.2.1, 192.0.2.2"}
            for _ in range(5):
                response = other.post("/api/admin/login", json={"password": "bad"}, headers=headers)
            self.assertEqual(response.status_code, 429)
            self.assertIn("192.0.2.2", self.admin.login_attempts)
            self.assertEqual(other.post("/api/admin/login", json={"password": self.admin.ADMIN_PASSWORD},
                                        headers={"X-Forwarded-For": "192.0.2.3"}).status_code, 200)
        future = time.monotonic() + self.admin.LOGIN_WINDOW + 1
        with patch.object(self.admin.time, "monotonic", return_value=future):
            self.login()

    def test_public_data_and_authenticated_admin_fields(self):
        for url in ("/api/restaurants", f"/api/restaurants/{self.id}", f"/api/restaurants/{self.id}/comments"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assert_public(response.json())
        self.assertEqual(self.client.get(f"/api/restaurants/{self.id}").json()["recommended_by"], "推薦者")
        for url in ("/api/admin/restaurants", f"/api/admin/restaurants/{self.id}"):
            self.assertEqual(self.client.get(url).status_code, 401)
        self.login()
        rows = self.client.get("/api/admin/restaurants").json()["restaurants"]
        detail = self.client.get(f"/api/admin/restaurants/{self.id}").json()
        for row in (rows[0], detail):
            self.assertTrue(PRIVATE.issubset(row))
            self.assertIn("created_by", row["comment_items"][0])
            self.assertIn("source_message_id", row["comment_items"][0])
        response = self.client.post(f"/api/restaurants/{self.id}/comments", json={"comment": "更多評論"})
        self.assertTrue(PRIVATE.issubset(response.json()))

    def test_url_validation_at_all_write_boundaries(self):
        self.login()
        for value in ("javascript:alert(1)", "data:text/html,x", "vbscript:x", "file:///tmp/x", "//evil.test",
                      "https://", "https://:80", "https://example.com:bad", "https://good.test\\@evil.test",
                      'https://<invalid>.test', "https://%0a.invalid"):
            for field in ("tabelog_url", "google_maps_url", "image_url"):
                with self.subTest(value=value, field=field):
                    with self.assertRaises(ValueError):
                        self.add_restaurant(**{field: value})
                    with self.assertRaises(ValueError):
                        self.db.import_restaurant(restaurant_id=self.id, name="x", category="x", area=None,
                                                  tabelog_url=value if field == "tabelog_url" else None,
                                                  google_maps_url=value if field == "google_maps_url" else None,
                                                  image_url=value if field == "image_url" else None, comments="", keywords=[])
                    payload = dict(name="x", category="x", tabelog_url=None, google_maps_url=None,
                                   image_url=None, comments="", keywords=[])
                    payload[field] = value
                    with self.assertRaises(ValueError):
                        self.db.update_restaurant(restaurant_id=self.id, area=None, **payload)
                    self.assertEqual(self.client.put(f"/api/restaurants/{self.id}", json=payload).status_code, 422)
            with self.assertRaises(ValueError):
                self.db.update_image_url(self.id, value)
        for url in ("http://example.com/a", "https://example.com/a", "/uploads/restaurants/file-1.png"):
            self.assertEqual(self.db.update_image_url(self.id, url).image_url, url)
        for value in ("/uploads/restaurants/../x", "/uploads/restaurants/a\\x", "/uploads/restaurants/%2e%2e/x"):
            with self.assertRaises(ValueError):
                validate_url(value, image=True)
        self.assertIsNone(validate_url(" "))
        for url in ("https://localhost:8000/", "https://[::1]/", "https://餐廳.example/"):
            self.assertEqual(validate_url(url), url)
        response = self.client.put(f"/api/restaurants/{self.id}", json={"name": "x", "category": "x",
            "tabelog_url": "http://example.com", "google_maps_url": "https://example.com",
            "image_url": "/uploads/restaurants/safe.png"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PRIVATE.issubset(response.json()))

    def test_sheet_import_sanitizes_unsafe_urls(self):
        self.login()
        rows = [{"name": "匯入餐廳", "tabelog_url": "javascript:alert(1)",
                 "google_maps_url": "data:text/html,x", "image_url": "javascript:x"},
                {"name": "安全餐廳", "tabelog_url": "https://example.com", "google_maps_url": "http://example.com",
                 "image_url": "/uploads/restaurants/import.png"}]
        with patch.object(self.admin, "GOOGLE_SHEETS_ID", "test"), \
             patch.object(self.admin, "GOOGLE_SERVICE_ACCOUNT_FILE", Path(__file__)), \
             patch.object(self.admin, "read_restaurants_from_sheet", return_value=rows):
            response = self.client.post("/api/import-sheet")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported"], 2)
        imported = self.db.search("匯入餐廳")[0]
        self.assertIsNone(imported.tabelog_url)
        self.assertIsNone(imported.google_maps_url)
        self.assertIsNone(imported.image_url)
        self.assertEqual(self.db.search("安全餐廳")[0].image_url, rows[1]["image_url"])

    def test_image_upload_magic_limits_replacement_and_delete(self):
        url = f"/api/restaurants/{self.id}/image"
        self.assertEqual(self.client.post(url, content=b"x").status_code, 401)
        self.login()
        for data, content_type in [(b"<html>spoof</html>", "image/png"),
                                   (b"\xff\xd8\xffmore", "image/png"),
                                   (b"\x89PNG\r\n\x1a\nmore", "image/svg+xml")]:
            self.assertEqual(self.client.post(url, content=data, headers={"Content-Type": content_type}).status_code, 400)
        self.assertEqual(self.client.post(url, content=b"", headers={"Content-Type": "image/png"}).status_code, 400)
        self.assertEqual(self.client.post(url, content=b"x", headers={
            "Content-Length": str(self.admin.MAX_IMAGE_BYTES + 1)}).status_code, 413)
        self.assertEqual(self.client.post(url, content=b"x" * (self.admin.MAX_IMAGE_BYTES + 1),
                                         headers={"Content-Length": "0"}).status_code, 413)
        old = None
        for magic, mime, extension in [(b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
                                       (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
                                       (b"GIF89a", "image/gif", ".gif"),
                                       (b"RIFF1234WEBP", "application/octet-stream", ".webp")]:
            response = self.client.post(url, content=magic + b"test", headers={"Content-Type": mime})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(PRIVATE.issubset(response.json()))
            path = self.images / response.json()["image_url"].rsplit("/", 1)[-1]
            self.assertEqual(path.suffix, extension)
            self.assertTrue(path.exists())
            if old:
                self.assertFalse(old.exists())
            old = path
        self.assertEqual(self.client.delete(f"/api/restaurants/{self.id}").status_code, 200)
        self.assertFalse(old.exists())

    def test_shared_image_is_preserved_until_unreferenced(self):
        self.login()
        path = self.images / "shared.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        local_url = "/uploads/restaurants/shared.png"
        self.db.update_image_url(self.id, local_url)
        other = self.add_restaurant(name="另一餐廳", image_url=local_url)
        self.client.post(f"/api/restaurants/{self.id}/image", content=b"\xff\xd8\xff", headers={"Content-Type": "image/jpeg"})
        self.assertTrue(path.exists())
        self.client.delete(f"/api/restaurants/{other}")
        self.assertFalse(path.exists())
        # Unsafe legacy paths can never be selected for deletion.
        sentinel = self.path / "keep.png"
        sentinel.write_bytes(b"keep")
        self.admin.cleanup_local_image("/uploads/restaurants/../keep.png")
        self.assertTrue(sentinel.exists())

    def test_sqlite_pragmas_and_consistent_backups(self):
        with self.db.session() as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 15000)
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        admin_backup = self.admin.create_database_backup()
        with patch.object(self.bot, "DB_PATH", self.db.path), patch.object(tempfile, "tempdir", str(self.path)):
            bot_backup = self.bot.create_database_backup()
        for path in (admin_backup, bot_backup):
            conn = sqlite3.connect(path)
            try:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("SELECT count(*) FROM restaurants").fetchone()[0], 1)
            finally:
                conn.close()

    def test_resaving_restaurant_preserves_comments_with_foreign_keys(self):
        self.db.append_comment(restaurant_id=self.id, comment="原有評論", created_by="member")
        self.assertEqual(self.add_restaurant(comments="新評論"), self.id)
        comments = [row.comment for row in self.db.comments_for(self.id)]
        self.assertIn("原有評論", comments)
        self.assertIn("新評論", comments)


    def test_secure_cookie_only_trusts_loopback_proxy(self):
        for host in ("127.0.0.1", "::1", "192.0.2.1", "testclient"):
            with self.subTest(host=host), TestClient(self.admin.app, client=(host, 1234)) as client:
                response = client.post("/api/admin/login", json={"password": self.admin.ADMIN_PASSWORD},
                                       headers={"X-Forwarded-Proto": "https"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual("Secure" in response.headers["set-cookie"], host in ("127.0.0.1", "::1"))
        with TestClient(self.admin.app, base_url="https://testserver", client=("192.0.2.1", 1234)) as client:
            response = client.post("/api/admin/login", json={"password": self.admin.ADMIN_PASSWORD},
                                   headers={"X-Forwarded-Proto": "http"})
            self.assertIn("Secure", response.headers["set-cookie"])

    def test_discord_save_sanitizes_extracted_urls(self):
        snippets = [MessageSnippet(author="member", content="restaurant link", attachment_urls=[])]
        for mode in ("automatic", "manual"):
            for unsafe_links in (False, True):
                with self.subTest(mode=mode, unsafe_links=unsafe_links):
                    result = ExtractionResult(
                        name=f"Extracted {mode} {unsafe_links}", category="拉麵", area="新宿",
                        tabelog_url="javascript:alert(1)" if unsafe_links else "https://tabelog.com/example",
                        google_maps_url="https://" if unsafe_links else "https://maps.google.com/",
                        image_url="//bad.example/pic.jpg", comments="Extracted comment", keywords=["濃厚"], reason="",
                    )
                    with patch.object(self.bot, "db", self.db), \
                         patch.object(self.bot, "extract_restaurant", return_value=result), \
                         patch.object(self.bot, "snippets_for_target_message", AsyncMock(return_value=snippets)):
                        if mode == "automatic":
                            message = SimpleNamespace(channel=SimpleNamespace(id=123), id=456,
                                                      author=SimpleNamespace(display_name="member"), reply=AsyncMock())
                            asyncio.run(self.bot.auto_save_restaurant_from_message(message))
                            message.reply.assert_awaited_once()
                        else:
                            interaction = SimpleNamespace(channel_id=123, user=SimpleNamespace(display_name="member"),
                                                          followup=SimpleNamespace(send=AsyncMock()))
                            asyncio.run(self.bot.save_extracted_restaurant(
                                interaction=interaction, snippets=snippets, source_message_id=456))
                            interaction.followup.send.assert_awaited_once()
                    saved = self.db.search(result.name)[0]
                    self.assertIsNone(saved.image_url)
                    self.assertEqual(saved.tabelog_url, None if unsafe_links else result.tabelog_url)
                    self.assertEqual(saved.google_maps_url, None if unsafe_links else result.google_maps_url)
                    self.assertEqual(self.db.comments_for(saved.id)[0].comment, result.comments)

    def test_edit_cleans_up_only_unreferenced_old_images(self):
        self.login()
        path = self.images / "shared-edit.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        local_url = "/uploads/restaurants/shared-edit.png"
        self.db.update_image_url(self.id, local_url)
        other = self.add_restaurant(name="Shared edit", image_url=local_url)
        payload = {"name": "Edited restaurant", "category": "拉麵", "image_url": local_url}
        self.assertEqual(self.client.put(f"/api/restaurants/{self.id}", json=payload).status_code, 200)
        self.assertTrue(path.exists())
        payload["image_url"] = "https://example.com/new.jpg"
        self.assertEqual(self.client.put(f"/api/restaurants/{self.id}", json=payload).status_code, 200)
        self.assertTrue(path.exists())
        payload["image_url"] = None
        self.assertEqual(self.client.put(f"/api/restaurants/{other}", json=payload).status_code, 200)
        self.assertFalse(path.exists())

    def test_upload_reads_latest_image_and_cleans_failed_uploads(self):
        self.login()
        old = self.images / "old.png"
        old.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.db.update_image_url(self.id, "/uploads/restaurants/old.png")
        url = f"/api/restaurants/{self.id}/image"
        for update in (None, sqlite3.OperationalError("update failed")):
            with self.subTest(update=update), patch.object(
                self.db, "update_image_url", **({"side_effect": update} if update else {"return_value": None})
            ):
                if update:
                    with self.assertRaises(sqlite3.OperationalError):
                        self.client.post(url, content=b"\xff\xd8\xff", headers={"Content-Type": "image/jpeg"})
                else:
                    self.assertEqual(self.client.post(url, content=b"\xff\xd8\xff",
                                                     headers={"Content-Type": "image/jpeg"}).status_code, 404)
                self.assertEqual(list(self.images.iterdir()), [old])
                self.assertEqual(self.db.get(self.id).image_url, "/uploads/restaurants/old.png")

        def replace_while_reading(data):
            latest = self.images / "latest.png"
            latest.write_bytes(b"\x89PNG\r\n\x1a\n")
            self.db.update_image_url(self.id, "/uploads/restaurants/latest.png")
            self.admin.cleanup_local_image("/uploads/restaurants/old.png")
            return "image/jpeg"

        with patch.object(self.admin, "detect_image_type", side_effect=replace_while_reading):
            response = self.client.post(url, content=b"\xff\xd8\xff", headers={"Content-Type": "image/jpeg"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse((self.images / "latest.png").exists())
        self.assertEqual([path.name for path in self.images.iterdir()], [response.json()["image_url"].rsplit("/", 1)[-1]])

    def test_upload_responses_disable_mime_sniffing(self):
        path = self.root / "uploads" / "restaurants" / "nosniff.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.addCleanup(path.unlink, missing_ok=True)
        response = self.client.get("/uploads/restaurants/nosniff.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.content, path.read_bytes())

    def test_import_matching_restaurant_preserves_id_comments_and_tags(self):
        for index, tabelog_url in enumerate(("https://tabelog.com/import", None)):
            with self.subTest(tabelog_url=tabelog_url):
                original_id = self.add_restaurant(name=f"Import regression {index}", tabelog_url=tabelog_url)
                self.db.append_comment(restaurant_id=original_id, comment="Second comment", created_by="member")
                original = self.db.get(original_id)
                comments = self.db.comments_for(original_id)
                with self.db.session() as conn:
                    tags = [tuple(row) for row in conn.execute(
                        "SELECT restaurant_id, tag FROM restaurant_tags WHERE restaurant_id = ? ORDER BY tag",
                        (original_id,),
                    )]
                self.assertTrue(tags)
                for requested_id in (None, 900 + index):
                    imported_id = self.db.import_restaurant(
                        restaurant_id=requested_id, name=f"  {original.name}  ", category=original.category,
                        area=original.area, tabelog_url=f" {tabelog_url} " if tabelog_url else " ",
                        google_maps_url=original.google_maps_url, comments="Imported description", keywords=original.tags,
                    )
                    self.assertEqual(imported_id, original_id)
                    self.assertEqual(self.db.get(original_id).comments, "Imported description")
                    self.assertEqual(self.db.comments_for(original_id), comments)
                    with self.db.session() as conn:
                        self.assertEqual([tuple(row) for row in conn.execute(
                            "SELECT restaurant_id, tag FROM restaurant_tags WHERE restaurant_id = ? ORDER BY tag",
                            (original_id,),
                        )], tags)
                    if requested_id is not None:
                        self.assertIsNone(self.db.get(requested_id))

    def test_import_existing_id_and_new_rows(self):
        values = dict(name="New import", category="拉麵", area=None, tabelog_url=None,
                      google_maps_url=None, comments="Imported", keywords=["濃厚"])
        comments = self.db.comments_for(self.id)
        self.assertEqual(self.db.import_restaurant(restaurant_id=self.id, **values), self.id)
        self.assertEqual(self.db.get(self.id).name, values["name"])
        self.assertEqual(self.db.comments_for(self.id), comments)
        values["name"] = "Explicit import"
        self.assertEqual(self.db.import_restaurant(restaurant_id=800, **values), 800)
        values["name"] = "Automatic import"
        new_id = self.db.import_restaurant(restaurant_id=None, **values)
        self.assertGreater(new_id, 800)
        self.assertEqual(self.db.get(new_id).name, values["name"])


if __name__ == "__main__":
    unittest.main()

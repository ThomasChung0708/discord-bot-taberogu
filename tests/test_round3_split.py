"""Offline web UI split regressions using isolated databases and uploads."""

from contextlib import ExitStack
from html.parser import HTMLParser
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from db import RestaurantDB


ROOT = Path(__file__).resolve().parents[1]


class AssetReferences(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.styles = []
        self.scripts = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.styles.append(attrs["href"])
        elif tag == "script" and "src" in attrs:
            self.scripts.append(attrs["src"])


class Round3SplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        cls.root = Path(cls.stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT)))
        cls.stack.enter_context(patch.dict(os.environ, {
            "DB_PATH": str(cls.root / "startup.sqlite3"),
            "UPLOAD_DIR": str(cls.root / "uploads"),
            "BACKUP_DIR": str(cls.root / "backups"),
            "ADMIN_PASSWORD": "round3-test-password",
            "SESSION_SECRET": "round3-test-secret-" * 8,
            "GOOGLE_MY_MAPS_URL": "",
        }))
        cls.stack.enter_context(patch("dotenv.load_dotenv"))
        previous = sys.modules.pop("admin_app", None)

        def restore_module():
            sys.modules.pop("admin_app", None)
            if previous is not None:
                sys.modules["admin_app"] = previous

        cls.stack.callback(restore_module)
        # Import from another cwd to verify both templates and static use BASE_DIR.
        original_cwd = Path.cwd()
        try:
            os.chdir(cls.root)
            cls.admin = importlib.import_module("admin_app")
        finally:
            os.chdir(original_cwd)

    def setUp(self):
        self.case_dir = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.case_dir.cleanup)
        path = Path(self.case_dir.name)
        self.db = RestaurantDB(str(path / "test.sqlite3"))
        for name, value in {"db": self.db, "DB_PATH": self.db.path,
                            "RESTAURANT_IMAGE_DIR": path / "restaurants",
                            "BACKUP_DIR": path / "backups"}.items():
            mocked = patch.object(self.admin, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.admin.login_attempts.clear()
        self.client = TestClient(self.admin.app)
        self.addCleanup(self.client.close)

    def login(self):
        response = self.client.post("/api/admin/login", json={
            "password": self.admin.ADMIN_PASSWORD,
        })
        self.assertEqual(response.status_code, 200, response.text)

    def assert_page(self, url, page):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        references = AssetReferences(response.text)
        self.assertEqual(references.styles, [f"/static/css/{page}.css"])
        self.assertEqual(references.scripts, [f"/static/js/{page}.js"])
        self.assertNotIn("<style>", response.text)
        self.assertNotIn("<script>", response.text)
        script = f'<script src="/static/js/{page}.js"></script>'
        self.assertIn(script, response.text)
        self.assertLess(response.text.index("</main>"), response.text.index(script))
        self.assertLess(response.text.index(script), response.text.index("</body>"))
        self.assertNotIn("defer", response.text)
        return response

    def test_public_and_map_page_references(self):
        self.assert_page("/", "public")
        self.assert_page("/map", "map")

    def test_admin_login_and_authenticated_workbench(self):
        response = self.assert_page("/admin", "login")
        self.assertIn('id="loginForm"', response.text)
        self.assertNotIn('id="editorForm"', response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.login()
        response = self.assert_page("/admin", "admin")
        self.assertIn('id="editorForm"', response.text)
        self.assertIn('id="logoutButton"', response.text)
        self.assertNotIn('id="loginForm"', response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_every_referenced_asset_is_public_with_mime_and_nosniff(self):
        pages = [self.assert_page("/", "public"), self.assert_page("/map", "map"),
                 self.assert_page("/admin", "login")]
        self.login()
        pages.append(self.assert_page("/admin", "admin"))
        self.client.cookies.clear()
        assets = set()
        for page in pages:
            references = AssetReferences(page.text)
            assets.update(references.styles + references.scripts)
        self.assertEqual(len(assets), 8)
        for url in sorted(assets):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                mime = response.headers["content-type"].split(";")[0]
                if url.endswith(".css"):
                    self.assertEqual(mime, "text/css")
                else:
                    # Python/OS MIME databases use either registered JavaScript type.
                    self.assertIn(mime, {"text/javascript", "application/javascript"})
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
                self.assertEqual(response.content, (ROOT / url.lstrip("/")).read_bytes())

    def test_map_embed_url_substitution(self):
        for configured, expected in [
            ("https://www.google.com/maps/d/viewer?mid=round3-map-id&usp=sharing",
             "https://www.google.com/maps/d/embed?mid=round3-map-id&ehbc=2E312F"),
            ("", ""),
        ]:
            with self.subTest(configured=configured), patch.object(
                    self.admin, "GOOGLE_MY_MAPS_URL", configured):
                response = self.assert_page("/map", "map")
                self.assertIn(f'src="{expected}"', response.text)
                self.assertNotIn("__MY_MAPS_EMBED_URL__", response.text)
        self.assertIn("__MY_MAPS_EMBED_URL__", self.admin.MAP_HTML)

    def test_templates_and_directories_are_not_exposed(self):
        paths = ["/templates", "/templates/", "/static/", "/static/css/", "/static/js/",
                 "/static/admin_app.py", "/static/%2e%2e/admin_app.py",
                 "/static/%2e%2e/templates/admin.html"]
        for page in ("login", "public", "map", "admin"):
            paths.extend([f"/templates/{page}.html", f"/{page}.html",
                          f"/static/templates/{page}.html"])
        for url in paths:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        response = self.client.get("/static/missing.js")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")


if __name__ == "__main__":
    unittest.main()

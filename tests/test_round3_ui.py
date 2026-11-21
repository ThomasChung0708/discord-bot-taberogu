"""Round 3 design and data-preservation regressions; isolated fixture only."""
from pathlib import Path
import unittest
from unittest.mock import patch

import test_round3_split as split

ROOT = split.ROOT


class Round3UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        split.Round3SplitTests.setUpClass.__func__(cls)

    def setUp(self):
        split.Round3SplitTests.setUp(self)

    def login(self):
        split.Round3SplitTests.login(self)

    def test_all_surfaces_share_tokens_favicon_and_language(self):
        pages = [self.client.get(url) for url in ("/", "/map", "/admin")]
        self.login()
        pages.append(self.client.get("/admin"))
        for response in pages:
            with self.subTest(title=response.text[:140]):
                self.assertEqual(response.status_code, 200)
                self.assertIn('/static/css/tokens.css', response.text)
                self.assertIn('/static/favicon.svg', response.text)
                self.assertIn('/static/js/notebook.js', response.text)
                self.assertIn('data-language="zh"', response.text)
                self.assertIn('data-language="ja"', response.text)
                self.assertIn('href="#main"', response.text)
        self.assertEqual(pages[-1].headers['cache-control'], 'no-store')
        self.assertEqual(pages[2].headers['cache-control'], 'no-store')

    def test_favicon_without_auth(self):
        response = self.client.get('/favicon.ico')
        self.assertEqual(response.status_code, 200)
        self.assertIn('image/svg+xml', response.headers['content-type'])
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        self.assertEqual(response.content, (ROOT / 'static/favicon.svg').read_bytes())

    def test_no_glass_or_api_html_parsing(self):
        # Include shared helpers: an unsafe sink must not move out of the page file.
        for directory, glob in [('templates', '*.html'), ('static/js', '*.js'), ('static/css', '*.css')]:
            for path in (ROOT / directory).glob(glob):
                with self.subTest(path=path.name):
                    source = path.read_text(encoding='utf-8')
                    self.assertNotIn('backdrop-filter', source)
                    self.assertNotRegex(source, r'\.innerHTML\s*[+]?=')
                    self.assertNotIn('insertAdjacentHTML', source)
        public = (ROOT / 'static/js/public.js').read_text(encoding='utf-8')
        self.assertIn("dataset.testid = 'shop-open'", public)
        self.assertIn('/comments', public)
        self.assertIn('showModal()', public)
        self.assertIn("event.key !== 'Tab'", public)
        self.assertIn('data-testid="shop-detail"', (ROOT / 'templates/public.html').read_text(encoding='utf-8'))

    def comment_fixture(self):
        restaurant_id = self.db.add_restaurant(
            name='測試喫茶', category='咖啡廳', area='新宿',
            tabelog_url='https://tabelog.com/example', google_maps_url=None,
            comments='初始筆記', keywords=['喫茶'], source_channel_id=123,
            source_message_id=456, created_by='private-user', recommended_by='友人')
        self.db.append_comment(restaurant_id=restaurant_id, comment='獨立評論', created_by='member')
        legacy = '  舊筆記原文\n第二行\n\n  '
        with self.db.session() as conn:
            conn.execute('UPDATE restaurants SET comments = ? WHERE id = ?', (legacy, restaurant_id))
        return restaurant_id, legacy

    def test_put_without_comments_preserves_exact_legacy_bytes(self):
        restaurant_id, legacy = self.comment_fixture()
        self.login()
        for extra in ({}, {'comments': None}):
            with self.subTest(extra=extra):
                response = self.client.put(f'/api/restaurants/{restaurant_id}', json={
                    'name': '修改後喫茶', 'category': '咖啡廳', **extra})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['comments'], legacy)
                with self.db.session() as conn:
                    stored = conn.execute('SELECT CAST(comments AS BLOB) FROM restaurants WHERE id = ?',
                                          (restaurant_id,)).fetchone()[0]
                self.assertEqual(stored, legacy.encode('utf-8'))

    def test_put_explicit_comments_keeps_exact_value_including_empty_string(self):
        restaurant_id, _ = self.comment_fixture()
        self.login()
        for value in ('  更新原文\r\n第二行\n\n  ', ''):
            with self.subTest(value=value):
                response = self.client.put(f'/api/restaurants/{restaurant_id}', json={
                    'name': '喫茶', 'category': '咖啡廳', 'comments': value})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['comments'], value)
                self.assertEqual(self.db.get(restaurant_id).comments, value)

    def test_editor_save_preserves_comments_appended_after_loading(self):
        restaurant_id, legacy = self.comment_fixture()
        self.login()
        shop = self.client.get(f'/api/admin/restaurants/{restaurant_id}').json()
        original_items = shop['comment_items']
        # Simulate a friend's Discord comment after the editor loaded its snapshot.
        appended = self.db.append_comment(restaurant_id=restaurant_id, comment='編輯期間的新評論',
                                          created_by='discord-friend', source_message_id=789)
        current = self.client.get(f'/api/admin/restaurants/{restaurant_id}').json()
        self.assertNotEqual(appended.comments, shop['comments'])
        self.assertIn(legacy.strip(), appended.comments)
        self.assertIn('追加評論（discord-friend）：\n編輯期間的新評論', appended.comments)
        self.assertEqual(current['comment_items'][:-1], original_items)
        self.assertEqual(current['comment_items'][-1]['comment'], '編輯期間的新評論')
        # Exactly the field set emitted by admin.js editorPayload (numeric empties are null).
        keys = ['name', 'category', 'area', 'recommended_by', 'lunch_budget_text',
                'dinner_budget_text', 'lunch_budget_min', 'lunch_budget_max',
                'dinner_budget_min', 'dinner_budget_max', 'business_hours_text',
                'tabelog_url', 'google_maps_url', 'image_url']
        payload = {key: shop[key] for key in keys}
        payload.update(keywords=shop['tags'], tags=shop['tags'])
        payload['name'] = '修改後喫茶'
        response = self.client.put(f'/api/restaurants/{restaurant_id}', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated['name'], payload['name'])
        self.assertEqual(updated['comments'], appended.comments)
        self.assertEqual(updated['comment_items'], current['comment_items'])
        self.assertEqual(self.db.get(restaurant_id).comments, appended.comments)
        self.assertIn('readonly', (ROOT / 'templates/admin.html').read_text(encoding='utf-8'))
        public = self.client.get(f'/api/restaurants/{restaurant_id}/comments').json()['comments']
        self.assertEqual(len(public), len(current['comment_items']))
        for comment in public:
            self.assertNotIn('author_label', comment)
            self.assertNotIn('created_by', comment)
            self.assertNotIn('source_message_id', comment)


    def test_public_comments_omit_authors_and_redact_legacy_headers(self):
        identifier = '123456789012345678'
        shop_id = self.db.add_restaurant(
            name='作者隱私測試', category='咖啡廳', area='京都', tabelog_url=None,
            google_maps_url=None, comments='', keywords=[], source_channel_id=11,
            source_message_id=22, created_by=identifier)
        self.db.append_comment(restaurant_id=shop_id, comment='第一則', created_by=identifier)
        self.db.append_comment(restaurant_id=shop_id, comment='第二則', created_by=identifier)
        self.db.append_comment(restaurant_id=shop_id, comment='另一位朋友', created_by='other-private-user')
        for creator in ('Admin', 'Google Sheet'):
            self.db.append_comment(restaurant_id=shop_id, comment='額外筆記', created_by=creator)
        response = self.client.get(f'/api/restaurants/{shop_id}/comments')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(identifier, response.text)
        self.assertNotIn('other-private-user', response.text)
        for public_url in (f'/api/restaurants/{shop_id}', '/api/restaurants'):
            public_response = self.client.get(public_url)
            self.assertNotIn(identifier, public_response.text)
            self.assertNotIn('other-private-user', public_response.text)
            shops = public_response.json().get('restaurants', [public_response.json()])
            for shop in shops:
                self.assertNotIn('created_by', shop)
                self.assertNotIn('追加評論（', shop['comments'])
                self.assertIn('追加評論：', shop['comments'])
                for comment in shop['comment_items']:
                    self.assertNotIn('author_label', comment)
                    self.assertNotIn('created_by', comment)
        self.assertIn(identifier, self.db.get(shop_id).comments)
        comments = response.json()['comments']
        for comment in comments:
            self.assertEqual(set(comment), {'id', 'restaurant_id', 'comment', 'created_at'})
        with patch.object(self.admin, 'SESSION_SECRET', 'different-after-restart'):
            self.assertEqual(comments, self.client.get(f'/api/restaurants/{shop_id}/comments').json()['comments'])
        self.login()
        internal = self.client.get(f'/api/admin/restaurants/{shop_id}').json()
        self.assertEqual(internal['comments'], self.db.get(shop_id).comments)
        self.assertEqual([item['created_by'] for item in internal['comment_items']],
                         [identifier, identifier, 'other-private-user', 'Admin', 'Google Sheet'])

    def test_map_shop_query_keeps_embed_substitution(self):
        with patch.object(self.admin, 'GOOGLE_MY_MAPS_URL', 'https://www.google.com/maps/d/viewer?mid=round3-fixture'):
            response = self.client.get('/map?shop=1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('https://www.google.com/maps/d/embed?mid=round3-fixture&ehbc=2E312F', response.text)
        self.assertNotIn('__MY_MAPS_EMBED_URL__', response.text)
        self.assertIn('id="mapList"', response.text)


if __name__ == '__main__':
    unittest.main()

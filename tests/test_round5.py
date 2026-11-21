"""Natural recommendation regressions: temp SQLite and mocked OpenAI only."""

import asyncio
from contextlib import ExitStack
from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from openai import BadRequestError

from db import RestaurantDB
import openai_util


def response(data):
    content = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class NaturalReplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        cls.root = Path(cls.stack.enter_context(tempfile.TemporaryDirectory()))
        cls.stack.enter_context(patch.dict(os.environ, {
            "DB_PATH": str(cls.root / "startup.sqlite3"), "DISCORD_TOKEN": "offline-token",
        }))
        cls.stack.enter_context(patch("dotenv.load_dotenv"))
        cls.stack.enter_context(patch("openai.OpenAI"))
        previous = sys.modules.pop("bot", None)

        def restore():
            sys.modules.pop("bot", None)
            if previous is not None:
                sys.modules["bot"] = previous

        cls.stack.callback(restore)
        cls.bot = importlib.import_module("bot")
        cls.stack.callback(lambda: asyncio.run(cls.bot.client.close()))

    def setUp(self):
        case = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(case.cleanup)
        self.db = RestaurantDB(str(Path(case.name) / "case.sqlite3"))
        self.stack_case = ExitStack()
        self.addCleanup(self.stack_case.close)
        self.stack_case.enter_context(patch.object(self.bot, "db", self.db))
        self.create = self.stack_case.enter_context(patch.object(
            self.bot.openai_client.chat.completions, "create"))
        self.create.return_value = response({})
        self.first = self.add("麵屋青", "拉麵", comments="魚介湯頭", business_hours_text="11:00–22:00")
        self.second = self.add("麵屋白", "拉麵")
        self.third = self.add("麵屋赤", "拉麵")
        self.curry = self.add("咖哩樹", "カレー", comments="香料味濃")
        self.sushi = self.add("鮨葉", "寿司", comments="午餐有握壽司")

    def add(self, name, category, **overrides):
        values = dict(name=name, category=category, area="新宿", comments="", keywords=[],
                      tabelog_url=f"https://tabelog.com/tokyo/{len(self.db.all()) + 1}/",
                      google_maps_url=f"https://maps.google.com/?q={name}",
                      source_channel_id=111111111111111111, source_message_id=222222222222222222,
                      created_by="private-author", recommended_by="小林")
        values.update(overrides)
        return self.db.get(self.db.add_restaurant(**values))

    def intent(self, request, **overrides):
        result = self.bot.rule_based_recommendation_intent(request)
        result.update(overrides)
        return self.bot.sanitize_recommendation_intent(result)

    def build(self, request, items=None, **intent_overrides):
        if items is None:
            items = [{"id": self.first.id, "reason": "留言提到魚介湯頭，想吃拉麵可以先選這間。"}]
        intent = self.intent(request, **intent_overrides)
        with patch.object(self.bot, "infer_recommendation_intent", return_value=intent):
            self.create.return_value = response({"recommendations": items})
            return self.bot.build_recommendation_response(request)

    def assert_no_meta(self, text):
        for word in ("????", "? DB", "??", "我理解成", "從 DB", "候選", "資料庫", "ID "):
            self.assertNotIn(word, text)
        self.assertLessEqual(len(text), 1900)

    def test_top_pick_names_categories_area_and_all_tidy_links(self):
        text = self.build("新宿 拉麵", food_terms=["拉麵"])
        self.assertTrue(text.startswith(f"我會先選：**{self.first.name}**\n"))
        self.assertEqual(text.count(self.first.name), 2)  # Heading and Maps query only.
        self.assertLess(text.index(self.first.name), text.index("也可以選"))
        for shop in (self.first, self.second, self.third):
            self.assertIn(shop.name, text)
            self.assertIn(shop.category, text)
            self.assertIn(f"[食べログ]({shop.tabelog_url})", text)
            self.assertIn(f"[Google Maps]({shop.google_maps_url})", text)
        self.assertNotIn("](<", text)
        self.assertIn("新宿", text)
        self.assertIn("魚介湯頭", text)
        self.assert_no_meta(text)

    def test_reason_id_sanitized_at_final_boundary_even_with_legacy_ranker(self):
        for prefix in ("ID 12｜", "ID?12：", "id：12 ", f"ID {self.first.id} "):
            with self.subTest(prefix=prefix), patch.object(
                self.bot, "rank_recommendations_with_ai", return_value=[(self.first, prefix + "魚介湯頭")]
            ):
                text = self.build("新宿 拉麵", food_terms=["拉麵"])
                self.assertIn("魚介湯頭", text)
                self.assertNotIn("ID 12", text)
                self.assert_no_meta(text)

    def test_payload_contains_text_comments_without_placeholders_or_metadata(self):
        shop = self.add("評論店", "カレー", comments="???????????????")
        self.db.append_comment(restaurant_id=shop.id, comment="香料香氣明顯", created_by="hidden-author",
                               source_message_id=333333333333333333)
        shop = self.db.get(shop.id)
        self.bot.rank_recommendations_with_ai("咖哩", [shop])
        messages = self.create.call_args.kwargs["messages"]
        payload = json.loads(messages[1]["content"])["candidates"][0]
        self.assertIn("香料香氣明顯", payload["comment_items"])
        self.assertTrue(all(isinstance(item, str) for item in payload["comment_items"]))
        encoded = json.dumps(payload, ensure_ascii=False)
        for forbidden in ("???", "hidden-author", "private-author", "created_by", "source_message_id",
                          "333333333333333333", "222222222222222222", "111111111111111111"):
            self.assertNotIn(forbidden, encoded)
        for field in ("id", "name", "category", "area", "keywords", "tags", "recommended_by",
                      "lunch_budget", "dinner_budget", "business_hours", "comment_items"):
            self.assertIn(field, payload)

    def test_real_legacy_comment_preserved_and_basic_placeholder_removed(self):
        shop = self.add("舊評論", "カレー", comments="????????????\n香氣足")
        self.assertIn("香氣足", self.bot.recommendation_comments(shop))
        bare = self.add("基本資訊店", "カレー", comments="僅保存了基本餐廳資訊，無評論內容。")
        self.assertEqual(self.bot.recommendation_comments(bare), [])

    def test_placeholder_variants_removed_from_payload_and_reasons(self):
        placeholders = (
            "僅儲存了基本餐廳資訊，目前沒有評論。",
            "已保存基本資訊，尚無評論內容。",
            "只收錄基本店家資料。",
            "此店尚未有評論內容。",
            "目前沒有評論。",
            "暫無留言。",
            "舊文字???無法辨識",
        )
        for placeholder in placeholders:
            with self.subTest(placeholder=placeholder):
                shop = self.add("佔位評論店", "カレー", comments=placeholder + "\n香料香氣明顯")
                self.db.append_comment(restaurant_id=shop.id, comment=placeholder,
                                       created_by="hidden-author")
                shop = self.db.get(shop.id)
                comments = self.bot.recommendation_comments(shop)
                self.assertTrue(comments)
                self.assertTrue(all(placeholder not in comment for comment in comments))
                self.bot.rank_recommendations_with_ai("咖哩", [shop])
                payload = json.loads(self.create.call_args.kwargs["messages"][1]["content"])["candidates"][0]
                self.assertNotIn(placeholder, json.dumps(payload, ensure_ascii=False))
                for reason in ("", f"留言提到「{placeholder}」，可以參考。"):
                    cleaned = self.bot.clean_recommendation_reason(reason, shop)
                    self.assertNotIn(placeholder, cleaned)
                    self.assertIn("香料香氣明顯", cleaned)

    def test_placeholder_only_comment_falls_back_to_hours(self):
        shop = self.add("基本資料店", "カレー", comments="已儲存基本資訊，尚無評論。",
                        business_hours_text="11:00–22:00")
        reason = self.bot.clean_recommendation_reason("", shop)
        self.assertIn("22:00", reason)
        self.assertNotIn("留言", reason)
        self.assertNotIn("基本資訊", reason)

    def test_vague_request_picks_and_exactly_one_follow_up(self):
        text = self.build("推薦晚餐", food_terms=[])
        self.assertIn(self.first.name, text)
        self.assertIn("想限定哪個地區或哪種料理嗎？", text)
        self.assertEqual(text.count("？"), 1)
        self.assert_no_meta(text)

    def test_specific_request_does_not_interrogate(self):
        text = self.build("新宿 拉麵", food_terms=["拉麵"])
        self.assertNotIn("？", text)
        self.assertNotIn("想吃哪一種", text)

    def test_area_only_diversifies_fixed_ranking_and_keeps_top_pick(self):
        ranked = [(r, "") for r in (self.first, self.second, self.third, self.curry, self.sushi)]
        with patch.object(self.bot, "rank_recommendations_with_ai", return_value=ranked):
            text = self.build("新宿", area_terms=["新宿"], food_terms=[])
        for shop in (self.first, self.curry, self.sushi):
            self.assertIn(shop.name, text)
        for shop in (self.second, self.third):
            self.assertNotIn(shop.name, text)
        self.assertLess(text.index(self.first.name), text.index(self.curry.name))

    def test_short_model_ranking_can_still_supply_different_cuisines(self):
        text = self.build("新宿", items=[{"id": r.id, "reason": ""}
                                        for r in (self.first, self.second, self.third)],
                          area_terms=["新宿"], food_terms=[])
        self.assertIn(self.curry.name, text)
        self.assertIn(self.sushi.name, text)

    def test_japanese_reason_and_template(self):
        text = self.build("新宿でラーメンが食べたい", items=[{
            "id": self.first.id, "reason": "コメントに魚介スープとあるので、まずはこちらがおすすめ。",
        }], food_terms=["ラーメン"])
        self.assertTrue(text.startswith(f"まずは**{self.first.name}**がおすすめ。\n"))
        self.assertEqual(text.count(f"**{self.first.name}**"), 1)
        self.assertIn("魚介スープ", text)
        self.assertNotIn("我會", text)
        self.assertNotIn("理由：", text)
        payload = json.loads(self.create.call_args.kwargs["messages"][1]["content"])
        self.assertEqual(payload["response_language"], "日本語")
        self.assert_no_meta(text)

    def test_japanese_vague_fallback_and_follow_up(self):
        text = self.build("新宿でデート", items=[], food_terms=[])
        self.assertIn("どんな料理の気分？", text)
        self.assertIn("コメントでは", text)
        self.assertNotIn("留言", text)

    def test_broad_japanese_request_offers_area_and_cuisine_without_scene_claims(self):
        text = self.build("デート", items=[], area_terms=[], food_terms=[])
        self.assertIn("エリアや料理の希望はある？", text)
        self.assertEqual(text.count("？"), 1)
        self.assertNotIn("雰囲気", text)
        self.assertNotIn("デートに", text)

    def test_chinese_request_with_japanese_area_stays_chinese(self):
        self.assertFalse(self.bot.recommendation_is_japanese("市ヶ谷有推薦的餐廳嗎"))

    def test_chinese_request_with_japanese_names_uses_chinese_reply_and_prompt(self):
        for request in ("想吃らぁ麺やまぐち，有推薦嗎？", "市ヶ谷有推薦的餐廳嗎？"):
            with self.subTest(request=request):
                text = self.build(request, food_terms=["拉麵"])
                self.assertTrue(text.startswith(f"我會先選：**{self.first.name}**\n"))
                payload = json.loads(self.create.call_args.kwargs["messages"][1]["content"])
                self.assertEqual(payload["response_language"], "繁體中文")

    def test_details_omit_each_corrupt_field_and_keep_the_other(self):
        for corrupt in ("拉\ufffd麵", "\ufffd", "????", "新??宿", "新？?宿"):
            for field in ("category", "area"):
                with self.subTest(corrupt=corrupt, field=field):
                    shop = replace(self.first, **{field: corrupt})
                    expected = shop.area if field == "category" else shop.category
                    with patch.object(self.bot, "recommendation_candidates", return_value=[shop]), patch.object(
                        self.bot, "rank_recommendations_with_ai", return_value=[(shop, "魚介湯頭")]
                    ):
                        text = self.build("推薦拉麵", food_terms=["拉麵"])
                    self.assertEqual(text.splitlines()[1], expected)
                    self.assertNotIn(corrupt, text)
        shop = replace(self.first, category="\ufffd", area="????", comments="",
                       business_hours_text="")
        with patch.object(self.bot, "recommendation_candidates", return_value=[shop]), patch.object(
            self.bot, "rank_recommendations_with_ai", return_value=[(shop, "")]
        ):
            text = self.build("推薦拉麵", food_terms=["拉麵"])
        self.assertNotIn("\ufffd", text)
        self.assertNotIn("??", text)

    def test_corrupt_fields_do_not_leak_through_fallback_in_either_language(self):
        shop = self.add("無評論店", "拉\ufffd麵", area="新??宿")
        for japanese in (False, True):
            with self.subTest(japanese=japanese):
                reason = self.bot.clean_recommendation_reason("", shop, japanese=japanese)
                self.assertTrue(reason)
                self.assertNotIn("\ufffd", reason)
                self.assertNotIn("??", reason)

    def test_unsupported_atmosphere_claims_use_factual_fallback(self):
        for claim in ("環境安靜", "氣氛浪漫", "很適合約會", "適合聚餐", "空間寬敞",
                      "座位舒適", "氣氛很好", "環境清幽", "裝潢溫馨", "很有氛圍",
                      "靜かな雰囲気", "デートにぴったり", "a cozy atmosphere"):
            with self.subTest(claim=claim):
                cleaned = self.bot.clean_recommendation_reason(claim, self.first)
                self.assertEqual(cleaned, self.bot.fallback_recommendation_reason(self.first))

    def test_atmosphere_terms_require_evidence_in_permitted_fields(self):
        for field, value in (("comments", "環境安靜"), ("tags", ["安靜"]),
                             ("keywords", ["安靜"]), ("category", "安靜咖啡館")):
            with self.subTest(field=field):
                shop = replace(self.first, **{field: value})
                self.assertEqual(self.bot.clean_recommendation_reason("環境安靜", shop), "環境安靜")
        shop = replace(self.first, area="安靜區", recommended_by="安靜",
                       business_hours_text="安靜 11:00–22:00")
        self.assertNotEqual(self.bot.clean_recommendation_reason("環境安靜", shop), "環境安靜")

    def test_atmosphere_uncertainty_is_honest_and_preserved(self):
        for reason in ("沒有足夠資訊判斷是否適合約會。", "無法確認環境是否安靜。",
                       "氣氛尚未記錄，不能確認是否浪漫。", "是否適合聚餐需要再確認。"):
            with self.subTest(reason=reason):
                self.assertEqual(self.bot.clean_recommendation_reason(reason, self.first), reason)
        reason = "無法確認是否安靜，但氣氛浪漫。"
        self.assertEqual(self.bot.clean_recommendation_reason(reason, self.first),
                         self.bot.fallback_recommendation_reason(self.first))

    def test_each_atmosphere_claim_needs_its_own_evidence(self):
        shop = self.add("氣氛店", "咖啡館", comments="環境安靜")
        for reason in ("環境安靜，但氣氛浪漫。", "無法確認是否浪漫，而且空間寬敞。",
                       "無法確認是否浪漫且空間寬敞。"):
            with self.subTest(reason=reason):
                self.assertEqual(self.bot.clean_recommendation_reason(reason, shop),
                                 self.bot.fallback_recommendation_reason(shop))
        self.db.append_comment(restaurant_id=shop.id, comment="氣氛浪漫", created_by="tester")
        self.assertEqual(self.bot.clean_recommendation_reason("環境安靜，氣氛浪漫。", shop),
                         "環境安靜，氣氛浪漫。")

    def test_reasons_and_fallback_quotes_strip_discord_mentions(self):
        mentions = "@everyone @here <@123456789012345678> <@!123456789012345678> <@&123456789012345678>"
        for reason in (f"{mentions} 魚介湯頭", f"魚介湯頭 {mentions}"):
            with self.subTest(reason=reason):
                cleaned = self.bot.clean_recommendation_reason(reason, self.first)
                self.assertIn("魚介湯頭", cleaned)
                for mention in mentions.split():
                    self.assertNotIn(mention, cleaned)
        shop = self.add("安全留言店", "拉麵", comments=f"{mentions} 魚介湯頭")
        self.db.append_comment(restaurant_id=shop.id, comment=f"香料味濃 {mentions}", created_by="tester")
        shop = self.db.get(shop.id)
        for text in [*self.bot.recommendation_comments(shop),
                     self.bot.fallback_recommendation_reason(shop),
                     self.bot.fallback_recommendation_reason(shop, japanese=True)]:
            for mention in mentions.split():
                self.assertNotIn(mention, text)
        self.assertIn("魚介湯頭", " ".join(self.bot.recommendation_comments(shop)))

    def test_mention_only_reasons_and_comments_use_factual_fallback(self):
        mentions = "@everyone @here <@123> <@!123> <@&123>"
        shop = self.add("純提及留言店", "咖哩", comments=mentions, business_hours_text="11:00–22:00")
        self.db.append_comment(restaurant_id=shop.id, comment=mentions, created_by="tester")
        self.assertEqual(self.bot.recommendation_comments(shop), [])
        for japanese in (False, True):
            with self.subTest(japanese=japanese):
                reason = self.bot.clean_recommendation_reason(mentions, shop, japanese=japanese)
                self.assertIn("11:00–22:00", reason)
                self.assertNotIn("@", reason)

    def test_recommendation_sends_disable_all_discord_mentions(self):
        target = SimpleNamespace(guild=None, reply=AsyncMock())
        status = SimpleNamespace(edit=AsyncMock())
        target.reply.return_value = status
        interaction = SimpleNamespace(guild_id=None, channel_id=None,
                                      response=SimpleNamespace(defer=AsyncMock()),
                                      followup=SimpleNamespace(send=AsyncMock()))
        with patch.object(self.bot, "build_recommendation_response", return_value="@everyone <@123>"):
            asyncio.run(self.bot.send_recommendations(target, "推薦拉麵"))
            asyncio.run(self.bot.send_recommendation_interaction(interaction, "推薦拉麵"))
        for call in (target.reply.call_args, status.edit.call_args, interaction.followup.send.call_args):
            self.assertEqual(call.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        target.reply.reset_mock()
        asyncio.run(self.bot.send_recommendations(target, ""))
        self.assertEqual(target.reply.call_args.kwargs["allowed_mentions"].to_dict(), {"parse": []})

    def test_no_match_messages_are_helpful_in_both_languages(self):
        for request in ("火星料理", "火星料理が食べたい"):
            with self.subTest(request=request), patch.object(self.bot, "recommendation_candidates", return_value=[]):
                text = self.build(request, area_terms=[], food_terms=["火星料理"])
                self.assertIn("？", text)
                self.assert_no_meta(text)

    def test_area_no_match_does_not_use_meta_talk(self):
        with patch.object(self.bot, "recommendation_candidates", return_value=[]), patch.object(
            self.bot, "infer_recommendation_intent", return_value={"area_terms": ["京都"], "food_terms": []}
        ):
            text = self.bot.build_recommendation_response("京都有推薦嗎")
        self.assertIn("京都", text)
        self.assertIn("其他地區", text)
        self.assert_no_meta(text)

    def test_scene_only_pool_widens_without_word_matches(self):
        for request in ("約會", "聚餐", "宵夜"):
            with self.subTest(request=request):
                result = self.bot.recommendation_candidates(request, intent=self.intent(request, food_terms=[]))
                self.assertGreaterEqual(len(result), 3)
                self.assertGreater(len({r.category for r in result[:3]}), 1)
                self.assertTrue({r.id for r in result} <= {r.id for r in self.db.all()})

    def test_scene_with_one_match_still_widens(self):
        dated = self.add("約會咖哩", "カレー")
        result = self.bot.recommendation_candidates("約會", intent=self.intent("約會", food_terms=[]))
        self.assertEqual(result[0].id, dated.id)
        self.assertGreaterEqual(len(result), 3)

    def test_widening_keeps_explicit_area_and_exclusions(self):
        outside = self.add("外地店", "カレー", area="池袋")
        intent = self.intent("新宿約會", area_terms=["新宿"], food_terms=[], exclude_terms=["カレー"])
        result = self.bot.recommendation_candidates("新宿約會", intent=intent)
        self.assertTrue(result)
        self.assertNotIn(outside.id, {r.id for r in result})
        self.assertTrue(all(r.area == "新宿" and r.category != "カレー" for r in result))

    def test_cheap_request_without_budget_never_claims_cheap(self):
        for request, reason in (("新宿便宜拉麵", "ID 12 便宜又划算，800円。"),
                                ("新宿で安いラーメン", "安くて500円！")):
            with self.subTest(request=request):
                text = self.build(request, items=[{"id": self.first.id, "reason": reason}], food_terms=["拉麵"])
                for invented in ("800", "500", "便宜", "划算", "安く"):
                    self.assertNotIn(invented, text)
                expected = "予算は未記録" if self.bot.recommendation_is_japanese(request) else "預算尚未記錄"
                self.assertIn(expected, text)
                self.assertIn("魚介湯頭", text)

    def test_stored_budget_is_preserved(self):
        shop = self.add("有預算店", "カレー", lunch_budget_text="￥1,000～￥1,999")
        text = self.build("新宿", items=[{"id": shop.id, "reason": "午餐預算有記錄。"}], area_terms=["新宿"])
        self.assertIn("午餐 ￥1,000～￥1,999", text)

    def test_invented_price_rejected_even_when_budget_is_stored(self):
        shop = self.add("有預算店", "カレー", lunch_budget_text="￥1,000～￥1,999")
        for claim in ("午餐只要850円。", "午餐只要￥850。", "午餐只要¥850。"):
            with self.subTest(claim=claim):
                reason = self.bot.clean_recommendation_reason(claim, shop)
                self.assertNotIn("850", reason)

    def test_unknown_budget_caveats_are_preserved(self):
        for reason in (
            "預算尚未記錄，無法確認是否便宜；留言提到魚介湯頭。",
            "預算未記錄，平價與否還需確認。",
            "預算未記錄，不能保證划算。",
            "想找平價料理的話，這間預算尚未記錄，請先確認菜單。",
            "予算は未記録なので、安いかどうかは確認が必要。",
        ):
            with self.subTest(reason=reason):
                self.assertEqual(self.bot.clean_recommendation_reason(reason, self.first), reason)

    def test_cheap_dinner_preserves_unknown_budget_reason_without_raw_hours(self):
        shop = self.add("晚餐咖哩", "カレー", business_hours_text=(
            "11:00～22:00 営業時間・定休日は変更となる場合がございますので、"
            "ご来店前に店舗にご確認ください。"))
        reason = "想找平價晚餐的話，這間預算尚未記錄，請先確認菜單價格。"
        text = self.build("新宿便宜晚餐", items=[{"id": shop.id, "reason": reason}],
                          area_terms=["新宿"], food_terms=[])
        self.assertIn(reason, text)
        self.assertNotIn("変更となる", text)
        self.assertNotIn("ご来店前", text)

    def test_unsupported_affordability_claim_still_rejected(self):
        for reason in ("便宜又划算。", "預算未記錄，但很便宜。", "安くておすすめ。"):
            with self.subTest(reason=reason):
                cleaned = self.bot.clean_recommendation_reason(reason, self.first)
                self.assertNotEqual(cleaned, reason)
                self.assertIn("魚介湯頭", cleaned)

    def test_hours_fallback_summarizes_ranges_without_disclaimer_in_both_languages(self):
        disclaimer = "営業時間・定休日は変更となる場合がございますので、ご来店前に店舗にご確認ください。"
        shop = self.add("晚餐店", "カレー", comments="尚無評論。", business_hours_text=(
            "営業時間\n月・火・水\n11:00～15:00\n17:00–22:00\n定休日\n日曜\n" + disclaimer))
        for japanese in (False, True):
            with self.subTest(japanese=japanese):
                reason = self.bot.clean_recommendation_reason("", shop, japanese=japanese)
                self.assertIn("11:00–15:00", reason)
                self.assertIn("17:00–22:00", reason)
                for raw in (disclaimer, "変更となる", "月・火・水", "尚無評論", "「"):
                    self.assertNotIn(raw, reason)
                self.assertLess(len(reason), 100)
        self.assertIn("22:00", self.bot.fallback_recommendation_reason(shop))

    def test_fallback_uses_next_valid_comment_before_hours(self):
        shop = self.add("評論備援店", "カレー", comments="平價，只要800円\n尚無評論。",
                        business_hours_text="11:00–22:00")
        self.db.append_comment(restaurant_id=shop.id, comment="香料味濃", created_by="食客")
        shop = self.db.get(shop.id)
        reason = self.bot.clean_recommendation_reason("", shop)
        self.assertIn("香料味濃", reason)
        self.assertNotIn("800", reason)
        self.assertNotIn("22:00", reason)

    def test_unusable_hours_fall_back_to_category_and_area(self):
        shop = self.add("無時段店", "カレー", comments="尚無評論。",
                        business_hours_text="営業時間・定休日は変更となる場合がございます。")
        for japanese in (False, True):
            with self.subTest(japanese=japanese):
                reason = self.bot.clean_recommendation_reason("", shop, japanese=japanese)
                self.assertIn(shop.category, reason)
                self.assertIn(shop.area, reason)
                self.assertNotIn("変更となる", reason)

    def test_fallback_comment_cannot_reintroduce_id_or_price_claim(self):
        shop = self.add("舊店", "カレー", comments="ID 12 平價，只要800円")
        reason = self.bot.clean_recommendation_reason("", shop)
        for forbidden in ("ID 12", "平價", "800"):
            self.assertNotIn(forbidden, reason)

    def test_model_meta_talk_replaced_with_facts(self):
        text = self.build("新宿 拉麵", items=[{
            "id": self.first.id, "reason": "我理解成拉麵，從 DB 挑了候選????",
        }], food_terms=["拉麵"])
        self.assertIn("魚介湯頭", text)
        self.assert_no_meta(text)

    def test_specific_request_stays_specific_when_api_intent_is_unavailable(self):
        intent = self.bot.sanitize_recommendation_intent(self.bot.rule_based_recommendation_intent("新宿 拉麵"))
        self.assertFalse(self.bot.recommendation_is_broad("新宿 拉麵", intent))

    def test_area_cuisine_fallback_is_explicit_and_local(self):
        text = self.build("新宿 燒肉", area_terms=["新宿"], food_terms=["燒肉"], expanded_terms=["燒肉"])
        self.assertIn("還沒找到指定料理", text)
        self.assertIn(self.first.name, text)
        self.assertIn(self.curry.name, text)
        self.assert_no_meta(text)

    def test_ranker_ignores_unknown_duplicate_and_malformed_ids(self):
        self.create.return_value = response({"recommendations": [
            {"id": "bad", "reason": "不存在"}, {"id": 987654, "reason": "不存在"},
            {"id": self.first.id, "reason": "ID 12｜魚介湯頭"},
        ]})
        ranked = self.bot.rank_recommendations_with_ai("拉麵", [self.first])
        self.assertEqual([(r.id, reason) for r, reason in ranked], [(self.first.id, "魚介湯頭")])
        self.create.return_value = response({"recommendations": [{"id": self.first.id}] * 3})
        self.assertEqual(len(self.bot.rank_recommendations_with_ai("拉麵", [self.first])), 1)

    def test_api_failure_has_natural_factual_fallback(self):
        self.create.side_effect = RuntimeError("offline")
        with patch("builtins.print"):
            text = self.build("新宿でラーメン", food_terms=["ラーメン"])
        self.assertIn("コメントでは", text)
        self.assert_no_meta(text)

    def test_summary_is_bounded_factual_and_no_secret_instruction(self):
        self.create.return_value = response("好" * 230)
        summary = self.bot.summarize_chat_memory("", [SimpleNamespace(author_name="食客", content="今天想吃咖哩")])
        self.assertEqual(len(summary), 200)
        prompt = self.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("不推測偏好", prompt)
        self.assertIn("金鑰", prompt)


class ReasoningDefaultTests(unittest.TestCase):
    def test_low_default_blank_invalid_and_rejected_parameter_retry(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(openai_util, "_REJECTED_PARAMETERS", {}):
            self.assertEqual(openai_util.get_reasoning_effort(), "low")
            self.assertEqual(openai_util.get_reasoning_effort(" "), "low")
            with self.assertLogs("openai_util", level="WARNING") as logs:
                self.assertEqual(openai_util.get_reasoning_effort("invalid-private-value"), "low")
            self.assertNotIn("invalid-private-value", " ".join(logs.output))
            client = MagicMock()
            error = BadRequestError("Unsupported parameter: reasoning_effort", response=httpx.Response(
                400, request=httpx.Request("POST", "https://offline.invalid")),
                body={"param": "reasoning_effort", "code": "unsupported_parameter"})
            client.chat.completions.create.side_effect = [error, response({})]
            with self.assertLogs("openai_util", level="WARNING"):
                openai_util.create_chat_completion(client, messages=[], max_completion_tokens=1536)
            first, second = [c.kwargs for c in client.chat.completions.create.call_args_list]
            self.assertEqual(first["reasoning_effort"], "low")
            self.assertNotIn("reasoning_effort", second)


if __name__ == "__main__":
    unittest.main()

"""Two-turn recommendation regressions; temporary SQLite and mocked APIs only."""

import asyncio
from contextlib import ExitStack
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from db import RestaurantDB, normalize_search_text


class FollowupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        cls.root = Path(cls.stack.enter_context(tempfile.TemporaryDirectory()))
        cls.stack.enter_context(patch.dict(os.environ, {
            "DB_PATH": str(cls.root / "startup.sqlite3"), "DISCORD_TOKEN": "offline-token",
        }))
        cls.stack.enter_context(patch("dotenv.load_dotenv"))
        cls.openai = cls.stack.enter_context(patch("openai.OpenAI"))
        cls.openai.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
        )
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
        self.case_stack = ExitStack()
        self.addCleanup(self.case_stack.close)
        self.case_stack.enter_context(patch.object(self.bot, "db", self.db))
        self.case_stack.enter_context(patch.object(self.bot, "recommendation_contexts", self.bot.OrderedDict()))
        self.bot_user = SimpleNamespace(id=999, bot=True)
        self.case_stack.enter_context(patch.object(self.bot.client._connection, "user", self.bot_user))
        self.case_stack.enter_context(patch.object(self.bot, "AUTO_SAVE_RESTAURANT_LINKS", False))
        self.case_stack.enter_context(patch.object(self.bot, "is_allowed_text_channel", return_value=True))
        self.memory = self.case_stack.enter_context(patch.object(self.bot, "remember_chat_message", new=AsyncMock()))
        self.local = self.add("池袋麵屋青", "ラーメン", "池袋", comments="魚介湯頭", keywords=["拉麵"])
        self.italian = self.add("神保町オリーブ", "イタリアン", "神保町", comments="手工義大利麵")

    def add(self, name, category, area, **overrides):
        values = dict(name=name, category=category, area=area, comments="", keywords=[],
                      tabelog_url=f"https://tabelog.com/tokyo/{len(self.db.all()) + 1}/",
                      google_maps_url=f"https://maps.google.com/?q={name}",
                      source_channel_id=111, source_message_id=222, created_by="offline-test")
        values.update(overrides)
        return self.db.get(self.db.add_restaurant(**values))

    def message(self, content, *, user=1, channel=10, mention=True, reference=None, sent_id=100):
        status = SimpleNamespace(id=sent_id, edit=AsyncMock())
        return SimpleNamespace(
            author=SimpleNamespace(id=user, bot=False), channel=SimpleNamespace(id=channel),
            guild=None, content=("<@999> " if mention else "") + content,
            mentions=[self.bot_user] if mention else [],
            reference=SimpleNamespace(message_id=reference) if reference is not None else None,
            reply=AsyncMock(return_value=status), status=status,
        )

    def turn(self, content, **kwargs):
        message = self.message(content, **kwargs)
        asyncio.run(self.bot.on_message(message))
        return message

    def context(self, user=1, channel=10, **kwargs):
        return self.bot.load_recommendation_context(user, channel, **kwargs)

    def build(self, request):
        return self.bot.build_recommendation_response(request)

    def assert_safe(self, text):
        self.assertLessEqual(len(text), 1900)
        for word in ("資料庫", "候選", "我理解成", "從 DB", "ID ", "????", "\ufffd"):
            self.assertNotIn(word, text)

    def test_reply_without_mention_carries_area_and_bot_message_id(self):
        first = self.turn("池袋 有推薦的餐廳嗎", sent_id=100)
        self.assertEqual(self.context().bot_message_id, 100)
        self.assertIn("今天比較想吃哪一種料理", first.status.edit.call_args.kwargs["content"])
        second = self.turn("想吃披薩", mention=False, reference=100, sent_id=101)
        context = self.context()
        self.assertEqual(context.intent["area_terms"], ["池袋"])
        self.assertIn("披薩", context.intent["food_terms"])
        self.assertEqual(context.bot_message_id, 101)
        text = second.status.edit.call_args.kwargs["content"]
        self.assertIn("池袋尚未記錄", text)
        self.assertIn(self.local.name, text)
        self.assertIn(self.italian.name, text)
        self.assertIn("僅記錄為義大利料理，未記錄有供應披薩", text)
        self.assert_safe(text)
        for call in (second.reply.call_args, second.status.edit.call_args):
            self.assertEqual(call.kwargs["allowed_mentions"].to_dict(), {"parse": []})

    def test_mention_context_expires_at_ttl(self):
        with patch.object(self.bot.time, "monotonic", return_value=1000):
            self.turn("找 池袋 晚餐")
        with patch.object(self.bot.time, "monotonic", return_value=1599):
            self.turn("想吃披薩")
            self.assertEqual(self.context().intent["area_terms"], ["池袋"])
        with patch.object(self.bot.time, "monotonic", return_value=2199):
            message = self.turn("想吃披薩")
            self.assertEqual(self.context().intent["area_terms"], [])
            self.assertNotIn("池袋尚未記錄", message.status.edit.call_args.kwargs["content"])

    def test_expired_reply_without_mention_is_ordinary_chat(self):
        with patch.object(self.bot.time, "monotonic", return_value=0):
            self.turn("找 池袋 晚餐")
        with patch.object(self.bot.time, "monotonic", return_value=600):
            message = self.turn("想吃披薩", mention=False, reference=100)
            message.reply.assert_not_awaited()
            self.assertIsNone(self.context())

    def test_other_user_or_channel_does_not_inherit(self):
        self.turn("找 池袋 晚餐")
        other = self.turn("想吃披薩", user=2)
        self.assertEqual(self.context(user=2).intent["area_terms"], [])
        self.assertNotIn("池袋尚未記錄", other.status.edit.call_args.kwargs["content"])
        self.turn("想吃披薩", channel=20)
        self.assertEqual(self.context(channel=20).intent["area_terms"], [])
        self.assertEqual(self.context().intent["area_terms"], ["池袋"])

    def test_other_user_reply_without_mention_is_ignored(self):
        self.turn("找 池袋 晚餐")
        message = self.turn("想吃披薩", user=2, mention=False, reference=100)
        message.reply.assert_not_awaited()
        self.assertIsNone(self.context(user=2))

    def test_unrelated_channel_chat_and_reply_are_not_hijacked(self):
        self.turn("找 池袋 晚餐")
        for reference in (None, 456):
            with self.subTest(reference=reference):
                message = self.turn("想吃披薩", mention=False, reference=reference)
                message.reply.assert_not_awaited()
        message = self.turn("想吃披薩", reference=456)
        self.assertNotIn("池袋尚未記錄", message.status.edit.call_args.kwargs["content"])

    def test_merge_replaces_area_food_and_preserves_other_constraints(self):
        previous = self.bot.RecommendationContext("池袋 拉麵", {
            "area_terms": ["池袋"], "food_terms": ["拉麵"], "scene_terms": ["約會"],
            "budget": 1500, "exclude_terms": ["燒肉"], "expanded_terms": ["拉麵"],
        }, 100, 0)
        new = self.bot.sanitize_recommendation_intent(self.bot.rule_based_recommendation_intent("神保町 想吃披薩"))
        text, intent = self.bot.merge_followup_request(previous, "神保町 想吃披薩", new)
        self.assertEqual(intent["area_terms"], ["神保町"])
        self.assertEqual(intent["food_terms"], ["披薩"])
        self.assertEqual(intent["scene_terms"], ["約會"])
        self.assertEqual(intent["budget"], 1500)
        self.assertEqual(intent["exclude_terms"], ["燒肉"])
        self.assertNotIn("池袋", text)
        self.assertNotIn("拉麵", text)
        self.assertNotIn("拉麵", intent["expanded_terms"])
        new["budget"] = 2000
        self.assertEqual(self.bot.merge_followup_request(previous, "神保町 想吃披薩 2000", new)[1]["budget"], 2000)
        _, local_only = self.bot.merge_followup_request(previous, "想吃披薩 2000 不要咖哩", {})
        self.assertEqual(local_only["budget"], 2000)
        self.assertIn("咖哩", local_only["exclude_terms"])

    def test_no_explicit_area_cannot_be_overridden_by_ai_memory_guess(self):
        self.turn("找 池袋 晚餐")
        prior = self.context()
        guessed = {"area_terms": ["神保町"], "food_terms": ["披薩"], "budget": None}
        _, merged = self.bot.merge_followup_request(prior, "想吃披薩", guessed)
        self.assertEqual(merged["area_terms"], ["池袋"])

    def test_inference_receives_merged_request_and_new_area_replaces_old(self):
        self.turn("找 池袋 拉麵")
        with patch.object(self.bot, "infer_recommendation_intent", wraps=self.bot.infer_recommendation_intent) as infer:
            # An explicit reply allows changing cuisine even without a preceding question.
            self.turn("想吃披薩", mention=False, reference=100)
            request = infer.call_args.args[0]
            self.assertIn("池袋", request)
            self.assertIn("披薩", request)
            self.assertNotIn("拉麵", request)
        self.turn("神保町")
        self.assertEqual(self.context().intent["area_terms"], ["神保町"])
        self.assertIn("披薩", self.context().intent["food_terms"])

    def test_standalone_new_area_and_constraints_start_new_topic(self):
        self.turn("找 池袋 晚餐 1500 不要燒肉")
        self.assertTrue(self.context().awaiting_followup)
        with patch.object(self.bot, "merge_followup_request", wraps=self.bot.merge_followup_request) as merge:
            self.turn("神保町 想吃披薩，預算 2000，有推薦的餐廳嗎？")
        merge.assert_not_called()
        intent = self.context().intent
        self.assertEqual(intent["area_terms"], ["神保町"])
        self.assertEqual(intent["budget"], 2000)
        self.assertEqual(intent["scene_terms"], [])
        self.assertEqual(intent["exclude_terms"], [])

    def test_unrelated_complete_question_does_not_structurally_merge(self):
        for text in ("明天想和朋友吃披薩，有推薦的餐廳嗎？", "讀書會有推薦的參考書嗎？"):
            with self.subTest(text=text):
                self.turn("找 池袋 晚餐")
                with patch.object(self.bot, "merge_followup_request", wraps=self.bot.merge_followup_request) as merge, patch.object(
                    self.bot, "infer_recommendation_intent", wraps=self.bot.infer_recommendation_intent,
                ) as infer:
                    self.turn(text)
                merge.assert_not_called()
                self.assertNotIn("池袋", infer.call_args.args[0])
                self.assertEqual(self.context().intent["area_terms"], [])

    def test_short_answer_requires_last_bot_question(self):
        self.turn("找 池袋 拉麵")
        self.assertFalse(self.context().awaiting_followup)
        self.assertIn("?q=", self.context().last_bot_reply)  # Maps URL is not a question.
        self.turn("想吃披薩")
        self.assertEqual(self.context().intent["area_terms"], [])
        self.turn("找 池袋 晚餐")
        first = self.context()
        self.assertTrue(first.awaiting_followup)
        self.assertIn("？", first.last_bot_reply)
        self.turn("想吃披薩")
        self.assertEqual(self.context().intent["area_terms"], ["池袋"])
        self.assertIn("披薩", self.context().intent["food_terms"])
        self.assertEqual(self.context().request, "想吃披薩")

    def test_short_answer_classifier_rejects_complete_or_unrelated_messages(self):
        for text in ("披薩", "想吃披薩", "ピザ食べたい", "want pizza", "神保町", "2000"):
            with self.subTest(text=text):
                self.assertTrue(self.bot.is_short_followup_answer(text))
        for text in ("神保町 想吃披薩", "想吃披薩 2000", "想吃披薩嗎？", "幫我推薦披薩", "我想了解披薩的歷史"):
            with self.subTest(text=text):
                self.assertFalse(self.bot.is_short_followup_answer(text))

    def test_intervening_addressed_request_consumes_old_question(self):
        self.turn("找 池袋 晚餐")
        self.assertTrue(self.context().awaiting_followup)
        self.turn("這是完全不同的搜尋關鍵字")
        self.assertFalse(self.context().awaiting_followup)
        self.turn("想吃披薩")
        self.assertEqual(self.context().intent["area_terms"], [])

    def test_empty_request_saves_followup_question(self):
        first = self.turn("推薦")
        self.assertTrue(self.context().awaiting_followup)
        self.assertEqual(self.context().last_bot_reply, first.reply.call_args.args[0])
        self.turn("披薩")
        self.assertIn("披薩", self.context().intent["food_terms"])

    def test_previous_exchange_reaches_inference_and_ranker_for_new_topic(self):
        first = self.turn("找 池袋 晚餐")
        prior = self.context()
        expected = {"user_request": "找 池袋 晚餐",
                    "bot_reply": first.status.edit.call_args.kwargs["content"],
                    "parsed_constraints": prior.intent}
        create = self.openai.return_value.chat.completions.create
        create.reset_mock()
        self.turn("神保町 義大利麵，有推薦的餐廳嗎？", reference=456)
        calls = create.call_args_list
        self.assertEqual(len(calls), 2)
        for call in calls:
            messages = call.kwargs["messages"]
            payload = json.loads(messages[1]["content"])
            self.assertEqual(payload["previous_exchange"], expected)
            self.assertNotIn("池袋", payload["request"])
            self.assertIn("新話題", messages[0]["content"])
            self.assertIn("忽略 previous_exchange", messages[0]["content"])
            self.assertIn("只使用新訊息", messages[0]["content"])
            self.assertIn("不能替代 previous_exchange", messages[0]["content"])
        self.assertIn("interpreted_intent", json.loads(calls[1].kwargs["messages"][1]["content"]))

    def test_previous_exchange_is_not_shared_or_reused_after_expiry(self):
        create = self.openai.return_value.chat.completions.create
        with patch.object(self.bot.time, "monotonic", return_value=1000):
            self.turn("找 池袋 晚餐")
        for kwargs, now in (({"user": 2}, 1001), ({"channel": 20}, 1001), ({}, 1600)):
            with self.subTest(kwargs=kwargs), patch.object(self.bot.time, "monotonic", return_value=now):
                create.reset_mock()
                self.turn("神保町 義大利麵推薦", **kwargs)
                for call in create.call_args_list:
                    payload = json.loads(call.kwargs["messages"][1]["content"])
                    self.assertIsNone(payload["previous_exchange"])

    def test_slash_recommendation_receives_previous_exchange_without_structural_merge(self):
        self.turn("找 池袋 晚餐")
        expected = self.bot.previous_recommendation_exchange(self.context())
        create = self.openai.return_value.chat.completions.create
        create.reset_mock()
        interaction = SimpleNamespace(
            guild_id=None, channel_id=10, user=SimpleNamespace(id=1),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=501))),
        )
        with patch.object(self.bot, "merge_followup_request", wraps=self.bot.merge_followup_request) as merge:
            asyncio.run(self.bot.send_recommendation_interaction(interaction, "神保町 義大利麵"))
        merge.assert_not_called()
        self.assertEqual(len(create.call_args_list), 2)
        for call in create.call_args_list:
            payload = json.loads(call.kwargs["messages"][1]["content"])
            self.assertEqual(payload["previous_exchange"], expected)
            self.assertEqual(payload["request"], "神保町 義大利麵")
        self.assertEqual(self.context().intent["area_terms"], ["神保町"])
        self.assertEqual(self.context().last_bot_reply, interaction.followup.send.call_args.args[0])

    def test_explicit_new_constraints_override_ai_previous_exchange_guess(self):
        self.turn("找 池袋 晚餐 1500")
        stale_guess = {"area_terms": ["池袋"], "food_terms": ["拉麵"], "budget": 1500}
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(stale_guess)))])
        with patch.object(self.bot, "create_chat_completion", return_value=response):
            self.turn("神保町 想吃披薩，預算 2000，有推薦嗎？")
        intent = self.context().intent
        self.assertEqual(intent["area_terms"], ["神保町"])
        self.assertEqual(intent["food_terms"], ["披薩"])
        self.assertEqual(intent["budget"], 2000)

    def test_reset_phrases_clear_only_current_user_channel_without_search(self):
        phrases = {
            "換個話題": "已清除", "新話題": "已清除", "重新開始": "已清除",
            "重置對話": "已清除", "清除上下文": "已清除", "忘記前面": "已清除",
            "請換個話題，我們重新來": "已清除", "重來": "已清除",
            "新しい話題": "リセット", "話題を変えて": "リセット", "最初から": "リセット",
            "リセットしてください": "リセット", "NEW TOPIC": "Cleared",
            "Please START OVER": "Cleared", "new topic: pizza": "Cleared",
        }
        self.turn("找 池袋 晚餐", user=2)
        self.turn("找 池袋 晚餐", channel=20)
        for phrase, confirmation in phrases.items():
            for mention in (True, False):
                with self.subTest(phrase=phrase, mention=mention):
                    self.turn("找 池袋 晚餐")
                    with patch.object(self.bot, "send_recommendations", new=AsyncMock()) as recommend, patch.object(
                        self.bot, "send_search_results", new=AsyncMock(),
                    ) as search, patch.object(self.bot, "create_chat_completion") as api:
                        message = self.turn(phrase, mention=mention, reference=None if mention else 100)
                    self.assertIsNone(self.context())
                    self.assertIn(confirmation, message.reply.call_args.args[0])
                    recommend.assert_not_awaited()
                    search.assert_not_awaited()
                    api.assert_not_called()
                    self.assertIsNotNone(self.context(user=2))
                    self.assertIsNotNone(self.context(channel=20))
                    self.turn("想吃披薩")
                    self.assertEqual(self.context().intent["area_terms"], [])

    def test_reset_detection_does_not_match_incidental_or_negated_text(self):
        for text in ("不要重新開始", "請不要換個話題", 'What does "new topic" mean?',
                     "想吃重來拉麵", "new topical menu", "don't start over"):
            with self.subTest(text=text):
                self.assertFalse(self.bot.is_recommendation_reset(text))

    def test_public_reset_slash_command_clears_current_user_channel(self):
        self.turn("找 池袋 晚餐")
        self.turn("找 池袋 晚餐", user=2)
        self.turn("找 池袋 晚餐", channel=20)
        command = self.bot.client.tree.get_command("reset")
        self.assertIsNotNone(command)
        self.assertIsNone(command.default_permissions)
        self.assertFalse(command.guild_only)
        interaction = SimpleNamespace(user=SimpleNamespace(id=1), channel_id=10,
                                      response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(self.bot, "create_chat_completion") as api:
            asyncio.run(command.callback(interaction))
        self.assertIsNone(self.context())
        self.assertIsNotNone(self.context(user=2))
        self.assertIsNotNone(self.context(channel=20))
        self.assertIn("已清除", interaction.response.send_message.call_args.args[0])
        api.assert_not_called()
        self.turn("想吃披薩")
        self.assertEqual(self.context().intent["area_terms"], [])
        # Reset is idempotent even when the caller has no saved exchange.
        self.bot.clear_recommendation_context(1, 10)
        asyncio.run(command.callback(interaction))

    def test_japanese_followup_preserves_language_with_inherited_scene(self):
        self.turn("池袋の夕方にrecommend")
        self.assertIn("晚餐", self.context().intent["scene_terms"])
        message = self.turn("ピザ食べたい", mention=False, reference=100)
        text = message.status.edit.call_args.kwargs["content"]
        self.assertIn("池袋ではピザのお店はまだ記録がない", text)
        self.assertNotIn("我會先選", text)
        self.assertEqual(text.count("？"), 1)

    def test_japanese_followup_ranker_uses_original_language(self):
        self.add("池袋ピッツァ", "イタリアン", "池袋", keywords=["ピザ"])
        self.turn("池袋の夕方にrecommend")
        self.turn("ピザ食べたい")
        create = self.openai.return_value.chat.completions.create
        payload = json.loads(create.call_args.kwargs["messages"][1]["content"])
        self.assertEqual(payload["response_language"], "日本語")

    def test_missing_bot_message_id_still_allows_mention_context(self):
        self.turn("找 池袋 晚餐", sent_id=None)
        message = self.turn("披薩")
        self.assertEqual(self.context().intent["area_terms"], ["池袋"])
        self.assertIn("池袋尚未記錄", message.status.edit.call_args.kwargs["content"])

    def test_slash_recommendation_can_start_reply_context(self):
        interaction = SimpleNamespace(
            guild_id=None, channel_id=10, user=SimpleNamespace(id=1),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=501))),
        )
        asyncio.run(self.bot.send_recommendation_interaction(interaction, "池袋 晚餐"))
        self.assertEqual(self.context().bot_message_id, 501)
        message = self.turn("想吃披薩", mention=False, reference=501)
        self.assertIn("池袋尚未記錄", message.status.edit.call_args.kwargs["content"])

    def test_reply_with_no_saved_context_remains_ordinary_chat(self):
        message = self.turn("想吃披薩", mention=False, reference=100)
        message.reply.assert_not_awaited()
        self.assertIsNone(self.context())

    def test_context_cap_eviction_expiry_and_copy_isolation(self):
        intent = {"area_terms": ["池袋"], "food_terms": [], "scene_terms": [],
                  "budget": 1500, "exclude_terms": ["燒肉"]}
        with patch.object(self.bot, "FOLLOWUP_CONTEXT_CAP", 2):
            for user in (1, 2, 3):
                self.bot.remember_recommendation_context(user, 10, "池袋", intent, now=user)
            self.assertIsNone(self.context(user=1, now=3))
            intent["area_terms"].append("神保町")
            saved = self.context(user=2, now=3)
            self.assertEqual(saved.intent["area_terms"], ["池袋"])
            saved.intent["area_terms"].clear()
            self.assertEqual(self.context(user=2, now=3).intent["area_terms"], ["池袋"])
            self.assertIsNone(self.context(user=2, now=602))

    def test_pizza_and_italian_synonyms_zh_ja_en(self):
        for term in ("披薩", "披萨", "比薩", "pizza", "PIZZA", "ピザ", "ピッツァ",
                     "義式", "義大利", "意大利", "italian", "パスタ"):
            with self.subTest(term=term):
                self.assertIn(normalize_search_text("イタリアン"), self.bot.recommendation_synonyms(term))
                self.assertIn(normalize_search_text("イタリアン"), self.bot.recommendation_tokens(f"想吃{term}"))

    def test_food_wish_routes_to_recommendation_not_search(self):
        for text in ("想吃披薩", "想吃拉麵", "ピザ食べたい", "want pizza"):
            with self.subTest(text=text), patch.object(self.bot, "send_recommendations", new=AsyncMock()) as recommend, patch.object(
                self.bot, "send_search_results", new=AsyncMock(),
            ) as search:
                self.turn(text)
                recommend.assert_awaited_once()
                search.assert_not_awaited()

    def test_bare_ramen_keeps_search_results_ui_without_context(self):
        message = self.turn("拉麵")
        self.assertIsInstance(message.reply.call_args.kwargs["view"], self.bot.SearchResultsView)
        message.status.edit.assert_not_awaited()
        self.assertIsNone(self.context())

    def test_bare_food_search_no_match_falls_through(self):
        message = self.turn("pizza")
        text = message.status.edit.call_args.kwargs["content"]
        self.assertIn("尚未記錄", text)
        self.assertIn(self.italian.name, text)
        self.assertNotIn("目前沒有找到「pizza」", text)

    def test_no_match_uses_real_local_and_related_cuisine_only(self):
        text = self.build("池袋 想吃披薩")
        self.assertIn("池袋尚未記錄符合「披薩」", text)
        self.assertIn(self.local.name, text)
        self.assertIn(self.italian.name, text)
        self.assertIn("神保町", text)
        self.assertIn("未記錄有供應披薩", text)
        self.assertEqual(text.count("我會先選："), 1)
        self.assertLessEqual(text.count("也可以選："), 2)
        self.assertEqual(text.count("？"), 1)
        self.assert_safe(text)
        # Any displayed shop heading must come from this temporary DB.
        import re
        names = re.findall(r"\*\*(.+?)\*\*", text)
        self.assertTrue(set(names) <= {r.name for r in self.db.all()})

    def test_italian_in_requested_area_is_still_only_a_pizza_alternative(self):
        self.add("池袋オリーブ", "イタリアン", "池袋")
        text = self.build("池袋 想吃披薩")
        self.assertIn("池袋尚未記錄符合「披薩」", text)
        self.assertIn("未記錄有供應披薩", text)

    def test_recorded_pizza_is_an_exact_match_and_does_not_claim_no_match(self):
        pizza = self.add("池袋ピッツァ", "イタリアン", "池袋", keywords=["ピザ"])
        for index in range(12):
            self.add(f"池袋義式料理{index}", "イタリアン", "池袋")
        text = self.build("池袋 想吃披薩")
        self.assertIn(pizza.name, text)
        self.assertNotIn("池袋尚未記錄", text)
        self.assertNotIn("未記錄有供應披薩", text)
        candidates = self.bot.recommendation_candidates("池袋 想吃披薩", limit=1)
        self.assertEqual([r.id for r in candidates], [pizza.id])

    def test_other_area_only_empty_db_and_japanese_no_match(self):
        text = self.build("新宿 想吃披薩")  # No saved shops in this canonical area.
        self.assertIn("新宿尚未記錄", text)
        self.assertIn(self.italian.name, text)
        text = self.build("池袋でピザ食べたい")
        self.assertIn("池袋ではピザのお店はまだ記録がない", text)
        self.assertIn("イタリア料理の記録のみ", text)
        self.assertNotIn("我會先選", text)
        with patch.object(self.bot.db, "all", return_value=[]):
            text = self.build("想吃披薩")
        self.assertIn("尚未記錄", text)
        self.assertIn("？", text)
        self.assertNotIn("**", text)

    def test_alternatives_respect_exclusions_and_have_at_most_three_shops(self):
        for index in range(5):
            self.add(f"池袋咖哩{index}", "カレー", "池袋")
        text = self.build("池袋 想吃披薩 不要拉麵")
        self.assertNotIn(f"**{self.local.name}**", text)
        self.assertIn(self.italian.name, text)
        self.assertLessEqual(text.count("**"), 6)

    def test_single_turn_excluded_cuisine_is_not_a_requested_cuisine(self):
        curry = self.add("池袋咖哩", "カレー", "池袋")
        message = self.turn("找 池袋 不要拉麵")
        intent = self.context().intent
        self.assertEqual(intent["food_terms"], [])
        self.assertIn("拉麵", intent["exclude_terms"])
        text = message.status.edit.call_args.kwargs["content"]
        self.assertIn(curry.name, text)
        self.assertNotIn(self.local.name, text)
        self.assertNotIn("尚未記錄", text)
        self.assert_safe(text)

    def test_exclusion_followup_removes_inherited_food_even_with_ai_conflict(self):
        curry = self.add("池袋咖哩", "カレー", "池袋")
        self.turn("找 池袋 拉麵")
        self.assertIn("拉麵", self.context().intent["food_terms"])
        _, merged = self.bot.merge_followup_request(
            self.context(), "不要拉麵", {"food_terms": ["ラーメン"]},
        )
        self.assertEqual(merged["food_terms"], [])
        self.assertEqual(merged["expanded_terms"], [])
        guessed = {"food_terms": ["拉麵", "ラーメン"]}
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(guessed)))])
        with patch.object(self.bot, "create_chat_completion", return_value=response):
            message = self.turn("不要拉麵", mention=False, reference=100)
        intent = self.context().intent
        self.assertEqual(intent["area_terms"], ["池袋"])
        self.assertEqual(intent["food_terms"], [])
        self.assertIn("拉麵", intent["exclude_terms"])
        text = message.status.edit.call_args.kwargs["content"]
        self.assertIn(curry.name, text)
        self.assertNotIn(self.local.name, text)
        self.assertNotIn("尚未記錄", text)

    def test_positive_food_followup_drops_inherited_exclusion_and_synonyms(self):
        self.add("池袋咖哩", "カレー", "池袋")
        for excluded, wanted in (("拉麵", "想吃拉麵"), ("拉麵", "ラーメン食べたい"),
                                 ("ラーメン", "想吃拉麵")):
            with self.subTest(excluded=excluded, wanted=wanted):
                self.bot.clear_recommendation_context(1, 10)
                self.turn(f"找 池袋 不要{excluded}")
                previous = self.context()
                self.assertEqual(previous.intent["food_terms"], [])
                self.assertIn(excluded, previous.intent["exclude_terms"])
                # A stale inference echo must not restore the previous exclusion.
                _, merged = self.bot.merge_followup_request(
                    previous, wanted, {"exclude_terms": [excluded, "燒肉"]},
                )
                self.assertEqual(merged["area_terms"], ["池袋"])
                self.assertTrue(merged["food_terms"])
                self.assertEqual(merged["exclude_terms"], ["燒肉"])
                message = self.turn(wanted, mention=False, reference=100)
                intent = self.context().intent
                self.assertEqual(intent["area_terms"], ["池袋"])
                self.assertIn("ラーメン" if "食べたい" in wanted else "拉麵", intent["food_terms"])
                self.assertEqual(intent["exclude_terms"], [])
                text = message.status.edit.call_args.kwargs["content"]
                self.assertIn(self.local.name, text)
                self.assertNotIn("尚未記錄", text)
                self.assertNotIn("まだ記録がない", text)

    def test_send_path_ignores_stale_ai_exclusion_after_positive_food_reply(self):
        self.add("池袋咖哩", "カレー", "池袋")
        self.turn("找 池袋 不要拉麵")
        self.assertIn("拉麵", self.context().intent["exclude_terms"])
        stale = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"exclude_terms": ["拉麵"]}),
        ))])
        with patch.object(self.openai.return_value.chat.completions, "create", return_value=stale) as create:
            message = self.turn("想吃拉麵", mention=False, reference=100)
        inference_payload = json.loads(create.call_args_list[0].kwargs["messages"][1]["content"])
        self.assertIn("拉麵", inference_payload["previous_exchange"]["parsed_constraints"]["exclude_terms"])
        intent = self.context().intent
        self.assertEqual(intent["area_terms"], ["池袋"])
        self.assertEqual(intent["food_terms"], ["拉麵"])
        self.assertEqual(intent["exclude_terms"], [])
        text = message.status.edit.call_args.kwargs["content"]
        self.assertIn(self.local.name, text)
        self.assertNotIn("尚未記錄", text)
        self.assertNotIn("まだ記録がない", text)

    def test_no_match_helpers_do_not_reinterpret_exclusions_as_food_wishes(self):
        request = "池袋 不要拉麵"
        # Defend even against a caller supplying a conflicting AI intent.
        intent = {"area_terms": ["池袋"], "food_terms": ["ラーメン"], "exclude_terms": ["拉麵"]}
        for japanese in (False, True):
            text = self.bot.recommendation_no_match_notice(request, intent, japanese=japanese)
            self.assertNotIn("拉麵", text)
            self.assertNotIn("ラーメン", text)
        alternatives = self.bot.no_match_alternatives(request, intent)
        self.assertNotIn(self.local.id, [r.id for r in alternatives])
        self.assertIn(self.italian.id, [r.id for r in alternatives])

    def test_excluded_pizza_does_not_require_a_recorded_pizza_shop(self):
        text = self.build("池袋 不要披薩")
        self.assertIn(self.local.name, text)
        self.assertNotIn("尚未記錄", text)
        self.assertNotIn("未記錄有供應披薩", text)

    def test_bot_reply_without_constraints_is_remembered_as_chat(self):
        self.turn("找 池袋 晚餐")
        previous = self.context()
        for mention in (False, True):
            with self.subTest(mention=mention), patch.object(self.bot, "create_chat_completion") as api, patch.object(
                self.bot, "send_recommendations", new=AsyncMock(),
            ) as recommend:
                self.memory.reset_mock()
                message = self.turn("謝謝你喔", mention=mention, reference=100)
                self.assertIsNone(self.bot.message_followup_context(message))
                self.memory.assert_awaited_once_with(message)
                message.reply.assert_not_awaited()
                message.status.edit.assert_not_awaited()
                api.assert_not_called()
                recommend.assert_not_awaited()
                self.assertEqual(self.context(), previous)

    def test_japanese_exclusion_uses_noun_before_marker(self):
        curry = self.add("池袋カレー", "カレー", "池袋")
        for phrase in ("ラーメン以外がいい", "ラーメンなしで", "ラーメン抜きで", "ラーメンはなしで"):
            with self.subTest(phrase=phrase):
                request = f"池袋 {phrase}"
                intent = self.bot.rule_based_recommendation_intent(request)
                self.assertEqual(intent["exclude_terms"], ["ラーメン"])
                self.assertEqual(intent["food_terms"], [])
                text = self.build(request)
                self.assertIn(curry.name, text)
                self.assertNotIn(self.local.name, text)
                self.assertNotIn("まだ記録がない", text)

    def test_spaceless_japanese_exclusion_keeps_area_and_other_local_shops(self):
        curry = self.add("池袋カレー", "カレー", "池袋")
        for request in ("池袋ラーメン以外", "池袋 ラーメン以外", "池袋ラーメンなし",
                        "池袋ラーメン抜き", "池袋ラーメンはなし"):
            with self.subTest(request=request):
                intent = self.bot.rule_based_recommendation_intent(request)
                self.assertEqual(intent["area_terms"], ["池袋"])
                self.assertEqual(intent["exclude_terms"], ["ラーメン"])
                self.assertEqual(intent["food_terms"], [])
                text = self.build(request)
                self.assertIn(curry.name, text)
                self.assertNotIn(self.local.name, text)
                self.assertNotIn("まだ記録がない", text)
                self.assertNotIn("尚未記錄", text)

    def test_generic_japanese_exclusion_strips_leading_area(self):
        curry = self.add("池袋カレー", "カレー", "池袋")
        self.assertNotIn("餃子", self.bot.food_words_in_text("池袋餃子以外"))
        for request in ("池袋餃子以外", "池袋餃子なし", "池袋餃子抜き", "池袋餃子はなし"):
            with self.subTest(request=request):
                intent = self.bot.rule_based_recommendation_intent(request)
                self.assertEqual(intent["area_terms"], ["池袋"])
                self.assertEqual(intent["exclude_terms"], ["餃子"])
                self.assertEqual(intent["food_terms"], [])
                text = self.build(request)
                self.assertIn(self.local.name, text)
                self.assertIn(curry.name, text)
                self.assertNotIn("まだ記録がない", text)
                self.assertNotIn("尚未記錄", text)

    def test_chinese_exclusion_stops_before_next_food_wish(self):
        pizza = self.add("池袋ピッツァ", "イタリアン", "池袋", keywords=["ピザ"])
        request = "池袋 不要拉麵想吃披薩"
        intent = self.bot.rule_based_recommendation_intent(request)
        self.assertEqual(intent["exclude_terms"], ["拉麵"])
        self.assertIn("披薩", intent["food_terms"])
        text = self.build(request)
        self.assertIn(pizza.name, text)
        self.assertNotIn(self.local.name, text)
        self.assertNotIn("尚未記錄", text)

    def test_short_food_answer_accepts_chinese_particles(self):
        self.assertTrue(self.bot.is_short_followup_answer("披薩吧"))
        self.assertTrue(self.bot.is_short_followup_answer("披薩的"))
        self.turn("找 池袋 晚餐")
        self.turn("披薩吧")
        self.assertEqual(self.context().intent["area_terms"], ["池袋"])
        self.assertIn("披薩", self.context().intent["food_terms"])


if __name__ == "__main__":
    unittest.main()

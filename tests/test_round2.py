"""Offline model migration regressions; no live API, bot login, or real DB."""

import ast
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
from unittest.mock import MagicMock, patch

import httpx
from openai import APIStatusError, BadRequestError

import extractor
import openai_util
from openai_util import create_chat_completion, get_openai_model, get_reasoning_effort


ROOT = Path(__file__).resolve().parents[1]


def fake_response(content="{}"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def status_error(message, *, status=400, body=None, error_type=BadRequestError):
    response = httpx.Response(status, request=httpx.Request("POST", "https://offline.invalid"))
    return error_type(message, response=response, body=body)


class CompletionHelperTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.cache = patch.object(openai_util, "_REJECTED_PARAMETERS", {})
        self.cache.start()
        self.addCleanup(self.cache.stop)
        self.client = MagicMock()
        self.response = fake_response()
        self.create = self.client.chat.completions.create
        self.create.return_value = self.response

    def complete(self, **kwargs):
        return create_chat_completion(
            self.client, messages=[{"role": "user", "content": "Return JSON"}],
            max_completion_tokens=1536, **kwargs,
        )

    def test_defaults_and_completion_budget(self):
        self.assertEqual(get_openai_model(), "gpt-6-luna")
        self.assertEqual(get_reasoning_effort(), "none")
        self.assertIs(self.complete(temperature=0.1), self.response)
        kwargs = self.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-6-luna")
        self.assertEqual(kwargs["reasoning_effort"], "none")
        self.assertEqual(kwargs["max_completion_tokens"], 1536)
        self.assertEqual(kwargs["temperature"], 0.1)
        self.assertEqual(kwargs["timeout"], 20)
        self.assertNotIn("max_tokens", kwargs)

    def test_environment_normalization_and_blank_defaults(self):
        with patch.dict(os.environ, {"OPENAI_MODEL": "  custom-model ",
                                     "OPENAI_REASONING_EFFORT": " LOW "}):
            self.complete()
            self.assertEqual(self.create.call_args.kwargs["model"], "custom-model")
            self.assertEqual(self.create.call_args.kwargs["reasoning_effort"], "low")
        with patch.dict(os.environ, {"OPENAI_MODEL": " ", "OPENAI_REASONING_EFFORT": " "}):
            self.assertEqual(get_openai_model(), "gpt-6-luna")
            self.assertEqual(get_reasoning_effort(), "none")

    def test_higher_reasoning_omits_sampling_parameters(self):
        for effort in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=effort):
                self.complete(temperature=0.2, reasoning_effort=effort)
                kwargs = self.create.call_args.kwargs
                self.assertEqual(kwargs["reasoning_effort"], effort)
                for parameter in ("temperature", "top_p", "logprobs", "top_logprobs"):
                    self.assertNotIn(parameter, kwargs)

    def test_invalid_effort_warns_without_echoing_value(self):
        with patch.dict(os.environ, {"OPENAI_REASONING_EFFORT": "invalid-secret-value"}):
            with self.assertLogs("openai_util", level="WARNING") as logs:
                self.complete(temperature=0.1)
        self.assertEqual(self.create.call_args.kwargs["reasoning_effort"], "none")
        self.assertIn("temperature", self.create.call_args.kwargs)
        self.assertNotIn("invalid-secret-value", " ".join(logs.output))

    def test_explicit_overrides_and_passthrough(self):
        with patch.dict(os.environ, {"OPENAI_MODEL": "environment-model",
                                     "OPENAI_REASONING_EFFORT": "high"}):
            self.complete(model=" custom-model ", reasoning_effort=" NONE ",
                          response_format={"type": "json_object"}, timeout=12)
        kwargs = self.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "custom-model")
        self.assertEqual(kwargs["reasoning_effort"], "none")
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        self.assertEqual(kwargs["timeout"], 12)
        self.assertNotIn("temperature", kwargs)

    def test_unsupported_reasoning_retries_legacy_model(self):
        self.create.side_effect = [status_error(
            "Unsupported parameter: 'reasoning_effort'", body={
                "code": "unsupported_parameter", "param": "reasoning_effort",
                "message": "Unsupported parameter: 'reasoning_effort'",
            }), self.response]
        with self.assertLogs("openai_util", level="WARNING") as logs:
            self.assertIs(self.complete(model="gpt-4.1-mini", temperature=0.1), self.response)
        first, second = [call.kwargs for call in self.create.call_args_list]
        self.assertEqual(first["reasoning_effort"], "none")
        self.assertNotIn("reasoning_effort", second)
        self.assertEqual(second, {key: value for key, value in first.items() if key != "reasoning_effort"})
        self.assertIn("gpt-4.1-mini", logs.output[0])
        self.assertIn("reasoning_effort", logs.output[0])

    def test_unrecognized_request_argument_retries_without_reasoning(self):
        message = "Unrecognized request argument supplied: reasoning_effort"
        for error_type in (APIStatusError, BadRequestError):
            with self.subTest(error_type=error_type.__name__):
                openai_util._REJECTED_PARAMETERS.clear()
                self.create.reset_mock()
                error = status_error(message, error_type=error_type, body={"error": {
                    "message": message, "type": "invalid_request_error",
                    "param": None, "code": None,
                }})
                self.assertIsNone(error.param)
                self.assertIsNone(error.code)
                self.create.side_effect = [error, self.response]
                with self.assertLogs("openai_util", level="WARNING"):
                    self.assertIs(self.complete(model="gpt-4.1-mini"), self.response)
                self.assertEqual(self.create.call_count, 2)
                first, second = [call.kwargs for call in self.create.call_args_list]
                self.assertEqual(first["reasoning_effort"], "none")
                self.assertNotIn("reasoning_effort", second)
                self.assertEqual(second, {
                    key: value for key, value in first.items() if key != "reasoning_effort"
                })

    def test_unknown_and_unrecognized_argument_wording(self):
        for wording in (
            "Unrecognized argument", "Unknown argument", "Unknown request argument",
            "Unrecognized request parameter", "Unknown request parameter",
        ):
            with self.subTest(wording=wording):
                openai_util._REJECTED_PARAMETERS.clear()
                self.create.reset_mock()
                self.create.side_effect = [
                    status_error(f"{wording}: reasoning_effort"), self.response,
                ]
                with self.assertLogs("openai_util", level="WARNING"):
                    self.assertIs(self.complete(), self.response)
                self.assertEqual(self.create.call_count, 2)
                self.assertNotIn("reasoning_effort", self.create.call_args.kwargs)

    def test_rejected_parameters_are_cached_per_model(self):
        for parameter in ("reasoning_effort", "temperature", "response_format"):
            with self.subTest(parameter=parameter):
                openai_util._REJECTED_PARAMETERS.clear()
                self.create.reset_mock()
                self.create.side_effect = [
                    status_error(f"Unknown parameter: {parameter}"), self.response,
                ]
                options = {"temperature": 0.1, "response_format": {"type": "json_object"}}
                with self.assertLogs("openai_util", level="WARNING"):
                    self.assertIs(self.complete(model="gpt-4.1-mini", **options), self.response)
                self.assertEqual(self.create.call_count, 2)
                self.assertNotIn(parameter, self.create.call_args.kwargs)

                self.create.reset_mock()
                self.create.side_effect = None
                self.assertIs(self.complete(model=" gpt-4.1-mini ", **options), self.response)
                self.create.assert_called_once()
                self.assertNotIn(parameter, self.create.call_args.kwargs)

                self.create.reset_mock()
                self.assertIs(self.complete(model="other-model", **options), self.response)
                self.create.assert_called_once()
                self.assertIn(parameter, self.create.call_args.kwargs)

    def test_rejected_reasoning_restores_temperature(self):
        self.create.side_effect = [
            status_error("Unsupported parameter: reasoning_effort"), self.response,
        ]
        with self.assertLogs("openai_util", level="WARNING"):
            self.complete(model="gpt-4.1-mini", reasoning_effort="high", temperature=0.2)
        first, second = [call.kwargs for call in self.create.call_args_list]
        self.assertNotIn("temperature", first)
        self.assertNotIn("reasoning_effort", second)
        self.assertEqual(second["temperature"], 0.2)

        self.create.reset_mock()
        self.create.side_effect = None
        self.complete(model="gpt-4.1-mini", reasoning_effort="high", temperature=0.2)
        self.create.assert_called_once()
        self.assertNotIn("reasoning_effort", self.create.call_args.kwargs)
        self.assertEqual(self.create.call_args.kwargs["temperature"], 0.2)

    def test_unrelated_retry_errors_preserve_original_exception(self):
        for status in (429, 401, 500, 400):
            with self.subTest(status=status):
                openai_util._REJECTED_PARAMETERS.clear()
                self.create.reset_mock()
                error = status_error("Unrelated failure", status=status, error_type=APIStatusError)
                self.create.side_effect = [
                    status_error("Unsupported parameter: reasoning_effort"), error,
                ]
                with self.assertLogs("openai_util", level="WARNING"):
                    with self.assertRaises(APIStatusError) as raised:
                        self.complete()
                self.assertIs(raised.exception, error)
                self.assertEqual(self.create.call_count, 2)

    def test_unsupported_temperature_400_retries(self):
        self.create.side_effect = [status_error(
            "Unknown parameter: temperature", error_type=APIStatusError), self.response]
        with self.assertLogs("openai_util", level="WARNING"):
            self.assertIs(self.complete(temperature=0.1), self.response)
        first, second = [call.kwargs for call in self.create.call_args_list]
        self.assertEqual(second, {key: value for key, value in first.items() if key != "temperature"})

    def test_multiple_rejections_are_bounded(self):
        self.create.side_effect = [
            status_error("Unsupported parameter: reasoning_effort"),
            status_error("Unsupported parameter: temperature"),
            status_error("Unsupported parameter: temperature"),
        ]
        with self.assertLogs("openai_util", level="WARNING"):
            with self.assertRaisesRegex(RuntimeError, "gpt-6-luna.*temperature") as raised:
                self.complete(temperature=0.1)
        self.assertEqual(self.create.call_count, 3)
        self.assertIsInstance(raised.exception.__cause__, APIStatusError)

    def test_unrelated_errors_and_required_parameters_do_not_retry(self):
        for error in (
            status_error("Invalid messages"),
            status_error("Unsupported parameter: max_completion_tokens"),
            status_error("Unrecognized request argument supplied"),
            status_error("Unknown request argument: max_completion_tokens"),
            status_error("Unsupported parameter: temperature", status=429, error_type=APIStatusError),
        ):
            with self.subTest(error=str(error)):
                self.create.reset_mock()
                self.create.side_effect = error
                with self.assertRaises(APIStatusError) as raised:
                    self.complete(temperature=0.1)
                self.assertIs(raised.exception, error)
                self.create.assert_called_once()


class CallSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        directory = cls.stack.enter_context(tempfile.TemporaryDirectory())
        cls.stack.enter_context(patch.dict(os.environ, {
            "DB_PATH": str(Path(directory) / "test.sqlite3"),
            "DISCORD_TOKEN": "offline-test-token", "OPENAI_MODEL": "gpt-6-luna",
            "OPENAI_REASONING_EFFORT": "none",
        }))
        cls.stack.enter_context(patch("dotenv.load_dotenv"))
        cls.stack.enter_context(patch("openai.OpenAI"))
        previous = sys.modules.pop("bot", None)

        def restore_module():
            sys.modules.pop("bot", None)
            if previous is not None:
                sys.modules["bot"] = previous

        cls.stack.callback(restore_module)
        cls.bot = importlib.import_module("bot")
        cls.stack.callback(lambda: asyncio.run(cls.bot.client.close()))

    def test_bot_summary_uses_helper_and_parses_text(self):
        with patch.object(self.bot, "create_chat_completion", wraps=create_chat_completion) as helper:
            self.bot.openai_client.chat.completions.create.return_value = fake_response("  Likes ramen  ")
            messages = [SimpleNamespace(author_name="User", content="I like ramen")]
            self.assertEqual(self.bot.summarize_chat_memory("", messages), "Likes ramen")
        self.assertIs(helper.call_args.args[0], self.bot.openai_client)
        self.assertGreaterEqual(helper.call_args.kwargs["max_completion_tokens"], 1024)

    def test_bot_intent_json_and_api_failure_fallback(self):
        fallback = {"food_terms": ["ramen"]}
        with patch.object(self.bot, "recommendation_known_values", return_value={"areas": []}), \
             patch.object(self.bot, "rule_based_recommendation_intent", return_value=fallback), \
             patch.object(self.bot, "create_chat_completion", wraps=create_chat_completion) as helper:
            self.bot.openai_client.chat.completions.create.return_value = fake_response(
                json.dumps({"food_terms": ["sushi"]}))
            result = self.bot.infer_recommendation_intent("dinner")
            self.assertIn("sushi", result["food_terms"])
            self.assertGreaterEqual(helper.call_args.kwargs["max_completion_tokens"], 1536)
            self.bot.openai_client.chat.completions.create.side_effect = RuntimeError("offline failure")
            try:
                with patch("builtins.print"):
                    result = self.bot.infer_recommendation_intent("dinner")
                self.assertIn("ramen", result["food_terms"])
            finally:
                self.bot.openai_client.chat.completions.create.side_effect = None

    def test_extractor_uses_helper_and_parses_json(self):
        client = MagicMock()
        client.chat.completions.create.return_value = fake_response(json.dumps({
            "name": "Test restaurant", "category": "Ramen", "area": "Tokyo",
            "comments": "Good noodles", "keywords": ["noodles"], "reason": "Found name",
        }))
        with ExitStack() as stack:
            helper = stack.enter_context(patch.object(
                extractor, "create_chat_completion", wraps=create_chat_completion))
            for name, result in {
                "fetch_tabelog_title": None, "find_tabelog_url_by_search": None,
                "fetch_tabelog_price_info": extractor.PriceInfo(),
                "fetch_tabelog_business_hours": extractor.BusinessHoursInfo(),
                "fetch_tabelog_image_url": None,
            }.items():
                stack.enter_context(patch.object(extractor, name, return_value=result))
            result = extractor.extract_restaurant(client=client, model="custom-model", messages=[
                extractor.MessageSnippet(author="User", content="Test restaurant", attachment_urls=[]),
            ])
        self.assertEqual(result.name, "Test restaurant")
        self.assertEqual(result.comments, "Good noodles")
        self.assertEqual(result.keywords, ["noodles"])
        self.assertIs(helper.call_args.args[0], client)
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "custom-model")
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        self.assertGreaterEqual(kwargs["max_completion_tokens"], 1536)

    def test_all_call_sites_use_helper_and_new_budget_parameter(self):
        for filename, expected in (("bot.py", 3), ("extractor.py", 1)):
            tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
            calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
            helper_calls = [node for node in calls if isinstance(node.func, ast.Name)
                            and node.func.id == "create_chat_completion"]
            self.assertEqual(len(helper_calls), expected)
            for call in calls:
                self.assertNotIn("max_tokens", {keyword.arg for keyword in call.keywords})
                self.assertNotEqual(ast.unparse(call.func), "openai_client.chat.completions.create")
                self.assertNotEqual(ast.unparse(call.func), "client.chat.completions.create")
            for call in helper_calls:
                self.assertIn("max_completion_tokens", {keyword.arg for keyword in call.keywords})


if __name__ == "__main__":
    unittest.main()

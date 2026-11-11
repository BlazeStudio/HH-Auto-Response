"""Ошибки сервиса ИИ понятными словами и повтор запроса при обрыве связи. Сеть не нужна.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import ssl
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта

from hh_auto.ai import client as ai  # noqa: E402
from hh_auto.ai.client import (FREE_ROUTER, AiClient, AiError, explain_http_error,  # noqa: E402
                               explain_network_error, service_message)
from hh_auto.core.config import Ai  # noqa: E402

OPENROUTER = "https://openrouter.ai/api/v1"
# Ответы из обращения пользователя
GONE_FREE = json.dumps({"error": {"message": "This model is unavailable for free. The paid version is available now "
                                             "- use this slug instead: deepseek/deepseek-chat-v3-0324", "code": 404},
                        "user_id": "user_x"})
SECURITY_POLICY = '{ "success": false, "error": "Access denied by security policy." }'
SSL_EOF = urllib.error.URLError(ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation "
                                                   "of protocol (_ssl.c:1006)"))


class ServiceMessageTest(unittest.TestCase):
    def test_openai_style(self):
        self.assertEqual(service_message('{"error": {"message": "Invalid  key", "code": 401}}'), "Invalid key")

    def test_ollama_style(self):
        self.assertEqual(service_message('{"error": "model \'x\' not found"}'), "model 'x' not found")

    def test_foreign_answers(self):
        self.assertIsNone(service_message(SECURITY_POLICY))
        self.assertIsNone(service_message("<html><body>Access denied</body></html>"))
        self.assertIsNone(service_message(""))


class ExplainHttpErrorTest(unittest.TestCase):
    def explain(self, code, body, model="deepseek/deepseek-chat-v3-0324:free", free=None, url=OPENROUTER):
        return explain_http_error(code, body, url, "openrouter", model, free)

    def test_free_model_gone(self):
        text = self.explain(404, GONE_FREE, free=["google/gemma-4-31b-it:free", "deepseek/deepseek-chat-v3-0324:free"])
        self.assertIn("бесплатно больше нет", text)
        self.assertIn(FREE_ROUTER, text)
        self.assertIn("google/gemma-4-31b-it:free", text)
        self.assertNotIn("одну из бесплатных сейчас: deepseek", text)  # саму сломанную модель не советуем
        self.assertIn("Платная версия — «deepseek/deepseek-chat-v3-0324»", text)

    def test_unknown_model(self):
        body = json.dumps({"error": {"message": "foo/bar is not a valid model ID", "code": 400}})
        text = self.explain(400, body, model="foo/bar")
        self.assertIn("модели «foo/bar» на OpenRouter нет", text)
        self.assertNotIn("Платная версия", text)

    def test_privacy_settings(self):
        body = json.dumps({"error": {"message": "No endpoints found matching your data policy (Free model "
                                                "publication). Configure: https://openrouter.ai/settings/privacy"}})
        self.assertIn("openrouter.ai/settings/privacy", self.explain(404, body))

    def test_blocked_on_the_way(self):
        text = self.explain(403, SECURITY_POLICY)
        self.assertIn("не сам сервис ИИ", text)
        self.assertIn("Access denied by security policy", text)
        self.assertIn("VPN", text)

    def test_service_refused_region(self):
        body = json.dumps({"error": {"message": "This model is not available in your region.", "code": 403}})
        text = self.explain(403, body)
        self.assertNotIn("не сам сервис ИИ", text)
        self.assertIn("в вашей стране", text)

    def test_bad_key(self):
        body = json.dumps({"error": {"message": "No auth credentials found", "code": 401}})
        self.assertIn("openrouter.ai → Keys", self.explain(401, body))

    def test_no_money(self):
        body = json.dumps({"error": {"message": "Insufficient credits", "code": 402}})
        self.assertIn(FREE_ROUTER, self.explain(402, body, model="deepseek/deepseek-chat-v3-0324"))

    def test_free_rate_limit(self):
        body = json.dumps({"error": {"message": "Rate limit exceeded: free-models-per-day", "code": 429}})
        self.assertIn("лимит запросов в минуту и в сутки", self.explain(429, body, model=FREE_ROUTER))

    def test_other_service_keeps_raw_answer(self):
        text = explain_http_error(418, '{"error": {"message": "teapot"}}', "https://api.deepseek.com", "deepseek",
                                  "deepseek-chat")
        self.assertEqual(text, "сервис ИИ ответил HTTP 418: teapot")


class ExplainNetworkErrorTest(unittest.TestCase):
    def test_ssl_eof(self):
        text = explain_network_error(SSL_EOF, OPENROUTER)
        self.assertIn("соединение с openrouter.ai обрывается", text)
        self.assertIn("UNEXPECTED_EOF_WHILE_READING", text)

    def test_dns(self):
        error = urllib.error.URLError(OSError(11001, "getaddrinfo failed"))
        self.assertIn("не нашёлся адрес openrouter.ai", explain_network_error(error, OPENROUTER))

    def test_other(self):
        error = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        self.assertIn("нет связи с сервисом ИИ", explain_network_error(error, OPENROUTER))


ANSWER = json.dumps({"choices": [{"message": {"content": "работает"}}]}).encode()


class CompleteTest(unittest.TestCase):
    def client(self, model=""):
        return AiClient(Ai(provider="openrouter", api_key=" sk-or-test \n", model=model))

    def test_default_model_is_free_router(self):
        self.assertEqual(self.client().model, FREE_ROUTER)

    def test_retries_cut_connection_once(self):
        calls = []

        def read(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise SSL_EOF
            return ANSWER

        with mock.patch.object(ai, "_read_interruptible", read), mock.patch.object(ai, "_short_pause"):
            self.assertEqual(self.client().ping(), "работает")
        self.assertEqual(len(calls), 2)
        headers = {k.lower(): v for k, v in calls[0].header_items()}
        self.assertEqual(headers["authorization"], "Bearer sk-or-test")  # пробелы вокруг ключа не мешают
        self.assertTrue(headers["user-agent"].startswith("HH-Auto-Response/"))

    def test_cut_twice_explains(self):
        with mock.patch.object(ai, "_read_interruptible", side_effect=SSL_EOF), \
                mock.patch.object(ai, "_short_pause"):
            with self.assertRaises(AiError) as caught:
                self.client().ping()
        self.assertIn("обрывается", str(caught.exception))

    def test_gone_free_model_lists_free_ones(self):
        error = urllib.error.HTTPError(f"{OPENROUTER}/chat/completions", 404, "Not Found", {}, None)
        error.read = lambda: GONE_FREE.encode()
        client = self.client(model="deepseek/deepseek-chat-v3-0324:free")
        with mock.patch.object(ai, "_read_interruptible", side_effect=error), \
                mock.patch.object(AiClient, "free_models", return_value=["google/gemma-4-31b-it:free"]):
            with self.assertRaises(AiError) as caught:
                client.ping()
        self.assertIn("google/gemma-4-31b-it:free", str(caught.exception))
        self.assertIn(FREE_ROUTER, str(caught.exception))


if __name__ == "__main__":
    unittest.main()

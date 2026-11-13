"""Капча в Telegram (web/captcha.py) и вызовы Bot API (core/notify.py) — без сети: urlopen подменён.
Весь путь с браузером и формой капчи — tests/e2e_mock_hh.py, часть captcha_telegram."""

from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hh_auto.core import notify  # noqa: E402
from hh_auto.core.config import Config  # noqa: E402
from hh_auto.web import captcha  # noqa: E402


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _ok(result) -> _Response:
    return _Response(json.dumps({"ok": True, "result": result}).encode())


class TelegramCallTest(unittest.TestCase):
    def test_photo_is_multipart_with_caption(self):
        with mock.patch("urllib.request.urlopen", return_value=_ok({})) as urlopen:
            notify.telegram_call("123:secret", "sendPhoto", {"chat_id": "42", "caption": "Капча"},
                                 {"photo": ("captcha.png", b"\x89PNGdata", "image/png")})
        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/bot123:secret/sendPhoto"))
        self.assertIn("multipart/form-data; boundary=", request.headers["Content-type"])
        body = request.data
        self.assertIn(b'name="photo"; filename="captcha.png"', body)
        self.assertIn(b"\x89PNGdata", body)
        self.assertIn("Капча".encode(), body)

    def test_lists_are_json(self):
        with mock.patch("urllib.request.urlopen", return_value=_ok([])) as urlopen:
            result = notify.telegram_call("t", "getUpdates", {"offset": -1, "allowed_updates": ["message"]})
        self.assertEqual(result, [])
        body = urlopen.call_args.args[0].data.decode()
        self.assertIn("offset=-1", body)
        self.assertIn("allowed_updates=%5B%22message%22%5D", body)

    def test_conflict_code_and_no_token_in_error(self):
        error = urllib.error.HTTPError("https://api.telegram.org/bot123:secret/getUpdates", 409, "Conflict", {},
                                       io.BytesIO(json.dumps({"ok": False, "error_code": 409,
                                                              "description": "Conflict: webhook is active"}).encode()))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(notify.TelegramError) as caught:
                notify.telegram_call("123:secret", "getUpdates")
        self.assertEqual(caught.exception.code, 409)
        self.assertNotIn("secret", str(caught.exception))

    def test_network_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
            with self.assertRaises(notify.TelegramError) as caught:
                notify.telegram_call("123:secret", "sendMessage", {"chat_id": 1, "text": "x"})
        self.assertIn("timed out", str(caught.exception))


class RelayForTest(unittest.TestCase):
    def setUp(self):
        captcha._relay = None

    def tearDown(self):
        if captcha._relay is not None:
            captcha._relay.retire()
            captcha._relay = None

    def cfg(self, **notify_values) -> Config:
        cfg = Config(search_url="")
        cfg.notify = replace(cfg.notify, **notify_values)
        return cfg

    def test_needs_telegram_token_chat_and_switch(self):
        full = {"telegram": True, "telegram_token": "1:a", "telegram_chat_id": "42"}
        self.assertIsNone(captcha.relay_for(None))
        self.assertIsNone(captcha.relay_for(self.cfg()))  # по умолчанию Telegram выключен
        self.assertIsNone(captcha.relay_for(self.cfg(**{**full, "telegram": False})))
        self.assertIsNone(captcha.relay_for(self.cfg(**{**full, "telegram_chat_id": ""})))
        self.assertIsNone(captcha.relay_for(self.cfg(**{**full, "telegram_captcha": False})))
        relay = captcha.relay_for(self.cfg(**full))
        self.assertIsNotNone(relay)
        self.assertIs(captcha.relay_for(self.cfg(**full)), relay)  # один бот на всю программу
        other = captcha.relay_for(self.cfg(**{**full, "telegram_chat_id": "43"}))
        self.assertIsNot(other, relay)
        self.assertTrue(relay._retired)

    def test_only_own_chat(self):
        relay = captcha.relay_for(self.cfg(telegram=True, telegram_token="1:a", telegram_chat_id="42"))
        self.assertTrue(relay._ours({"chat": {"id": 42}}))
        self.assertFalse(relay._ours({"chat": {"id": 43}}))
        self.assertFalse(relay._ours({}))
        by_name = captcha.Relay("1:b", "@MyChannel")
        by_name.retire()
        self.assertTrue(by_name._ours({"chat": {"id": -100, "username": "mychannel"}}))


class SendRetryTest(unittest.TestCase):
    def test_network_failure_is_retried(self):
        calls = []

        def call(*_args, **_kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise notify.TelegramError("[WinError 10060] timed out")
            return {"message_id": 1}

        pauses = []
        result = notify.telegram_send("t", "sendMessage", {"chat_id": 1}, pause=pauses.append, call=call)
        self.assertEqual(result, {"message_id": 1})
        self.assertEqual(len(calls), 3)
        self.assertEqual(pauses, [1, 3])

    def test_refusal_is_not_retried(self):
        calls = []

        def call(*_args, **_kwargs):
            calls.append(1)
            raise notify.TelegramError("Bad Request: chat not found", 400)

        with self.assertRaises(notify.TelegramError):
            notify.telegram_send("t", "sendMessage", pause=lambda _s: None, call=call)
        self.assertEqual(len(calls), 1)

    def test_too_many_requests_waits_as_asked(self):
        answers = [notify.TelegramError("Too Many Requests", 429, retry_after=7), {}]

        def call(*_args, **_kwargs):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        pauses = []
        notify.telegram_send("t", "sendMessage", pause=pauses.append, call=call)
        self.assertEqual(pauses, [7])

    def test_cut_off_response_is_a_network_error(self):
        import http.client

        class Broken(_Response):
            def read(self, *_args):
                raise http.client.IncompleteRead(b"{")

        with mock.patch("urllib.request.urlopen", return_value=Broken(b"")):
            with self.assertRaises(notify.TelegramError) as caught:
                notify.telegram_call("t", "getUpdates")
        self.assertTrue(caught.exception.transient)


class FindChatTest(unittest.TestCase):
    def bot(self, updates: list[list[dict]], webhook: str = ""):
        seen = []

        def call(_token, method, params=None, _files=None, timeout=20):
            seen.append((method, dict(params or {})))
            if method == "getMe":
                return {"username": "my_hh_bot"}
            if method == "getWebhookInfo":
                return {"url": webhook}
            return updates.pop(0) if updates else []

        return call, seen

    def test_takes_latest_chat_including_earlier_message(self):
        call, seen = self.bot([[
            {"update_id": 5, "message": {"chat": {"id": 111, "first_name": "Чужой"}, "text": "hi"}},
            {"update_id": 6, "message": {"chat": {"id": 587, "first_name": "Антон", "username": "anton"},
                                         "text": "/start"}},
        ]])
        with mock.patch.object(notify, "telegram_call", call):
            found = notify.find_chat("1:a", wait=5)
        self.assertEqual(found, {"id": "587", "title": "Антон (@anton)", "bot": "my_hh_bot"})
        first_poll = next(params for method, params in seen if method == "getUpdates")
        self.assertNotIn("offset", first_poll)  # написанное до нажатия кнопки тоже считается

    def test_waits_for_message_then_gives_up(self):
        call, seen = self.bot([[], [], []])
        with mock.patch.object(notify, "telegram_call", call), mock.patch.object(notify.time, "monotonic",
                                                                                side_effect=[0, 0, 1, 1, 99, 99]):
            found = notify.find_chat("1:a", wait=5)
        self.assertEqual(found, {"bot": "my_hh_bot"})
        self.assertGreaterEqual(sum(method == "getUpdates" for method, _ in seen), 2)

    def test_webhook_is_explained(self):
        call, _seen = self.bot([], webhook="https://example.org/hook")
        with mock.patch.object(notify, "telegram_call", call):
            with self.assertRaises(notify.TelegramError) as caught:
                notify.find_chat("1:a", wait=5)
        self.assertIn("вебхук", str(caught.exception))


class AnswerTest(unittest.TestCase):
    def test_answer_from_phone_is_cleaned(self):
        self.assertEqual(captcha.clean_answer("  вынесет путь "), "вынесет путь")
        self.assertEqual(captcha.clean_answer("«Вынесет  путь»."), "Вынесет путь")
        self.assertEqual(captcha.clean_answer('"x7k2p"'), "x7k2p")
        self.assertEqual(captcha.clean_answer("вынесет\nпуть"), "вынесет путь")
        self.assertEqual(captcha.clean_answer("Другая!"), "Другая")

    def test_russian_keys_are_on_russian_layout(self):
        self.assertEqual(captcha.ru_key("й"), ("KeyQ", 81))
        self.assertEqual(captcha.ru_key("Ж"), ("Semicolon", 186))
        self.assertEqual(captcha.ru_key("ю"), ("Period", 190))
        self.assertEqual(captcha.ru_key("ё"), ("Backquote", 192))
        self.assertIsNone(captcha.ru_key("q"))
        self.assertIsNone(captcha.ru_key("7"))

    def test_capital_russian_letter_is_typed_with_shift(self):
        sent = []
        cdp = mock.Mock(send=lambda method, params: sent.append(params))
        captcha._press_ru(cdp, "В", "KeyD", 68)
        self.assertEqual([(p["type"], p["key"]) for p in sent],
                         [("rawKeyDown", "Shift"), ("keyDown", "В"), ("keyUp", "В"), ("keyUp", "Shift")])
        self.assertEqual(sent[1]["text"], "В")
        self.assertEqual(sent[1]["modifiers"], 8)


class RelaySendTest(unittest.TestCase):
    def relay(self, call):
        """Отправить картинку капчи через call — без потоков бота и без пауз между повторами."""
        with mock.patch.object(captcha.threading, "Thread"):
            relay = captcha.Relay("1:a", "42")
        relay._active.set()  # капча на экране
        relay.photo(b"png", "капча")
        with mock.patch.object(captcha, "telegram_call", call), mock.patch.object(notify, "SEND_PAUSES", (0,)):
            relay._deliver(*relay._out.get_nowait())
        return relay

    def test_solved_captcha_picture_is_not_sent(self):
        calls = []
        with mock.patch.object(captcha.threading, "Thread"):
            relay = captcha.Relay("1:a", "42")
        relay.photo(b"png", "капча")  # капчу уже решили в браузере (_active не стоит)
        with mock.patch.object(captcha, "telegram_call", lambda *a, **k: calls.append(a)):
            relay._deliver(*relay._out.get_nowait())
        self.assertEqual(calls, [])

    def test_photo_after_network_hiccup_is_delivered(self):
        tries = []

        def call(_token, method, *_args, **_kwargs):
            if method == "sendPhoto":
                tries.append(1)
                if len(tries) == 1:
                    raise notify.TelegramError("timed out")
                return {}
            return []

        relay = self.relay(call)
        self.assertEqual(len(tries), 2)
        self.assertFalse(relay.photo_failed.is_set())

    def test_photo_lost_to_network_is_resent_but_refusal_is_not(self):
        def down(_token, method, *_args, **_kwargs):
            if method == "sendPhoto":
                raise notify.TelegramError("timed out")
            return []

        def refused(_token, method, *_args, **_kwargs):
            if method == "sendPhoto":
                raise notify.TelegramError("Unauthorized", 401)
            return []

        self.assertTrue(self.relay(down).photo_failed.is_set())
        self.assertFalse(self.relay(refused).photo_failed.is_set())  # иначе слал бы картинку каждую секунду


class BiggerTest(unittest.TestCase):
    def test_small_picture_is_scaled_up(self):
        from PIL import Image

        out = io.BytesIO()
        Image.new("RGB", (200, 60), "white").save(out, "PNG")
        bigger = Image.open(io.BytesIO(captcha._bigger(out.getvalue())))
        self.assertGreaterEqual(bigger.width, 400)
        self.assertEqual(bigger.width / bigger.height, 200 / 60)

    def test_not_an_image_is_sent_as_is(self):
        self.assertEqual(captcha._bigger(b"not png"), b"not png")


if __name__ == "__main__":
    unittest.main()

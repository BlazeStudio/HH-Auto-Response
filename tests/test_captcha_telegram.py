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

"""Окно браузера при капче (web/browser.py wait_captcha): само выходит на экран, только если включено
[browser] show_on_captcha. Браузер и окно подменены — проверяется только, что программа делает с окном."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hh_auto.core.config import Config  # noqa: E402
from hh_auto.web import browser  # noqa: E402


class CaptchaWindowTest(unittest.TestCase):
    def tearDown(self):
        browser._captchas.clear()  # «после капчи паузы длиннее» — не для других проверок

    def solve(self, show_on_captcha: bool, user_opens: bool = False):
        """Капча на экране два шага, потом решена. user_opens — человек сам открыл браузер, пока она ждала."""
        cfg = Config(search_url="")
        cfg.browser = replace(cfg.browser, show_on_captcha=show_on_captcha, headless=False, hide_window=True)
        window = {"hidden": True}
        actions: list[str] = []
        shown = iter([True, True, True, False])

        def sleep(_seconds):
            if user_opens:
                window["hidden"] = False

        def show(*_args, **_kwargs):
            actions.append("show")
            window["hidden"] = False

        def hide(*_args, **_kwargs):
            actions.append("hide")
            window["hidden"] = True

        page = mock.Mock()
        with mock.patch.object(browser, "_cfg", cfg), \
                mock.patch.object(browser, "captcha_shown", side_effect=lambda _page: next(shown)), \
                mock.patch.object(browser, "hides_window", return_value=True), \
                mock.patch.object(browser.browser_window, "pump"), \
                mock.patch.object(browser.browser_window, "is_hidden", side_effect=lambda *_a: window["hidden"]), \
                mock.patch.object(browser.browser_window, "show", side_effect=show), \
                mock.patch.object(browser.browser_window, "hide", side_effect=hide), \
                mock.patch.object(browser.control, "sleep", side_effect=sleep), \
                mock.patch.object(browser, "_alert_captcha") as alert, \
                mock.patch.object(browser, "log") as log:
            self.assertTrue(browser.wait_captcha(page))
        warning = " ".join(str(call.args[0]) for call in log.warning.call_args_list)
        return actions, page, alert, warning

    def test_off_by_default(self):
        self.assertFalse(Config(search_url="").browser.show_on_captcha)

    def test_window_stays_hidden_and_user_is_told_how_to_open_it(self):
        actions, page, alert, warning = self.solve(show_on_captcha=False)
        self.assertEqual(actions, [])  # ни показать, ни спрятать
        page.bring_to_front.assert_not_called()  # и вперёд не выводится
        self.assertIn("откройте браузер", warning)
        self.assertTrue(alert.call_args.kwargs["closed"])

    def test_window_opened_for_captcha_is_hidden_again(self):
        actions, _page, _alert, _warning = self.solve(show_on_captcha=False, user_opens=True)
        self.assertEqual(actions, ["hide"])

    def test_auto_open_shows_and_hides_back(self):
        actions, page, alert, warning = self.solve(show_on_captcha=True)
        self.assertEqual(actions, ["show", "hide"])
        page.bring_to_front.assert_called()
        self.assertNotIn("откройте браузер", warning)
        self.assertFalse(alert.call_args.kwargs["closed"])


if __name__ == "__main__":
    unittest.main()

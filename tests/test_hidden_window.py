"""Спрятанный браузер на macOS и Linux (web/window.py, режим cdp): окно сворачивает только рабочий поток,
окно приложения оставляет поручения; «уснувшая» или несвёрнутая страница — окно остаётся на экране."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hh_auto.core.config import Config  # noqa: E402
from hh_auto.web import browser, window  # noqa: E402


class FakeSession:
    """CDP-сессия вкладки: окно и проверка кадров. obey=False — система не сворачивает окно."""

    def __init__(self):
        self.state = "normal"
        self.obey = True
        self.visibility, self.frames = "visible", 1
        self.calls: list[tuple[str, dict]] = []
        self.thread = threading.get_ident()
        self.wrong: list[str] = []  # вызовы не из рабочего потока — их быть не должно
        self.detached = False

    def send(self, method, params=None):
        params = params or {}
        if threading.get_ident() != self.thread:
            self.wrong.append(method)
            raise RuntimeError(f"{method} не из рабочего потока")
        self.calls.append((method, params))
        if method == "Browser.getWindowForTarget":
            return {"windowId": 7, "bounds": {"windowState": self.state}}
        if method == "Browser.getWindowBounds":
            return {"bounds": {"windowState": self.state}}
        if method == "Browser.setWindowBounds":
            state = params["bounds"].get("windowState")
            if state and (self.obey or state != "minimized"):
                self.state = state
            return {}
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "F"}}}
        if method == "Page.createIsolatedWorld":
            return {"executionContextId": 5}
        if method == "Runtime.evaluate":
            if params.get("contextId") is None:
                return {"result": {"value": [0, 25, 1280, 775]}}  # экран
            if params["expression"] == window.PROBE_START:
                return {"result": {"value": self.visibility}}
            return {"result": {"value": self.frames}}
        return {}

    def detach(self):
        self.detached = True

    def states(self) -> list[str]:
        return [p["bounds"]["windowState"] for m, p in self.calls
                if m == "Browser.setWindowBounds" and "windowState" in p["bounds"]]


class FakePage:
    def __init__(self, session: FakeSession):
        self.context = mock.Mock()
        self.context.pages = [self]
        self.context.new_cdp_session.return_value = session


def other_thread(action):
    """Как кнопка в окне приложения — из другого потока."""
    result = []
    thread = threading.Thread(target=lambda: result.append(action()))
    thread.start()
    thread.join()
    return result[0]


class CdpWindowTest(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(window, "MODE", "cdp"),
                        mock.patch.object(window.time, "sleep", lambda _s: None),
                        mock.patch.object(window._Cdp, "MINIMIZE_WAIT", 0.05),
                        mock.patch.object(window, "PROBE_WAIT", 0.05)]
        for patch in self.patches:
            patch.start()
        window._Cdp.kept = ""
        self.session = FakeSession()
        self.page = FakePage(self.session)

    def tearDown(self):
        self.assertEqual(self.session.wrong, [])
        window.unbind()
        window._Cdp.kept = ""
        for patch in self.patches:
            patch.stop()

    def bind_and_hide(self):
        self.assertTrue(window.bind(self.page))
        self.assertTrue(window.hide())

    def test_bind_fits_window_on_screen(self):
        self.assertTrue(window.bind(self.page))
        bounds = next(p["bounds"] for m, p in self.session.calls if m == "Browser.setWindowBounds")
        self.assertEqual((bounds["width"], bounds["height"]), (1280, 775))  # меньше WORK_SIZE — по экрану
        self.assertEqual((bounds["left"], bounds["top"]), (0, 25))
        self.assertEqual(window.find(), 7)
        self.assertFalse(window.is_hidden())

    def test_hide_on_worker_thread_minimizes_and_checks_page(self):
        self.bind_and_hide()
        self.assertEqual(self.session.state, "minimized")
        self.assertTrue(window.is_hidden())
        self.assertTrue(window._Cdp.checked)
        self.assertIn(("Page.createIsolatedWorld", {"frameId": "F", "worldName": "hh-auto-window"}),
                      self.session.calls)

    def test_requests_from_app_thread_wait_for_pump(self):
        self.bind_and_hide()
        sent = len(self.session.calls)
        self.assertTrue(other_thread(window.show))
        self.assertEqual(len(self.session.calls), sent)  # окно приложения само в браузер не ходит
        self.assertFalse(window.is_hidden())  # но превью уже показывает, что попросили
        self.assertEqual(self.session.state, "minimized")
        window.pump()
        self.assertEqual(self.session.state, "normal")
        self.assertIn(("Page.bringToFront", {}), self.session.calls)
        self.assertTrue(other_thread(window.hide))
        self.assertTrue(window.is_hidden())
        window.pump()
        self.assertEqual(self.session.state, "minimized")

    def test_pump_on_app_thread_does_nothing(self):
        self.bind_and_hide()
        other_thread(window.show)
        other_thread(window.pump)
        self.assertEqual(self.session.state, "minimized")

    def test_user_restored_window_is_noticed(self):
        self.bind_and_hide()
        self.session.state = "normal"  # щелчок по значку в Dock / на панели задач
        window.pump(fresh=True)
        self.assertFalse(window.is_hidden())
        self.assertFalse(window.opened_by_user())  # окно и так на экране — двигать нечего

    def test_sleeping_page_keeps_window_on_screen(self):
        self.session.visibility, self.session.frames = "hidden", 0
        self.assertTrue(window.bind(self.page))
        with self.assertLogs("hh", level="WARNING"):
            self.assertFalse(window.hide())
        self.assertEqual(self.session.state, "normal")
        self.assertIn("засыпает", window.kept_shown())
        self.assertFalse(window.hide())
        self.assertFalse(other_thread(window.hide))
        self.assertEqual(self.session.state, "normal")

    def test_not_minimized_by_system_restores_for_sure(self):
        self.session.obey = False
        self.assertTrue(window.bind(self.page))
        with self.assertLogs("hh", level="INFO") as logs:
            self.assertFalse(window.hide())
        self.assertIn("не сворачивает", "\n".join(logs.output))
        # дважды просили свернуть, потом на весь экран и обратно — вдруг система свернула молча
        self.assertEqual(self.session.states(), ["minimized", "minimized", "maximized", "normal"])
        self.assertTrue(window.kept_shown())

    def test_later_check_needs_two_misses(self):
        self.bind_and_hide()
        self.session.frames = 0
        with self.assertLogs("hh", level="DEBUG"):
            for misses in (1, 2):
                window._Cdp.next_check = 0.0
                window.pump(fresh=True)
                self.assertEqual(bool(window.kept_shown()), misses == 2)
        self.assertEqual(self.session.state, "normal")

    def test_check_skipped_while_other_tab_open(self):
        self.bind_and_hide()
        self.session.frames = 0
        self.page.context.pages = [self.page, object()]  # вкладка работы может быть в фоне
        for _ in range(3):
            window._Cdp.next_check = 0.0
            window.pump(fresh=True)
        self.assertFalse(window.kept_shown())

    def test_unbind_forgets_window(self):
        self.bind_and_hide()
        window.unbind()
        self.assertTrue(self.session.detached)
        self.assertIsNone(window.find())
        self.assertFalse(window.is_hidden())
        self.assertFalse(other_thread(window.show))

    def test_closed_window_is_not_found(self):
        self.bind_and_hide()

        def broken(method, params=None):
            raise RuntimeError("Target closed")

        self.session.send = broken
        window.pump(fresh=True)
        self.assertIsNone(window.find())
        self.assertFalse(window.hide())


class PlatformTest(unittest.TestCase):
    def cfg(self) -> Config:
        cfg = Config(search_url="")
        cfg.browser.headless, cfg.browser.hide_window = False, True
        return cfg

    def test_supported(self):
        cases = [("win32", {}, True), ("darwin", {}, True), ("linux", {"DISPLAY": ":0"}, True),
                 ("linux", {"WAYLAND_DISPLAY": "wayland-0"}, False),  # чистый Wayland: обратно не развернуть
                 ("linux", {"DISPLAY": ":0", "WSL_DISTRO_NAME": "Ubuntu"}, False)]
        for platform, env, expected in cases:
            with self.subTest(platform=platform, env=env), mock.patch.object(window.sys, "platform", platform), \
                    mock.patch.dict(window.os.environ, env, clear=True), \
                    mock.patch.object(window.os.path, "exists", lambda _path: False):
                self.assertEqual(window.supported(), expected)
        with mock.patch.object(window.sys, "platform", "linux"), \
                mock.patch.dict(window.os.environ, {"DISPLAY": ":99"}, clear=True), \
                mock.patch.object(window.os.path, "exists", lambda path: path == "/.dockerenv"):
            self.assertFalse(window.supported())  # Docker: экран виртуальный, вход — по VNC

    def test_window_args(self):
        width, height = window.WORK_SIZE
        with mock.patch.object(window, "MODE", "win32"), mock.patch.object(window.sys, "platform", "win32"):
            self.assertEqual(browser.window_args(self.cfg()), ["--window-position=-32000,-32000",
                                                               f"--window-size={width},{height}"])
        with mock.patch.object(window, "MODE", "cdp"), mock.patch.object(window.sys, "platform", "darwin"):
            self.assertEqual(browser.window_args(self.cfg()), [f"--window-size={width},{height}"])
        with mock.patch.object(window, "MODE", "cdp"), mock.patch.object(window.sys, "platform", "linux"), \
                mock.patch.dict(window.os.environ, {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}, clear=True):
            self.assertIn("--ozone-platform=x11", browser.window_args(self.cfg()))
        with mock.patch.object(window, "MODE", "cdp"), mock.patch.object(window.sys, "platform", "linux"), \
                mock.patch.dict(window.os.environ, {"DISPLAY": ":0"}, clear=True):
            self.assertNotIn("--ozone-platform=x11", browser.window_args(self.cfg()))
        cfg = self.cfg()
        cfg.browser.hide_window = False
        self.assertEqual(browser.window_args(cfg), [])

    def test_win32_mode_ignores_worker_calls(self):
        with mock.patch.object(window, "MODE", "win32"):
            self.assertFalse(window.bind(object()))
            window.pump(fresh=True)
            window.unbind()
            self.assertEqual(window.kept_shown(), "")


if __name__ == "__main__":
    unittest.main()

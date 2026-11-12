"""Трей (ui/tray.py): на Linux он включается, только если на экране есть область уведомлений, — иначе свёрнутое
окно было бы не вернуть. Без экрана и без X11: Xlib подменён."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hh_auto.ui import tray  # noqa: E402


def fake_xlib(owner: int, fail: bool = False) -> dict:
    """Модули Xlib с одним экраном: у выделения трея владелец owner (0 — нет)."""
    asked = []

    class Display:
        def __init__(self):
            if fail:
                raise OSError("Can't connect to display")

        def get_default_screen(self):
            return 0

        def intern_atom(self, name):
            asked.append(name)
            return 42

        def get_selection_owner(self, atom):
            return owner if atom == 42 else 0

        def close(self):
            asked.append("closed")

    package = types.ModuleType("Xlib")
    package.X = types.SimpleNamespace(NONE=0)
    package.display = types.SimpleNamespace(Display=Display)
    package.asked = asked
    return {"Xlib": package, "Xlib.X": package.X, "Xlib.display": package.display}


class X11TrayTest(unittest.TestCase):
    def test_panel_owns_tray_selection(self):
        modules = fake_xlib(owner=7)
        with mock.patch.dict(sys.modules, modules):
            self.assertTrue(tray.x11_tray_present())
        self.assertEqual(modules["Xlib"].asked, ["_NET_SYSTEM_TRAY_S0", "closed"])

    def test_no_tray_on_screen(self):
        with mock.patch.dict(sys.modules, fake_xlib(owner=0)):
            self.assertFalse(tray.x11_tray_present())

    def test_no_display(self):
        with mock.patch.dict(sys.modules, fake_xlib(owner=7, fail=True)):
            self.assertFalse(tray.x11_tray_present())


class AvailableTest(unittest.TestCase):
    def tearDown(self):
        tray.available.cache_clear()

    def test_linux_without_tray_area(self):
        tray.available.cache_clear()
        with mock.patch.object(tray, "LINUX", True), mock.patch.object(tray.sys, "platform", "linux"), \
                mock.patch.dict(tray.os.environ, {"PYSTRAY_BACKEND": "xorg"}), \
                mock.patch.dict(sys.modules, {"pystray": types.ModuleType("pystray")}), \
                mock.patch.object(tray, "x11_tray_present", return_value=False):
            self.assertFalse(tray.available())

    def test_linux_with_tray_area(self):
        tray.available.cache_clear()
        with mock.patch.object(tray, "LINUX", True), mock.patch.object(tray.sys, "platform", "linux"), \
                mock.patch.dict(tray.os.environ, {"PYSTRAY_BACKEND": "xorg"}), \
                mock.patch.dict(sys.modules, {"pystray": types.ModuleType("pystray")}), \
                mock.patch.object(tray, "x11_tray_present", return_value=True):
            self.assertTrue(tray.available())

    def test_place_names(self):
        self.assertIn(tray.PLACE, ("у часов", "в строке меню", "в области уведомлений"))


if __name__ == "__main__":
    unittest.main()

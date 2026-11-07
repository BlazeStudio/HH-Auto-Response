"""Сочетания клавиш в любой раскладке на всех системах. Окна здесь не нужны.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта

from hh_auto.ui.widgets import shortcut_event  # noqa: E402


def mac_keycode(virtual: int, char: str) -> int:
    """Код клавиши в Tk на macOS: виртуальная клавиша в верхнем байте, символ — в младших битах."""
    return virtual << 24 | ord(char)


class Mac(unittest.TestCase):
    def test_russian_layout(self):
        for keysym, event in (("Cyrillic_em", "<<Paste>>"), ("Cyrillic_es", "<<Copy>>"), ("Cyrillic_che", "<<Cut>>"),
                              ("Cyrillic_ef", "<<SelectAll>>"), ("Cyrillic_ya", "<<Undo>>"),
                              ("CYRILLIC_EM", "<<Paste>>")):
            self.assertEqual(shortcut_event(keysym, 0, False, "darwin"), event, keysym)

    def test_unknown_keysym_is_found_by_physical_key(self):
        for virtual, char, event in ((9, "м", "<<Paste>>"), (8, "с", "<<Copy>>"), (7, "ч", "<<Cut>>"),
                                     (0, "ф", "<<SelectAll>>"), (6, "я", "<<Undo>>")):
            self.assertEqual(shortcut_event("??", mac_keycode(virtual, char), False, "darwin"), event, char)

    def test_old_tk_keeps_physical_key_in_another_byte(self):
        self.assertEqual(shortcut_event("??", 9 << 16 | ord("м"), False, "darwin"), "<<Paste>>")

    def test_latin_is_left_to_tk(self):
        for keysym, virtual in (("v", 9), ("c", 8), ("a", 0), ("z", 6)):
            self.assertIsNone(shortcut_event(keysym, mac_keycode(virtual, keysym), False, "darwin"), keysym)

    def test_dvorak_is_not_mistaken_for_the_physical_key(self):
        self.assertIsNone(shortcut_event("k", mac_keycode(9, "k"), False, "darwin"))

    def test_shift_undo_is_redo(self):
        self.assertEqual(shortcut_event("Cyrillic_YA", 0, True, "darwin"), "<<Redo>>")


class Windows(unittest.TestCase):
    def test_russian_layout_by_keycode(self):
        for keycode, event in ((86, "<<Paste>>"), (67, "<<Copy>>"), (88, "<<Cut>>"), (65, "<<SelectAll>>"),
                               (90, "<<Undo>>"), (89, "<<Redo>>")):
            self.assertEqual(shortcut_event("??", keycode, False, "win32"), event, keycode)

    def test_latin_is_left_to_tk(self):
        self.assertIsNone(shortcut_event("v", 86, False, "win32"))

    def test_shift_undo_is_redo(self):
        self.assertEqual(shortcut_event("Z", 90, True, "win32"), "<<Redo>>")

    def test_other_keys_do_nothing(self):
        self.assertIsNone(shortcut_event("??", 77, False, "win32"))


class Linux(unittest.TestCase):
    def test_russian_layout(self):
        self.assertEqual(shortcut_event("Cyrillic_em", 55, False, "linux"), "<<Paste>>")

    def test_latin_is_left_to_tk(self):
        self.assertIsNone(shortcut_event("v", 55, False, "linux"))

    def test_shift_undo_is_redo(self):
        self.assertEqual(shortcut_event("Z", 52, True, "linux"), "<<Redo>>")


if __name__ == "__main__":
    unittest.main()

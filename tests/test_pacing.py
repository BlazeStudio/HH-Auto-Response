"""Серии откликов с перерывами после капчи (hh/pacing.py). Браузер и настоящие паузы не нужны.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта

from hh_auto.core.config import Limits  # noqa: E402
from hh_auto.hh import pacing  # noqa: E402
from hh_auto.hh.pacing import MIN_SERIES, Pacer  # noqa: E402


class PacerTest(unittest.TestCase):
    def setUp(self):
        self.captchas = 0
        self.slept: list[float] = []
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / pacing.FILE
        patches = [mock.patch.object(pacing, "captcha_count", lambda: self.captchas),
                   mock.patch.object(pacing.control, "sleep", self.slept.append),
                   mock.patch.object(pacing.random, "uniform", lambda a, b: 1.0)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.dir.cleanup)

    def pacer(self, rest_minutes: float = 5) -> Pacer:
        return Pacer(Limits(captcha_rest=rest_minutes), self.path)

    def respond(self, pacer: Pacer, count: int) -> list[bool]:
        """count откликов подряд: после каждого — rest_if_due()."""
        rests = []
        for _ in range(count):
            pacer.sent()
            rests.append(pacer.rest_if_due())
        return rests

    def test_no_captcha_no_rests(self):
        pacer = self.pacer()
        self.assertEqual(self.respond(pacer, 30), [False] * 30)
        self.assertEqual(self.slept, [])
        self.assertFalse(self.path.exists())

    def test_captcha_sets_series_and_rests(self):
        pacer = self.pacer()
        self.respond(pacer, 5)
        self.captchas += 1  # капча на 6-м отклике (hh его всё же принял)
        pacer.sent()
        self.assertTrue(pacer.rest_if_due())
        self.assertEqual(pacer.series, 5)
        self.assertEqual(self.slept, [300])
        # дальше — перерыв после каждых 5 откликов
        self.assertEqual(self.respond(pacer, 5), [False] * 4 + [True])
        self.assertEqual(len(self.slept), 2)

    def test_series_grows_and_rest_shrinks_without_captcha(self):
        pacer = self.pacer()
        self.respond(pacer, 3)
        self.captchas += 1
        pacer.sent()
        pacer.rest_if_due()  # серия 3, перерыв 5 мин
        self.captchas += 1
        pacer.sent()
        pacer.rest_if_due()  # капча на 1-м отклике после перерыва: серия минимальная, перерыв длиннее
        self.assertEqual(pacer.series, MIN_SERIES)
        self.assertEqual(pacer.rest, 450)
        for _ in range(pacing.GROW_AFTER):
            self.respond(pacer, pacer.series)
        self.assertEqual(pacer.series, MIN_SERIES + 1)
        self.assertLess(pacer.rest, 450)
        self.assertGreaterEqual(pacer.rest, 300)

    def test_rest_is_capped(self):
        pacer = self.pacer(rest_minutes=2)
        for _ in range(10):
            self.captchas += 1
            pacer.sent()
            pacer.rest_if_due()
        self.assertEqual(pacer.rest, 2 * 60 * pacing.REST_MAX)

    def test_remembered_between_runs(self):
        pacer = self.pacer()
        self.respond(pacer, 4)
        self.captchas += 1
        pacer.sent()
        pacer.rest_if_due()
        again = self.pacer()
        self.assertEqual(again.series, 4)
        self.assertEqual(self.respond(again, 4), [False] * 3 + [True])

    def test_old_memory_is_forgotten(self):
        self.path.write_text(json.dumps({"series": 3, "rest": 600, "at": time.time() - pacing.FORGET_AFTER - 60}),
                             encoding="utf-8")
        self.assertIsNone(self.pacer().series)

    def test_disabled(self):
        pacer = self.pacer(rest_minutes=0)
        self.captchas += 1
        pacer.sent()
        self.assertFalse(pacer.rest_if_due())
        self.assertEqual(self.respond(pacer, 20), [False] * 20)
        self.assertEqual(self.slept, [])


if __name__ == "__main__":
    unittest.main()

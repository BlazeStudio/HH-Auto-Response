"""Запрет сна (core/power.py): причины независимы, сон разрешается, когда снята последняя."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hh_auto.core import power  # noqa: E402


class PowerTest(unittest.TestCase):
    def setUp(self):
        self.applied = []
        self.patch = mock.patch.object(power, "_apply", lambda: self.applied.append(bool(power._reasons)))
        self.patch.start()
        power._reasons.clear()

    def tearDown(self):
        self.patch.stop()
        power._reasons.clear()

    def test_reasons_are_independent(self):
        power.hold("работа")
        power.hold("расписание")
        power.release("работа")
        self.assertTrue(power.held())
        power.release("расписание")
        self.assertFalse(power.held())
        self.assertEqual(self.applied, [True, True, True, False])

    def test_repeated_hold_and_release_do_nothing(self):
        power.hold("работа")
        power.hold("работа")
        power.release("работа")
        power.release("работа")
        self.assertEqual(self.applied, [True, False])

    def test_awake_releases_on_error(self):
        with self.assertRaises(RuntimeError):
            with power.awake("работа"):
                self.assertTrue(power.held())
                raise RuntimeError
        self.assertFalse(power.held())

    def test_set_held(self):
        power.set_held("расписание", True)
        self.assertTrue(power.held())
        power.set_held("расписание", False)
        self.assertFalse(power.held())


class HelperCommandTest(unittest.TestCase):
    """macOS и Linux: запрет сна держит отдельный процесс, который сам кончается вместе с программой."""

    def test_macos_caffeinate(self):
        with mock.patch.object(power.sys, "platform", "darwin"):
            command = power._helper_command()
        self.assertEqual(command[:3], ["caffeinate", "-i", "-w"])
        self.assertEqual(command[3], str(power.os.getpid()))

    def test_linux_systemd_inhibit_blocks_sleep_not_screen(self):
        with mock.patch.object(power.sys, "platform", "linux"), \
                mock.patch.object(power.shutil, "which", return_value="/usr/bin/systemd-inhibit"):
            command = power._helper_command()
        self.assertEqual(command[0], "/usr/bin/systemd-inhibit")
        self.assertIn("--what=sleep", command)  # только сон: экран гаснет как обычно
        self.assertIn("--mode=block", command)
        self.assertIn(f"kill -0 {power.os.getpid()}", command[-1])  # программа упала — блокировка снимется

    def test_linux_without_systemd(self):
        with mock.patch.object(power.sys, "platform", "linux"), \
                mock.patch.object(power.shutil, "which", return_value=None):
            self.assertIsNone(power._helper_command())

    def test_helper_started_once_and_stopped(self):
        process = mock.MagicMock(pid=12345)
        process.poll.return_value = None
        with mock.patch.object(power, "_helper_command", return_value=["helper"]), \
                mock.patch.object(power.subprocess, "Popen", return_value=process) as popen, \
                mock.patch.object(power.os, "killpg", create=True) as killpg:
            power._helper = None
            power._helper_set(True)
            power._helper_set(True)  # уже держит — второй не запускается
            power._helper_set(False)
        popen.assert_called_once()
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        killpg.assert_called_once()
        self.assertIsNone(power._helper)


@unittest.skipUnless(sys.platform == "win32", "Windows")
class WindowsKeeperTest(unittest.TestCase):
    def test_keeper_thread_sets_and_clears(self):
        import time

        calls = []
        fake = mock.MagicMock()
        fake.SetThreadExecutionState.side_effect = lambda flags: calls.append(flags) or 1
        with mock.patch("ctypes.windll") as windll:
            windll.kernel32 = fake
            power._thread = None
            power.hold("проверка")
            deadline = time.monotonic() + 3
            while not calls and time.monotonic() < deadline:
                time.sleep(0.02)
            power.release("проверка")
            while len(calls) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
        self.assertEqual(calls[:2], [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED, power.ES_CONTINUOUS])


if __name__ == "__main__":
    unittest.main()

"""Проверка собранного приложения перед релизом: оно запускается и окно живёт, а самопроверка проходит.

    python packaging/check_build.py            после python packaging/build.py
    xvfb-run -a python packaging/check_build.py   на Linux без экрана

Самопроверка (--self-test) окна не открывает, поэтому сборку, у которой падает само окно, она не замечала:
Linux-версия 1.1.1-beta не запускалась («No module named 'PIL._tkinter_finder'»). Здесь приложение запускается
как у человека, с пустой папкой данных, и должно проработать WAIT секунд без crash.log.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "HH-Auto-Response"
WAIT = 25


def app_path() -> Path:
    dist = ROOT / "dist"
    if sys.platform == "win32":
        return dist / NAME / f"{NAME}.exe"
    if sys.platform == "darwin":
        return dist / f"{NAME}.app" / "Contents" / "MacOS" / NAME
    return dist / NAME / NAME


def run(app: Path, *args: str, wait: float) -> tuple[int | None, Path]:
    """Запустить с пустой папкой данных. Код возврата или None — ещё работает (тогда закрываем сами)."""
    home = Path(tempfile.mkdtemp(prefix="hh-build-check-"))
    proc = subprocess.Popen([str(app), *args], env=dict(os.environ, HH_AUTO_HOME=str(home)))
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.5)
    code = proc.poll()
    if code is None:
        proc.kill()
        proc.wait(timeout=30)
    return code, home


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    app = app_path()
    if not app.exists():
        print(f"✗ нет собранного приложения: {app} — сначала python packaging/build.py")
        return 1
    failed = False

    code, home = run(app, wait=WAIT)
    crash = home / "logs" / "crash.log"
    crashed = crash.exists() and crash.read_text(encoding="utf-8", errors="replace").strip()
    if code is not None or crashed:
        failed = True
        print(f"✗ окно приложения: {'закрылось само, код ' + str(code) if code is not None else 'есть crash.log'}")
        if crashed:
            print(crash.read_text(encoding="utf-8", errors="replace")[-3000:])
    else:
        print(f"✓ окно приложения работает {WAIT} с, crash.log пуст")

    code, home = run(app, "--self-test", wait=180)
    report = home / "logs" / "self_test.txt"
    text = report.read_text(encoding="utf-8", errors="replace") if report.exists() else "(нет self_test.txt)"
    if code != 0:
        failed = True
        print(f"✗ самопроверка: код {code}\n{text}")
    else:
        print(f"✓ самопроверка:\n{text}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

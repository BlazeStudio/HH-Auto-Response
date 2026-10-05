"""Сборка приложения под текущую систему.

    Windows:  build.bat (двойной клик) или python packaging/build.py
    macOS:    ./build-macos.sh → dist/HH-Auto-Response.app и dist/HH-Auto-Response-macos-<arch>.zip
    Linux:    ./build.sh       → dist/HH-Auto-Response/HH-Auto-Response и dist/HH-Auto-Response-linux-<arch>.tar.gz
    Linux из Windows или macOS через Docker: ./build-linux.sh
    Все три системы сразу — GitHub Actions (.github/workflows/build.yml)

PyInstaller собирает только под ту ОС, на которой запущен: exe для Windows — на Windows, .app — на Mac,
Linux-версию — на Linux (или в Docker: ./build-linux.sh). Всё ставится в отдельное окружение
.venv-build, чтобы в сборку не попали лишние библиотеки. Нужны Python 3.11+ и интернет для pip.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv-build"
DIST = ROOT / "dist"
APP_NAME = "HH-Auto-Response"
APP_DIR = DIST / APP_NAME
PLATFORM = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
# архитектура в имени архива: Mac бывают на Apple Silicon (arm64) и Intel (x86_64)
ARCH = {"amd64": "x86_64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())


def step(title: str) -> None:
    print(f"\n━━━ {title}", flush=True)


def run(*cmd) -> None:
    print("  $ " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT)


def folder_size(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024 / 1024


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if PLATFORM == "windows" else "bin/python")


def tk_font_system() -> str:
    """xft — нормальные шрифты с кириллицей; x11 — старые X-шрифты без неё (так у портативных сборок Python).
    Пусто — проверить не удалось (например, нет экрана): тогда не мешаем сборке."""
    code = (  # у части сборок Tk нет ::tk::pkgconfig — тогда смотрим, подгрузилась ли libXft
        "import os, tkinter\nr = tkinter.Tk()\n"
        "try:\n    print(r.tk.call('::tk::pkgconfig', 'get', 'fontsystem'))\n"
        "except tkinter.TclError:\n"
        "    maps = '/proc/self/maps'\n"
        "    print('xft' if os.path.exists(maps) and 'libXft' in open(maps).read() else 'x11')\n"
    )
    try:
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def archive() -> Path:
    """Архив для передачи: zip для Windows и macOS, tar.gz для Linux (сохраняет права на запуск)."""
    if PLATFORM == "macos":
        target = DIST / f"{APP_NAME}-macos-{ARCH}.zip"
        target.unlink(missing_ok=True)
        if shutil.which("ditto"):  # родной архиватор macOS: сохраняет подпись и права внутри .app
            run("ditto", "-c", "-k", "--keepParent", DIST / f"{APP_NAME}.app", target)
            return target
        return Path(shutil.make_archive(str(target.with_suffix("")), "zip", root_dir=DIST, base_dir=f"{APP_NAME}.app"))
    if PLATFORM == "linux":
        return Path(shutil.make_archive(str(DIST / f"{APP_NAME}-linux-{ARCH}"), "gztar", root_dir=DIST,
                                        base_dir=APP_NAME))
    return Path(shutil.make_archive(str(DIST / f"{APP_NAME}-windows"), "zip", root_dir=DIST, base_dir=APP_NAME))


def main() -> int:
    started = time.monotonic()
    if sys.version_info < (3, 11):
        print(f"! Нужен Python 3.11+, а запущен {sys.version.split()[0]}")
        return 1
    if PLATFORM != "windows":
        import importlib.util

        if importlib.util.find_spec("tkinter") is None:  # окно приложения на Tk
            print("! Нет модуля tkinter. Linux: sudo apt install python3-tk; macOS: brew install python-tk@3.11")
            return 1

    if PLATFORM == "linux" and tk_font_system() == "x11":
        print("! Tk этого Python собран без Xft: в окне приложения не будет кириллицы (одни пустые места).\n"
              "  Соберите системным Python: sudo apt install python3 python3-venv python3-tk && PYTHON=/usr/bin/python3 "
              "./build.sh\n  или в Docker: ./build-linux.sh")
        return 1

    python = venv_python()
    step(f"1/5 Окружение для сборки (.venv-build), система: {PLATFORM}")
    if not python.exists():
        run(sys.executable, "-m", "venv", VENV)
    else:
        print("  уже есть")

    step("2/5 Библиотеки")
    run(python, "-m", "pip", "install", "--upgrade", "--quiet", "pip")
    run(python, "-m", "pip", "install", "--upgrade", "--quiet", "-r", "requirements.txt", "-r", "requirements-app.txt",
        "pyinstaller>=6", "pillow")

    step("3/5 Иконка")
    icons = [ROOT / "packaging" / name for name in ("icon.ico", "icon.png", "icon.icns")]
    if all(icon.exists() for icon in icons):
        # готовые иконки из репозитория: на всех системах одинаковые (рисовать заново — шрифтом этой ОС,
        # на Linux вышел бы DejaVu вместо Segoe UI). Перерисовать: python packaging/make_icon.py
        print("  готовые: packaging/icon.ico, icon.png, icon.icns")
    else:
        run(python, ROOT / "packaging" / "make_icon.py")

    step("4/5 PyInstaller")
    run(python, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", DIST, "--workpath", ROOT / "build",
        ROOT / "packaging" / f"{APP_NAME}.spec")
    app = {"windows": APP_DIR / f"{APP_NAME}.exe", "macos": DIST / f"{APP_NAME}.app",
           "linux": APP_DIR / APP_NAME}[PLATFORM]
    if not app.exists():
        print(f"! Не найден {app} — смотрите вывод PyInstaller выше")
        return 1
    node = "node.exe" if PLATFORM == "windows" else "node"
    if next((app if app.is_dir() else APP_DIR).rglob(f"playwright/driver/{node}"), None) is None:
        print(f"! В сборку не попал драйвер Playwright (playwright/driver/{node})")
        return 1

    step("5/5 Архив для передачи")
    packed = archive()

    minutes, seconds = divmod(int(time.monotonic() - started), 60)
    hint = {
        "windows": f"Передавайте архив целиком: {APP_NAME}.exe работает только вместе с папкой _internal рядом.",
        "macos": "Приложение не подписано: при первом запуске — правый клик по нему → «Открыть».",
        "linux": f"Распакуйте и запустите ./{APP_NAME}/{APP_NAME}. Нужен Google Chrome (или Chromium, см. README).",
    }[PLATFORM]
    print(f"""
✓ Готово за {minutes} мин {seconds} с
  приложение: {app}  ({folder_size(app if app.is_dir() else APP_DIR):.0f} МБ)
  архив:      {packed}  ({packed.stat().st_size / 1024 / 1024:.0f} МБ)

{hint}
""")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        print(f"\n✗ Шаг завершился с ошибкой (код {e.returncode}): {' '.join(map(str, e.cmd))}")
        sys.exit(1)

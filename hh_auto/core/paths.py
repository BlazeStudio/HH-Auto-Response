"""Где лежат config.toml, логи, память и профиль браузера.

Из исходников — в папке hh-data/ внутри проекта (общая для приложения и консольных скриптов).
В собранном приложении — в папке данных пользователя, куда всегда можно писать:
Windows — %LOCALAPPDATA%\\HH-Auto-Response, macOS — ~/Library/Application Support/HH-Auto-Response,
Linux — ~/.local/share/HH-Auto-Response (или $XDG_DATA_HOME/HH-Auto-Response).
Переменная окружения HH_AUTO_HOME задаёт свою папку (так работает Docker-образ).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]  # hh_auto/core/paths.py → корень проекта
# До 1.0.4 из исходников всё это лежало прямо в папке проекта
_OLD_SOURCE_LAYOUT = ("config.toml", "app_prefs.json", "browser-profile", "data", "logs")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def user_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home())
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def move_old_source_layout(project: Path, root: Path) -> None:
    """Перенести данные из корня проекта (как было до 1.0.4) в hh-data/. Занятое (например, профиль
    открытого браузера) остаётся на месте и переедет при следующем запуске."""
    for name in _OLD_SOURCE_LAYOUT:
        old, new = project / name, root / name
        if old.exists() and not new.exists():
            try:
                root.mkdir(parents=True, exist_ok=True)
                old.rename(new)
            except OSError:
                pass


def app_root() -> Path:
    if os.environ.get("HH_AUTO_HOME"):  # своя папка данных — например, volume в Docker
        root = Path(os.environ["HH_AUTO_HOME"]).expanduser()
    elif is_frozen():
        root = user_data_dir() / "HH-Auto-Response"
    else:
        root = PROJECT_DIR / "hh-data"
        move_old_source_layout(PROJECT_DIR, root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def bundled(relative: str) -> Path:
    """Файл, упакованный внутрь приложения (иконка и т.п.)."""
    base = Path(getattr(sys, "_MEIPASS", PROJECT_DIR))
    return base / relative

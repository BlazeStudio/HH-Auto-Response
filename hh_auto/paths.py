"""Где лежат config.toml, логи, память и профиль браузера.

Из исходников — в папке проекта (общие с консольными скриптами).
В собранном приложении — в папке данных пользователя, куда всегда можно писать:
Windows — %LOCALAPPDATA%\\HH-Auto-Response, macOS — ~/Library/Application Support/HH-Auto-Response,
Linux — ~/.local/share/HH-Auto-Response (или $XDG_DATA_HOME/HH-Auto-Response).
Переменная окружения HH_AUTO_HOME задаёт свою папку (так работает Docker-образ).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def user_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home())
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def app_root() -> Path:
    if os.environ.get("HH_AUTO_HOME"):  # своя папка данных — например, volume в Docker
        root = Path(os.environ["HH_AUTO_HOME"]).expanduser()
    elif is_frozen():
        root = user_data_dir() / "HH-Auto-Response"
    else:
        root = Path(__file__).resolve().parent.parent
    root.mkdir(parents=True, exist_ok=True)
    return root


def bundled(relative: str) -> Path:
    """Файл, упакованный внутрь приложения (иконка и т.п.)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative

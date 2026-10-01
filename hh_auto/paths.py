"""Где лежат config.toml, логи, память и профиль браузера.

Из исходников — в папке проекта (общие с консольными скриптами).
В собранном приложении (.exe) — в %LOCALAPPDATA%\\HH-Auto-Response: туда всегда можно писать,
даже если само приложение лежит в Program Files.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    if is_frozen():
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "HH-Auto-Response"
    else:
        root = Path(__file__).resolve().parent.parent
    root.mkdir(parents=True, exist_ok=True)
    return root


def bundled(relative: str) -> Path:
    """Файл, упакованный внутрь приложения (иконка и т.п.)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative

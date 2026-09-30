"""Логирование: цветная консоль (INFO) + подробный файл (DEBUG) на каждый запуск."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

SUCCESS = 25
logging.addLevelName(SUCCESS, "OK")


class HHLogger(logging.Logger):
    def success(self, msg: str, *args, **kwargs) -> None:
        if self.isEnabledFor(SUCCESS):
            self._log(SUCCESS, msg, args, **kwargs)


logging.setLoggerClass(HHLogger)
log: HHLogger = logging.getLogger("hh")  # type: ignore[assignment]
logging.setLoggerClass(logging.Logger)

_COLORS = {
    logging.DEBUG: "\033[90m",  # серый
    SUCCESS: "\033[32m",  # зелёный
    logging.WARNING: "\033[33m",  # жёлтый
    logging.ERROR: "\033[31m",  # красный
    logging.CRITICAL: "\033[1;31m",
}
_RESET = "\033[0m"


class _ConsoleFormatter(logging.Formatter):
    def __init__(self, use_color: bool):
        super().__init__("%(asctime)s | %(levelname)-5s | %(message)s", "%H:%M:%S")
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        color = _COLORS.get(record.levelno) if self.use_color else None
        return f"{color}{text}{_RESET}" if color else text


def setup_logging(log_dir: Path, verbose: bool = False) -> Path:
    """Настраивает логгер 'hh' и возвращает путь к файлу лога этого запуска."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run_{datetime.now():%Y-%m-%d_%H-%M-%S}.log"

    # Кириллица и символы ✓/→ в консоли Windows и при перенаправлении вывода в файл
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    if use_color and os.name == "nt":
        os.system("")  # включает обработку ANSI-цветов в консоли Windows

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(_ConsoleFormatter(use_color))

    file = logging.FileHandler(log_path, encoding="utf-8")
    file.setLevel(logging.DEBUG)
    file.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-5s | %(message)s", "%Y-%m-%d %H:%M:%S")
    )

    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    log.addHandler(console)
    log.addHandler(file)
    log.propagate = False
    return log_path

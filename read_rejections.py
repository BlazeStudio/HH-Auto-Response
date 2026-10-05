"""Прочитать отказы: открывает непрочитанные чаты hh, где последнее сообщение — «Отказ».

    python read_rejections.py              открыть все непрочитанные чаты с отказом
    python read_rejections.py --dry-run    только показать, какие чаты были бы открыты

Использует тот же config.toml и тот же профиль браузера (вход), что и main.py.
Не запускайте одновременно с main.py: у них общий профиль браузера.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hh_auto.app import run_chats
from hh_auto.chats import ChatStats
from hh_auto.config import ConfigError, load_config
from hh_auto.logger import log, setup_logging
from hh_auto.paths import app_root

ROOT = app_root()  # папка проекта или HH_AUTO_HOME


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Открыть непрочитанные чаты hh с отказами")
    p.add_argument("--config", type=Path, default=ROOT / "config.toml", help="путь к config.toml")
    p.add_argument("--dry-run", action="store_true", help="только показать чаты, не открывая их")
    p.add_argument("-v", "--verbose", action="store_true", help="подробный лог (DEBUG) и в консоли")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    log_path = setup_logging(ROOT / "logs", args.verbose, name="chats")
    log.info("═" * 60)
    log.info("HH-Auto-Response: прочитать отказы в чатах")
    log.info(f"подробный лог этого запуска: {log_path}")
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log.error(f"Ошибка конфигурации: {e}")
        return 2
    return run_chats(cfg, args.dry_run, ROOT, ChatStats(), log_path)


if __name__ == "__main__":
    sys.exit(main())

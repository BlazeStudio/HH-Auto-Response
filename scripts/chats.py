"""Чаты hh: читает непрочитанные отказы, а если включён ИИ — отвечает на вопросы работодателей.

    python scripts/chats.py              открыть непрочитанные чаты с отказом и ответить на анкеты
    python scripts/chats.py --dry-run    только показать, что было бы сделано
    python scripts/chats.py --only-rejections   только прочитать отказы, остальные чаты не трогать

Использует тот же config.toml и тот же профиль браузера (вход), что и scripts/responses.py.
Не запускайте одновременно с откликами: у них общий профиль браузера.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта

from hh_auto.app import run_chats  # noqa: E402
from hh_auto.chats import ChatStats  # noqa: E402
from hh_auto.config import ConfigError, load_config  # noqa: E402
from hh_auto.logger import log, setup_logging  # noqa: E402
from hh_auto.paths import app_root  # noqa: E402

ROOT = app_root()  # hh-data/ в папке проекта или HH_AUTO_HOME


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Открыть непрочитанные чаты hh с отказами")
    p.add_argument("--config", type=Path, default=ROOT / "config.toml", help="путь к config.toml")
    p.add_argument("--dry-run", action="store_true", help="только показать чаты, не открывая их")
    p.add_argument("--only-rejections", action="store_true",
                   help="только прочитать отказы, остальные чаты не трогать (как [chats] mode = \"rejections\")")
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
    if args.only_rejections:
        cfg.chats.mode = "rejections"
    return run_chats(cfg, args.dry_run, ROOT, ChatStats(), log_path)


if __name__ == "__main__":
    sys.exit(main())

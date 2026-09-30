"""Прочитать отказы: открывает непрочитанные чаты hh, где последнее сообщение — «Отказ».

    python read_rejections.py              открыть все непрочитанные чаты с отказом
    python read_rejections.py --dry-run    только показать, какие чаты были бы открыты

Использует тот же config.toml и тот же профиль браузера (вход), что и main.py.
Не запускайте одновременно с main.py: у них общий профиль браузера.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from hh_auto.auth import ensure_logged_in
from hh_auto.browser import FatalError, check_region, launch
from hh_auto.chats import ChatStats, log_chat_summary, read_rejections
from hh_auto.config import ConfigError, load_config
from hh_auto.logger import log, setup_logging

ROOT = Path(__file__).resolve().parent


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
    log.info("hh-auto: прочитать отказы в чатах")
    log.info(f"подробный лог этого запуска: {log_path}")
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log.error(f"Ошибка конфигурации: {e}")
        return 2
    cfg.browser.profile_dir = str(ROOT / cfg.browser.profile_dir)

    stats = ChatStats()
    started = time.monotonic()
    exit_code = 0
    with sync_playwright() as pw:
        context = None
        try:
            context = launch(pw, cfg)
            page = context.pages[0] if context.pages else context.new_page()
            log.info("")
            check_region(page, cfg)
            log.info("")
            ensure_logged_in(page, cfg)
            stats = read_rejections(page, cfg, args.dry_run)
        except KeyboardInterrupt:
            log.warning("Остановлено пользователем (Ctrl+C)")
            exit_code = 130
        except FatalError as e:
            log.error(f"Остановка: {e}")
            exit_code = 1
        finally:
            log_chat_summary(stats, time.monotonic() - started)
            log.info(f"подробный лог: {log_path}")
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

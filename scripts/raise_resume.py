"""Поднять резюме в поиске hh — бесплатно, раз в 4 часа ([raising] в config.toml).

    python scripts/raise_resume.py           поднять, если подошло время (раньше срока hh не трогается)
    python scripts/raise_resume.py --force   попробовать сейчас, не глядя на время прошлого подъёма

Запускайте по расписанию (Планировщик заданий Windows или cron), хоть каждый час: браузер открывается,
только когда пора. Использует тот же config.toml и тот же профиль браузера (вход), что и scripts/responses.py.
Не запускайте одновременно с откликами или чатами: у них общий профиль браузера (они и сами поднимают
резюме в начале работы, если подошло время).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта

from hh_auto.app import run_raise  # noqa: E402
from hh_auto.core.config import ConfigError, load_config  # noqa: E402
from hh_auto.core.logger import log, setup_logging  # noqa: E402
from hh_auto.core.paths import app_root  # noqa: E402
from hh_auto.hh import raising  # noqa: E402

ROOT = app_root()  # hh-data/ в папке проекта или HH_AUTO_HOME


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Поднять резюме в поиске hh (раз в 4 часа)")
    p.add_argument("--config", type=Path, default=ROOT / "config.toml", help="путь к config.toml")
    p.add_argument("--force", action="store_true", help="поднять сейчас, даже если по записям ещё рано")
    p.add_argument("-v", "--verbose", action="store_true", help="подробный лог (DEBUG) и в консоли")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):  # кириллица в консоли Windows — ещё до настройки лога
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        cfg = load_config(args.config, check_search_url=False)
    except ConfigError as e:
        print(f"Ошибка конфигурации: {e}", file=sys.stderr)
        return 2
    if args.force:
        cfg.raising.enabled = True
        raising.forget_due(ROOT)
    due = raising.next_due(ROOT, cfg)
    # выключено или ещё рано — одна строка в консоль, без браузера и без файла лога (cron зовёт хоть каждый час)
    if due is None:
        print("поднятие резюме выключено: [raising] enabled = false в config.toml")
        return 0
    if due > datetime.now():
        print(f"поднимать резюме ещё рано — следующее {due:%d.%m в %H:%M}")
        return 0
    log_path = setup_logging(ROOT / "logs", args.verbose, name="raise")
    log.info("HH-Auto-Response: поднять резюме в поиске")
    return run_raise(cfg, ROOT, log_path)


if __name__ == "__main__":
    sys.exit(main())

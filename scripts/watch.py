"""Фоновая проверка: всё, чему подошло время, за один запуск браузера.

    python scripts/watch.py      подъём резюме ([raising]), приглашения в чатах ([watch] invitations),
                                 свежие вакансии «компаний мечты» ([dream]) — что пора

Запускайте по расписанию (Планировщик заданий Windows или cron), например каждые 15–30 минут: браузер
открывается, только когда что-то пора, — иначе одна строка в консоль и выход. Тот же config.toml и тот же
профиль браузера (вход), что у scripts/responses.py. Не запускайте одновременно с откликами или чатами.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта

from hh_auto.app import run_background  # noqa: E402
from hh_auto.core.config import ConfigError, load_config  # noqa: E402
from hh_auto.core.logger import log, setup_logging  # noqa: E402
from hh_auto.core.paths import app_root  # noqa: E402
from hh_auto.hh import watch  # noqa: E402

ROOT = app_root()  # hh-data/ в папке проекта или HH_AUTO_HOME


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Фоновая проверка: подъём резюме, приглашения, компании мечты")
    p.add_argument("--config", type=Path, default=ROOT / "config.toml", help="путь к config.toml")
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
    tasks = watch.due_tasks(ROOT, cfg)
    if not tasks:  # без браузера и без файла лога: зовут хоть каждые 15 минут
        moment = watch.next_any(ROOT, cfg)
        print("фоновая проверка: делать нечего" + (f" — следующая {moment:%d.%m в %H:%M}" if moment else
                                                    " (всё выключено: [raising], [watch], [dream])"))
        return 0
    log_path = setup_logging(ROOT / "logs", args.verbose, name="watch")
    log.info("HH-Auto-Response: фоновая проверка")
    return run_background(cfg, ROOT, log_path, tasks=tasks)


if __name__ == "__main__":
    sys.exit(main())

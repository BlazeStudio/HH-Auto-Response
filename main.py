"""hh-auto — автоматические отклики на вакансии hh.ru с сопроводительным письмом.

    python main.py                 обычный запуск
    python main.py --dry-run       вход + проверка резюме + список вакансий, без откликов
    python main.py --limit 1       один отклик — для проверки, что всё работает
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hh_auto.app import run_responses
from hh_auto.config import ConfigError, load_config
from hh_auto.logger import log, setup_logging
from hh_auto.runner import RunOptions, Stats

ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Автоотклики на вакансии hh.ru")
    p.add_argument("--config", type=Path, default=ROOT / "config.toml", help="путь к config.toml")
    p.add_argument("--url", help="ссылка на поиск вакансий (вместо search_url из config)")
    p.add_argument("--resume", help="ссылка на резюме для проверки (вместо resume_url из config)")
    p.add_argument("--dry-run", action="store_true", help="пройти выдачу и показать вакансии, не откликаясь")
    p.add_argument("--limit", type=int, help="максимум откликов за запуск (вместо [limits] max_responses)")
    p.add_argument("--start-page", type=int, default=1, help="с какой страницы выдачи начать (с 1)")
    p.add_argument("--retry-skipped", action="store_true", help="снова пробовать вакансии, пропущенные ранее")
    p.add_argument("-v", "--verbose", action="store_true", help="подробный лог (DEBUG) и в консоли")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    log_path = setup_logging(ROOT / "logs", args.verbose)
    log.info("═" * 60)
    log.info("hh-auto: автоотклики на вакансии hh.ru")
    log.info(f"подробный лог этого запуска: {log_path}")

    try:
        cfg = load_config(args.config)
        if args.url:
            cfg.search_url = args.url
        if args.resume:
            cfg.resume_url = args.resume
    except ConfigError as e:
        log.error(f"Ошибка конфигурации: {e}")
        return 2

    opts = RunOptions(
        dry_run=args.dry_run,
        limit=args.limit,
        start_page=max(args.start_page, 1) - 1,
        retry_skipped=args.retry_skipped,
    )
    return run_responses(cfg, opts, ROOT, Stats(), log_path)


if __name__ == "__main__":
    sys.exit(main())

"""hh-auto — автоматические отклики на вакансии hh.ru с сопроводительным письмом.

    python main.py                 обычный запуск
    python main.py --dry-run       вход + проверка резюме + список вакансий, без откликов
    python main.py --limit 1       один отклик — для проверки, что всё работает
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from hh_auto.auth import ensure_logged_in
from hh_auto.browser import FatalError, check_region, launch
from hh_auto.config import ConfigError, load_config
from hh_auto.logger import log, setup_logging
from hh_auto.resume import verify_resume
from hh_auto.runner import RunOptions, Stats, log_summary, run
from hh_auto.state import ResultsCsv, State

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
    log_dir = ROOT / "logs"
    log_path = setup_logging(log_dir, args.verbose)
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
    cfg.browser.profile_dir = str(ROOT / cfg.browser.profile_dir)
    log.info(f"ссылка поиска: {cfg.search_url}")

    opts = RunOptions(
        dry_run=args.dry_run,
        limit=args.limit,
        start_page=max(args.start_page, 1) - 1,
        retry_skipped=args.retry_skipped,
    )
    state = State(ROOT / "data" / "state.json")
    results = ResultsCsv(log_dir / "responses.csv")
    stats = Stats()
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
            log.info("")
            resume_title = verify_resume(page, cfg)
            run(page, cfg, opts, state, results, stats, resume_title, log_dir / "snapshots")
        except KeyboardInterrupt:
            log.warning("Остановлено пользователем (Ctrl+C)")
            exit_code = 130
        except FatalError as e:
            log.error(f"Остановка: {e}")
            exit_code = 1
        finally:
            log_summary(stats, time.monotonic() - started)
            log.info(f"таблица всех откликов: {results.path}")
            log.info(f"подробный лог: {log_path}")
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

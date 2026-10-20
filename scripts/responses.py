"""HH-Auto-Response — автоматические отклики на вакансии hh.ru с сопроводительным письмом.

    python scripts/responses.py              обычный запуск
    python scripts/responses.py --dry-run    вход + проверка резюме + список вакансий, без откликов
    python scripts/responses.py --limit 1    один отклик — для проверки, что всё работает
    python scripts/responses.py --login      только войти в hh и сохранить сессию (первый запуск на сервере)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта

from hh_auto.app import run_login, run_responses  # noqa: E402
from hh_auto.config import ConfigError, load_config, validate_search_source  # noqa: E402
from hh_auto.logger import log, setup_logging  # noqa: E402
from hh_auto.paths import app_root  # noqa: E402
from hh_auto.runner import RunOptions, Stats  # noqa: E402

ROOT = app_root()  # hh-data/ в папке проекта или HH_AUTO_HOME: config.toml, логи, профиль браузера


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Автоотклики на вакансии hh.ru")
    p.add_argument("--config", type=Path, default=ROOT / "config.toml", help="путь к config.toml")
    p.add_argument("--url", help="ссылка на поиск вакансий (вместо search_url из config)")
    p.add_argument("--query", action="append",
                   help="общий поиск hh по запросу, например \"python разработчик\" (вместо ссылки); "
                        "можно несколько раз: --query python --query django")
    p.add_argument("--resume", help="ссылка на резюме для проверки (вместо resume_url из config)")
    p.add_argument("--dry-run", action="store_true", help="пройти выдачу и показать вакансии, не откликаясь")
    p.add_argument("--limit", type=int, help="максимум откликов за запуск (вместо [limits] max_responses)")
    p.add_argument("--start-page", type=int, default=1, help="с какой страницы выдачи начать (с 1)")
    p.add_argument("--login", action="store_true", help="только войти в hh (сессия сохранится) и выйти")
    p.add_argument("--retry-skipped", action="store_true", help="снова пробовать вакансии, пропущенные ранее")
    p.add_argument("-v", "--verbose", action="store_true", help="подробный лог (DEBUG) и в консоли")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    log_path = setup_logging(ROOT / "logs", args.verbose)
    log.info("═" * 60)
    log.info("HH-Auto-Response: автоотклики на вакансии hh.ru")
    log.info(f"подробный лог этого запуска: {log_path}")

    try:
        cfg = load_config(args.config, check_search_url=False)
        if args.login:
            return run_login(cfg, ROOT, log_path)
        if args.url:
            cfg.search_url, cfg.search.mode = args.url, "resume"
        if args.query:
            cfg.search.query, cfg.search.mode = "\n".join(args.query), "query"
        validate_search_source(cfg)
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

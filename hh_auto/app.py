"""Запуск сценариев целиком: браузер → проверка РФ → вход → работа → итоги.

Общий код для консольных скриптов (main.py, read_rejections.py) и оконного приложения.
"""

from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .auth import ensure_logged_in
from .browser import FatalError, check_region, launch
from .chats import ChatStats, log_chat_summary, read_rejections
from .config import Config
from .control import StopRequested
from .logger import log
from .resume import verify_resume
from .search import describe_search
from .runner import RunOptions, Stats, log_summary, run
from .results import ResultsBook, log_totals
from .state import State

EXIT_OK, EXIT_FATAL, EXIT_STOPPED = 0, 1, 130


def run_responses(cfg: Config, opts: RunOptions, root: Path, stats: Stats, log_path: Path) -> int:
    """Отклики на вакансии. Возвращает код завершения."""
    log_dir = root / "logs"
    results = ResultsBook(root)
    started = time.monotonic()
    log.info(f"поиск: {describe_search(cfg)}")

    def work(page):
        log.info("")
        resume_title = verify_resume(page, cfg)
        ai, resume_text = _form_ai(page, cfg, root)
        run(page, cfg, opts, State(root / "data" / "state.json"), results, stats, resume_title, log_dir / "snapshots",
            ai, resume_text)

    code = _with_browser(cfg, root, work)
    log_summary(stats, time.monotonic() - started)
    log_totals(results.summary())
    if results.save_xlsx():
        log.info(f"таблица откликов (Excel): {results.xlsx_path}")
    log.info(f"подробный лог: {log_path}")
    return code


def _form_ai(page, cfg: Config, root: Path):
    """ИИ для анкет работодателей при отклике: включён ли, отвечает ли, плюс текст резюме."""
    if not cfg.ai.enabled:
        log.info("  анкеты работодателей: без ИИ (отвечаем только на один вопрос о зарплате)")
        return None, ""
    from .ai import AiClient, AiError
    from .resume import fetch_resume_text

    try:
        ai = AiClient(cfg.ai)
    except AiError as e:
        log.warning(f"  ! ИИ для анкет недоступен ({e}) — отвечаем только на вопрос о зарплате")
        return None, ""
    log.info(f"  анкеты работодателей заполняет ИИ: {ai.describe()}")
    return ai, fetch_resume_text(page, cfg, root / "data" / "resume.txt")


def run_chats(cfg: Config, dry_run: bool, root: Path, stats: ChatStats, log_path: Path) -> int:
    """Прочитать отказы в чатах. Возвращает код завершения."""
    started = time.monotonic()
    code = _with_browser(cfg, root, lambda page: read_rejections(page, cfg, dry_run, stats, root))
    log_chat_summary(stats, time.monotonic() - started)
    if (stats.answered or dry_run and cfg.ai.enabled) and ResultsBook(root).save_xlsx():
        log.info(f"ответы ИИ — в таблице: {root / 'logs' / 'responses.xlsx'} (лист «Ответы в чатах»)")
    log.info(f"подробный лог: {log_path}")
    return code


def _with_browser(cfg: Config, root: Path, work) -> int:
    cfg.browser.profile_dir = str(root / cfg.browser.profile_dir)
    with sync_playwright() as pw:
        context = None
        try:
            context = launch(pw, cfg)
            page = context.pages[0] if context.pages else context.new_page()
            log.info("")
            check_region(page, cfg)
            log.info("")
            ensure_logged_in(page, cfg)
            work(page)
            return EXIT_OK
        except KeyboardInterrupt:
            log.warning("Остановлено пользователем (Ctrl+C)")
            return EXIT_STOPPED
        except StopRequested:
            log.warning("Остановлено пользователем")
            return EXIT_STOPPED
        except FatalError as e:
            log.error(f"Остановка: {e}")
            return EXIT_FATAL
        except PlaywrightError as e:
            first_line = (str(e).splitlines() or [""])[0]
            if "closed" in first_line.lower():
                log.error("Остановка: окно браузера закрыто")
            else:
                log.error(f"Остановка: ошибка браузера: {first_line}")
                log.debug("трассировка:", exc_info=True)
            return EXIT_FATAL
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass


def fetch_resume(cfg: Config, root: Path, log_path: Path) -> int:
    """Загрузить свежий текст резюме с hh в data/resume.txt (для ИИ и песочницы)."""
    from .resume import fetch_resume_text

    code = _with_browser(cfg, root, lambda page: fetch_resume_text(page, cfg, root / "data" / "resume.txt"))
    log.info(f"подробный лог: {log_path}")
    return code

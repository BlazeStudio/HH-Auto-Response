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
    summary = results.summary()
    log_totals(summary)
    notify_summary(cfg, f"Отклики: {_ending(code)}",
                   f"Отправлено {stats.applied} (с письмом {stats.with_letter}), пропущено {sum(stats.skipped.values())}, "
                   f"ошибок {stats.failed}. Сегодня всего {summary.today.applied}.")
    if results.save_xlsx():
        log.info(f"таблица откликов (Excel): {results.xlsx_path}")
    log.info(f"подробный лог: {log_path}")
    return code


def _ending(code: int) -> str:
    return {EXIT_OK: "готово", EXIT_STOPPED: "остановлено"}.get(code, "остановлено с ошибкой")


def notify_summary(cfg: Config, title: str, text: str) -> None:
    """Уведомление по итогам — только если вы его разрешили ([notify])."""
    if cfg.notify.desktop or cfg.notify.telegram:
        from . import notify

        notify.send(cfg.notify, f"HH-Auto-Response — {title}", text)


def _form_ai(page, cfg: Config, root: Path):
    """ИИ для откликов: анкеты работодателей ([ai] enabled) и письма ([letter] mode = ai). Плюс текст резюме."""
    letters = cfg.letter.mode == "ai"
    if not cfg.ai.enabled:
        log.info("  анкеты работодателей: без ИИ (отвечаем только на один вопрос о зарплате)")
        if not letters:
            return None, ""
    from .ai import AiClient, AiError
    from .resume import fetch_resume_text

    try:
        ai = AiClient(cfg.ai)
    except AiError as e:
        if letters:
            raise FatalError(f"письма пишет нейросеть ([letter] mode = ai), но она недоступна: {e}. "
                             "Проверьте «Настройки» → «ИИ-ответы в чатах» → «Проверить подключение к ИИ»") from e
        log.warning(f"  ! ИИ для анкет недоступен ({e}) — отвечаем только на вопрос о зарплате")
        return None, ""
    if cfg.ai.enabled:
        log.info(f"  анкеты работодателей заполняет ИИ: {ai.describe()}")
    if letters or cfg.letter.mode == "generate" and cfg.ai.enabled:
        log.info(f"  сопроводительные письма {'пишет' if letters else 'без подписки hh пишет'} ИИ: {ai.describe()}")
    return ai, fetch_resume_text(page, cfg, root / "data" / "resume.txt")


def run_chats(cfg: Config, dry_run: bool, root: Path, stats: ChatStats, log_path: Path) -> int:
    """Прочитать отказы в чатах. Возвращает код завершения."""
    started = time.monotonic()
    code = _with_browser(cfg, root, lambda page: read_rejections(page, cfg, dry_run, stats, root))
    log_chat_summary(stats, time.monotonic() - started)
    notify_summary(cfg, f"Чаты: {_ending(code)}",
                   f"Прочитано отказов {stats.opened}, ответов ИИ {stats.answered}, "
                   f"ждут вашего ответа {len(stats.other_unread)}.")
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


def run_login(cfg: Config, root: Path, log_path: Path) -> int:
    """Только вход в hh: открыть браузер, дождаться входа и сохранить сессию (первый запуск на сервере)."""
    code = _with_browser(cfg, root, lambda page: log.success("Сессия сохранена — можно запускать отклики и чаты"))
    log.info(f"подробный лог: {log_path}")
    return code


def fetch_resume(cfg: Config, root: Path, log_path: Path) -> int:
    """Загрузить свежий текст резюме с hh в data/resume.txt (для ИИ и песочницы)."""
    from .resume import fetch_resume_text

    code = _with_browser(cfg, root, lambda page: fetch_resume_text(page, cfg, root / "data" / "resume.txt"))
    log.info(f"подробный лог: {log_path}")
    return code

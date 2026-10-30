"""Запуск сценариев целиком: браузер → проверка РФ → вход → работа → итоги.

Общий код для консольных скриптов (scripts/responses.py, scripts/chats.py) и оконного приложения.
"""

from __future__ import annotations

import copy
import gc
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .web.auth import ensure_logged_in, is_logged_in
from .web import browser
from .web.browser import FatalError, check_region, launch
from .hh.chats import ChatStats, check_invitations, log_chat_summary, read_rejections
from .core.config import Config
from .core import control
from .core.control import BrowserClosed, StopRequested
from .core.logger import log
from .hh import raising
from .hh.resume import verify_resume
from .hh.search import describe_search
from .hh.runner import RunOptions, Stats, log_summary, run
from .reports.results import ResultsBook, log_totals
from .core.state import State
from .reports.stats import chats_counts, record as record_stats, responses_counts
from .hh.vacancy_meta import SkillStats
from .hh import watch

EXIT_OK, EXIT_FATAL, EXIT_STOPPED = 0, 1, 130


def prune_snapshots(folder: Path, keep_days: int) -> None:
    """Снимки страниц копятся быстро (за 12 дней — 220 МБ): старше keep_days удаляем. 0 — хранить всё."""
    if keep_days <= 0 or not folder.is_dir():
        return
    border = time.time() - keep_days * 86400
    removed = size = 0
    for item in folder.iterdir():
        try:
            if item.is_file() and item.stat().st_mtime < border:
                size += item.stat().st_size
                item.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        log.info(f"удалил снимки страниц старше {keep_days} дн.: {removed} файлов, {size / 1024 / 1024:.0f} МБ "
                 "([logs] keep_days)")


def run_responses(cfg: Config, opts: RunOptions, root: Path, stats: Stats, log_path: Path) -> int:
    """Отклики на вакансии. Возвращает код завершения."""
    log_dir = root / "logs"
    results = ResultsBook(root)
    started = time.monotonic()
    prune_snapshots(log_dir / "snapshots", cfg.logs.keep_days)
    log_config_notes(cfg)
    log.info(f"поиск: {describe_search(cfg)}")

    code = _with_browser(cfg, root, lambda page: _apply_work(page, cfg, opts, root, results, stats))
    log_summary(stats, time.monotonic() - started)
    if not opts.dry_run:  # вкладка «Статистика»: только числа за день
        record_stats(root, cfg, responses_counts(stats, time.monotonic() - started))
    summary = results.summary()
    log_totals(summary)
    notify_summary(cfg, f"Отклики: {_ending(code)}",
                   f"Отправлено {stats.applied} (с письмом {stats.with_letter}), пропущено {sum(stats.skipped.values())}, "
                   f"ошибок {stats.failed}. Сегодня всего {summary.today.applied}.")
    if results.save_xlsx():
        log.info(f"таблица откликов (Excel): {results.xlsx_path}")
    log.info(f"подробный лог: {log_path}")
    return code


def _apply_work(page, cfg: Config, opts: RunOptions, root: Path, results: ResultsBook, stats: Stats,
                searches: list[tuple[str, str]] | None = None) -> None:
    """Отклики в открытом браузере: проверка резюме, нейросеть, выдачи. searches — свои выдачи вместо
    выдач из настроек («компании мечты»)."""
    log.info("")
    resume_title = verify_resume(page, cfg)
    ai, resume_text = _form_ai(page, cfg, root)
    if cfg.stats.skill_tips and not resume_text:
        _refresh_resume(page, cfg, root)
    run(page, cfg, opts, State(root / "data" / "state.json"), results, stats, resume_title, root / "logs" / "snapshots",
        ai, resume_text, searches=searches, skills=SkillStats(root) if cfg.stats.skill_tips else None)


RESUME_FRESH = timedelta(days=3)  # текст резюме для советов на вкладке «Статистика» обновляем не чаще


def _refresh_resume(page, cfg: Config, root: Path) -> None:
    """Советы к резюме сравнивают навыки из выдачи с текстом резюме — держим его свежим (раз в 3 дня),
    даже если нейросеть выключена. Резюме неизвестно или не открылось — советы по прошлому тексту."""
    from .hh.resume import fetch_resume_text

    path = root / "data" / "resume.txt"
    try:
        fresh = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime) < RESUME_FRESH
    except OSError:
        fresh = False
    if fresh or not cfg.resume_id:
        return
    try:
        fetch_resume_text(page, cfg, path)
    except Exception as e:  # noqa: BLE001 — советы не важнее откликов (StopRequested сюда не попадает)
        log.debug(f"  резюме для советов не обновилось: {e}")


def log_config_notes(cfg: Config) -> None:
    """Что программа сделала с config.toml при загрузке: дописала новые параметры, пропустила незнакомые."""
    added = getattr(cfg, "added", [])
    if added:
        log.info(f"config.toml дополнен новыми параметрами ({len(added)}): {', '.join(added[:8])}"
                 + ("…" if len(added) > 8 else "") + ". Прежний файл — config.toml.bak")
    ignored = getattr(cfg, "ignored", [])
    if ignored:
        log.warning(f"в config.toml есть незнакомые программе параметры — пропускаю: {', '.join(ignored)} "
                    "(от другой версии или опечатка)")


def _ending(code: int) -> str:
    return {EXIT_OK: "готово", EXIT_STOPPED: "остановлено"}.get(code, "остановлено с ошибкой")


def notify_summary(cfg: Config, title: str, text: str) -> None:
    """Уведомление по итогам — только если вы его разрешили ([notify])."""
    if cfg.notify.desktop or cfg.notify.telegram:
        from .core import notify

        notify.send(cfg.notify, f"HH-Auto-Response — {title}", text)


def _form_ai(page, cfg: Config, root: Path):
    """ИИ для откликов: анкеты ([ai] enabled), письма ([letter] mode = ai) и фильтр вакансий ([ai_filter]).
    Резюме как контекст нужно только анкетам и письмам — для фильтра его не тянем."""
    letters = cfg.letter.mode == "ai"
    needs_resume = cfg.ai.enabled or letters
    if not cfg.ai.enabled:
        log.info("  анкеты работодателей: без ИИ (отвечаем только на один вопрос о зарплате)")
    if not (needs_resume or cfg.ai_filter.enabled):
        return None, ""
    from .ai.client import AiClient, AiError
    from .hh.resume import fetch_resume_text

    try:
        ai = AiClient(cfg.ai)
    except AiError as e:
        if letters:
            raise FatalError(f"письма пишет нейросеть ([letter] mode = ai), но она недоступна: {e}. "
                             "Проверьте «Настройки» → «ИИ-ответы в чатах» → «Проверить подключение к ИИ»") from e
        if cfg.ai.enabled:
            log.warning(f"  ! ИИ для анкет недоступен ({e}) — отвечаем только на вопрос о зарплате")
        if cfg.ai_filter.enabled:
            log.warning(f"  ! ИИ-фильтр вакансий включён, но нейросеть недоступна ({e}) — "
                        + ("вакансии пропускаются ([ai_filter] skip_on_error = true)"
                           if cfg.ai_filter.skip_on_error else "вакансии обработаются без проверки"))
        return None, ""
    if cfg.ai.enabled:
        log.info(f"  анкеты работодателей заполняет ИИ: {ai.describe()}")
    if letters or cfg.letter.mode == "generate" and cfg.ai.enabled:
        log.info(f"  сопроводительные письма {'пишет' if letters else 'без подписки hh пишет'} ИИ: {ai.describe()}")
    if cfg.ai_filter.enabled:
        log.info(f"  интересность вакансий оценивает нейросеть: {ai.describe()}")
    resume_text = fetch_resume_text(page, cfg, root / "data" / "resume.txt") if needs_resume else ""
    return ai, resume_text


def run_chats(cfg: Config, dry_run: bool, root: Path, stats: ChatStats, log_path: Path) -> int:
    """Прочитать отказы в чатах. Возвращает код завершения."""
    started = time.monotonic()
    log_config_notes(cfg)
    code = _with_browser(cfg, root, lambda page: read_rejections(page, cfg, dry_run, stats, root))
    log_chat_summary(stats, time.monotonic() - started)
    if not dry_run:
        record_stats(root, cfg, chats_counts(stats, time.monotonic() - started))
    notify_summary(cfg, f"Чаты: {_ending(code)}",
                   f"Прочитано отказов {stats.opened}, ответов ИИ {stats.answered}, "
                   f"ждут вашего ответа {len(stats.other_unread)}.")
    if (stats.answered or dry_run and cfg.ai.enabled) and ResultsBook(root).save_xlsx():
        log.info(f"ответы ИИ — в таблице: {root / 'logs' / 'responses.xlsx'} (лист «Ответы в чатах»)")
    log.info(f"подробный лог: {log_path}")
    return code


def _with_browser(cfg: Config, root: Path, work, auto_raise: bool = True, login: bool = True) -> int:
    """auto_raise — заодно поднять резюме, если пора; login — нет входа: показать окно входа и ждать
    (False — сразу закончить с ошибкой: так работают фоновые запуски, чтобы окно не выскакивало само)."""
    try:
        return _browser_session(cfg, root, work, auto_raise, login)
    except Exception:
        if not control.user_stopped():
            raise
        # «Остановить» завершило зависший браузер — драйвер Playwright при выходе уже не отвечает
        log.debug("после остановки:", exc_info=True)
        return EXIT_STOPPED
    finally:
        _after_browser()


def _after_browser() -> None:
    """После работы — освободить память сразу: процессы браузера и драйвера этого запуска, если какие-то
    не закрылись, завершить; модель Ollama, которую загружали мы, выгрузить (иначе Ollama держит её ещё
    5 минут — 5–6 ГБ). Следующий запуск загрузит модель заново за секунды."""
    left = browser.finish_processes()
    if left:
        log.debug(f"после закрытия браузера оставалось процессов: {left} — завершены")
    from .ai.client import release_models

    release_models()
    gc.collect()


def _browser_session(cfg: Config, root: Path, work, auto_raise: bool, login: bool) -> int:
    cfg.browser.profile_dir = str(root / cfg.browser.profile_dir)
    control.set_starting(True)  # до начала работы «Остановить» закрывает браузер почти сразу — терять нечего
    with sync_playwright() as pw:
        context = None
        try:
            # «Остановить» проверяем между шагами запуска: сам шаг (запуск браузера, открытие hh) не прервать,
            # и раньше после него запуск шёл дальше — до первой паузы уже в работе
            control.check()
            context = launch(pw, cfg)
            control.check()
            page = context.pages[0] if context.pages else context.new_page()
            browser.attach(page)  # закрыли окно браузера — работа остановится сразу, а не по таймауту
            log.info("")
            check_region(page, cfg)
            control.check()
            log.info("")
            if login:
                ensure_logged_in(page, cfg)
            elif not is_logged_in(page):
                raise FatalError("нет входа в hh — запустите «Отклики» или «Чаты» и войдите в открывшемся окне")
            control.check()
            if auto_raise:
                _raise_if_due(page, cfg, root)
            control.set_starting(False)
            work(page)
            return EXIT_OK
        except KeyboardInterrupt:
            log.warning("Остановлено пользователем (Ctrl+C)")
            return EXIT_STOPPED
        except BrowserClosed as e:
            log.warning("Остановлено пользователем" if control.user_stopped() else f"Остановка: {e} — работа прервана")
            return EXIT_STOPPED
        except StopRequested:
            log.warning("Остановлено пользователем")
            return EXIT_STOPPED
        except FatalError as e:
            if control.user_stopped():  # «Остановить» закрыло браузер посреди шага — это не ошибка
                log.warning("Остановлено пользователем")
                log.debug(f"шаг прервался: {e}")
                return EXIT_STOPPED
            log.error(f"Остановка: {e}")
            browser.last_error = str(e)
            return EXIT_FATAL
        except PlaywrightError as e:
            first_line = (str(e).splitlines() or [""])[0]
            if "closed" in first_line.lower():
                log.warning("Остановлено пользователем" if control.user_stopped()
                            else "Остановка: окно браузера закрыто — работа прервана")
                return EXIT_STOPPED
            else:
                log.error(f"Остановка: ошибка браузера: {first_line}")
                log.debug("трассировка:", exc_info=True)
            return EXIT_FATAL
        except Exception:
            if not control.user_stopped():
                raise
            # браузер не закрывался, и «Остановить» завершило его процессы — связь с драйвером оборвалась
            log.warning("Остановлено пользователем")
            log.debug("трассировка:", exc_info=True)
            return EXIT_STOPPED
        finally:
            control.set_starting(False)
            browser.detach()
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass


def _raise_if_due(page, cfg: Config, root: Path) -> None:
    """Браузер уже открыт и вход есть — заодно поднять резюме, если подошло время ([raising]). Сбой подъёма
    основную работу не останавливает."""
    try:
        if raising.raise_due(page, cfg, root) is not None:
            log.info("")
    except Exception as e:  # noqa: BLE001 — «Остановить» (StopRequested) сюда не попадает
        control.check()  # …но браузер, закрытый «Остановить» посреди подъёма, — это остановка, а не сбой подъёма
        log.warning(f"  ! поднять резюме не получилось: {(str(e).splitlines() or [''])[0]}")
        log.debug("трассировка:", exc_info=True)


def run_raise(cfg: Config, root: Path, log_path: Path, result: dict | None = None, login: bool = True) -> int:
    """Только поднять резюме (scripts/raise_resume.py). Если ещё рано — браузер не открываем."""
    due = raising.next_due(root, cfg)
    if due is None:
        log_config_notes(cfg)
        log.warning("поднятие резюме выключено: [raising] enabled = false")
        return EXIT_OK
    if due > datetime.now():
        log_config_notes(cfg)
        log.info(f"поднимать резюме ещё рано — следующее в {due:%H:%M} ({due:%d.%m})")
        return EXIT_OK
    return run_background(cfg, root, log_path, result, login=login, tasks=["raise"])


def run_background(cfg: Config, root: Path, log_path: Path, result: dict | None = None, login: bool = True,
                   tasks: list[str] | None = None, force_raise: bool = False) -> int:
    """Фоновая проверка: всё, чему подошло время, — за один запуск браузера: подъём резюме ([raising]),
    приглашения в чатах ([watch] invitations), свежие вакансии «компаний мечты» ([dream]). Ничего не пора —
    браузер не открываем. tasks — что делать (по умолчанию — что пора); force_raise — «Поднять сейчас».
    result — итоги для окна приложения: "raise" (RaiseResult), "invitations", "dream" (Stats), "error".
    login=False — нет входа в hh: не ждать его, а закончить (фоновый запуск в приложении)."""
    log_config_notes(cfg)
    result = result if result is not None else {}
    if force_raise:
        cfg.raising.enabled = True
        raising.forget_due(root)
    todo = list(tasks) if tasks is not None else watch.due_tasks(root, cfg)
    if not todo:
        moment = watch.next_any(root, cfg)
        log.info("фоновая проверка: сейчас делать нечего" + (f" — следующая {moment:%d.%m в %H:%M}" if moment else ""))
        return EXIT_OK
    searches = watch.dream_searches(cfg) if "dream" in todo else []
    if "dream" in todo and not searches:
        log.warning("компании мечты: не задан поиск (ссылка или запросы) — без него откликались бы на все вакансии "
                    "компании, проверка пропущена")
        watch.mark(root, "dream")
        todo.remove("dream")
        if not todo:
            return EXIT_OK
    log.info("фоновая проверка: " + ", ".join(watch.TASKS[t] for t in todo))
    started = time.monotonic()
    stats = Stats()
    results = ResultsBook(root)

    def work(page):
        if "raise" in todo:
            outcome = raising.raise_due(page, cfg, root, force=force_raise)
            result["raise"] = outcome
            if todo == ["raise"] and outcome is not None and outcome.failed and not (outcome.raised or outcome.early):
                raise FatalError("резюме поднять не получилось — подробности выше")
        if "invitations" in todo:
            watch.mark(root, "invitations")  # и при сбое: следующая попытка — через every_minutes, а не сразу
            log.info("")
            result["invitations"] = check_invitations(page, cfg, root)
        if "dream" in todo:
            watch.mark(root, "dream")
            result["dream"] = stats
            log.info("")
            log.info(f"Компании мечты: свежие вакансии ({len(searches)} комп.), не больше {cfg.dream.max_per_check} "
                     "откликов за проверку")
            dream_cfg = copy.copy(cfg)  # смотрим только первые страницы: свежие вакансии — сверху
            dream_cfg.limits = replace(cfg.limits, max_pages=min(cfg.limits.max_pages, 3))
            _apply_work(page, dream_cfg, RunOptions(limit=cfg.dream.max_per_check), root, results, stats, searches)

    browser.last_error = ""
    code = _with_browser(cfg, root, work, auto_raise=False, login=login)
    if code == EXIT_FATAL:
        result["error"] = browser.last_error  # «нет входа в hh …» — для меню слева
    if "dream" in todo and (stats.applied or stats.failed or stats.skipped):
        log_summary(stats, time.monotonic() - started)
        record_stats(root, cfg, responses_counts(stats, time.monotonic() - started))
        if stats.applied:
            notify_summary(cfg, "компании мечты", f"Отправлено откликов: {stats.applied}")
            results.save_xlsx()
    _prune_logs(root / "logs", "raise_", keep=30)
    _prune_logs(root / "logs", "watch_", keep=60)
    log.info(f"подробный лог: {log_path}")
    return code


def _prune_logs(folder: Path, prefix: str, keep: int) -> None:
    """Журналы фоновых проверок пишутся каждый час — храним последние keep, чтобы не заслоняли остальные."""
    logs = sorted(folder.glob(f"{prefix}*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in logs[keep:]:
        try:
            path.unlink()
        except OSError:
            pass


def run_login(cfg: Config, root: Path, log_path: Path) -> int:
    """Только вход в hh: открыть браузер, дождаться входа и сохранить сессию (первый запуск на сервере)."""
    code = _with_browser(cfg, root, lambda page: log.success("Сессия сохранена — можно запускать отклики и чаты"))
    log.info(f"подробный лог: {log_path}")
    return code


def fetch_resume(cfg: Config, root: Path, log_path: Path) -> int:
    """Загрузить свежий текст резюме с hh в data/resume.txt (для ИИ и песочницы)."""
    from .hh.resume import fetch_resume_text

    code = _with_browser(cfg, root, lambda page: fetch_resume_text(page, cfg, root / "data" / "resume.txt"))
    log.info(f"подробный лог: {log_path}")
    return code

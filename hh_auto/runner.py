"""Главный цикл: страницы выдачи → вакансии → отклик → пауза → следующая."""

from __future__ import annotations

import random
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page

from .browser import FatalError
from .config import Config
from .logger import log
from .responder import Responder, Status
from .search import has_next_page, open_results_page, results_page_url
from .state import ResultsCsv, State


@dataclass
class RunOptions:
    dry_run: bool = False
    limit: int | None = None
    start_page: int = 0
    retry_skipped: bool = False


@dataclass
class Stats:
    applied: int = 0
    with_letter: int = 0
    failed: int = 0
    already: int = 0
    dry_run: int = 0
    skipped: Counter = field(default_factory=Counter)


def run(
    page: Page,
    cfg: Config,
    opts: RunOptions,
    state: State,
    results: ResultsCsv,
    stats: Stats,
    resume_title: str | None,
    snapshots_dir: Path,
) -> None:
    limits = cfg.limits
    max_responses = opts.limit or limits.max_responses
    log.info("")
    log.info("Шаг 3/3: отклики на вакансии" + ("  [DRY-RUN: только список, без кликов]" if opts.dry_run else ""))
    log.info(
        f"  лимит на этот запуск: {max_responses} откликов, пауза между вакансиями "
        f"{limits.delay_min:g}–{limits.delay_max:g} с, страниц выдачи максимум {limits.max_pages}"
    )
    responder = Responder(page, cfg, snapshots_dir, resume_title)
    errors_in_row = 0

    for page_num in range(opts.start_page, limits.max_pages):
        url = results_page_url(cfg.search_url, page_num)
        log.info("")
        log.info(f"━━━━━━━━━━ Страница выдачи №{page_num + 1} ━━━━━━━━━━")
        vacancies = open_results_page(page, url)
        if not vacancies:
            log.info("  вакансий на странице нет — выдача закончилась")
            return
        can_respond = sum(v.can_respond for v in vacancies)
        done_before = sum(1 for v in vacancies if state.seen(v.id, opts.retry_skipped))
        log.success(
            f"  ✓ подтянуто вакансий: {len(vacancies)}, с кнопкой «Откликнуться»: {can_respond}, "
            f"обработаны в прошлых запусках: {done_before}"
        )

        for i, vac in enumerate(vacancies, 1):
            tag = f"[стр. {page_num + 1}, {i}/{len(vacancies)}]"
            seen = state.seen(vac.id, opts.retry_skipped)
            if seen:
                stats.already += 1
                log.debug(f"{tag} «{vac.title}» — {seen}")
                continue
            if not vac.can_respond:
                stats.already += 1
                log.info(f"{tag} «{vac.title}» — кнопки «Откликнуться» нет ({vac.button_text or 'уже откликались'}), пропускаю")
                continue

            log.info("")
            log.info(f"{tag} ▶ «{vac.title}» — {vac.company}")
            log.info(f"  {vac.url}")
            if opts.dry_run:
                stats.dry_run += 1
                log.info("  [dry-run] здесь был бы отклик")
                continue

            result = responder.apply(vac)
            results.add(vac, result)
            if result.status is Status.APPLIED:
                stats.applied += 1
                stats.with_letter += result.letter
                state.mark_applied(vac, result.letter)
                if result.reason:
                    log.warning(f"  ! {result.reason}")
                log.info(f"  итого отправлено откликов: {stats.applied} из {max_responses}")
            elif result.status is Status.SKIPPED:
                stats.skipped[result.reason] += 1
                if result.remember:
                    state.mark_skipped(vac, result.reason)
                log.warning(f"  ⤼ вакансия пропущена: {result.reason}")
            elif result.status is Status.FAILED:
                stats.failed += 1
                log.error(f"  ✗ не удалось откликнуться: {result.reason}")
            else:  # Status.LIMIT
                log.error("hh сообщил, что лимит откликов исчерпан — останавливаюсь. Попробуйте через сутки.")
                return

            errors_in_row = errors_in_row + 1 if result.status is Status.FAILED else 0
            if errors_in_row >= limits.max_consecutive_errors:
                raise FatalError(
                    f"{errors_in_row} ошибок подряд — похоже, hh поменял вёрстку или слетела сессия. "
                    f"Скриншоты и HTML страниц: {snapshots_dir}"
                )
            if stats.applied >= max_responses:
                log.success(f"Достигнут лимит этого запуска: {max_responses} откликов")
                return

            responder.back_to_results(url)
            _sleep(limits.delay_min, limits.delay_max, "перед следующей вакансией")

        if not has_next_page(page):
            log.info("  это последняя страница выдачи")
            return
        _sleep(limits.page_delay_min, limits.page_delay_max, "перед следующей страницей выдачи")

    log.info(f"Пройдено максимальное число страниц ([limits] max_pages = {limits.max_pages})")


def log_summary(stats: Stats, elapsed: float) -> None:
    minutes, seconds = divmod(int(elapsed), 60)
    log.info("")
    log.info("══════════════ Итоги запуска ══════════════")
    log.success(f"  откликов отправлено:        {stats.applied} (из них с письмом: {stats.with_letter})")
    if stats.dry_run:
        log.info(f"  [dry-run] вакансий к отклику: {stats.dry_run}")
    log.info(f"  пропущено в этом запуске:   {sum(stats.skipped.values())}")
    for reason, count in stats.skipped.most_common():
        log.info(f"      {count} × {reason}")
    log.info(f"  уже откликались / ранее:    {stats.already}")
    (log.error if stats.failed else log.info)(f"  ошибок:                     {stats.failed}")
    log.info(f"  время работы:               {minutes} мин {seconds} с")


def _sleep(low: float, high: float, what: str) -> None:
    delay = random.uniform(low, high)
    log.info(f"  ⏸ пауза {delay:.1f} с {what}")
    time.sleep(delay)

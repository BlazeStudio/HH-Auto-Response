"""Главный цикл: страницы выдачи → вакансии → отклик → пауза → следующая."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page

from . import control
from .browser import FatalError
from .config import Config
from .logger import log
from .responder import Responder, Status
from .search import build_search_url, describe_hh_filters, next_page_info, open_results_page, results_page_url
from .results import ResultsBook
from .state import State


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
    results: ResultsBook,
    stats: Stats,
    resume_title: str | None,
    snapshots_dir: Path,
    ai=None,
    resume_text: str = "",
) -> None:
    limits = cfg.limits
    max_responses = opts.limit or limits.max_responses
    log.info("")
    log.info("Шаг 3/3: отклики на вакансии" + ("  [DRY-RUN: только список, без кликов]" if opts.dry_run else ""))
    log.info(
        f"  лимит на этот запуск: {max_responses} откликов, пауза между вакансиями "
        f"{limits.delay_min:g}–{limits.delay_max:g} с, страниц выдачи максимум {limits.max_pages}"
    )
    search_url = build_search_url(cfg)
    log.info(f"  фильтры hh: {describe_hh_filters(search_url)}")
    responder = Responder(page, cfg, snapshots_dir, resume_title, ai, resume_text)
    errors_in_row = 0

    for page_num in range(opts.start_page, limits.max_pages):
        url = results_page_url(search_url, page_num)
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

        skipped_before = sum(stats.skipped.values())
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

            excluded = _find_word(vac.title, cfg.filters.exclude_title_words)
            if excluded:
                stats.skipped[f"в названии «{excluded}»"] += 1
                log.info(f"{tag} «{vac.title}» — в названии «{excluded}» ([filters]), пропускаю")
                continue
            company_word = _find_word(vac.company, cfg.filters.exclude_company_words)
            if company_word:
                stats.skipped[f"компания: «{company_word}»"] += 1
                log.info(f"{tag} «{vac.title}» — компания «{vac.company}» содержит «{company_word}» ([filters]), пропускаю")
                continue
            if cfg.filters.include_words:
                where = f"{vac.title} {vac.snippet}" if cfg.filters.include_in_snippet else vac.title
                if not _find_word(where, cfg.filters.include_words):
                    stats.skipped["нет нужных слов ([filters] include_words)"] += 1
                    log.info(f"{tag} «{vac.title}» — нет ни одного слова из [filters] include_words, пропускаю")
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
                    (log.info if result.letter else log.warning)(f"  {'' if result.letter else '! '}{result.reason}")
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

        skipped_on_page = sum(stats.skipped.values()) - skipped_before
        if skipped_on_page >= len(vacancies) * 0.8:
            log.warning(f"  ! на этой странице фильтры отсеяли {skipped_on_page} из {len(vacancies)} вакансий — "
                        "если откликов мало, проверьте [filters] (особенно «только со словами»)")
        has_next, why = next_page_info(page, page_num)
        if not has_next:
            log.info(f"  выдача закончилась: {why}")
            return
        log.info(f"  дальше: {why}")
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


def _find_word(title: str, words: list[str]) -> str | None:
    lowered = title.lower()
    return next((w for w in words if w.lower() in lowered), None)


def _sleep(low: float, high: float, what: str) -> None:
    delay = random.uniform(low, high)
    log.info(f"  ⏸ пауза {delay:.1f} с {what}")
    control.sleep(delay)

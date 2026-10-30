"""Главный цикл: страницы выдачи → вакансии → отклик → пауза → следующая."""

from __future__ import annotations

import hashlib
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page

from . import control
from .ai import AiError
from .browser import FatalError
from .config import Config
from .logger import log
from .responder import MOVED, Responder, Result, Status
from .search import (Vacancy, collect_vacancies, describe_hh_filters, next_page_info, open_results_page,
                     results_page_url, search_urls, wait_all_cards)
from .results import ResultsBook
from .state import State, company_key
from .vacancy_meta import SkillStats, skip_reason


RESCANS = 2  # сколько раз дособрать вакансии, которые hh подвинул на текущую страницу
AI_SKIP = "ИИ-фильтр: не подходит под ваше описание"  # одна строка в итогах; причина по каждой вакансии — в Excel


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
    searches: list[tuple[str, str]] | None = None,
    skills: SkillStats | None = None,
) -> None:
    """searches — свои выдачи (подпись, ссылка) вместо выдач из настроек (так проверяются «компании мечты»);
    skills — куда считать ключевые навыки из выдачи (советы к резюме, [stats] skill_tips)."""
    limits = cfg.limits
    max_responses = opts.limit or limits.max_responses
    log.info("")
    log.info("Шаг 3/3: отклики на вакансии" + ("  [DRY-RUN: только список, без кликов]" if opts.dry_run else ""))
    log.info(
        f"  лимит на этот запуск: {max_responses} откликов, пауза между вакансиями "
        f"{limits.delay_min:g}–{limits.delay_max:g} с, страниц выдачи максимум {limits.max_pages}"
    )
    searches = searches or search_urls(cfg)
    log.info(f"  фильтры hh: {describe_hh_filters(searches[0][1])}")
    if limits.per_company:
        log.info(f"  в одну компанию — не больше {limits.per_company} откл. за {limits.per_company_days} дн. "
                 "([limits] per_company)")
    # Нейросеть для писем и анкет — только если вы их включали. Включённый [ai_filter] тоже создаёт
    # нейросеть, но письма от этого писать ей нельзя: резюме для фильтра даже не загружается.
    responder_ai = ai if (cfg.ai.enabled or cfg.letter.mode == "ai") else None
    responder = Responder(page, cfg, snapshots_dir, resume_title, responder_ai, resume_text)
    errors_in_row = 0
    filters_warned = False
    criteria_key = _criteria_key(cfg)
    handled: set[str] = set()  # вакансии этого запуска: в выдаче другого запроса их второй раз не смотрим
    planned: Counter = Counter()  # пробный прогон: «откликнулись бы» по компаниям — для лимита per_company

    def handle(vac: Vacancy, tag: str, url: str, label: str) -> bool:
        """Одна вакансия: фильтры → отклик → итог. False — запуск пора заканчивать."""
        nonlocal errors_in_row
        if vac.id in handled:
            log.debug(f"{tag} «{vac.title}» — уже была в этом запуске")
            return True
        handled.add(vac.id)
        seen = state.seen(vac.id, opts.retry_skipped)
        if seen and _ai_skip_outdated(state, vac.id, cfg, criteria_key):
            seen = None  # отказ ИИ-фильтра по старому описанию (или фильтр выключили) — смотрим заново
        if seen:
            stats.already += 1
            log.debug(f"{tag} «{vac.title}» — {seen}")
            return True
        if not vac.can_respond:
            stats.already += 1
            log.info(f"{tag} «{vac.title}» — кнопки «Откликнуться» нет ({vac.button_text or 'уже откликались'}), пропускаю")
            return True

        excluded = _find_word(vac.title, cfg.filters.exclude_title_words)
        if excluded:
            stats.skipped[f"в названии «{excluded}»"] += 1
            log.info(f"{tag} «{vac.title}» — в названии «{excluded}» ([filters]), пропускаю")
            return True
        company_word = _find_word(vac.company, cfg.filters.exclude_company_words)
        if company_word:
            stats.skipped[f"компания: «{company_word}»"] += 1
            log.info(f"{tag} «{vac.title}» — компания «{vac.company}» содержит «{company_word}» ([filters]), пропускаю")
            return True
        if cfg.filters.include_company_words and not _find_word(vac.company, cfg.filters.include_company_words):
            stats.skipped["компания не из списка ([filters] include_company_words)"] += 1
            log.info(f"{tag} «{vac.title}» — компания «{vac.company}» не из списка include_company_words, пропускаю")
            return True
        if cfg.filters.include_words:
            where = f"{vac.title} {vac.snippet}" if cfg.filters.include_in_snippet else vac.title
            if not _find_word(where, cfg.filters.include_words):
                stats.skipped["нет нужных слов ([filters] include_words)"] += 1
                log.info(f"{tag} «{vac.title}» — нет ни одного слова из [filters] include_words, пропускаю")
                return True
        skip = skip_reason(vac.title, vac.snippet, vac.meta, cfg)  # уровень, данные hh, вопросы при отклике
        if skip:
            stats.skipped[skip[0]] += 1
            log.info(f"{tag} «{vac.title}» — {skip[1]}, пропускаю")
            return True
        if limits.per_company and vac.company:
            sent = state.company_applied(vac.company, limits.per_company_days) + planned[company_key(vac.company)]
            if sent >= limits.per_company:
                stats.skipped[f"лимит откликов в одну компанию ([limits] per_company)"] += 1
                log.info(f"{tag} «{vac.title}» — в «{vac.company}» уже {sent} откл. за {limits.per_company_days} дн. "
                         "([limits] per_company), пропускаю")
                return True
        if cfg.ai_filter.enabled:
            ai_reason, rejected = _ai_skip_reason(cfg, ai, vac, tag)
            if ai_reason:
                stats.skipped[AI_SKIP if rejected else ai_reason] += 1
                log.info(f"{tag} «{vac.title}» — {ai_reason}, пропускаю")
                if rejected and not opts.dry_run:
                    # запоминаем: в следующих запусках не спрашиваем нейросеть снова (токены и время), а в
                    # таблице на листе «Пропущенные» видно, что и почему отсеяно, — по ней удобно править описание
                    state.mark_skipped(vac, f"{AI_SKIP} [{criteria_key}]: {ai_reason}")
                    results.add(vac, Result(Status.SKIPPED, ai_reason, remember=True))
                return True

        log.info("")
        log.info(f"{tag} ▶ «{vac.title}» — {vac.company}")
        log.info(f"  {vac.url}")
        if opts.dry_run:
            stats.dry_run += 1
            planned[company_key(vac.company)] += 1
            log.info("  [dry-run] здесь был бы отклик")
            return True

        result = responder.apply(vac)
        if result.reason != MOVED:  # вакансию ещё не трогали — в таблицу её не пишем
            # для воронки на вкладке «Статистика»: из какого поиска, какое письмо, совпадение по навыкам
            results.add(vac, result, {"query": label, "letter_kind": responder.letter_kind if result.letter else "none",
                                      "match": vac.meta.match if vac.meta else None})
        else:
            handled.discard(vac.id)  # hh увёз её на другую страницу — встретим там и обработаем
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
            (log.info if result.reason == MOVED else log.warning)(f"  ⤼ вакансия пропущена: {result.reason}")
        elif result.status is Status.FAILED:
            stats.failed += 1
            log.error(f"  ✗ не удалось откликнуться: {result.reason}")
        else:  # Status.LIMIT
            log.error("hh сообщил, что лимит откликов исчерпан — останавливаюсь. Попробуйте через сутки.")
            return False

        errors_in_row = errors_in_row + 1 if result.status is Status.FAILED else 0
        if errors_in_row >= limits.max_consecutive_errors:
            raise FatalError(
                f"{errors_in_row} ошибок подряд — похоже, hh поменял вёрстку или слетела сессия. "
                + (f"Скриншоты и HTML страниц: {snapshots_dir}" if cfg.logs.snapshots else
                   "Включите снимки страниц ([logs] snapshots), чтобы было по чему разобраться")
            )
        if stats.applied >= max_responses:
            log.success(f"Достигнут лимит этого запуска: {max_responses} откликов")
            return False

        responder.back_to_results(url)
        if responder.clicked:
            _sleep(limits.delay_min, limits.delay_max, "перед следующей вакансией")
        else:  # в hh ничего не нажимали (вакансия уехала, кнопки уже нет) — паузе 6–15 с нечего разделять
            log.debug("  в hh ничего не нажимали — без паузы")
        return True

    def walk(label: str, search_url: str, start_page: int) -> bool:
        """Одна выдача по страницам. False — запуск закончен (лимит откликов, лимит hh)."""
        nonlocal filters_warned
        for page_num in range(start_page, limits.max_pages):
            url = results_page_url(search_url, page_num)
            log.info("")
            log.info(f"━━━━━━━━━━ Страница выдачи №{page_num + 1} ━━━━━━━━━━")
            vacancies = open_results_page(page, url)
            if not vacancies:
                log.info("  вакансий на странице нет — выдача закончилась")
                return True
            if skills is not None:
                skills.record([v.meta for v in vacancies if v.meta])
            can_respond = sum(v.can_respond for v in vacancies)
            done_before = sum(1 for v in vacancies if state.seen(v.id, opts.retry_skipped))
            log.success(
                f"  ✓ подтянуто вакансий: {len(vacancies)}, с кнопкой «Откликнуться»: {can_respond}, "
                f"обработаны в прошлых запусках: {done_before}"
            )

            skipped_before = sum(stats.skipped.values())
            on_page = {v.id for v in vacancies}
            batch = vacancies
            for rescan in range(RESCANS + 1):
                for i, vac in enumerate(batch, 1):
                    if not handle(vac, f"[стр. {page_num + 1}, {i}/{len(batch)}{'+' if rescan else ''}]", url, label):
                        return False
                # После перезагрузки выдачи hh заново сортирует «подходящие к резюме»: часть вакансий уезжает
                # на другие страницы, а на эту приезжают новые. Их проходим сразу, не дожидаясь следующего запуска.
                if opts.dry_run or rescan == RESCANS or not responder.on_search_page():
                    break
                wait_all_cards(page)
                batch = [v for v in collect_vacancies(page) if v.id not in on_page]
                on_page.update(v.id for v in batch)
                fresh = [v for v in batch if v.can_respond and not state.seen(v.id, opts.retry_skipped)]
                if not fresh:
                    break
                log.info("")
                log.info(f"  hh пересортировал выдачу: на этой странице появилось ещё {len(fresh)} новых вакансий — прохожу их")

            skipped_on_page = sum(stats.skipped.values()) - skipped_before
            if skipped_on_page >= len(on_page) * 0.8 and not filters_warned:
                filters_warned = True  # одного раза за запуск достаточно
                log.warning(f"  ! на этой странице фильтры отсеяли {skipped_on_page} из {len(on_page)} вакансий — "
                            "если откликов мало, проверьте [filters] (особенно «только со словами»)")
            has_next, why = next_page_info(page, page_num)
            if not has_next:
                log.info(f"  выдача закончилась: {why}")
                return True
            log.info(f"  дальше: {why}")
            _sleep(limits.page_delay_min, limits.page_delay_max, "перед следующей страницей выдачи")

        log.info(f"Пройдено максимальное число страниц ([limits] max_pages = {limits.max_pages})")
        return True

    for number, (label, search_url) in enumerate(searches, 1):
        if len(searches) > 1:
            log.info("")
            log.info(f"══════════ Поиск {number} из {len(searches)}: {label} ══════════")
        if not walk(label, search_url, opts.start_page if number == 1 else 0):
            return
        if number < len(searches):
            _sleep(limits.page_delay_min, limits.page_delay_max, "перед следующим запросом")


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


def _criteria_key(cfg: Config) -> str:
    """Отпечаток описания из [ai_filter]: поменяли описание — старые отказы фильтра пересматриваются."""
    text = " ".join(cfg.ai_filter.criteria.lower().split())
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def _ai_skip_outdated(state: State, vacancy_id: str, cfg: Config, key: str) -> bool:
    """Вакансию раньше отсеял ИИ-фильтр, но фильтр с тех пор выключили или поменяли описание."""
    entry = state.skipped.get(vacancy_id)
    if not entry or not str(entry.get("reason", "")).startswith(AI_SKIP):
        return False
    return not cfg.ai_filter.enabled or f"[{key}]" not in entry["reason"]


def _ai_skip_reason(cfg: Config, ai, vac: Vacancy, tag: str) -> tuple[str, bool]:
    """Спросить нейросеть, интересна ли вакансия ([ai_filter]). Возвращает (причина пропуска или '',
    отказ ли это нейросети). '' — вакансию обрабатываем как обычно. При сбое ИИ решает [ai_filter] skip_on_error;
    такой пропуск не запоминается — в следующий раз спросим снова."""
    if ai is None:
        log.warning(f"{tag} [ai_filter] включён, но нейросеть недоступна — пропускаю проверку")
        return ("ИИ: не удалось проверить (нейросеть недоступна)" if cfg.ai_filter.skip_on_error else ""), False
    log.info(f"{tag} нейросеть оценивает вакансию…")
    try:
        verdict = ai.evaluate_vacancy(cfg.ai_filter.criteria, vac.title, vac.company, vac.snippet,
                                      cfg.ai_filter.max_snippet_chars, remote=vac.remote)
    except AiError as e:
        log.warning(f"{tag} нейросеть не оценила вакансию: {e}")
        return ("ИИ: не удалось проверить (ошибка нейросети)" if cfg.ai_filter.skip_on_error else ""), False
    if verdict.interesting:
        log.success(f"{tag} нейросеть: интересна" + (f" — {verdict.reason}" if verdict.reason else ""))
        return "", False
    return "ИИ: не интересна" + (f" — {verdict.reason}" if verdict.reason else ""), True


def _sleep(low: float, high: float, what: str) -> None:
    delay = random.uniform(low, high)
    log.info(f"  ⏸ пауза {delay:.1f} с {what}")
    control.sleep(delay)

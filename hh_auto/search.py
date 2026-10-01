"""Страницы выдачи: переход по страницам и сбор карточек вакансий."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from . import selectors as S
from .auth import hh_role, is_auth_url
from .browser import FatalError, wait_captcha
from .logger import log


@dataclass
class Vacancy:
    id: str
    title: str
    company: str
    button_text: str  # текст кнопки отклика в карточке ("" — кнопки нет)
    remote: bool = False  # в карточке есть «Можно удалённо»
    snippet: str = ""  # обязанности и требования из карточки (коротко)

    @property
    def url(self) -> str:
        return f"https://hh.ru/vacancy/{self.id}"

    @property
    def can_respond(self) -> bool:
        return "Откликнуться" in self.button_text


_COLLECT_JS = """
(s) => Array.from(document.querySelectorAll(s.card)).map((card) => {
  const text = (sel) => (card.querySelector(sel)?.textContent || '').replace(/\\s+/g, ' ').trim();
  const link = card.querySelector(s.link);
  const match = link && link.href.match(/\\/vacancy\\/(\\d+)/);
  return {
    id: match ? match[1] : '', title: text(s.title), company: text(s.employer),
    button_text: text(s.button), remote: !!card.querySelector(s.remote),
    snippet: Array.from(card.querySelectorAll(s.snippet)).map((el) => el.textContent).join(' ').replace(/\s+/g, ' ').trim(),
  };
}).filter((v) => v.id)
"""


SEARCH_PAGE = "https://hh.ru/search/vacancy"


def build_search_url(cfg) -> str:
    """Ссылка выдачи для запуска: ссылка из настроек или общий поиск по запросу, плюс фильтры hh."""
    if cfg.search.mode == "query":
        query = [("text", cfg.search.query.strip())]
        if cfg.search.area:
            query.append(("area", cfg.search.area))
        if cfg.search.title_only:
            query.append(("search_field", "name"))
        query += [("enable_snippets", "true"), ("hhtmFrom", "vacancy_search_list")]
        url = f"{SEARCH_PAGE}?{urlencode(query)}"
    else:
        url = cfg.search_url
    return apply_hh_filters(url, cfg.filters.experience, cfg.filters.work_format)


def describe_search(cfg) -> str:
    if cfg.search.mode == "query":
        from .config import AREAS

        area = AREAS.get(cfg.search.area, f"регион {cfg.search.area}")
        where = "в названии" if cfg.search.title_only else "везде"
        return f"запрос «{cfg.search.query.strip()}» ({area}, искать {where})"
    return f"ссылка: {cfg.search_url}"


def apply_hh_filters(search_url: str, experience: list[str], work_format: list[str]) -> str:
    """Подставляет в ссылку поиска фильтры hh «Опыт работы» и «Формат работы».

    Пустой список — параметр не трогаем (остаётся как в исходной ссылке).
    """
    u = urlparse(search_url)
    query = parse_qsl(u.query, keep_blank_values=True)
    for key, values in (("experience", experience), ("work_format", work_format)):
        if values:
            query = [(k, v) for k, v in query if k != key] + [(key, v) for v in values]
    if any(len(v) > 1 for v in (experience, work_format)) and not any(k == "ored_clusters" for k, _ in query):
        query.append(("ored_clusters", "true"))  # несколько значений одного фильтра — через «ИЛИ»
    return urlunparse(u._replace(query=urlencode(query)))


def describe_hh_filters(search_url: str) -> str:
    """Человекочитаемое описание фильтров опыта и формата в ссылке — для лога."""
    from .config import EXPERIENCE, WORK_FORMAT

    query = parse_qsl(urlparse(search_url).query)
    parts = []
    for key, title, names in (("experience", "опыт", EXPERIENCE), ("work_format", "формат", WORK_FORMAT)):
        values = [names.get(v, v) for k, v in query if k == key]
        parts.append(f"{title}: {', '.join(values) if values else 'любой'}")
    return "; ".join(parts)


def results_page_url(search_url: str, page_num: int) -> str:
    """Ссылка на страницу выдачи page_num (нумерация hh с нуля)."""
    u = urlparse(search_url)
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k != "page"]
    if page_num:
        query.append(("page", str(page_num)))
    return urlunparse(u._replace(query=urlencode(query)))


def open_results_page(page: Page, url: str) -> list[Vacancy]:
    log.info(f"  открываю выдачу: {url}")
    page.goto(url, wait_until="domcontentloaded")
    wait_captcha(page)
    if is_auth_url(page.url) or hh_role(page) == "anonymous":
        raise FatalError("сессия hh истекла (вы больше не авторизованы), перезапустите скрипт и войдите заново")
    try:
        page.locator(S.VACANCY_CARD).first.wait_for(timeout=20_000)
    except PlaywrightTimeout:
        log.warning("  ! карточки вакансий не появились за 20 с")
        return []

    selectors = {
        "card": S.VACANCY_CARD,
        "link": S.VACANCY_TITLE_LINK,
        "title": S.VACANCY_TITLE_TEXT,
        "employer": S.VACANCY_EMPLOYER,
        "button": S.RESPONSE_BUTTON,
        "remote": S.REMOTE_LABEL,
        "snippet": S.SNIPPET,
    }
    vacancies = [Vacancy(**raw) for raw in page.evaluate(_COLLECT_JS, selectors)]
    for v in vacancies:
        log.debug(f"    • {v.id} «{v.title}» — {v.company} [кнопка: {v.button_text or 'нет'}{', удалённо' if v.remote else ''}]")
    return vacancies


def has_next_page(page: Page) -> bool:
    return page.locator(S.PAGER_NEXT).count() > 0

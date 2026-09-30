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
  };
}).filter((v) => v.id)
"""


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
    }
    vacancies = [Vacancy(**raw) for raw in page.evaluate(_COLLECT_JS, selectors)]
    for v in vacancies:
        log.debug(f"    • {v.id} «{v.title}» — {v.company} [кнопка: {v.button_text or 'нет'}{', удалённо' if v.remote else ''}]")
    return vacancies


def has_next_page(page: Page) -> bool:
    return page.locator(S.PAGER_NEXT).count() > 0

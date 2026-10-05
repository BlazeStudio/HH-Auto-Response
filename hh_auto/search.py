"""Страницы выдачи: переход по страницам и сбор карточек вакансий."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
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


class _DescriptionParser(HTMLParser):
    """Текст блоков страницы вакансии с нужным data-qa (описание, ключевые навыки)."""

    VOID = {"br", "img", "hr", "input", "meta", "link", "wbr", "source"}
    BREAKS = {"p", "li", "br", "div", "ul", "ol", "h1", "h2", "h3", "h4", "tr"}

    def __init__(self, wanted: tuple[str, ...]):
        super().__init__()
        self.wanted, self.depth, self.parts = wanted, 0, []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            if tag in self.BREAKS:
                self.parts.append("\n")
            if tag not in self.VOID:
                self.depth += 1
        elif any(v and v.startswith(self.wanted) for k, v in attrs if k == "data-qa") and tag not in self.VOID:
            self.depth = 1
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.depth and tag not in self.VOID:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


def fetch_vacancy_text(page: Page, vac: Vacancy, limit: int = 4000) -> str:
    """Описание вакансии и ключевые навыки — для письма нейросетью. Запрос идёт фоном через браузер
    (с вашей сессией), без новых вкладок. Не получилось — пустая строка (письмо пишется по карточке)."""
    try:
        response = page.context.request.get(vac.url, timeout=20_000)
        if not response.ok:
            return ""
        parser = _DescriptionParser(("vacancy-description", "skills-element", "bloko-tag__text"))
        parser.feed(response.text())
    except Exception as e:  # сеть, капча, вёрстка — письмо всё равно напишем по карточке
        log.debug(f"  описание вакансии не загрузилось: {e}")
        return ""
    text = re.sub(r"[ \t ]+", " ", "".join(parser.parts))
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text[:limit]


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
    wait_all_cards(page)

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


def wait_all_cards(page: Page, timeout: float = 12, settle: float = 1.5) -> None:
    """hh сначала рисует 20 карточек, а остальные (до 50 — «50 вакансий» в выдаче) догружает через пару секунд.
    Без ожидания программа брала только первые 20 и теряла остальные 30 на каждой странице."""
    cards = page.locator(S.VACANCY_CARD)
    count, stable_since, deadline = cards.count(), time.monotonic(), time.monotonic() + timeout
    while time.monotonic() < deadline and time.monotonic() - stable_since < settle:
        page.mouse.wheel(0, 4000)  # на случай, если догрузка завязана на прокрутку
        page.wait_for_timeout(400)
        now = cards.count()
        if now != count:
            count, stable_since = now, time.monotonic()
    page.evaluate("window.scrollTo(0, 0)")


_PAGER_JS = """
(s) => ({
  next: document.querySelectorAll(s.next).length,
  pages: Array.from(document.querySelectorAll(s.page)).map((a) => parseInt(a.textContent, 10)).filter((n) => !isNaN(n)),
  cards: document.querySelectorAll(s.card).length,
  header: (document.querySelector(s.header) || {}).innerText || '',
})
"""


def next_page_info(page: Page, page_num: int) -> tuple[bool, str]:
    """Есть ли страница после page_num (с нуля) и почему так решили — для понятного лога.

    hh то показывает стрелку «дальше», то только номера страниц (стрелки нет, а страниц 9),
    поэтому смотрим на всё: стрелку, номера страниц и «Найдено N вакансий» в шапке.
    """
    try:
        info = page.evaluate(_PAGER_JS, {"next": S.PAGER_NEXT, "page": S.PAGER_PAGE, "card": S.VACANCY_CARD,
                                         "header": S.SEARCH_HEADER})
    except Exception:
        return False, "не удалось прочитать переключатель страниц"
    current = page_num + 1
    found = re.search(r"Найден\w*\s+([\d\s ]+)", info["header"])
    total = int(re.sub(r"\D", "", found.group(1))) if found else None
    total_note = f"найдено {total} вакансий, " if total else ""
    if info["next"]:
        return True, "есть кнопка «дальше»"
    if info["pages"]:  # номера страниц видны — решают они (на последней странице карточек меньше)
        if max(info["pages"]) > current:
            return True, f"{total_note}страниц не меньше {max(info['pages'])}"
        return False, f"{total_note}это последняя страница ({current} из {max(info['pages'])})"
    if total and info["cards"] and total > current * info["cards"]:
        return True, f"{total_note}по {info['cards']} на странице"
    if total is not None:
        return False, f"{total_note}это последняя страница ({current})"
    return False, "переключателя страниц нет — выдача на одной странице"

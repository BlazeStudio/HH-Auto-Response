"""Шаг 2: проверяем, что резюме из ссылки существует и принадлежит вашему аккаунту."""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Page

from . import selectors as S
from .auth import RESUMES_URL
from .config import Config
from .logger import log


def verify_resume(page: Page, cfg: Config) -> str | None:
    """Возвращает название резюме (должность) — по нему сверяем резюме в окне отклика."""
    log.info("Шаг 2/3: проверка резюме")
    resume_id = cfg.resume_id
    if not resume_id:
        log.warning("  ! ID резюме не найден ни в resume_url, ни в параметре resume= ссылки поиска — проверка пропущена")
        return None
    source = "resume_url" if cfg.resume_url else "параметра resume= в ссылке поиска"
    log.info(f"  ID резюме (из {source}): {resume_id}")

    if page.url.split("?")[0] != RESUMES_URL:
        page.goto(RESUMES_URL, wait_until="domcontentloaded")
    links = page.locator(f'a[href*="/resume/{resume_id}"]')
    try:
        links.first.wait_for(state="attached", timeout=10_000)
        log.success("  ✓ резюме найдено в списке ваших резюме")
    except Exception:
        log.warning("  ! резюме с таким ID нет в списке ваших резюме (hh.ru/applicant/resumes) — проверьте ссылку")

    page.goto(f"https://hh.ru/resume/{resume_id}", wait_until="domcontentloaded")
    for selector in S.RESUME_PAGE_TITLE:
        title_el = page.locator(selector)
        if title_el.count():
            title = " ".join(title_el.first.inner_text().split())
            log.success(f"  ✓ резюме: «{title}»")
            return title

    log.warning(
        f"  ! не удалось прочитать название резюме (заголовок страницы: «{page.title()}») — "
        "сверка резюме в окне отклика отключена, название будет только выводиться в лог"
    )
    return None


_UI_LINES = {"редактировать", "добавить", "профиль", "/", "показать ещё", "скрыть", "изменить", "указать уровни",
             "перейти к тестам", "подробнее", "электронные сертификаты", "поднятие резюме", "портфолио",
             "рекомендации", "желаемую зарплату", "категория прав", "наличие автомобиля"}
# Реклама и подсказки hh на странице резюме — не часть резюме
_UI_NOISE = re.compile(r"промокод|подобрали для вас|автоподнят|минус \d+[–-]\d+%|видно (?:всем|работодател)", re.I)
_PHONE = re.compile(r"\+?\d[\d\s()\-]{8,}\d")
_EMAIL = re.compile(r"\S+@\S+\.\S+")


def clean_resume_text(raw: str) -> str:
    """Текст резюме без контактов и кнопок интерфейса: нейросети телефон и почта не нужны."""
    lines: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.lower() in _UI_LINES or _UI_NOISE.search(line):
            continue
        line = _PHONE.sub(lambda m: "[телефон скрыт]" if 10 <= sum(c.isdigit() for c in m.group()) <= 15
                          else m.group(), line)  # «2018 - 2022» — это годы, а не телефон
        line = _EMAIL.sub("[почта скрыта]", line)
        if not lines or lines[-1] != line:  # hh дублирует заголовки
            lines.append(line)
    return "\n".join(lines)


def fetch_resume_text(page: Page, cfg: Config, cache: Path, max_chars: int = 8000) -> str:
    """Текст резюме со страницы hh — контекст для ИИ-ответов. Если не открылось — берём прошлый из кеша."""
    resume_id = cfg.resume_id
    if resume_id:
        try:
            page.goto(f"https://hh.ru/resume/{resume_id}", wait_until="domcontentloaded")
            area = page.locator('[data-qa="resume"], main, [data-qa="main-content"]').first
            area.wait_for(timeout=15_000)
            text = clean_resume_text(area.inner_text())[:max_chars]
            if len(text) > 200:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(text, encoding="utf-8")
                log.success(f"  ✓ резюме для ИИ загружено с hh ({len(text)} симв.)")
                return text
        except Exception as e:  # страница резюме не критична — есть кеш
            log.warning(f"  ! не удалось прочитать резюме с hh: {(str(e).splitlines() or [''])[0]}")
    else:
        log.warning("  ! ID резюме неизвестен (resume_url или resume= в ссылке поиска) — беру резюме из кеша")
    if cache.exists():
        text = cache.read_text(encoding="utf-8")
        log.info(f"  резюме для ИИ — из прошлой загрузки ({len(text)} симв.)")
        return text
    log.warning("  ! резюме для ИИ нет — нейросеть будет отвечать только по контексту из настроек")
    return ""

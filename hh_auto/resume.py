"""Шаг 2: проверяем, что резюме из ссылки существует и принадлежит вашему аккаунту."""

from __future__ import annotations

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

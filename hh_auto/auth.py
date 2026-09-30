"""Шаг 1: авторизация.

Логин/пароль скрипт не хранит и не вводит: при первом запуске вы входите сами
в открывшемся окне (телефон/почта + код), а сессия сохраняется в профиле браузера.
Кто вошёл, hh пишет в cookie hhrole: anonymous — гость, applicant — соискатель.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from playwright.sync_api import Page

from . import control
from . import selectors as S
from .browser import FatalError, wait_captcha
from .config import Config
from .logger import log

RESUMES_URL = "https://hh.ru/applicant/resumes"
LOGIN_URL = "https://hh.ru/account/login?backurl=%2Fapplicant%2Fresumes"


def is_auth_url(url: str) -> bool:
    """Страницы входа/регистрации/подтверждения — всё под /account/."""
    return urlparse(url).path.startswith("/account")


def hh_role(page: Page) -> str:
    cookies = page.context.cookies("https://hh.ru")
    return next((c["value"] for c in cookies if c["name"] == "hhrole"), "")


def is_logged_in(page: Page) -> bool:
    page.goto(RESUMES_URL, wait_until="domcontentloaded")
    wait_captcha(page)
    role = hh_role(page)
    log.debug(f"  cookie hhrole = {role or '(нет)'}, адрес: {page.url}")
    if role == "employer":
        raise FatalError("вы вошли как работодатель — выйдите и войдите в аккаунт соискателя")
    return (
        role not in ("", "anonymous")
        and not is_auth_url(page.url)
        and page.locator(S.LOGIN_FORM).count() == 0
    )


def ensure_logged_in(page: Page, cfg: Config) -> None:
    log.info("Шаг 1/3: авторизация на hh.ru")
    if is_logged_in(page):
        log.success("  ✓ вы уже авторизованы (сессия сохранена в профиле браузера)")
        return

    if cfg.browser.headless:
        raise FatalError("вход не выполнен. Для первого входа запустите с [browser] headless = false")

    minutes = cfg.browser.login_timeout // 60
    log.warning("  ! вход не выполнен. Войдите в аккаунт соискателя ВРУЧНУЮ в открывшемся окне браузера")
    log.warning(f"    (телефон или почта → код из SMS/письма). Скрипт ждёт до {minutes} мин и продолжит сам.")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    deadline = time.monotonic() + cfg.browser.login_timeout
    next_reminder = time.monotonic() + 30
    while time.monotonic() < deadline:
        control.sleep(2)
        # Пока вы вводите код, страницу не трогаем — только смотрим на cookie
        if hh_role(page) not in ("", "anonymous"):
            control.sleep(2)  # даём hh закончить редиректы после входа
            if is_logged_in(page):
                log.success("  ✓ вход выполнен, сессия сохранена — в следующий раз входить не придётся")
                return
            log.warning("  ! похоже, вход ещё не завершён — продолжаю ждать")
        if time.monotonic() >= next_reminder:
            left = int(deadline - time.monotonic())
            log.info(f"  …жду, пока вы войдёте в аккаунт (осталось {left // 60} мин {left % 60} с)")
            next_reminder += 30

    raise FatalError("не дождался входа в аккаунт. Запустите скрипт ещё раз.")

"""Запуск браузера, проверка доступности hh.ru из РФ, ожидание ручного прохождения капчи."""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, Playwright
from playwright.sync_api import Error as PlaywrightError

from . import control
from . import selectors as S
from .config import Config
from .logger import log

HH_HOME = "https://hh.ru/"
IP_INFO_URL = "https://ipinfo.io/json"

REGION_HINT = (
    "hh.ru работает только из России. Отключите VPN (или добавьте hh.ru в исключения VPN) "
    "либо укажите российский прокси в config.toml → [geo] proxy."
)


class FatalError(Exception):
    """Ошибка, после которой продолжать работу нет смысла."""


def launch(pw: Playwright, cfg: Config) -> BrowserContext:
    """Открывает браузер с постоянным профилем: вход в hh сохраняется между запусками."""
    channels = [cfg.browser.channel] if cfg.browser.channel else []
    channels += [c for c in ("chrome", "msedge", "") if c not in channels]

    last_error: Exception | None = None
    for channel in channels:
        name = channel or "chromium (встроенный в Playwright)"
        profile = Path(cfg.browser.profile_dir) / (channel or "chromium")
        log.info(f"Запускаю браузер: {name}, профиль: {profile}")
        try:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                channel=channel or None,
                headless=cfg.browser.headless,
                slow_mo=cfg.browser.slow_mo,
                locale=cfg.geo.locale,
                timezone_id=cfg.geo.timezone,
                proxy=cfg.geo.playwright_proxy(),
                viewport={"width": 1440, "height": 900},
            )
        except PlaywrightError as e:
            last_error = e
            log.warning(f"  ! не удалось запустить {name}: {str(e).splitlines()[0]}")
            continue
        context.set_default_timeout(20_000)
        log.success(f"  ✓ браузер запущен ({name})")
        return context
    raise FatalError(f"не удалось запустить ни один браузер. Последняя ошибка: {last_error}")


def check_region(page: Page, cfg: Config) -> None:
    """Шаг 0: убеждаемся, что hh.ru открывается с текущего IP."""
    log.info("Шаг 0/3: проверка доступа к hh.ru (сервис работает только из РФ)")
    log.info(f"  прокси: {cfg.geo.proxy_for_log()}")
    log.info(f"  язык браузера: {cfg.geo.locale}, часовой пояс: {cfg.geo.timezone}")

    if cfg.geo.check_ip:
        _check_ip_country(page, cfg)

    try:
        response = page.goto(HH_HOME, wait_until="domcontentloaded", timeout=45_000)
    except PlaywrightError as e:
        raise FatalError(f"hh.ru не открывается ({str(e).splitlines()[0]}). {REGION_HINT}") from e
    wait_captcha(page)

    host = urlparse(page.url).hostname or ""
    if host != "hh.ru" and not host.endswith(".hh.ru"):
        raise FatalError(f"вместо hh.ru открылся {host} — hh определил, что вы не в РФ. {REGION_HINT}")
    if response is not None and response.status >= 400:
        raise FatalError(f"hh.ru ответил HTTP {response.status} — похоже, доступ с вашего IP ограничен. {REGION_HINT}")
    log.success(f"  ✓ hh.ru доступен (HTTP {response.status if response else '?'})")


def _check_ip_country(page: Page, cfg: Config) -> None:
    try:
        info = page.request.get(IP_INFO_URL, timeout=10_000).json()
    except Exception as e:  # сервис определения IP не критичен
        log.warning(f"  ! не удалось определить страну IP ({type(e).__name__}) — пропускаю эту проверку")
        return
    country = info.get("country", "?")
    log.info(f"  внешний IP: {info.get('ip', '?')}, страна: {country}, город: {info.get('city', '?')}")
    if country == "RU":
        log.success("  ✓ IP российский")
        return
    message = f"IP не российский ({country}): hh.ru может не пускать или ограничивать отклики. {REGION_HINT}"
    if cfg.geo.require_ru_ip:
        raise FatalError(message)
    log.warning(f"  ! {message}")


def captcha_shown(page: Page) -> bool:
    if "captcha" in page.url.lower():
        return True
    try:
        return page.locator(S.CAPTCHA).filter(visible=True).count() > 0
    except PlaywrightError:  # страница как раз перезагружается
        return False


def wait_captcha(page: Page, timeout: float = 600) -> None:
    """Капчу не обходим: просим пользователя решить её руками и ждём."""
    if not captcha_shown(page):
        return
    log.warning("  ! hh.ru показал капчу — решите её вручную в окне браузера, скрипт подождёт")
    deadline = time.monotonic() + timeout
    while captcha_shown(page):
        if time.monotonic() > deadline:
            raise FatalError("капча не пройдена за отведённое время")
        control.sleep(2)
    log.success("  ✓ капча пройдена, продолжаю")

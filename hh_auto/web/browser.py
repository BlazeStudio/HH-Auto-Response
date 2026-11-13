"""Запуск браузера, проверка доступности hh.ru из РФ, ожидание ручного прохождения капчи."""

from __future__ import annotations

import base64
import os
import random
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, Playwright
from playwright.sync_api import Error as PlaywrightError

from . import window as browser_window
from ..core import control
from . import selectors as S
from ..core.config import Config
from ..core.logger import log

HH_HOME = "https://hh.ru/"
IP_INFO_URL = "https://ipinfo.io/json"

REGION_HINT = (
    "hh.ru работает только из России. Отключите VPN (или добавьте hh.ru в исключения VPN) "
    "либо укажите российский прокси в config.toml → [geo] proxy."
)


class FatalError(Exception):
    """Ошибка, после которой продолжать работу нет смысла."""


class CaptchaShown(Exception):
    """hh показал капчу посреди действия: решит её человек, а действие нужно повторить."""


CAPTCHA_WAIT = 30 * 60  # сколько ждать, пока человек решит капчу
_cfg: Config | None = None  # настройки текущего запуска: окно браузера и уведомления — для капчи

# Кадры для превью в приложении: frame_sink(jpeg) — задаёт приложение (в консоли не задан — кадров нет);
# frame_wanted() — видит ли их сейчас кто-нибудь (окно программы свёрнуто — не снимаем зря)
last_error = ""  # причина последней остановки с ошибкой (FatalError) — для окна приложения
frame_sink = None
frame_wanted = None
FRAME_EVERY = 1.5  # сек между кадрами превью
PULSE_EVERY = 0.5  # сек между проверками «браузер ещё открыт» (~1 мс каждая)


def launch(pw: Playwright, cfg: Config) -> BrowserContext:
    """Открывает браузер с постоянным профилем: вход в hh сохраняется между запусками."""
    global _cfg
    _cfg = cfg
    channels = [cfg.browser.channel] if cfg.browser.channel else []
    channels += [c for c in ("chrome", "msedge", "") if c not in channels]

    errors: list[str] = []
    for channel in channels:
        name = channel or "chromium (встроенный в Playwright)"
        if not channel and not _bundled_chromium(pw):
            log.debug("  встроенный Chromium не установлен — пропускаю")
            continue
        profile = Path(cfg.browser.profile_dir) / (channel or "chromium")
        log.info(f"Запускаю браузер: {name}, профиль: {profile}")
        if hides_window(cfg) and browser_window.MODE == "win32":
            _forget_window_placement(profile)
        sandbox = SANDBOX
        for attempt in range(1, PROFILE_BUSY_ATTEMPTS + 1):
            control.check()  # остановили, пока запускался прошлый — другой браузер не пробуем
            try:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile),
                    channel=channel or None,
                    headless=cfg.browser.headless,
                    slow_mo=cfg.browser.slow_mo,
                    locale=cfg.geo.locale,
                    timezone_id=cfg.geo.timezone,
                    proxy=cfg.geo.playwright_proxy(),
                    # спрятанное окно: страница — по настоящему размеру окна. С заданным размером страницы (1440×900)
                    # окно, выехавшее на экран с другим размером и масштабом монитора, показывало белую пустоту
                    viewport=None if hides_window(cfg) else {"width": 1440, "height": 900},
                    no_viewport=hides_window(cfg),
                    chromium_sandbox=sandbox,
                    args=window_args(cfg) + AUTOMATION_ARGS,
                )
            except PlaywrightError as e:
                first = (str(e).splitlines() or [""])[0]
                if _profile_busy(str(e)) and attempt < PROFILE_BUSY_ATTEMPTS:
                    # окно прошлого запуска с этим профилем ещё закрывается — Chrome отдаёт ему работу и выходит
                    log.warning(f"  ! профиль {name} ещё занят прошлым окном браузера — жду и пробую снова")
                    control.sleep(3)
                    continue
                if sandbox and not _not_installed(str(e)) and attempt < PROFILE_BUSY_ATTEMPTS:
                    sandbox = False  # песочнице что-то мешает (права, антивирус) — запускаем, как раньше, без неё
                    log.warning(f"  ! {name} не запустился с песочницей ({first}) — пробую без неё")
                    continue
                errors.append(f"{name}: " + ("профиль занят другим окном этого браузера — закройте окно, "
                                             "которое открывала программа" if _profile_busy(str(e)) else first))
                log.warning(f"  ! не удалось запустить {name}: {first}")
                break
            context.set_default_timeout(20_000)
            log.success(f"  ✓ браузер запущен ({name})")
            _watch_close(context)
            if hides_window(cfg):
                _hide_window(context)
            return context
    raise FatalError("не удалось запустить браузер. " + ("; ".join(errors) if errors else
                     "Установите Google Chrome или Microsoft Edge"))


PROFILE_BUSY_ATTEMPTS = 4
# Признак автоматизации: Playwright включает navigator.webdriver = true — первое, что смотрят проверки «не робот ли».
# Этот флаг его выключает (проверено: страница видит то же, что у Chrome, подключённого по CDP без Playwright).
# Капчу он не отменяет — её hh показывает в основном за частые отклики (hh/pacing.py)
AUTOMATION_ARGS = ["--disable-blink-features=AutomationControlled"]
# Песочница Chrome — как у обычного браузера. Playwright по умолчанию запускает Chrome с --no-sandbox (так проще
# в CI), а с ним вкладки hh и открытых ссылок работают без защиты. Linux — тоже с ней, кроме запуска от root и
# контейнеров (Docker): там Chrome с песочницей не запускается. Не запустился с ней — launch() пробует без неё


def _sandbox_possible() -> bool:
    if sys.platform in ("win32", "darwin"):
        return True
    in_container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    return hasattr(os, "geteuid") and os.geteuid() != 0 and not in_container


SANDBOX = _sandbox_possible()


def _not_installed(error: str) -> bool:
    """Браузера нет на компьютере — песочница тут ни при чём, без неё пробовать незачем."""
    text = error.lower()
    return "is not found at" in text or "doesn't exist" in text or "executable" in text and "not found" in text


def _forget_window_placement(profile: Path) -> None:
    """Chrome запоминает в профиле, где было окно (например, на экране — после капчи), и открывается там же,
    не глядя на «за краем экрана». Стираем это место в профиле программы — окно откроется спрятанным."""
    import json

    path = profile / "Default" / "Preferences"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("browser", {}).pop("window_placement", None) is None:
            return
        tmp = path.with_name("Preferences.hh-tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except (OSError, ValueError, AttributeError):
        pass  # нет профиля или файл другой — тогда окно уберёт hide() после запуска


def _profile_busy(error: str) -> bool:
    """Профиль занят: открыто окно этого браузера с тем же профилем (часто — ещё закрывается прошлое)."""
    text = error.lower()
    return any(mark in text for mark in ("existing browser session", "user data directory is already in use",
                                         "has been closed", "processsingleton"))


def _bundled_chromium(pw: Playwright) -> bool:
    try:
        return Path(pw.chromium.executable_path).exists()
    except Exception:
        return False


def _hide_window(context: BrowserContext) -> None:
    """Сразу после запуска — окно с глаз долой. Windows: Chrome всё же открыл окно на экране — за край.
    macOS, Linux: окно открывается на экране и тут же сворачивается (с ним связывается рабочий поток)."""
    if browser_window.MODE == "cdp":
        page = context.pages[0] if context.pages else context.new_page()  # она и станет вкладкой работы
        if not browser_window.bind(page):
            log.info("  окно браузера остаётся на экране: связаться с ним не вышло")
            return
        if browser_window.kept_shown():  # выяснилось в прошлый запуск
            log.info(f"  окно браузера остаётся на экране: {browser_window.kept_shown()}")
            return
    if browser_window.find(refresh=True) and not browser_window.is_hidden():
        browser_window.hide()  # macOS, Linux: не свернулось — почему, напишет web/window.py


def window_args(cfg: Config) -> list[str]:
    """Аргументы запуска спрятанного окна. Windows: сразу за краем экрана (без мелькания при старте).
    macOS, Linux: размер, как на Windows (страница ~1440 шириной); Linux с Wayland — окно через X11 (XWayland):
    свёрнутое окно Wayland браузер сам обратно не развернёт, и для капчи оно не выехало бы (проверено)."""
    if not hides_window(cfg):
        return []
    width, height = browser_window.WORK_SIZE
    if browser_window.MODE == "win32":
        return [f"--window-position={browser_window.HIDDEN_AT},{browser_window.HIDDEN_AT}",
                f"--window-size={width},{height}"]
    args = [f"--window-size={width},{height}"]
    if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
        args.append("--ozone-platform=x11")
    return args


def hides_window(cfg: Config | None) -> bool:
    return bool(cfg) and cfg.browser.hide_window and not cfg.browser.headless and browser_window.supported()


class _Watch:
    """Пока идёт работа: окно браузера закрыли — остановить её; приложению — кадры для превью."""

    page: Page | None = None
    processes: dict = {}  # pid → имя: браузер и драйвер Playwright этого запуска
    cdp = None  # своя связь с вкладкой — для кадров превью
    closing = False  # браузер закрывает сама программа — это не «закрыли окно»
    next_pulse = 0.0
    next_frame = 0.0


def _watch_close(context: BrowserContext) -> None:
    _Watch.closing = False
    context.on("close", lambda *_args: None if _Watch.closing else control.browser_closed(_close_reason()))
    _Watch.processes = browser_window.processes()  # браузер и драйвер этого запуска — на случай, если не закроются


def _close_reason(what: str = "окно браузера закрыто") -> str:
    return "остановлено пользователем" if control.user_stopped() else what


def finish_processes(everything: bool = False) -> int:
    """После закрытия: процессы браузера и драйвера этого запуска, которые почему-то остались, — завершить
    (иначе они держат память до перезагрузки). everything — и те, что ещё не записаны: «Остановить», пока
    браузер только запускается (его процессы записываются, когда он открылся). Возвращает, сколько пришлось
    завершить."""
    left = browser_window.still_running(_Watch.processes)
    if everything:
        left = sorted(set(left) | set(browser_window.processes()))
    _Watch.processes = {}
    if left:
        browser_window.terminate(left)
    return len(left)


def attach(page: Page) -> None:
    """Главная вкладка работы: следим, что её не закрыли, и снимаем кадры для превью во время пауз."""
    _Watch.page = page
    _Watch.next_pulse = _Watch.next_frame = 0.0
    def closed(*_args) -> None:
        if not _Watch.closing:  # окно закрыли целиком — сначала закрывается вкладка, других уже нет
            others = [p for p in page.context.pages if p is not page and not p.is_closed()]
            control.browser_closed(_close_reason("вкладку с hh закрыли" if others else "окно браузера закрыто"))

    page.on("close", closed)
    control.set_idle(_idle)


def detach() -> None:
    """Работа закончилась: браузер сейчас закроет сама программа."""
    _Watch.closing = True
    _Watch.page = None
    if _Watch.cdp is not None:
        try:
            _Watch.cdp.detach()
        except Exception:
            pass
        _Watch.cdp = None
    browser_window.unbind()
    control.set_idle(None)


def _idle() -> None:
    """Во время пауз (рабочий поток): жив ли браузер и, если приложение смотрит, кадр для превью. macOS, Linux:
    и «Показать» / «Спрятать» из окна приложения — окно браузера двигает только этот поток."""
    page = _Watch.page
    if page is None:
        return
    browser_window.pump()
    now = time.monotonic()
    # Кадр — только пока окно спрятано. Окно на экране (капча, вход, «Показать») человек видит сам, а снимок
    # видимого окна заставлял его моргать раз в полторы секунды и сбивал ввод капчи
    frame = (frame_sink is not None and now >= _Watch.next_frame and (frame_wanted is None or frame_wanted())
             and browser_window.is_hidden())
    if not frame and now < _Watch.next_pulse:
        return
    _Watch.next_pulse = now + PULSE_EVERY
    try:
        page.evaluate("0")  # жив ли браузер: без обращения к нему событие «окно закрыли» до программы не доходит
    except PlaywrightError as e:
        _note_closed(e)
        return
    if not frame or page.is_closed() or control.stop_requested():
        return  # снимок закрытой вкладки ждал бы ответа, пока браузер не завершится совсем (до 15 с)
    _Watch.next_frame = now + FRAME_EVERY
    try:
        picture = _capture(page)
        if picture:
            frame_sink(picture)
    except PlaywrightError as e:
        _note_closed(e)


def _capture(page: Page) -> bytes:
    """Кадр вкладки напрямую из браузера: без вмешательства в страницу (page.screenshot на это время прячет
    курсор ввода, вставляя в страницу свой стиль) и сразу уменьшенный — ~10 КБ."""
    if _Watch.cdp is None:
        _Watch.cdp = page.context.new_cdp_session(page)
    view = _Watch.cdp.send("Page.getLayoutMetrics")["cssVisualViewport"]
    width, height = view["clientWidth"], view["clientHeight"]
    if width < 10 or height < 10:
        return b""
    data = _Watch.cdp.send("Page.captureScreenshot", {
        "format": "jpeg", "quality": 60, "optimizeForSpeed": True,
        "clip": {"x": view["pageX"], "y": view["pageY"], "width": width, "height": height,
                 "scale": min(1.0, 520 / width)},
    })
    return base64.b64decode(data["data"])


def _note_closed(error: Exception) -> None:
    text = str(error).lower()
    if "closed" in text or "target crashed" in text or "disconnected" in text:
        control.browser_closed("окно браузера закрыто")


def show_for_user(page: Page) -> bool:
    """Нужен человек (вход, капча): окно — на экран и вперёд. True — окно было спрятано программой."""
    browser_window.pump(fresh=True)  # macOS, Linux: что с окном сейчас (Windows узнаёт это сразу)
    hidden = hides_window(_cfg) and browser_window.is_hidden()
    if hidden:
        browser_window.show()
    try:
        page.bring_to_front()
    except PlaywrightError:
        pass
    return hidden


def hide_after_user(was_hidden: bool) -> None:
    """Человек закончил — спрятать окно обратно, если прятала программа."""
    if was_hidden and hides_window(_cfg):
        browser_window.hide()


def check_region(page: Page, cfg: Config) -> None:
    """Шаг 0: убеждаемся, что hh.ru открывается с текущего IP."""
    log.info("Шаг 0/3: проверка доступа к hh.ru (сервис работает только из РФ)")
    log.info(f"  прокси: {cfg.geo.proxy_for_log()}")
    log.info(f"  язык браузера: {cfg.geo.locale}, часовой пояс: {cfg.geo.timezone}")

    if cfg.geo.check_ip:
        _check_ip_country(page, cfg)
        control.check()

    try:
        # Для проверки хватает ответа сервера: код и адрес после перенаправлений. Ждать, пока тяжёлая главная
        # hh дорисуется (2–7 с), незачем — следом всё равно переход к резюме, там и капчу видно
        response = page.goto(HH_HOME, wait_until="commit", timeout=45_000)
    except PlaywrightError as e:
        control.check()  # браузер закрыли («Остановить» или крестиком) — это не «hh.ru не открывается»
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


def open_url(page: Page, url: str, attempts: int = 3, timeout: float = 45) -> None:
    """Переход на страницу hh с повтором. hh бывает медленным (20 с не хватало), а после отправки формы
    сам уводит на другую страницу — тогда наш переход прерывается («interrupted by another navigation»)."""
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            return
        except PlaywrightError as e:
            if attempt == attempts or "closed" in str(e).lower():
                raise
            log.debug(f"  переход не удался ({(str(e).splitlines() or [''])[0][:120]}) — повторяю")
            try:  # дать hh закончить свой переход, если он был
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except PlaywrightError:
                pass
            control.sleep(2 * attempt)


def captcha_shown(page: Page) -> bool:
    if "captcha" in page.url.lower():
        return True
    try:
        return page.locator(S.CAPTCHA).filter(visible=True).count() > 0
    except PlaywrightError:  # страница как раз перезагружается
        return False


# Капча — знак, что hh считает, что мы торопимся. Обходить её программа не будет: она замедляется сама.
SLOW_FOR = 20 * 60  # с: столько после капчи паузы между действиями длиннее
BREATHER = (20, 40)  # с: передышка сразу после капчи (× slowdown / 2)
_captchas: list[float] = []  # когда за этот сеанс hh показывал капчу (time.monotonic)


def captcha_count() -> int:
    """Сколько капч решили за этот сеанс программы — так серии откликов (hh/pacing.py) замечают новую."""
    return len(_captchas)


def slowdown() -> float:
    """Во сколько раз удлинить паузы: после капчи за последние SLOW_FOR — вдвое, после двух и больше — втрое."""
    now = time.monotonic()
    recent = sum(1 for moment in _captchas if now - moment < SLOW_FOR)
    return 1.0 if not recent else 2.0 if recent == 1 else 3.0


def wait_captcha(page: Page, timeout: float = CAPTCHA_WAIT) -> bool:
    """Капчу не обходим и не решаем: зовём человека и ждём, пока он её решит. True — капча была.
    Решить можно в окне браузера или, если включено ([notify] telegram_captcha), ответом боту в Telegram —
    кто первый (web/captcha.py). Окно браузера само выходит на экран, только если включено
    [browser] show_on_captcha; иначе программа сообщает о капче, а браузер человек открывает сам («Показать»),
    и после капчи окно прячется обратно. После капчи — передышка и более длинные паузы (slowdown): чаще всего
    капча — от слишком частых действий."""
    if not captcha_shown(page):
        return False
    from . import captcha as phone

    relay = phone.relay_for(_cfg)
    headless = _cfg is not None and _cfg.browser.headless
    if headless and relay is None:
        raise FatalError("hh.ru показал капчу, а браузер работает без окна ([browser] headless = true) — решить её "
                         "некому. Запустите с окном и решите капчу сами или включите капчу в Telegram "
                         "([notify] telegram, telegram_captcha)")
    show = _cfg is None or _cfg.browser.show_on_captcha
    closed = not headless and not show and _window_hidden()  # окно спрятано, и само оно не выйдет
    if headless:
        where = "ответьте боту в Telegram символами с картинки"
    else:
        where = OPEN_BROWSER + " и решите её" if closed else "решите её в окне браузера"
        where += " или ответьте боту в Telegram" if relay else ""
    log.warning(f"  ! hh.ru показал капчу — {where}. Программа подождёт до {timeout / 60:.0f} мин и продолжит сама")
    was_hidden = show_for_user(page) if show else False  # выключено — окно не трогаем, даже не выводим вперёд
    _alert_captcha(telegram=relay is None, closed=closed)
    deadline = time.monotonic() + timeout
    solver = phone.PhoneSolver(page, relay, timeout) if relay else None
    outcome = "stopped"
    try:
        while captcha_shown(page):
            if time.monotonic() > deadline:
                outcome = "timeout"
                raise FatalError(f"капча не пройдена за {timeout / 60:.0f} мин")
            if solver is not None:
                solver.step()
            control.sleep(1 if solver is not None else 2)
        outcome = "passed"
    finally:
        if solver is not None:
            solver.finish(outcome)
    log.success("  ✓ капча пройдена, продолжаю")
    hide_after_user(was_hidden or (closed and not _window_hidden()))  # открыли ради капчи — спрятать обратно
    _captchas.append(time.monotonic())
    rest = random.uniform(*BREATHER) * slowdown() / 2
    log.info(f"  hh просит помедленнее: передышка {rest:.0f} с, следующие {SLOW_FOR // 60} мин паузы "
             f"между действиями ×{slowdown():g}")
    control.sleep(rest)
    return True


def _window_hidden() -> bool:
    """Окно браузера сейчас спрятано программой (за краем экрана или свёрнуто)."""
    if not hides_window(_cfg):
        return False
    browser_window.pump(fresh=True)  # macOS, Linux: что с окном сейчас (Windows узнаёт это сразу)
    return browser_window.is_hidden()


# как открыть спрятанный браузер — в журнале и в уведомлении о капче
OPEN_BROWSER = "откройте браузер («Показать» у превью слева или «Показать браузер» в трее)"


def _alert_captcha(telegram: bool = True, closed: bool = False) -> None:
    """Вдруг вы отошли: уведомление на компьютер или в Telegram — если вы их разрешили ([notify]).
    telegram=False — в Telegram уже идёт картинка капчи (web/captcha.py), текстом не дублируем.
    closed — окно браузера спрятано и само не выйдет: подсказать, как его открыть."""
    cfg = _cfg
    if cfg is None or not (cfg.notify.desktop or cfg.notify.telegram and telegram):
        return
    from dataclasses import replace

    from ..core import notify

    where = OPEN_BROWSER[0].upper() + OPEN_BROWSER[1:] + " и решите её" if closed else "Решите её в окне браузера"
    if not telegram and cfg.notify.telegram:
        where += " или ответьте боту в Telegram"
    text = f"hh.ru просит пройти капчу. {where} — программа ждёт и потом продолжит сама."
    settings = replace(cfg.notify, telegram=cfg.notify.telegram and telegram)
    threading.Thread(target=notify.send, args=(settings, "HH-Auto-Response — нужна капча", text),
                     daemon=True).start()

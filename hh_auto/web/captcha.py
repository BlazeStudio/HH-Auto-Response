"""Капча с телефона: картинка — в Telegram, ответ — в браузер.

Капчу по-прежнему решает человек, программа её не распознаёт. Она только переносит её туда, где человек сейчас:
бот присылает картинку, вы отвечаете символами с неё, программа вводит их в поле капчи и жмёт «Отправить».
Окно браузера при этом тоже открыто — решить можно и там, кто первый. Не подошло — бот пришлёт новую картинку;
ответ «другая» — нажать «Другой текст» и прислать другую.

Нужны [notify] telegram = true, токен и chat id; выключается [notify] telegram_captcha = false. Сообщения
принимаются только из вашего чата (chat id) и только пока на экране капча.

Ответ — как на картинке: русские и латинские буквы, цифры, несколько слов через пробел («вынесет путь»).
Кавычки, точку в конце и лишние пробелы с телефона программа убирает. Вводит по одной букве нажатиями клавиш,
как человек: латиница — на английской раскладке, кириллица — на русской (ЙЦУКЕН), а не вставкой из буфера.

Сеть — в своих потоках (Relay): Telegram из России бывает медленным, и ожидание ответа не должно задерживать
«Остановить». Отправка и чтение ответов — в разных потоках: картинка уходит сразу, а не после очередного
запроса ответов; при сбое связи отправка повторяется, картинка, которая так и не ушла, присылается заново.
Браузер трогает только рабочий поток (PhoneSolver.step), как и весь остальной Playwright.
"""

from __future__ import annotations

import io
import queue
import random
import threading
import time
from datetime import datetime, timedelta

from playwright.sync_api import Page
from playwright.sync_api import Error as PlaywrightError

from ..core.config import Config
from ..core.logger import log
from ..core.notify import TelegramError, bot_token, telegram_call, telegram_send
from . import selectors as S

POLL = 5  # с: сколько Telegram держит запрос новых сообщений (long polling)
CHECK_FOR = 10  # с: столько ждём, принял ли hh ответ, прежде чем прислать картинку заново
RENEW_FOR = 8  # с: столько ждём новую картинку после «Другой текст»
NO_PICTURE_AFTER = 15  # с: капча есть, а картинки нет (другая вёрстка) — сказать, что решать в браузере
RESEND_AFTER = 20  # с: картинка не ушла и после повторов — новую не раньше, чем через столько
MAX_ANSWER = 40  # символов: длиннее — это не ответ на капчу (у hh бывает и два слова: «вынесет путь»)
MAX_WORDS = 4  # слов через пробел, не больше
RENEW_WORDS = {"другая", "другую", "новая", "новую", "обнови", "обновить", "ещё", "еще", "new", "/new", "/renew"}
_TRIM = "\"'`«»„“”‘’.,!?;:"  # снимается с краёв ответа: кавычки, точка от автозамены телефона
KEY_DELAY = (70, 180)  # мс между нажатиями — как человек

# Русская раскладка ЙЦУКЕН: буква → та же клавиша на английской (code и код клавиши для Chrome)
_RU = "йцукенгшщзхъфывапролджэячсмитьбюё"
_US = "qwertyuiop[]asdfghjkl;'zxcvbnm,.`"
_US_PUNCT = {"[": ("BracketLeft", 219), "]": ("BracketRight", 221), ";": ("Semicolon", 186),
             "'": ("Quote", 222), ",": ("Comma", 188), ".": ("Period", 190), "`": ("Backquote", 192)}


def clean_answer(raw: str) -> str:
    """Ответ из Telegram — как его вводить: один пробел между словами, без кавычек и точки по краям."""
    return " ".join(raw.split()).strip(_TRIM).strip()


def ru_key(char: str) -> tuple[str, int] | None:
    """Клавиша русской буквы (code, windowsVirtualKeyCode); не русская буква — None."""
    i = _RU.find(char.lower())
    if i < 0:
        return None
    us = _US[i]
    return _US_PUNCT.get(us) or (f"Key{us.upper()}", ord(us.upper()))


def relay_for(cfg: Config | None) -> "Relay | None":
    """Связь с ботом, если капча в Telegram включена и бот настроен; иначе None."""
    if cfg is None:
        return None
    notify = cfg.notify
    token, chat = bot_token(notify.telegram_token), str(notify.telegram_chat_id).strip()
    if not (notify.telegram and notify.telegram_captcha and token and chat):
        return None
    global _relay
    with _relay_lock:
        if _relay is None or (_relay.token, _relay.chat_id) != (token, chat):
            if _relay is not None:
                _relay.retire()
            _relay = Relay(token, chat)
        return _relay


_relay: "Relay | None" = None
_relay_lock = threading.Lock()


class _Cancelled(Exception):
    """Отправку больше не ждём: капчу уже решили или программа закрывается."""


class Relay:
    """Бот: отправляет то, что положил рабочий поток, и, пока капча на экране, принимает ваши ответы.
    Один на всю программу: два одновременных запроса новых сообщений Telegram не разрешает (409 Conflict).
    Отправка и чтение — в разных потоках: Telegram разрешает слать сообщения, пока ждём новые."""

    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id
        self._out: queue.Queue = queue.Queue()
        self._in: queue.Queue = queue.Queue()
        self._active = threading.Event()  # капча на экране — слушаем ответы
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._fresh = False  # перед первым запросом пропустить всё, что писали боту раньше
        self._offset = 0
        self._warned: set[str] = set()
        self.photo_failed = threading.Event()  # картинка так и не ушла — PhoneSolver пришлёт новую
        threading.Thread(target=self._run, name="telegram-captcha", daemon=True).start()
        threading.Thread(target=self._send_run, name="telegram-captcha-send", daemon=True).start()

    @property
    def _retired(self) -> bool:
        return self._stop.is_set()

    # ---------- рабочий поток ----------

    def begin(self) -> None:
        """Новая капча: прошлые ответы не в счёт."""
        while not self._in.empty():
            self._in.get_nowait()
        self.photo_failed.clear()
        self._fresh = True
        self._active.set()
        self._wake.set()

    def end(self, text: str = "") -> None:
        if text:
            self.text(text)
        self._active.clear()
        self._wake.set()

    def photo(self, png: bytes, caption: str) -> None:
        """Картинка капчи. Не нужна, если капчу успели решить, пока она шла, — тогда не отправляется."""
        self._out.put(("sendPhoto", {"chat_id": self.chat_id, "caption": caption},
                       {"photo": ("captcha.png", png, "image/png")}, True))

    def text(self, text: str) -> None:
        self._out.put(("sendMessage", {"chat_id": self.chat_id, "text": text}, None, False))

    def answers(self) -> list[str]:
        found = []
        while not self._in.empty():
            found.append(self._in.get_nowait())
        return found

    def retire(self) -> None:
        self._stop.set()
        self._active.clear()
        self._wake.set()
        self._out.put(None)  # разбудить поток отправки

    # ---------- поток отправки ----------

    def _send_run(self) -> None:
        while not self._retired:
            item = self._out.get()
            if item is not None:
                self._deliver(*item)

    def _deliver(self, method: str, params: dict, files: dict | None, captcha_only: bool) -> None:
        if captcha_only and not self._active.is_set():
            return  # капчу уже решили — старая картинка не нужна

        def pause(seconds: float) -> None:
            if self._stop.wait(seconds) or (captcha_only and not self._active.is_set()):
                raise _Cancelled

        try:
            telegram_send(self.token, method, params, files, pause=pause, call=telegram_call)
        except _Cancelled:
            pass
        except TelegramError as e:
            if not e.transient:  # неверный токен или chat id, бот заблокирован — повтор не поможет
                self._warn("refused", f"  ! Telegram не принял сообщение бота ({e}) — проверьте токен и chat id "
                           "в «Уведомлениях»; капчу решите в окне браузера")
            elif method == "sendPhoto":
                self.photo_failed.set()
                self._warn("photo", f"  ! картинка капчи не ушла в Telegram ({e}) — пришлю ещё раз; решить "
                           "можно и в окне браузера")
            else:
                self._warn("text", f"  ! сообщение в Telegram не отправилось ({e})")
        except Exception:  # noqa: BLE001 — поток не должен умирать
            log.debug("  капча в Telegram: сбой отправки", exc_info=True)

    # ---------- поток ответов ----------

    def _run(self) -> None:
        while not self._retired:
            try:
                self._loop()
            except Exception:  # noqa: BLE001 — странный ответ Telegram: поток не должен умирать
                log.debug("  капча в Telegram: сбой потока", exc_info=True)
                self._wake.wait(3)  # offset сохранён — уже пришедшие ответы не потеряются
                self._wake.clear()

    def _loop(self) -> None:
        failures = 0
        while not self._retired:
            if not self._active.is_set():
                self._wake.wait(30)
                self._wake.clear()
                continue
            try:
                if self._fresh:
                    last = telegram_call(self.token, "getUpdates", {"offset": -1, "timeout": 0}, timeout=15)
                    self._offset = last[-1]["update_id"] + 1 if last else 0
                    self._fresh = False
                updates = telegram_call(self.token, "getUpdates",
                                        {"offset": self._offset, "timeout": POLL, "allowed_updates": ["message"]},
                                        timeout=POLL + 15)
                failures = 0
            except TelegramError as e:
                failures += 1
                log.debug(f"  капча в Telegram: ответы не прочитались ({e})")
                if failures >= 3:
                    if e.code == 409:
                        self._warn("conflict", "  ! ответы боту в Telegram забирает кто-то другой (вебхук или другая "
                                   "программа с этим ботом) — решите капчу в окне браузера")
                    else:
                        self._warn("poll", f"  ! не получается читать ответы боту в Telegram ({e}) — "
                                   "решите капчу в окне браузера")
                self._wake.wait(min(10, failures))  # сбой связи обычно на секунды — пробуем почти сразу
                self._wake.clear()
                continue
            for update in updates or []:
                self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                message = update.get("message") or {}
                if self._active.is_set() and self._ours(message) and isinstance(message.get("text"), str):
                    self._in.put(message["text"])

    def _ours(self, message: dict) -> bool:
        chat = message.get("chat") or {}
        return str(chat.get("id")) == self.chat_id or (
            bool(chat.get("username")) and self.chat_id.lstrip("@").lower() == str(chat["username"]).lower())

    def _warn(self, key: str, text: str) -> None:
        if key not in self._warned:  # одно и то же — один раз за сеанс, дальше — в подробный лог
            self._warned.add(key)
            log.warning(text)
        else:
            log.debug(text)


class PhoneSolver:
    """Одна капча: прислать картинку, ввести ответы из Telegram, сообщить итог. Всё — из рабочего потока."""

    def __init__(self, page: Page, relay: Relay, timeout: float):
        self.page, self.relay = page, relay
        self.until = (datetime.now() + timedelta(seconds=timeout)).strftime("%H:%M")
        self.started = time.monotonic()
        self.shown = ""  # картинка (её адрес), которую сейчас видит человек в Telegram
        self.sent = 0  # сколько картинок отправлено
        self.sent_at = 0.0  # когда отдали последнюю картинку потоку отправки
        self.caption = ""  # подпись к следующей картинке, если нужна особая («Не подошло…»)
        self.submitted = 0.0  # когда ввели ответ из Telegram (0 — ответа не ждём)
        self.submitted_src = ""
        self.renew_until = 0.0  # нажали «Другой текст» — до этого ждём новую картинку
        self.no_picture_told = False
        relay.begin()

    def step(self) -> None:
        try:
            self._step()
        except PlaywrightError as e:  # страница как раз перезагружается — в следующий раз
            log.debug(f"  капча в Telegram: {(str(e).splitlines() or [''])[0]}")

    def finish(self, outcome: str) -> None:
        """outcome: passed — капчи больше нет; timeout — не дождались; stopped — работу остановили."""
        if outcome == "passed":
            text = ("✓ Капча пройдена — продолжаю" if self.submitted else
                    "✓ Капчу решили в окне браузера — продолжаю")
        elif outcome == "timeout":
            text = "✗ Капча так и не пройдена — программа остановилась"
        else:
            text = "Работа остановлена — капча больше не нужна" if self.sent else ""
        self.relay.end(text)

    # ---------- шаг ----------

    def _step(self) -> None:
        now = time.monotonic()
        src = self._src()
        if self.relay.photo_failed.is_set() and now - self.sent_at > RESEND_AFTER:
            # картинка не дошла и после повторов (сбой связи) — снять и прислать заново
            self.relay.photo_failed.clear()
            self.shown = ""
            self.sent = max(0, self.sent - 1)
        if self.submitted:
            # hh не принял ответ: надпись «Неверный текст», картинка сменилась или ничего не произошло
            wrong = now - self.submitted > 1.5 and (self._error_shown() or (src and src != self.submitted_src))
            if not wrong and now - self.submitted < CHECK_FOR:
                return
            self.submitted = 0.0
            log.warning("  ! ответ из Telegram не подошёл — отправляю картинку ещё раз")
            self.caption = "Не подошло — вот картинка ещё раз. Ответьте символами с неё"
            self.shown = ""  # даже та же самая картинка — отправить снова
        if self.renew_until:
            if src == self.shown and now < self.renew_until:
                return  # ждём новую картинку
            if src == self.shown:
                self.relay.text("Другой картинки hh не дал — ответьте символами с прошлой")
            self.renew_until = 0.0
        if src and src != self.shown:
            caption = self.caption or (self._caption() if not self.sent else "Картинка сменилась — вот новая")
            if self._send(src, caption):
                self.caption = ""
        elif not src and not self.sent and not self.no_picture_told and now - self.started > NO_PICTURE_AFTER:
            self.no_picture_told = True
            self._dump_markup()
            self.relay.text("hh.ru просит капчу, но картинку прислать не получилось — решите её в окне браузера")
        for text in self.relay.answers():
            self._answer(text)

    def _caption(self) -> str:
        return (f"hh.ru просит капчу. Ответьте символами с картинки, слова — через пробел: программа введёт их "
                f"сама. Или решите её в окне браузера. «другая» — другая картинка. Жду до {self.until}")

    def _send(self, src: str, caption: str) -> bool:
        picture = self._picture()
        if picture is None:
            return False
        try:
            png = picture.screenshot(timeout=5000, animations="disabled", caret="initial")
        except PlaywrightError as e:
            log.debug(f"  снимок капчи не вышел: {(str(e).splitlines() or [''])[0]}")
            return False
        self.shown = src
        self.sent += 1
        self.sent_at = time.monotonic()
        self.relay.photo(_bigger(png), caption)
        if self.sent == 1:
            log.info("  картинка капчи отправлена в Telegram — ответьте боту символами с неё")
        return True

    def _answer(self, raw: str) -> None:
        text = clean_answer(raw)
        if self.submitted:
            self.relay.text("Проверяю прошлый ответ — подождите несколько секунд")
            return
        if text.lower() in RENEW_WORDS:
            renew = self.page.locator(S.CAPTCHA_RENEW).filter(visible=True)
            if renew.count():
                self.shown = self._src()
                renew.first.click(timeout=5000)
                self.renew_until = time.monotonic() + RENEW_FOR
                log.info("  из Telegram попросили другую картинку")
            else:
                self.relay.text("Кнопки «Другой текст» нет — ответьте символами с этой картинки")
            return
        if not text or len(text) > MAX_ANSWER or len(text.split()) > MAX_WORDS or text.startswith("/"):
            self.relay.text("Не похоже на ответ — пришлите одним сообщением то, что написано на картинке, "
                            "русскими или латинскими буквами, слова через пробел (или «другая» — другая картинка)")
            return
        field = self._input()
        if field is None:
            self._dump_markup()
            self.relay.text("Не нашёл поле для ответа — решите капчу в окне браузера")
            return
        self.submitted_src = self._src()
        self._type(field, text)
        submit = self.page.locator(S.CAPTCHA_SUBMIT).filter(visible=True)
        if submit.count():
            submit.first.click(timeout=5000)
        else:
            field.press("Enter", timeout=5000)
        self.submitted = time.monotonic()
        log.info(f"  ответ из Telegram введён ({text}) — проверяю, принял ли hh")

    def _type(self, field, text: str) -> None:
        """Ввести ответ по одной букве нажатиями клавиш, а не вставкой. Латиница, цифры, пробел — клавиатурой
        Playwright (английская раскладка). Кириллицу Playwright только вставляет, без нажатий: её — нажатиями
        клавиш русской раскладки через Chrome (CDP). Не Chromium — вставкой по букве, как раньше."""
        field.click(timeout=5000)
        field.fill("", timeout=5000)
        cdp = self._cdp() if any(ru_key(char) for char in text) else None
        try:
            for char in text:
                key = ru_key(char) if cdp is not None else None
                if key is not None:
                    _press_ru(cdp, char, *key)
                else:
                    self.page.keyboard.type(char)
                self.page.wait_for_timeout(random.randint(*KEY_DELAY))
        finally:
            if cdp is not None:
                try:
                    cdp.detach()
                except Exception:  # noqa: BLE001 — сессия уже закрыта вместе со страницей
                    pass

    def _cdp(self):
        try:
            return self.page.context.new_cdp_session(self.page)
        except Exception as e:  # noqa: BLE001 — не Chromium: русские буквы вставятся без нажатий
            log.debug(f"  капча: нет CDP для русской раскладки ({(str(e).splitlines() or [''])[0]})")
            return None

    # ---------- страница ----------

    def _picture(self):
        found = self.page.locator(S.CAPTCHA_PICTURE).filter(visible=True)
        return found.first if found.count() else None

    def _src(self) -> str:
        """Адрес загруженной картинки капчи ("" — её нет или она ещё грузится)."""
        picture = self._picture()
        if picture is None:
            return ""
        return picture.evaluate("img => img.complete && img.naturalWidth > 0 ? (img.currentSrc || img.src) : ''",
                                timeout=2000) or ""

    def _input(self):
        field = self.page.locator(S.CAPTCHA_INPUT).filter(visible=True)
        if field.count():
            return field.first
        form = self.page.locator("form").filter(has=self.page.locator(S.CAPTCHA_PICTURE))
        field = form.locator('input[type="text"], input:not([type])').filter(visible=True)
        return field.first if field.count() else None

    def _error_shown(self) -> bool:
        return self.page.locator(S.CAPTCHA_ERROR).filter(visible=True).count() > 0

    def _dump_markup(self) -> None:
        """Вёрстка капчи не та, что ждали, — в подробный лог, чтобы поправить selectors.py."""
        try:
            markup = self.page.evaluate("""() => {
                const el = document.querySelector('[data-qa*="captcha"], img[src*="captcha"]');
                const box = el && (el.closest('form, [role="dialog"], [role="alertdialog"]') || el.parentElement);
                return box ? box.outerHTML.replace(/ class="[^"]*"/g, '').slice(0, 3000) : '';
            }""")
            log.debug(f"  вёрстка капчи ({self.page.url}): {markup}")
        except PlaywrightError:
            pass


def _press_ru(cdp, char: str, code: str, key_code: int) -> None:
    """Нажать клавишу русской буквы, как на клавиатуре с раскладкой ЙЦУКЕН: keydown → ввод символа → keyup.
    Заглавная — с Shift. Те же события, что Playwright шлёт для латиницы (Input.dispatchKeyEvent)."""
    shift = char != char.lower()
    shift_key = {"key": "Shift", "code": "ShiftLeft", "windowsVirtualKeyCode": 16, "nativeVirtualKeyCode": 16,
                 "location": 1}
    key = {"key": char, "code": code, "windowsVirtualKeyCode": key_code, "nativeVirtualKeyCode": key_code,
           "modifiers": 8 if shift else 0}
    if shift:
        cdp.send("Input.dispatchKeyEvent", {**shift_key, "type": "rawKeyDown", "modifiers": 8})
    cdp.send("Input.dispatchKeyEvent", {**key, "type": "keyDown", "text": char, "unmodifiedText": char.lower()})
    cdp.send("Input.dispatchKeyEvent", {**key, "type": "keyUp"})
    if shift:
        cdp.send("Input.dispatchKeyEvent", {**shift_key, "type": "keyUp", "modifiers": 0})


def _bigger(png: bytes) -> bytes:
    """Картинка капчи мелкая (~200×60) — увеличить, чтобы на телефоне читалась."""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(png))
        if image.width >= 400:
            return png
        factor = min(4, max(2, 400 // max(image.width, 1)))
        image = image.resize((image.width * factor, image.height * factor), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001 — нет Pillow или картинка необычная: отправим как есть
        return png

"""Капча с телефона: картинка — в Telegram, ответ — в браузер.

Капчу по-прежнему решает человек, программа её не распознаёт. Она только переносит её туда, где человек сейчас:
бот присылает картинку, вы отвечаете символами с неё, программа вводит их в поле капчи и жмёт «Отправить».
Окно браузера при этом тоже открыто — решить можно и там, кто первый. Не подошло — бот пришлёт новую картинку;
ответ «другая» — нажать «Другой текст» и прислать другую.

Нужны [notify] telegram = true, токен и chat id; выключается [notify] telegram_captcha = false. Сообщения
принимаются только из вашего чата (chat id) и только пока на экране капча.

Сеть — в своём потоке (Relay): Telegram из России бывает медленным, и ожидание ответа не должно задерживать
«Остановить». Браузер трогает только рабочий поток (PhoneSolver.step), как и весь остальной Playwright.
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
from ..core.notify import TelegramError, bot_token, telegram_call
from . import selectors as S

POLL = 5  # с: сколько Telegram держит запрос новых сообщений (long polling)
CHECK_FOR = 10  # с: столько ждём, принял ли hh ответ, прежде чем прислать картинку заново
RENEW_FOR = 8  # с: столько ждём новую картинку после «Другой текст»
NO_PICTURE_AFTER = 15  # с: капча есть, а картинки нет (другая вёрстка) — сказать, что решать в браузере
MAX_ANSWER = 16  # символов: длиннее — это не ответ на капчу
RENEW_WORDS = {"другая", "другую", "новая", "новую", "обнови", "обновить", "ещё", "еще", "new", "/new", "/renew"}


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


class Relay:
    """Бот: отправляет то, что положил рабочий поток, и, пока капча на экране, принимает ваши ответы.
    Один на всю программу: два одновременных запроса новых сообщений Telegram не разрешает (409 Conflict)."""

    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id
        self._out: queue.Queue = queue.Queue()
        self._in: queue.Queue = queue.Queue()
        self._active = threading.Event()  # капча на экране — слушаем ответы
        self._wake = threading.Event()
        self._retired = False
        self._fresh = False  # перед первым запросом пропустить всё, что писали боту раньше
        self._warned: set[str] = set()
        threading.Thread(target=self._run, name="telegram-captcha", daemon=True).start()

    # ---------- рабочий поток ----------

    def begin(self) -> None:
        """Новая капча: прошлые ответы не в счёт."""
        while not self._in.empty():
            self._in.get_nowait()
        self._fresh = True
        self._active.set()
        self._wake.set()

    def end(self, text: str = "") -> None:
        if text:
            self.text(text)
        self._active.clear()
        self._wake.set()

    def photo(self, png: bytes, caption: str) -> None:
        self._out.put(("sendPhoto", {"chat_id": self.chat_id, "caption": caption},
                       {"photo": ("captcha.png", png, "image/png")}))
        self._wake.set()

    def text(self, text: str) -> None:
        self._out.put(("sendMessage", {"chat_id": self.chat_id, "text": text}, None))
        self._wake.set()

    def answers(self) -> list[str]:
        found = []
        while not self._in.empty():
            found.append(self._in.get_nowait())
        return found

    def retire(self) -> None:
        self._retired = True
        self._active.clear()
        self._wake.set()

    # ---------- свой поток ----------

    def _run(self) -> None:
        while not self._retired:
            try:
                self._loop()
            except Exception:  # noqa: BLE001 — странный ответ Telegram: поток не должен умирать
                log.debug("  капча в Telegram: сбой потока", exc_info=True)
                self._fresh = True  # прошлые сообщения не принимать заново
                self._wake.wait(10)
                self._wake.clear()

    def _loop(self) -> None:
        offset = 0
        failures = 0
        while not self._retired:
            self._flush()
            if not self._active.is_set():
                self._wake.wait(30)
                self._wake.clear()
                continue
            try:
                if self._fresh:
                    last = telegram_call(self.token, "getUpdates", {"offset": -1, "timeout": 0}, timeout=15)
                    offset = last[-1]["update_id"] + 1 if last else 0
                    self._fresh = False
                updates = telegram_call(self.token, "getUpdates",
                                        {"offset": offset, "timeout": POLL, "allowed_updates": ["message"]},
                                        timeout=POLL + 15)
                failures = 0
            except TelegramError as e:
                failures += 1
                if failures >= 3:
                    if e.code == 409:
                        self._warn("conflict", "  ! ответы боту в Telegram забирает кто-то другой (вебхук или другая "
                                   "программа с этим ботом) — решите капчу в окне браузера")
                    else:
                        self._warn("poll", f"  ! не получается читать ответы боту в Telegram ({e}) — "
                                   "решите капчу в окне браузера")
                self._wake.wait(min(30, 3 * failures))
                self._wake.clear()
                continue
            for update in updates or []:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                message = update.get("message") or {}
                if self._active.is_set() and self._ours(message) and isinstance(message.get("text"), str):
                    self._in.put(message["text"])

    def _flush(self) -> None:
        while not self._out.empty():
            method, params, files = self._out.get_nowait()
            try:
                telegram_call(self.token, method, params, files, timeout=30)
            except TelegramError as e:
                self._warn(method, f"  ! в Telegram не отправилось ({e}) — капчу можно решить в окне браузера")

    def _ours(self, message: dict) -> bool:
        chat = message.get("chat") or {}
        return str(chat.get("id")) == self.chat_id or (
            bool(chat.get("username")) and self.chat_id.lstrip("@").lower() == str(chat["username"]).lower())

    def _warn(self, key: str, text: str) -> None:
        if key not in self._warned:  # одно и то же — один раз за сеанс
            self._warned.add(key)
            log.warning(text)


class PhoneSolver:
    """Одна капча: прислать картинку, ввести ответы из Telegram, сообщить итог. Всё — из рабочего потока."""

    def __init__(self, page: Page, relay: Relay, timeout: float):
        self.page, self.relay = page, relay
        self.until = (datetime.now() + timedelta(seconds=timeout)).strftime("%H:%M")
        self.started = time.monotonic()
        self.shown = ""  # картинка (её адрес), которую сейчас видит человек в Telegram
        self.sent = 0  # сколько картинок отправлено
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
        return (f"hh.ru просит капчу. Ответьте символами с картинки — программа введёт их сама. Или решите её "
                f"в окне браузера. «другая» — другая картинка. Жду до {self.until}")

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
        self.relay.photo(_bigger(png), caption)
        if self.sent == 1:
            log.info("  картинка капчи отправлена в Telegram — ответьте боту символами с неё")
        return True

    def _answer(self, raw: str) -> None:
        text = raw.strip().strip("\"'«»`").strip()
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
        if not text or len(text) > MAX_ANSWER or " " in text:
            self.relay.text("Пришлите только символы с картинки (или «другая» — другая картинка)")
            return
        field = self._input()
        if field is None:
            self._dump_markup()
            self.relay.text("Не нашёл поле для ответа — решите капчу в окне браузера")
            return
        self.submitted_src = self._src()
        field.fill("", timeout=5000)
        field.press_sequentially(text, delay=random.randint(60, 140), timeout=10_000)  # как человек, по букве
        submit = self.page.locator(S.CAPTCHA_SUBMIT).filter(visible=True)
        if submit.count():
            submit.first.click(timeout=5000)
        else:
            field.press("Enter", timeout=5000)
        self.submitted = time.monotonic()
        log.info("  ответ из Telegram введён — проверяю, принял ли hh")

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

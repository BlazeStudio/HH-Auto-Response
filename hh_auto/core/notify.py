"""Уведомление по итогам запуска: системное (Windows / macOS / Linux) и/или сообщение в Telegram.

Ничего не включено по умолчанию: приложение спрашивает разрешение после первого запуска,
в консоли и на сервере — секция [notify] в config.toml. Ошибка отправки никогда не ломает запуск.
"""

from __future__ import annotations

import http.client
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import Notify
from .logger import log

APP_NAME = "HH-Auto-Response"

# Тост Windows 10/11 от имени приложения: ID регистрируется в HKCU (имя и иконка, как у обычных программ),
# если не вышло — от имени PowerShell. Текст передаём через переменные окружения — без экранирования.
APP_ID = "HH-Auto-Response"  # тот же ID, что у окна приложения (панель задач)
POWERSHELL_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def _register_app_id() -> bool:
    """Чтобы уведомление было подписано «HH-Auto-Response» с нашей иконкой, а не «Windows PowerShell»."""
    try:
        import winreg

        from .paths import bundled

        icon = bundled("packaging/icon.png")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}") as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            if icon.exists():
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon.resolve()))
            winreg.SetValueEx(key, "IconBackgroundColor", 0, winreg.REG_SZ, "FF2563EB")
        return True
    except OSError as e:
        log.debug(f"  не удалось зарегистрировать приложение для уведомлений: {e}")
        return False


_WIN_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text/><text/></binding></visual></toast>')
$t = $xml.GetElementsByTagName('text')
$t.Item(0).AppendChild($xml.CreateTextNode($env:HH_NOTIFY_TITLE)) > $null
$t.Item(1).AppendChild($xml.CreateTextNode($env:HH_NOTIFY_TEXT)) > $null
$app = $env:HH_NOTIFY_APPID
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show(
    [Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def desktop(title: str, text: str) -> bool:
    """Системное уведомление. True — показано (или передано системе)."""
    try:
        if sys.platform == "win32":
            app_id = APP_ID if _register_app_id() else POWERSHELL_ID
            env = dict(os.environ, HH_NOTIFY_TITLE=title, HH_NOTIFY_TEXT=text, HH_NOTIFY_APPID=app_id)
            done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WIN_TOAST], env=env,
                                  capture_output=True, timeout=20,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif sys.platform == "darwin":
            script = f"display notification {json.dumps(text)} with title {json.dumps(title)}"
            done = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=20)
        else:
            if not shutil.which("notify-send"):
                log.debug("  уведомление: нет notify-send (пакет libnotify-bin)")
                return False
            done = subprocess.run(["notify-send", "-a", APP_NAME, title, text], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.debug(f"  уведомление не показано: {e}")
        return False
    if done.returncode:
        log.debug(f"  уведомление не показано: {done.stderr.decode(errors='replace')[:300]}")
    return done.returncode == 0


def telegram(token: str, chat_id: str, text: str) -> bool:
    token = bot_token(token)
    if not token or not chat_id:
        log.warning("  ! уведомление в Telegram: не заданы [notify] telegram_token и telegram_chat_id")
        return False
    try:
        telegram_send(token, "sendMessage", {"chat_id": chat_id, "text": text}, attempts=2)
        return True
    except TelegramError as e:
        log.warning(f"  ! уведомление в Telegram не отправлено: {e}")
        return False


class TelegramError(Exception):
    def __init__(self, text: str, code: int = 0, retry_after: float = 0):
        super().__init__(text)
        self.code = code  # 409 — сообщения бота забирает кто-то другой (вебхук или другая программа)
        self.retry_after = retry_after  # 429: Telegram просит подождать столько секунд

    @property
    def transient(self) -> bool:
        """Сбой связи или Telegram занят — стоит повторить. 400/401/403 (неверный chat id, токен, бот
        заблокирован) — нет: повтор даст то же самое."""
        return self.code == 0 or self.code == 429 or self.code >= 500


def bot_token(token: str) -> str:
    return token or os.environ.get("HH_TELEGRAM_TOKEN", "")


# Повторы отправки: из России связь с Telegram то и дело рвётся на секунды (WinError 10060, сброс соединения),
# а новое соединение обычно проходит сразу. Короткое ожидание и повтор быстрее одного длинного ожидания.
SEND_TIMEOUT = 15  # с на одну попытку
SEND_PAUSES = (1, 3, 6, 10)  # с между попытками


def telegram_send(token: str, method: str, params: dict | None = None, files: dict | None = None,
                  attempts: int = len(SEND_PAUSES) + 1, timeout: float = SEND_TIMEOUT,
                  pause=time.sleep, call=None) -> object:
    """telegram_call с повтором при сбое связи. pause — чем ждать между попытками (поток бота ждёт
    своим событием, чтобы его можно было остановить); call — вместо telegram_call (проверки)."""
    call = call or telegram_call
    for attempt in range(attempts):
        try:
            return call(token, method, params, files, timeout=timeout)
        except TelegramError as e:
            if not e.transient or attempt + 1 >= attempts:
                raise
            wait = e.retry_after or SEND_PAUSES[min(attempt, len(SEND_PAUSES) - 1)]
            log.debug(f"  Telegram: {method} не прошёл ({e}) — повтор через {wait:g} с")
            pause(wait)
    raise TelegramError("Telegram не ответил")  # attempts < 1


def telegram_call(token: str, method: str, params: dict | None = None, files: dict | None = None,
                  timeout: float = 20) -> object:
    """Вызов Bot API: поле result ответа или TelegramError. files — {поле: (имя файла, байты, тип)}.
    Токен — только в адресе запроса: в тексте ошибок его нет."""
    params = {key: json.dumps(value) if isinstance(value, (list, dict)) else str(value)
              for key, value in (params or {}).items()}
    if files:
        boundary = os.urandom(12).hex()
        body = _multipart(params, files, boundary)
        kind = f"multipart/form-data; boundary={boundary}"
    else:
        body, kind = urllib.parse.urlencode(params).encode(), "application/x-www-form-urlencoded"
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=body,
                                     headers={"Content-Type": kind})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except (OSError, ValueError, http.client.HTTPException):
            raise TelegramError(f"HTTP {e.code}", e.code) from None
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        # HTTPException — оборванный посреди ответ (IncompleteRead и т.п.): тоже сбой связи
        raise TelegramError(str(getattr(e, "reason", "") or e) or type(e).__name__) from None
    if not isinstance(data, dict) or not data.get("ok"):
        description = data.get("description", "") if isinstance(data, dict) else ""
        code = data.get("error_code", 0) if isinstance(data, dict) else 0
        retry = (data.get("parameters") or {}).get("retry_after", 0) if isinstance(data, dict) else 0
        raise TelegramError(description or "Telegram не принял запрос", code, float(retry or 0))
    return data.get("result")


def chat_code() -> str:
    """Одноразовый код для «Узнать chat id»: 6 цифр — легко отправить и вручную, угадать за минуту нельзя."""
    return f"{secrets.randbelow(900_000) + 100_000}"


def chat_link(bot: str, code: str) -> str:
    """Ссылка на бота с кодом: Telegram откроет чат и кнопку «Запустить», она пришлёт боту «/start код»."""
    return f"https://t.me/{bot}?start={code}"


def has_code(text: str, code: str) -> bool:
    """В сообщении — наш код: «/start 482913» по ссылке или просто «482913», отправленное вручную."""
    return code in text.replace("/start", " ").split()


def find_chat(token: str, code: str, wait: float = 120, stopped=lambda: False, waiting=lambda bot: None) -> dict:
    """Узнать chat id без ручного getUpdates. Боту могут писать и другие люди, поэтому берётся не последнее
    сообщение, а только то, в котором одноразовый код (chat_code): его знает лишь тот, кто нажал кнопку.
    waiting(имя бота) — начинаем ждать: окно открывает ссылку chat_link и показывает код. Ждёт до wait секунд.
    Возвращает {"id", "title", "bot"}; не дождались — {"bot"} без "id". Ошибка Telegram — TelegramError."""
    me = telegram_send(token, "getMe", attempts=3)
    bot = str((me or {}).get("username", "")) if isinstance(me, dict) else ""
    if (telegram_send(token, "getWebhookInfo", attempts=3) or {}).get("url"):
        raise TelegramError("у бота включён вебхук — его сообщения забирает другая программа. Отключите вебхук "
                            "(или заведите для HH-Auto-Response отдельного бота у @BotFather)", 409)
    waiting(bot)
    deadline = time.monotonic() + wait
    offset = 0
    while True:
        timeout = max(0, min(10, int(deadline - time.monotonic())))
        updates = telegram_send(token, "getUpdates", {"offset": offset, "timeout": timeout,
                                                      "allowed_updates": ["message"]},
                                attempts=3, timeout=timeout + SEND_TIMEOUT)
        for update in updates or []:
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            message = update.get("message") or {}
            chat = message.get("chat")
            if isinstance(chat, dict) and "id" in chat and has_code(str(message.get("text") or ""), code):
                title = chat.get("title") or " ".join(filter(None, (chat.get("first_name"), chat.get("last_name"))))
                if chat.get("username"):
                    title = f"{title} (@{chat['username']})" if title else f"@{chat['username']}"
                return {"id": str(chat["id"]), "title": title, "bot": bot}
        if stopped() or time.monotonic() >= deadline:
            return {"bot": bot}


def _multipart(fields: dict[str, str], files: dict, boundary: str) -> bytes:
    out = bytearray()
    for name, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += value.encode("utf-8") + b"\r\n"
    for name, (filename, data, kind) in files.items():
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {kind}\r\n\r\n").encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out)


def send(cfg: Notify, title: str, text: str) -> None:
    """Итоги запуска — во все включённые каналы."""
    if cfg.desktop and desktop(title, text):
        log.info("  уведомление показано")
    if cfg.telegram and telegram(cfg.telegram_token, cfg.telegram_chat_id, f"{title}\n{text}"):
        log.info("  итоги отправлены в Telegram")

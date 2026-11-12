"""Уведомление по итогам запуска: системное (Windows / macOS / Linux) и/или сообщение в Telegram.

Ничего не включено по умолчанию: приложение спрашивает разрешение после первого запуска,
в консоли и на сервере — секция [notify] в config.toml. Ошибка отправки никогда не ломает запуск.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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
    token = token or os.environ.get("HH_TELEGRAM_TOKEN", "")
    if not token or not chat_id:
        log.warning("  ! уведомление в Telegram: не заданы [notify] telegram_token и telegram_chat_id")
        return False
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=body, timeout=15):
            return True
    except (urllib.error.URLError, OSError) as e:
        log.warning(f"  ! уведомление в Telegram не отправлено: {getattr(e, 'code', '') or e}")
        return False


class TelegramError(Exception):
    def __init__(self, text: str, code: int = 0):
        super().__init__(text)
        self.code = code  # 409 — сообщения бота забирает кто-то другой (вебхук или другая программа)


def bot_token(token: str) -> str:
    return token or os.environ.get("HH_TELEGRAM_TOKEN", "")


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
        except (OSError, ValueError):
            raise TelegramError(f"HTTP {e.code}", e.code) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise TelegramError(str(getattr(e, "reason", "") or e)) from None
    if not isinstance(data, dict) or not data.get("ok"):
        description = data.get("description", "") if isinstance(data, dict) else ""
        raise TelegramError(description or "Telegram не принял запрос",
                            data.get("error_code", 0) if isinstance(data, dict) else 0)
    return data.get("result")


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

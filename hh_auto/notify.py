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

# Тост Windows 10/11 от имени PowerShell: своего AppUserModelID в меню «Пуск» у портативного приложения нет,
# а чужие ID Windows молча игнорирует. Текст передаём через переменные окружения — без экранирования.
_WIN_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text/><text/></binding></visual></toast>')
$t = $xml.GetElementsByTagName('text')
$t.Item(0).AppendChild($xml.CreateTextNode($env:HH_NOTIFY_TITLE)) > $null
$t.Item(1).AppendChild($xml.CreateTextNode($env:HH_NOTIFY_TEXT)) > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show(
    [Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def desktop(title: str, text: str) -> bool:
    """Системное уведомление. True — показано (или передано системе)."""
    try:
        if sys.platform == "win32":
            env = dict(os.environ, HH_NOTIFY_TITLE=title, HH_NOTIFY_TEXT=text)
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


def send(cfg: Notify, title: str, text: str) -> None:
    """Итоги запуска — во все включённые каналы."""
    if cfg.desktop and desktop(title, text):
        log.info("  уведомление показано")
    if cfg.telegram and telegram(cfg.telegram_token, cfg.telegram_chat_id, f"{title}\n{text}"):
        log.info("  итоги отправлены в Telegram")

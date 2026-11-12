"""Корневые сертификаты для HTTPS-запросов программы: проверка обновлений, нейросеть, Telegram.

Python проверяет сертификаты сайтов по файлу, путь к которому зашит в его OpenSSL при сборке. В собранном
приложении на macOS это путь с машины сборки (…/Python.framework/…/etc/openssl/cert.pem): у пользователя его
нет, и любой запрос падал с «CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate» — проверка
обновлений, ответы нейросети, уведомления в Telegram. Помогало только задать SSL_CERT_FILE вручную.

Теперь, если своего файла Python не находит, программа сама указывает ему системный (SSL_CERT_FILE): на macOS это
/etc/ssl/cert.pem, его обновляет сама система. Windows берёт сертификаты из хранилища системы — там ничего не
нужно. Заданные вами SSL_CERT_FILE или SSL_CERT_DIR не трогаем.
"""

from __future__ import annotations

import os
import ssl
import sys

SYSTEM_BUNDLES = (
    "/etc/ssl/cert.pem",  # macOS, Alpine, Arch
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/opt/homebrew/etc/ca-certificates/cert.pem",  # Homebrew (Apple Silicon)
    "/usr/local/etc/ca-certificates/cert.pem",  # Homebrew (Intel)
)


def _has_files(folder: str | None) -> bool:
    if not folder:
        return False
    try:
        with os.scandir(folder) as entries:
            return any(entry.is_file() for entry in entries)
    except OSError:
        return False


def _certifi() -> str:
    try:
        import certifi
    except ImportError:
        return ""
    try:
        return certifi.where()
    except Exception:  # noqa: BLE001 — нет файла внутри сборки и т.п.: просто не годится
        return ""


def ca_bundle(platform: str = sys.platform, env=None, paths=None, exists=os.path.isfile) -> str:
    """Какой файл сертификатов указать Python. Пусто — ничего делать не нужно: свой файл есть, система сама
    (Windows) или путь задан в переменных окружения."""
    env = os.environ if env is None else env
    if platform == "win32" or env.get("SSL_CERT_FILE") or env.get("SSL_CERT_DIR"):
        return ""
    paths = ssl.get_default_verify_paths() if paths is None else paths
    if paths.cafile or _has_files(paths.capath):  # cafile и capath заполнены, только если они есть на диске
        return ""
    return next((path for path in (*SYSTEM_BUNDLES, _certifi()) if path and exists(path)), "")


def ensure_ca_bundle() -> str:
    """Указать Python файл сертификатов, если своего он не находит. Возвращает выбранный путь (или пусто)."""
    try:
        path = ca_bundle()
    except Exception:  # noqa: BLE001 — без этого запросы работают как раньше
        return ""
    if path:
        os.environ["SSL_CERT_FILE"] = path
    return path

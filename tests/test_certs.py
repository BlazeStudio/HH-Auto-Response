"""Какой файл корневых сертификатов программа указывает Python (HTTPS на macOS). Сеть не нужна.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта

from hh_auto.core.certs import ca_bundle  # noqa: E402

NOTHING = SimpleNamespace(cafile=None, capath=None)  # своего файла сертификатов у Python нет


class CaBundle(unittest.TestCase):
    def test_frozen_macos_gets_system_file(self):
        # собранное приложение: путь с машины сборки у пользователя не существует
        self.assertEqual(ca_bundle("darwin", {}, NOTHING, exists=lambda p: p == "/etc/ssl/cert.pem"),
                         "/etc/ssl/cert.pem")

    def test_homebrew_file_when_no_system_one(self):
        brew = "/opt/homebrew/etc/ca-certificates/cert.pem"
        self.assertEqual(ca_bundle("darwin", {}, NOTHING, exists=lambda p: p == brew), brew)

    def test_linux_distributions(self):
        for path in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
            self.assertEqual(ca_bundle("linux", {}, NOTHING, exists=lambda p, path=path: p == path), path)

    def test_own_file_is_kept(self):
        own = SimpleNamespace(cafile="/opt/homebrew/etc/openssl@3/cert.pem", capath=None)
        self.assertEqual(ca_bundle("darwin", {}, own, exists=lambda _p: True), "")

    def test_own_folder_with_certificates_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "1234abcd.0").write_text("cert", encoding="utf-8")
            self.assertEqual(ca_bundle("linux", {}, SimpleNamespace(cafile=None, capath=folder),
                                       exists=lambda _p: True), "")

    def test_empty_own_folder_does_not_count(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(ca_bundle("darwin", {}, SimpleNamespace(cafile=None, capath=folder),
                                       exists=lambda p: p == "/etc/ssl/cert.pem"), "/etc/ssl/cert.pem")

    def test_user_settings_win(self):
        for env in ({"SSL_CERT_FILE": "/my/cert.pem"}, {"SSL_CERT_DIR": "/my/certs"}):
            self.assertEqual(ca_bundle("darwin", env, NOTHING, exists=lambda _p: True), "", env)

    def test_windows_uses_system_store(self):
        self.assertEqual(ca_bundle("win32", {}, NOTHING, exists=lambda _p: True), "")


if __name__ == "__main__":
    unittest.main()

"""HH-Auto-Response — автоматические отклики на вакансии hh.ru с сопроводительным письмом."""

# Версия приложения — одна на всё: окно, сборка, проверка обновлений. Тег релиза на GitHub должен совпадать с ней.
__version__ = "1.1.2-beta"

# HTTPS-запросы (обновления, нейросеть, Telegram) — с системными сертификатами, если у Python своих нет (macOS)
from .core.certs import ensure_ca_bundle as _ensure_ca_bundle  # noqa: E402

_ensure_ca_bundle()

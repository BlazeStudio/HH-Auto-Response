# HH-Auto-Response на сервере: консольные скрипты + Chromium в виртуальном экране (Xvfb).
# Для первого входа в hh экран открывается по VNC — только через SSH-туннель (см. README → «Docker на сервере»).
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    HH_AUTO_HOME=/data \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:99 \
    TZ=Europe/Moscow \
    LANG=ru_RU.UTF-8

# Виртуальный экран, VNC-сервер, простой оконный менеджер, шрифты с кириллицей, русская локаль
RUN apt-get update \
 && apt-get install -y --no-install-recommends xvfb x11vnc fluxbox fonts-dejavu fonts-liberation locales tzdata \
 && sed -i 's/# ru_RU.UTF-8/ru_RU.UTF-8/' /etc/locale.gen && locale-gen \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY hh_auto ./hh_auto
COPY main.py read_rejections.py ai_test.py config.example.toml ./
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

VOLUME /data
ENTRYPOINT ["/entrypoint.sh"]
CMD ["loop"]

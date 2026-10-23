#!/usr/bin/env bash
# Команды контейнера:
#   login              открыть браузер для первого входа в hh (смотреть по VNC через SSH-туннель)
#   responses [опции]  один запуск откликов (опции как у scripts/responses.py: --dry-run, --limit 5 …)
#   chats [опции]      один запуск чатов (опции как у scripts/chats.py)
#   raise [--force]    поднять резюме в поиске, если пора ([raising] в config.toml)
#   loop               отклики и чаты по кругу раз в INTERVAL_HOURS часов (по умолчанию 6), а между ними
#                      раз в час — подъём резюме, если он включён и пора
#   любая другая команда выполняется как есть (например, bash)
set -euo pipefail

DATA="${HH_AUTO_HOME:-/data}"
mkdir -p "$DATA"
if [ ! -f "$DATA/config.toml" ]; then
  # Первый запуск: конфиг из примера, браузер — встроенный Chromium с окном (окно видно только по VNC)
  sed -e 's/^channel = .*/channel = ""/' -e 's/^headless = .*/headless = false/' /app/config.example.toml \
    > "$DATA/config.toml"
  echo "Создан $DATA/config.toml — впишите ссылку поиска (search_url) или запрос и перезапустите."
fi

# Виртуальный экран: браузер работает «с окном», как у человека, но без монитора
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1440x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
for _ in $(seq 50); do [ -e /tmp/.X11-unix/X99 ] && break; sleep 0.1; done
fluxbox >/tmp/fluxbox.log 2>&1 &

start_vnc() {
  # VNC слушает порт 5900 контейнера; в docker-compose он проброшен только на 127.0.0.1 сервера,
  # поэтому снаружи недоступен — подключаться через SSH-туннель
  if [ -n "${VNC_PASSWORD:-}" ]; then
    x11vnc -storepasswd "$VNC_PASSWORD" /tmp/vncpass >/dev/null
    auth=(-rfbauth /tmp/vncpass)
  else
    auth=(-nopw)
  fi
  x11vnc -display :99 -forever -shared -rfbport 5900 "${auth[@]}" >/tmp/x11vnc.log 2>&1 &
  echo "VNC: порт 5900. С вашего компьютера: ssh -L 5900:127.0.0.1:5900 user@server, затем VNC-клиент на localhost:5900"
}

cmd="${1:-loop}"
shift || true
case "$cmd" in
  login)
    start_vnc
    exec python /app/scripts/responses.py --login "$@"
    ;;
  responses)
    exec python /app/scripts/responses.py "$@"
    ;;
  chats)
    exec python /app/scripts/chats.py "$@"
    ;;
  raise)
    exec python /app/scripts/raise_resume.py "$@"
    ;;
  loop)
    hours="${INTERVAL_HOURS:-6}"
    [ "${VNC_ALWAYS:-0}" = "1" ] && start_vnc
    while true; do
      echo "━━━ $(date '+%F %T') отклики"
      python /app/scripts/responses.py || echo "отклики завершились с кодом $?"
      if [ "${RUN_CHATS:-1}" = "1" ]; then
        echo "━━━ $(date '+%F %T') чаты"
        python /app/scripts/chats.py || echo "чаты завершились с кодом $?"
      fi
      echo "━━━ следующий запуск через ${hours} ч"
      for _ in $(seq "$hours"); do
        sleep 3600
        # резюме — раз в 4 часа ([raising]); выключено или рано — скрипт сразу выходит, браузер не открывается
        python /app/scripts/raise_resume.py || echo "подъём резюме завершился с кодом $?"
      done
    done
    ;;
  *)
    exec "$cmd" "$@"
    ;;
esac

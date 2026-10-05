#!/usr/bin/env bash
# Linux-версия приложения из любой системы (Windows через Git Bash, macOS, Linux) — в Docker.
#   ./build-linux.sh
# Результат: dist/HH-Auto-Response-linux-x86_64.tar.gz
#
# Внутри — Debian 12 и его системный Python 3.11 с python3-tk: у него Tk с Xft, кириллица в окне
# отображается нормально. Сборка идёт на копии проекта внутри контейнера, ваши .venv-build и dist/
# не трогаются (кроме самого архива). Нужен только запущенный Docker.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p dist
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd)":/src python:3.11-slim-bookworm bash -c '
  set -e
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends python3 python3-venv python3-tk binutils >/dev/null
  mkdir -p /build && cd /src
  tar --exclude=./.venv-build --exclude=./build --exclude=./dist --exclude=./.git --exclude=./browser-profile \
      --exclude=./data --exclude=./logs --exclude=./hh-data --exclude=./config.toml -cf - . | tar -xf - -C /build
  cd /build && sed -i "s/\r$//" build.sh && PYTHON=/usr/bin/python3 ./build.sh
  cp dist/HH-Auto-Response-linux-*.tar.gz /src/dist/
'
echo "Готово: $(ls dist/HH-Auto-Response-linux-*.tar.gz)"

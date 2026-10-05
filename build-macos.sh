#!/usr/bin/env bash
# macOS-версия приложения: HH-Auto-Response.app и архив dist/HH-Auto-Response-macos-<arm64|x86_64>.zip
#   ./build-macos.sh
#
# Собирается только на Mac: PyInstaller не умеет собирать под другую систему. Архитектура — как у этого Mac
# (Apple Silicon → arm64, Intel → x86_64). Нет Mac — запустите сборку в GitHub Actions
# (вкладка Actions → «Сборка» → Run workflow), она соберёт обе архитектуры.
#
# Нужен Python 3.11+ с Tk:  brew install python@3.11 python-tk@3.11
set -euo pipefail
cd "$(dirname "$0")"
if [ "$(uname -s)" != "Darwin" ]; then
  echo "! Это не macOS. Mac-версию собирает Mac или GitHub Actions (.github/workflows/build.yml)."
  exit 1
fi
if [ -z "${PYTHON:-}" ]; then
  for candidate in python3.11 python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import tkinter" 2>/dev/null; then
      PYTHON="$candidate"; break
    fi
  done
fi
if [ -z "${PYTHON:-}" ]; then
  echo "! Не найден Python с tkinter. Поставьте: brew install python@3.11 python-tk@3.11"
  exit 1
fi
export PYTHONIOENCODING=utf-8
"$PYTHON" packaging/build.py "$@"

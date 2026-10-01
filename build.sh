#!/usr/bin/env bash
# Сборка hh-auto.exe из Git Bash: ./build.sh
# Результат: dist/hh-auto/hh-auto.exe и dist/hh-auto-windows.zip
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
PYTHON="${PYTHON:-python}"
"$PYTHON" packaging/build.py "$@"

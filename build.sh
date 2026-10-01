#!/usr/bin/env bash
# Сборка HH-Auto-Response.exe из Git Bash: ./build.sh
# Результат: dist/HH-Auto-Response/HH-Auto-Response.exe и dist/HH-Auto-Response-windows.zip
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
PYTHON="${PYTHON:-python}"
"$PYTHON" packaging/build.py "$@"

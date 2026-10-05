#!/usr/bin/env bash
# Сборка приложения под текущую систему: macOS, Linux или Windows (Git Bash).
#   ./build.sh
# Результат в dist/: HH-Auto-Response.app (macOS), HH-Auto-Response/HH-Auto-Response (Linux),
# HH-Auto-Response/HH-Auto-Response.exe (Windows) и архив для передачи.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
if [ -z "${PYTHON:-}" ]; then
  if command -v python3 >/dev/null 2>&1; then PYTHON=python3; else PYTHON=python; fi
fi
"$PYTHON" packaging/build.py "$@"

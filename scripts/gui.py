"""Оконное приложение HH-Auto-Response: python scripts/gui.py (из исходников) или HH-Auto-Response.exe (собранное)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта

from hh_auto.ui.gui import main  # noqa: E402

if __name__ == "__main__":
    main()

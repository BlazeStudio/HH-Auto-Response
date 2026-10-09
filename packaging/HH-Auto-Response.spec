# -*- mode: python ; coding: utf-8 -*-
# Сборка оконного приложения HH-Auto-Response (PyInstaller) под текущую ОС. Запуск — через packaging/build.py.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
ICONS = ROOT / "packaging"
MAC = sys.platform == "darwin"
# Linux: у исполняемого файла иконки нет — окно ставит её само из icon.png
icon = {"win32": ICONS / "icon.ico", "darwin": ICONS / "icon.icns"}.get(sys.platform)

# Драйвер Playwright (node + JS, ~100 МБ) под эту ОС — готового хука у PyInstaller нет, кладём сами.
# Браузер не упаковываем: приложение использует установленный Chrome или Edge.
datas = collect_data_files("playwright", include_py_files=False)
datas += [
    (str(ICONS / "icon.ico"), "packaging"),
    (str(ICONS / "icon.png"), "packaging"),
    (str(ROOT / "config.example.toml"), "."),
]

a = Analysis(
    [str(ROOT / "gui.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=[],
    excludes=["numpy", "pandas", "matplotlib", "scipy", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HH-Auto-Response",
    icon=str(icon) if icon and icon.exists() else None,
    console=False,  # без окна консоли (Windows) / как обычное приложение (macOS)
    upx=False,  # UPX чаще вызывает ложные срабатывания антивирусов
)
coll = COLLECT(exe, a.binaries, a.datas, name="HH-Auto-Response", upx=False)

if MAC:
    app = BUNDLE(
        coll,
        name="HH-Auto-Response.app",
        icon=str(icon),
        bundle_identifier="io.github.blazestudio.hh-auto-response",
        info_plist={"NSHighResolutionCapable": True, "LSMinimumSystemVersion": "11.0",
                    "CFBundleShortVersionString": "1.0.4"},
    )

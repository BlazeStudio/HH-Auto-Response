"""customtkinter в глубокой папке Windows.

customtkinter при импорте подгружает свои шрифты через GDI Windows (Roboto и шрифт для скруглённых углов),
а GDI не открывает файл, путь к которому длиннее 260 символов (даже с префиксом длинного пути). Если программа
распакована в глубокую папку, шрифт не грузится, а предупреждение об этом customtkinter пишет в sys.stderr —
которого у оконного .exe нет, и программа закрывалась при запуске, не показав окна.
"""

from __future__ import annotations

import io
import shutil
import sys
import tempfile
from pathlib import Path

MAX_PATH = 260
FONTS = ("CustomTkinter_shapes_font.otf", "Roboto/Roboto-Regular.ttf", "Roboto/Roboto-Medium.ttf")


def import_customtkinter() -> None:
    """Первый импорт customtkinter: без stderr он не падает, а шрифты загружаются из папки любой глубины."""
    if "customtkinter" in sys.modules:
        return
    no_stderr = sys.stderr is None
    if no_stderr:  # оконный .exe: консоли нет, предупреждение customtkinter всё равно некому читать
        sys.stderr = io.StringIO()
    try:
        import customtkinter
    finally:
        if no_stderr:
            sys.stderr = None
    if sys.platform == "win32":
        load_from_short_path(Path(customtkinter.__file__).parent / "assets" / "fonts")


def load_from_short_path(fonts: Path) -> None:
    """Шрифты лежат слишком глубоко — загрузить их копию из временной папки."""
    if all(len(str(fonts / name)) < MAX_PATH for name in FONTS):
        return
    from customtkinter.windows.widgets.core_rendering import DrawEngine
    from customtkinter.windows.widgets.font import FontManager

    short = Path(tempfile.gettempdir()) / "HH-Auto-Response-fonts"
    loaded = []
    for name in FONTS:
        source = "\\\\?\\" + str(fonts.resolve() / name)  # сам Python длинный путь откроет с этим префиксом
        copy = short / Path(name).name
        try:
            if not copy.is_file() or copy.stat().st_size != Path(source).stat().st_size:
                short.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, copy)
        except OSError:  # копию держит другая запущенная программа (тогда она уже нужной версии) или нет места
            pass
        if copy.is_file() and len(str(copy)) < MAX_PATH and FontManager.load_font(str(copy)):
            loaded.append(name)
    if FONTS[0] in loaded and DrawEngine.preferred_drawing_method == "circle_shapes":
        DrawEngine.preferred_drawing_method = "font_shapes"  # customtkinter уже перешёл на грубые углы — вернуть

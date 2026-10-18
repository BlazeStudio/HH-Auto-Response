"""Лёгкие виджеты для больших страниц: текст, рамка, группа вариантов-«чипов».

У customtkinter каждая подпись — три окна Windows (рамка, холст, метка), флажок — четыре, строка-рамка — два.
На «Настройках» их набиралось 915, и Windows перерисовывала их по одному: при переключении страниц, после
закрытия окон и при быстрой прокрутке были видны куски, белые и чёрные пятна, «размножившиеся» строки.
Здесь текст и рамка — одно окно, а целая группа флажков — одна картинка на одном холсте. Темы (светлая и
тёмная) и масштаб экрана они подхватывают так же, как виджеты customtkinter.
"""

from __future__ import annotations

import sys
import tkinter
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

import customtkinter as ctk
from customtkinter.windows.widgets.appearance_mode import CTkAppearanceModeBaseClass
from customtkinter.windows.widgets.scaling import CTkScalingBaseClass
from PIL import Image, ImageDraw, ImageFont, ImageTk

from .theme import ACCENT, ACCENT_SOFT, BORDER, CARD_BG, TEXT


@lru_cache(maxsize=None)
def _family() -> str:
    return ctk.CTkFont().cget("family")  # тот же шрифт, что у customtkinter (на Windows — Roboto)


class _Light(CTkAppearanceModeBaseClass, CTkScalingBaseClass):
    """Общее для лёгких виджетов: тема, масштаб и отступы pack/grid в масштабе экрана, как у customtkinter."""

    def _init_light(self) -> None:
        CTkAppearanceModeBaseClass.__init__(self)
        CTkScalingBaseClass.__init__(self, scaling_type="widget")

    def _restyle(self) -> None:
        raise NotImplementedError

    def _set_appearance_mode(self, mode_string: str) -> None:
        CTkAppearanceModeBaseClass._set_appearance_mode(self, mode_string)
        self._restyle()

    def _set_scaling(self, new_widget_scaling, new_window_scaling) -> None:
        CTkScalingBaseClass._set_scaling(self, new_widget_scaling, new_window_scaling)
        self._restyle()

    def pack(self, **kwargs):
        return tkinter.Pack.pack_configure(self, **self._apply_argument_scaling(kwargs))

    def grid(self, **kwargs):
        return tkinter.Grid.grid_configure(self, **self._apply_argument_scaling(kwargs))

    def _destroy_light(self) -> None:
        CTkAppearanceModeBaseClass.destroy(self)
        CTkScalingBaseClass.destroy(self)


class PlainFrame(_Light, tkinter.Frame):
    """Рамка без отрисовки: одно окно вместо двух у CTkFrame. bg — (светлая, тёмная)."""

    def __init__(self, master, bg=CARD_BG, **kwargs):
        tkinter.Frame.__init__(self, master, bd=0, highlightthickness=0, **kwargs)
        self._bg = bg
        self._init_light()
        self._restyle()

    def _restyle(self) -> None:
        tkinter.Frame.configure(self, bg=self._apply_appearance_mode(self._bg))

    def cget(self, key):
        # customtkinter спрашивает у родителя цвет фона через cget("bg") — отдаём обе темы сразу, тогда
        # поля и кнопки внутри перекрашиваются при смене темы правильно
        if key in ("bg", "background", "fg_color"):
            return self._bg
        return tkinter.Frame.cget(self, key)

    def destroy(self):
        self._destroy_light()
        tkinter.Frame.destroy(self)


class TextLabel(_Light, tkinter.Label):
    """Текст: одно окно вместо трёх у CTkLabel. color, bg — (светлая, тёмная); size — как у CTkFont."""

    def __init__(self, master, text: str = "", color=TEXT, size: int = 13, weight: str = "normal",
                 wraplength: int = 0, bg=CARD_BG, **kwargs):
        kwargs.setdefault("anchor", "w")
        kwargs.setdefault("justify", "left")
        tkinter.Label.__init__(self, master, text=text, bd=0, padx=0, pady=0, highlightthickness=0, **kwargs)
        self._color, self._bg, self._size, self._weight, self._wrap = color, bg, size, weight, wraplength
        self._init_light()
        self._restyle()

    def _restyle(self) -> None:
        scale = self._get_widget_scaling()
        tkinter.Label.configure(self, fg=self._apply_appearance_mode(self._color),
                                bg=self._apply_appearance_mode(self._bg),
                                font=(_family(), -round(self._size * scale), self._weight),
                                wraplength=round(self._wrap * scale))

    def set(self, text: str | None = None, color=None) -> None:
        if color is not None:
            self._color = color
        tkinter.Label.configure(self, text=self.cget("text") if text is None else text,
                                fg=self._apply_appearance_mode(self._color))

    def destroy(self):
        self._destroy_light()
        tkinter.Label.destroy(self)


@lru_cache(maxsize=None)
def _chip_font(px: int) -> ImageFont.ImageFont:
    roboto = Path(ctk.__file__).parent / "assets" / "fonts" / "Roboto" / "Roboto-Regular.ttf"
    for candidate in (str(roboto), "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, px)
        except OSError:
            continue
    return ImageFont.load_default()


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


class ChipGroup(_Light, tkinter.Canvas):
    """Несколько вариантов на выбор — «чипы» вместо флажков: вся группа одна картинка на одном холсте
    (одно окно вместо четырёх на каждый флажок). Щелчок — выбрать / снять, наведение — подсветка."""

    SS = 2  # фигуры рисуем вдвое крупнее и уменьшаем усреднением (reduce — быстро) — гладкие скругления

    def __init__(self, master, options, bg=CARD_BG, selected: set | None = None, on_change=None):
        """options — {значение: подпись} или [(значение, подпись)]. selected — общий набор выбранного (если
        несколько групп выбирают из одного справочника), on_change — вызывается после щелчка."""
        tkinter.Canvas.__init__(self, master, bd=0, highlightthickness=0, height=32, cursor="hand2")
        self.options = list(options.items() if isinstance(options, dict) else options)  # (значение, подпись)
        self.selected: set[str] = selected if selected is not None else set()
        self.on_change = on_change
        self._bg = bg
        self._hover: str | None = None
        self._boxes: list[tuple[float, float, float, float, str]] = []
        self._width = 0
        self._photo = None
        self._init_light()
        self.bind("<Configure>", self._on_resize)
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._move)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self._restyle()

    # --- значение ---
    def get(self) -> list[str]:
        return [value for value, _ in self.options if value in self.selected]

    def set(self, values) -> None:
        self.selected.clear()
        self.selected.update(str(v) for v in values or [])
        self._render()

    def set_options(self, options) -> None:
        """Другой набор вариантов (например, после поиска по названию)."""
        self.options = list(options.items() if isinstance(options, dict) else options)
        self._hover = None
        self._render()

    # --- события ---
    def _on_resize(self, event) -> None:
        if abs(event.width - self._width) > 2:
            self._width = event.width
            self._render()

    def _hit(self, x, y) -> str | None:
        return next((value for x0, y0, x1, y1, value in self._boxes if x0 <= x <= x1 and y0 <= y <= y1), None)

    def _click(self, event) -> None:
        value = self._hit(event.x, event.y)
        if value is not None:
            self.selected ^= {value}
            self._render()
            if self.on_change:
                self.on_change()

    def _move(self, event) -> None:
        self._set_hover(self._hit(event.x, event.y))

    def _set_hover(self, value) -> None:
        if value != self._hover:
            self._hover = value
            self._render()

    def _restyle(self) -> None:
        tkinter.Canvas.configure(self, bg=self._apply_appearance_mode(self._bg))
        self._render()

    # --- рисование ---
    def _layout(self, width: int, scale: float):
        font = _chip_font(max(8, round(13 * scale)))
        height, gap, pad, check = round(30 * scale), round(8 * scale), round(12 * scale), round(16 * scale)
        x = y = 0
        boxes = []
        for value, label in self.options:
            w = round(font.getlength(label)) + pad * 2 + check
            if x and x + w > width:
                x, y = 0, y + height + gap
            boxes.append((x, y, x + w, y + height, value, label))
            x += w + gap
        total = (y + height) if boxes else height
        return font, boxes, total, pad, check

    def _render(self) -> None:
        scale = self._get_widget_scaling()
        width = self._width if self._width > 10 else round(760 * scale)
        font, boxes, total, pad, check = self._layout(width, scale)
        if self._width <= 10:  # ширина ещё не известна (не на экране) — только высота, картинку нарисуем потом
            self._boxes = [(x0, y0, x1, y1, value) for x0, y0, x1, y1, value, _ in boxes]
            if int(float(self.cget("height"))) != total:
                tkinter.Canvas.configure(self, height=total)
            return
        mode = self._apply_appearance_mode
        bg, border, accent, soft, text = (_rgb(mode(c)) for c in (self._bg, BORDER, ACCENT, ACCENT_SOFT, TEXT))
        ss = self.SS
        shapes = Image.new("RGB", (width * ss, total * ss), bg)
        draw = ImageDraw.Draw(shapes)
        radius = round(9 * scale) * ss
        for x0, y0, x1, y1, value, _ in boxes:
            on, hover = value in self.selected, value == self._hover
            box = (x0 * ss, y0 * ss, x1 * ss - 1, y1 * ss - 1)
            if on:
                draw.rounded_rectangle(box, radius, fill=accent)
            else:
                draw.rounded_rectangle(box, radius, fill=soft if hover else bg, outline=accent if hover else border,
                                       width=max(ss, round(1 * scale * ss)))
            # галочка у выбранного, пустой кружок у остальных — место одинаковое, чипы не прыгают
            cx, cy = (x0 + pad + check * 0.35) * ss, (y0 + y1) / 2 * ss
            r = check * 0.32 * ss
            if on:
                draw.line((cx - r, cy, cx - r * 0.25, cy + r * 0.7, cx + r, cy - r * 0.75), fill=(255, 255, 255),
                          width=max(ss, round(2 * scale * ss)), joint="curve")
            else:
                draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=accent if hover else border,
                             width=max(ss, round(1.5 * scale * ss)))
        image = shapes.reduce(ss)
        draw = ImageDraw.Draw(image)
        for x0, y0, x1, y1, value, label in boxes:
            color = (255, 255, 255) if value in self.selected else text
            draw.text((x0 + pad + check, (y0 + y1) / 2), label, font=font, fill=color, anchor="lm")
        self._boxes = [(x0, y0, x1, y1, value) for x0, y0, x1, y1, value, _ in boxes]
        self._photo = ImageTk.PhotoImage(image, master=self)
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)
        if int(float(self.cget("height"))) != total:
            tkinter.Canvas.configure(self, height=total)

    def destroy(self):
        self._destroy_light()
        tkinter.Canvas.destroy(self)


class ItemList(_Light, tkinter.Text):
    """Список строк для выбора (например, логи в «Журналах»): одно окно на весь список вместо кнопки
    на каждую строку, прокрутка колесом — своя. Наведение подсвечивает строку, щелчок — выбирает."""

    def __init__(self, master, on_select, bg=CARD_BG, **kwargs):
        tkinter.Text.__init__(self, master, bd=0, highlightthickness=0, wrap="none", cursor="hand2",
                              insertwidth=0, takefocus=0, **kwargs)
        self._bg, self.on_select = bg, on_select
        self.keys: list = []
        self.current = None
        self._hover: int | None = None
        self._init_light()
        self.bind("<Motion>", self._move)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self.bind("<Button-1>", self._click)
        self.bind("<<Selection>>", lambda _e: self.tag_remove("sel", "1.0", "end"))  # без синего выделения текста
        self._restyle()

    def _restyle(self) -> None:
        scale = self._get_widget_scaling()
        mode = self._apply_appearance_mode
        tkinter.Text.configure(self, bg=mode(self._bg), fg=mode(TEXT), font=(_family(), -round(13 * scale)),
                               spacing1=round(6 * scale), spacing3=round(6 * scale), padx=round(10 * scale),
                               selectbackground=mode(self._bg), inactiveselectbackground=mode(self._bg))
        self.tag_configure("hover", background=mode(ACCENT_SOFT))
        self.tag_configure("current", background=mode(ACCENT_SOFT), foreground=mode(ACCENT))

    def set_items(self, items: list[tuple[object, str]], current=None) -> None:
        """items — (ключ, подпись). Список перерисовывается целиком, но это одно окно — быстро и без мигания."""
        self.keys = [key for key, _ in items]
        self._hover = None
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.insert("1.0", "\n".join(text for _, text in items))
        self.configure(state="disabled")
        self.select(current)

    def select(self, key) -> None:
        self.current = key
        self.tag_remove("current", "1.0", "end")
        if key in self.keys:
            line = self.keys.index(key) + 1
            self.tag_add("current", f"{line}.0", f"{line}.0 lineend +1c")

    def _line(self, event) -> int | None:
        line = int(self.index(f"@{event.x},{event.y}").split(".")[0]) - 1
        bbox = self.bbox(f"{line + 1}.0")
        if not self.keys or not 0 <= line < len(self.keys) or bbox is None or event.y > bbox[1] + bbox[3] + 12:
            return None
        return line

    def _set_hover(self, line: int | None) -> None:
        if line == self._hover:
            return
        self._hover = line
        self.tag_remove("hover", "1.0", "end")
        if line is not None:
            self.tag_add("hover", f"{line + 1}.0", f"{line + 1}.0 lineend +1c")
        self.tag_raise("current")

    def _move(self, event) -> None:
        self._set_hover(self._line(event))

    def _click(self, event):
        line = self._line(event)
        if line is not None:
            self.select(self.keys[line])
            self.on_select(self.keys[line])
        return "break"

    def destroy(self):
        self._destroy_light()
        tkinter.Text.destroy(self)


# ───────────────────── перестройка окна без кусков и вспышек (Windows) ─────────────────────

_frozen: set[int] = set()


@contextmanager
def frozen(widget: tkinter.Misc, flush: bool = False):
    """На время перестройки (смена страницы, вкладки, сдвиг прокрутки) окно не рисуется, потом
    перерисовывается целиком.

    Без этого Windows показывает промежуточные состояния: старая страница наполовину, белые и чёрные
    прямоугольники на месте ещё не нарисованных окон, а при прокрутке — старую картинку, скопированную на новое
    место. flush — дорисовать сразу, не возвращаясь в общий цикл событий (для прокрутки: следующий сдвиг
    придёт уже на готовую картинку)."""
    if sys.platform != "win32":
        yield
        return
    import ctypes
    import _tkinter

    user32 = ctypes.windll.user32
    hwnd = widget.winfo_id()
    if hwnd in _frozen:  # уже заморожено снаружи
        yield
        return
    _frozen.add(hwnd)
    user32.SendMessageW(hwnd, 0x000B, 0, 0)  # WM_SETREDRAW: не рисовать
    try:
        yield
        widget.update_idletasks()  # раскладка — пока экран не меняется
    finally:
        user32.SendMessageW(hwnd, 0x000B, 1, 0)
        _frozen.discard(hwnd)
        # RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_FRAME (+ RDW_UPDATENOW): перерисовать всё, фон не стирать —
        # стирание и даёт белые вспышки
        user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0080 | 0x0400 | (0x0100 if flush else 0))
        if flush:
            try:
                for _ in range(500):  # Windows прислал «перерисуй» — разобрать их (только оконные события)
                    if not widget.tk.dooneevent(_tkinter.WINDOW_EVENTS | _tkinter.DONT_WAIT):
                        break
                widget.update_idletasks()
            except tkinter.TclError:
                pass

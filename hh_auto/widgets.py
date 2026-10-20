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

from .theme import ACCENT, ACCENT_SOFT, BORDER, CARD_BG, MUTED, TEXT


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
        _copy_menu(self, [("Копировать", lambda: _copy(self, self.cget("text")))])  # подпись — целиком

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


def _copy(widget: tkinter.Misc, text: str) -> None:
    widget.clipboard_clear()
    widget.clipboard_append(text)


def _copy_menu(widget: tkinter.Misc, items) -> None:
    """Меню по правой кнопке: items — [(подпись, команда)]."""
    menu = tkinter.Menu(widget, tearoff=0)
    for label, command in items:
        menu.add_command(label=label, command=command)
    for button in ("<Button-3>", "<Button-2>") if sys.platform == "darwin" else ("<Button-3>",):
        widget.bind(button, lambda e: menu.tk_popup(e.x_root, e.y_root), add="+")


def _is_copy_key(event) -> bool:
    """Ctrl+C в любой раскладке: в русской keysym другой, а keycode 67 — та же клавиша C (Windows)."""
    return event.keysym.lower() in ("c", "cyrillic_es") or event.keycode == 67


class SelectableText(_Light, tkinter.Text):
    """Текст, который можно выделить мышью и скопировать: Ctrl+C (в любой раскладке), правая кнопка →
    «Копировать». Для подсказок, путей и текста ошибок. Высота сама подстраивается под текст и ширину.

    width_px — ширина, по которой считать перенос строк, пока виджет ещё не на экране (как wraplength)."""

    def __init__(self, master, text: str = "", color=TEXT, size: int = 13, weight: str = "normal", bg=CARD_BG,
                 width_px: int = 600, **kwargs):
        tkinter.Text.__init__(self, master, bd=0, highlightthickness=0, relief="flat", wrap="word", height=1,
                              width=1, padx=0, pady=0, insertwidth=0, takefocus=0, cursor="xterm", **kwargs)
        self._color, self._bg, self._size, self._weight, self._width_px = color, bg, size, weight, width_px
        self._text = text
        self._init_light()
        self.bind("<Configure>", lambda _e: self._fit())
        self.bind("<Button-1>", lambda _e: self.focus_set(), add="+")  # иначе Ctrl+C уйдёт в другое поле
        self.bind("<Command-KeyPress>" if sys.platform == "darwin" else "<Control-KeyPress>", self._on_ctrl)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", self._wheel)
        self.bind("<Button-5>", self._wheel)
        _copy_menu(self, [("Копировать", self.copy), ("Копировать всё", lambda: _copy(self, self._text))])
        self._restyle()
        self.set(text)

    def _restyle(self) -> None:
        scale = self._get_widget_scaling()
        mode = self._apply_appearance_mode
        self._font = (_family(), -round(self._size * scale), self._weight)
        tkinter.Text.configure(self, fg=mode(self._color), bg=mode(self._bg), font=self._font,
                               selectbackground=mode(ACCENT_SOFT), selectforeground=mode(TEXT),
                               inactiveselectbackground=mode(ACCENT_SOFT))
        self._fit()

    def set(self, text: str, color=None) -> None:
        self._text = text
        if color is not None:
            self._color = color
            tkinter.Text.configure(self, fg=self._apply_appearance_mode(color))
        tkinter.Text.configure(self, state="normal")
        self.delete("1.0", "end")
        self.insert("1.0", text)
        tkinter.Text.configure(self, state="disabled")
        self._fit()

    def _fit(self) -> None:
        """Высота по числу строк после переноса: на экране — как посчитал сам Tk, до этого — по ширине width_px."""
        # у скрытого окна ширина ещё не настоящая (Windows применит её при показе) — считаем по width_px
        if self.winfo_ismapped() and self.winfo_width() > 20:
            try:  # update — пересчитать раскладку строк по нынешней ширине, а не по прошлой
                counted = self.count("1.0", "end-1c", "update", "displaylines")
                lines = (counted[0] if isinstance(counted, tuple) else counted or 0) + 1
            except tkinter.TclError:
                lines = self._estimate(self.winfo_width())
        else:
            lines = self._estimate(round(self._width_px * self._get_widget_scaling()))
        if int(self.cget("height")) != max(lines, 1):
            tkinter.Text.configure(self, height=max(lines, 1))

    def _estimate(self, width: int) -> int:
        import tkinter.font as tkfont

        font = tkfont.Font(root=self, font=self._font)
        lines = 0
        for paragraph in self._text.split("\n"):
            line = ""
            count = 1
            for word in paragraph.split(" "):
                candidate = f"{line} {word}" if line else word
                if line and font.measure(candidate) > width:
                    count += 1
                    line = word
                else:
                    line = candidate
            lines += count
        return lines

    def copy(self) -> None:
        try:
            _copy(self, self.get("sel.first", "sel.last"))
        except tkinter.TclError:  # ничего не выделено — весь текст
            _copy(self, self._text)

    def _on_ctrl(self, event):
        if _is_copy_key(event):
            self.copy()
            return "break"
        if event.keysym.lower() in ("a", "cyrillic_ef") or event.keycode == 65:
            self.tag_add("sel", "1.0", "end-1c")
            return "break"
        return None

    def _wheel(self, event):
        """Колесо над текстом листает страницу, а не сдвигает строки самого текста."""
        widget = self.master
        while widget is not None and not hasattr(widget, "_mouse_wheel_all"):
            widget = getattr(widget, "master", None)
        if widget is not None:
            widget._mouse_wheel_all(event)
        return "break"

    def destroy(self):
        self._destroy_light()
        tkinter.Text.destroy(self)


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

def flush_paint(widget: tkinter.Misc) -> None:
    """Дорисовать сразу всё, что Windows пометила к перерисовке после сдвига, не дожидаясь общего цикла событий.
    Тогда следующий кадр прокрутки приходит уже на готовую картинку и старые куски не копируются дальше."""
    widget.update_idletasks()  # Tk передвинул окна
    if sys.platform != "win32":
        return
    import ctypes
    import _tkinter

    try:
        ctypes.windll.user32.RedrawWindow(widget.winfo_id(), None, None, 0x0080 | 0x0100)  # ALLCHILDREN | UPDATENOW
        for _ in range(500):  # «перерисуй» от Windows — разобрать их (только оконные события)
            if not widget.tk.dooneevent(_tkinter.WINDOW_EVENTS | _tkinter.DONT_WAIT):
                break
        widget.update_idletasks()
    except (OSError, tkinter.TclError):
        pass


class precise_timer:
    """Точный системный таймер Windows (1 мс) на время анимации: обычный тикает раз в 15,6 мс, и after(8)
    на деле срабатывает реже 64 раз в секунду. Включается только пока что-то двигается."""

    _users = 0

    @classmethod
    def acquire(cls) -> None:
        if sys.platform == "win32" and cls._users == 0:
            try:
                import ctypes

                ctypes.windll.winmm.timeBeginPeriod(1)
            except (AttributeError, OSError):
                pass
        cls._users += 1

    @classmethod
    def release(cls) -> None:
        cls._users = max(0, cls._users - 1)
        if sys.platform == "win32" and cls._users == 0:
            try:
                import ctypes

                ctypes.windll.winmm.timeEndPeriod(1)
            except (AttributeError, OSError):
                pass


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


# ───────────────────── смена темы за один кадр ─────────────────────

class theme_switch:
    """Светлая ↔ тёмная без подвисания. customtkinter перекрашивает все ~500 элементов разом, и 90% из них —
    на скрытых страницах и вкладках (0,3–0,45 с). Здесь видимое перекрашивается сразу и показывается одним
    кадром, а скрытое — понемногу в фоне (окно отвечает). Если скрытую страницу открыли раньше, чем фон
    дошёл до неё, она докрашивается перед показом (ensure)."""

    _root: tkinter.Misc | None = None
    _pending: list = []
    _job = None
    BUDGET = 0.012  # сек на порцию фоновой перекраски — меньше кадра

    @classmethod
    def install(cls, root: tkinter.Misc) -> None:
        from customtkinter.windows.widgets.appearance_mode.appearance_mode_tracker import AppearanceModeTracker

        cls._root = root
        AppearanceModeTracker.update_callbacks = classmethod(lambda tracker: cls._switch(tracker))

    @staticmethod
    def _mode(tracker) -> str:
        return "Dark" if tracker.appearance_mode == 1 else "Light"

    @classmethod
    def _switch(cls, tracker) -> None:
        mode = cls._mode(tracker)
        visible, hidden = [], []
        for callback in list(tracker.callback_list):
            widget = getattr(callback, "__self__", None)
            try:
                shown = not isinstance(widget, tkinter.Misc) or bool(widget.winfo_viewable())
            except tkinter.TclError:  # окно уже закрыли
                continue
            (visible if shown else hidden).append(callback)
        root = cls._root
        try:
            alive = root is not None and bool(root.winfo_exists())
        except tkinter.TclError:
            alive = False
        if alive:
            with frozen(root, flush=True):  # всё видимое меняется на экране разом
                for callback in visible:
                    cls._call(callback, mode)
        else:
            for callback in visible:
                cls._call(callback, mode)
        cls._pending = hidden
        cls._schedule()

    @staticmethod
    def _call(callback, mode: str) -> None:
        try:
            callback(mode)
        except Exception:  # как в customtkinter: один сломанный элемент не мешает остальным
            pass

    @classmethod
    def _schedule(cls) -> None:
        if cls._pending and cls._job is None and cls._root is not None:
            try:
                cls._job = cls._root.after(1, cls._drain)
            except tkinter.TclError:
                cls._pending = []

    @classmethod
    def _drain(cls) -> None:
        import time
        from customtkinter.windows.widgets.appearance_mode.appearance_mode_tracker import AppearanceModeTracker

        cls._job = None
        mode = cls._mode(AppearanceModeTracker)
        deadline = time.perf_counter() + cls.BUDGET
        while cls._pending and time.perf_counter() < deadline:
            cls._call(cls._pending.pop(), mode)
        cls._schedule()

    @classmethod
    def ensure(cls, widget: tkinter.Misc | None = None) -> None:
        """Докрасить сразу всё ожидающее внутри widget (или вообще всё) — перед тем как показать."""
        if not cls._pending:
            return
        from customtkinter.windows.widgets.appearance_mode.appearance_mode_tracker import AppearanceModeTracker

        prefix = str(widget) if widget is not None else ""
        mode, rest = cls._mode(AppearanceModeTracker), []
        for callback in cls._pending:
            owner = getattr(callback, "__self__", None)
            name = str(owner) if isinstance(owner, tkinter.Misc) else ""
            if not prefix or name == prefix or name.startswith(prefix + "."):
                cls._call(callback, mode)
            else:
                rest.append(callback)
        cls._pending = rest


def set_titlebar_dark(window: tkinter.Misc, dark: bool) -> None:
    """Тёмный или светлый заголовок окна Windows. customtkinter для этого прячет окно и показывает снова —
    оно мигает и перерисовывается целиком; достаточно сменить атрибут и перерисовать рамку."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (и до Windows 10 20H1)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED — только рамка
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    except (OSError, AttributeError, tkinter.TclError):
        pass


# ───────────────────── Ctrl+C / V / X / A / Z в любой раскладке, отмена в полях ввода ─────────────────────

# В русской раскладке Tk не узнаёт букву под Ctrl (вместо «c» приходит «??»), и стандартные сочетания молчат.
# Узнаём клавишу по её коду: на Windows это виртуальный код клавиши, он от раскладки не зависит.
_KEYCODES = {67: "<<Copy>>", 86: "<<Paste>>", 88: "<<Cut>>", 65: "<<SelectAll>>", 90: "<<Undo>>", 89: "<<Redo>>"}
_CYRILLIC = {"cyrillic_es": "<<Copy>>", "cyrillic_em": "<<Paste>>", "cyrillic_che": "<<Cut>>",
             "cyrillic_ef": "<<SelectAll>>", "cyrillic_ya": "<<Undo>>", "cyrillic_en": "<<Redo>>"}  # Linux, macOS


def install_editing(root: tkinter.Misc) -> None:
    """Для всех полей ввода приложения: сочетания в любой раскладке, Ctrl+Z / Ctrl+Y (и Ctrl+Shift+Z),
    меню правой кнопки «Отменить · Вырезать · Копировать · Вставить · Выделить всё»."""
    root.bind_all("<Control-KeyPress>", _layout_shortcut, add="+")
    root.bind_all("<FocusIn>", _focus_in, add="+")
    root.bind_all("<FocusOut>", _focus_out, add="+")
    root.bind_all("<KeyRelease>", lambda e: _EntryHistory.touch(e.widget), add="+")
    for event in ("<<Paste>>", "<<Cut>>", "<<Clear>>"):
        root.bind_class("Entry", event, lambda e: e.widget.after_idle(_EntryHistory.touch, e.widget), add="+")
    # в Tk «выделить всё» в многострочном поле берёт и служебный последний перевод строки — копировалась
    # лишняя пустая строка
    root.bind_class("Text", "<<SelectAll>>", lambda e: e.widget.tag_add("sel", "1.0", "end-1c"))
    root.bind_class("Entry", "<<Undo>>", lambda e: _EntryHistory.step(e.widget, undo=True))
    root.bind_class("Entry", "<<Redo>>", lambda e: _EntryHistory.step(e.widget, undo=False))
    root.bind_all("<Button-3>", _edit_menu, add="+")


def _layout_shortcut(event):
    widget = event.widget
    if not isinstance(widget, (tkinter.Entry, tkinter.Text)):
        return None
    keysym = event.keysym or ""
    shift = bool(event.state & 0x1)
    if sys.platform == "win32":
        name = _KEYCODES.get(event.keycode)
    else:
        name = _CYRILLIC.get(keysym.lower()) or ("<<Undo>>" if keysym.lower() == "z" else None)
    if name == "<<Undo>>" and shift:
        name = "<<Redo>>"  # Ctrl+Shift+Z — как в браузере и редакторах
    elif len(keysym) == 1 and keysym.isascii():
        return None  # латиница: Tk сам превратил сочетание в <<Copy>> и т.п.
    if name is None:
        return None
    widget.event_generate(name)
    return "break"


def _editable(widget) -> bool:
    try:
        return str(widget.cget("state")) not in ("disabled", "readonly")
    except tkinter.TclError:
        return False


def _focus_in(event) -> None:
    widget = event.widget
    if isinstance(widget, tkinter.Entry):
        _EntryHistory.attach(widget)
    elif isinstance(widget, tkinter.Text) and _editable(widget):
        try:
            if not widget.cget("undo"):
                widget.configure(undo=True, autoseparators=True, maxundo=-1)
                widget.edit_reset()
            elif getattr(widget, "_hh_text", None) != widget.get("1.0", "end-1c"):
                widget.edit_reset()  # текст поменяла программа (загрузка настроек) — это не отменяется
        except tkinter.TclError:
            pass


def _focus_out(event) -> None:
    widget = event.widget
    if isinstance(widget, tkinter.Text):
        try:
            widget._hh_text = widget.get("1.0", "end-1c")
        except tkinter.TclError:
            pass


class _EntryHistory:
    """Отмена и повтор для однострочных полей: в Tk у Entry их нет. Набор подряд (пауза меньше секунды) —
    один шаг, как в редакторах."""

    _all: dict[str, "_EntryHistory"] = {}
    GROUP = 1.0

    def __init__(self, entry: tkinter.Entry):
        self.entry = entry
        self.last = entry.get()
        self.undo: list[str] = []
        self.redo: list[str] = []
        self.typed_at = 0.0

    @classmethod
    def attach(cls, entry: tkinter.Entry) -> "_EntryHistory":
        history = cls._all.get(str(entry))
        if history is None or history.entry is not entry:
            history = cls._all[str(entry)] = cls(entry)
            entry.bind("<Destroy>", lambda e: cls._all.pop(str(e.widget), None), add="+")
        elif entry.get() != history.last:  # поле заполнила программа — с этого места и отменяем
            history.last, history.undo, history.redo = entry.get(), [], []
        return history

    @classmethod
    def touch(cls, widget) -> None:
        if not isinstance(widget, tkinter.Entry):
            return
        history = cls._all.get(str(widget))
        if history is None:
            return
        import time

        try:
            value = widget.get()
        except tkinter.TclError:
            return
        if value == history.last:
            return
        now = time.monotonic()
        typing = abs(len(value) - len(history.last)) == 1
        if not (history.undo and typing and now - history.typed_at < cls.GROUP):
            history.undo.append(history.last)
        history.typed_at = now if typing else 0.0
        history.redo.clear()
        history.last = value

    @classmethod
    def step(cls, widget, undo: bool) -> str:
        history = cls._all.get(str(widget)) or cls.attach(widget)
        cls.touch(widget)
        source, target = (history.undo, history.redo) if undo else (history.redo, history.undo)
        if source and _editable(widget):
            target.append(history.last)
            value = source.pop()
            widget.delete(0, "end")
            widget.insert(0, value)
            widget.icursor("end")
            history.last, history.typed_at = value, 0.0
        return "break"


def _edit_menu(event):
    """Правая кнопка в поле ввода. У виджетов со своим меню (подсказки, журнал) — их меню."""
    widget = event.widget
    if not isinstance(widget, (tkinter.Entry, tkinter.Text)) or widget.bind("<Button-3>"):
        return None
    editable = _editable(widget)
    try:
        widget.focus_set()
        has_selection = bool(widget.selection_present() if isinstance(widget, tkinter.Entry)
                             else widget.tag_ranges("sel"))
    except tkinter.TclError:
        return None
    menu = tkinter.Menu(widget, tearoff=0)

    def send(name: str) -> None:
        widget.event_generate(name)

    if editable:
        menu.add_command(label="Отменить", accelerator="Ctrl+Z", command=lambda: send("<<Undo>>"))
        menu.add_separator()
        menu.add_command(label="Вырезать", accelerator="Ctrl+X", command=lambda: send("<<Cut>>"),
                         state="normal" if has_selection else "disabled")
    menu.add_command(label="Копировать", accelerator="Ctrl+C", command=lambda: send("<<Copy>>"),
                     state="normal" if has_selection else "disabled")
    if editable:
        menu.add_command(label="Вставить", accelerator="Ctrl+V", command=lambda: send("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label="Выделить всё", accelerator="Ctrl+A", command=lambda: send("<<SelectAll>>"))
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()
    return "break"


# ───────────────────── график для вкладки «Статистика» ─────────────────────

def _nice_ceiling(value: float) -> float:
    """Верх оси «круглым» числом: 7 → 8, 13 → 15, 230 → 250."""
    import math

    if value <= 0:
        return 4
    power = 10 ** math.floor(math.log10(value))
    for mult in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if value <= mult * power:
            return mult * power
    return 10 * power


def _number(value: float) -> str:
    return f"{value:.0f}" if abs(value - round(value)) < 0.05 else f"{value:.1f}".replace(".", ",")


class Chart(_Light, tkinter.Canvas):
    """График одной картинкой (Pillow), как «чипы»: одно окно Windows вместо сотен. Виды: bar — столбцы по
    периодам, line — линии, total — итоги за период горизонтальными полосами. Наведение подсвечивает столбец
    и сообщает on_hover(индекс или None)."""

    SS = 2
    HEIGHT = 220

    def __init__(self, master, bg=CARD_BG, on_hover=None):
        tkinter.Canvas.__init__(self, master, bd=0, highlightthickness=0, height=self.HEIGHT)
        self._bg = bg
        self.on_hover = on_hover
        self.labels: list[str] = []
        self.series: list[tuple[str, tuple, list[float]]] = []  # (подпись, цвет (светлая, тёмная), значения)
        self.kind = "bar"
        self.empty_text = ""
        self._hover: int | None = None
        self._width = 0
        self._geometry = (0.0, 0.0, 0)  # (левый край области, ширина столбца, сколько столбцов) — для наведения
        self._photo = None
        self._init_light()
        self.bind("<Configure>", self._on_resize)
        self.bind("<Motion>", self._move)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self._restyle()

    def set_data(self, labels: list[str], series, kind: str, empty_text: str = "") -> None:
        self.labels, self.series, self.kind, self.empty_text = list(labels), list(series), kind, empty_text
        self._hover = None
        self._render()

    def _on_resize(self, event) -> None:
        if abs(event.width - self._width) > 2:
            self._width = event.width
            self._render()

    def _move(self, event) -> None:
        left, step, count = self._geometry
        if self.kind == "total" or not count or step <= 0:
            return
        index = int((event.x - left) // step)
        self._set_hover(index if 0 <= index < count else None)

    def _set_hover(self, index) -> None:
        if index != self._hover:
            self._hover = index
            self._render()
            if self.on_hover:
                self.on_hover(index)

    def _restyle(self) -> None:
        tkinter.Canvas.configure(self, bg=self._apply_appearance_mode(self._bg))
        self._render()

    def _render(self) -> None:
        if self._width <= 10:
            return
        scale = self._get_widget_scaling()
        mode = self._apply_appearance_mode
        bg, grid, muted, text, soft = (_rgb(mode(c)) for c in (self._bg, BORDER, MUTED, TEXT, ACCENT_SOFT))
        ss = self.SS
        width = self._width
        values = [v for _, _, vals in self.series for v in vals]
        total_rows = len(self.series)
        if self.kind == "total":
            height = max(round(60 * scale), round((total_rows * 30 + 12) * scale))
        else:
            height = round(self.HEIGHT * scale)
        image = Image.new("RGB", (width * ss, height * ss), bg)
        draw = ImageDraw.Draw(image)
        font = _chip_font(max(8, round(12 * scale * ss)))
        small = _chip_font(max(8, round(11 * scale * ss)))
        if not self.series or (self.kind != "total" and not self.labels) or not any(values):
            draw.text((width * ss / 2, height * ss / 2), self.empty_text or "Нет данных за этот период", font=font,
                      fill=muted, anchor="mm")
            self._geometry = (0.0, 0.0, 0)
        elif self.kind == "total":
            self._draw_totals(draw, width, height, scale, font, text, muted)
        else:
            self._draw_axes(draw, width, height, scale, font, small, grid, muted, soft)
        self._photo = ImageTk.PhotoImage(image.reduce(ss), master=self)
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)
        if int(float(self.cget("height"))) != height:
            tkinter.Canvas.configure(self, height=height)

    def _draw_totals(self, draw, width, height, scale, font, text, muted) -> None:
        """Итоги за период: по полосе на показатель, самые большие сверху."""
        ss, mode = self.SS, self._apply_appearance_mode
        rows = sorted(((name, color, sum(vals)) for name, color, vals in self.series), key=lambda r: -r[2])
        label_w = max(draw.textlength(name, font=font) for name, _, _ in rows) + 12 * scale * ss
        top = 6 * scale * ss
        row_h = 30 * scale * ss
        bar_left = label_w
        bar_right = width * ss - 60 * scale * ss
        peak = max(v for _, _, v in rows) or 1
        radius = 5 * scale * ss
        for i, (name, color, value) in enumerate(rows):
            y = top + i * row_h + row_h / 2
            draw.text((0, y), name, font=font, fill=text, anchor="lm")
            length = (bar_right - bar_left) * value / peak
            if value > 0:
                draw.rounded_rectangle((bar_left, y - 9 * scale * ss, bar_left + max(length, radius * 2),
                                        y + 9 * scale * ss), radius, fill=_rgb(mode(color)))
            draw.text((bar_left + length + 8 * scale * ss, y), _number(value), font=font, fill=muted, anchor="lm")
        self._geometry = (0.0, 0.0, 0)

    def _draw_axes(self, draw, width, height, scale, font, small, grid, muted, soft) -> None:
        ss, mode = self.SS, self._apply_appearance_mode
        count = len(self.labels)
        peak = max(max(vals) for _, _, vals in self.series)
        top_value = _nice_ceiling(peak)
        # делений столько, чтобы подписи были целыми: 30 → 0, 6, 12… а не 7,5 и 22,5
        ticks = next((t for t in (4, 5, 3, 6, 2) if (top_value / t) == int(top_value / t)), 4)
        tick_labels = [_number(top_value * i / ticks) for i in range(ticks + 1)]
        left = max(draw.textlength(t, font=small) for t in tick_labels) + 10 * scale * ss
        right = width * ss - 8 * scale * ss
        top = 10 * scale * ss
        bottom = height * ss - 24 * scale * ss
        plot_h = bottom - top
        # сетка и подписи оси
        for i in range(ticks + 1):
            y = bottom - plot_h * i / ticks
            draw.line((left, y, right, y), fill=grid, width=max(1, round(scale * ss)))
            draw.text((left - 6 * scale * ss, y), tick_labels[i], font=small, fill=muted, anchor="rm")
        step = (right - left) / count
        self._geometry = (left / ss, step / ss, count)
        if self._hover is not None and 0 <= self._hover < count:
            x0 = left + step * self._hover
            draw.rectangle((x0, top, x0 + step, bottom), fill=soft)
        # подписи периодов: сколько влезает без наложения
        label_w = max(draw.textlength(label, font=small) for label in self.labels) + 10 * scale * ss
        every = max(1, int(label_w // step) + 1)
        for i, label in enumerate(self.labels):
            if i % every == 0 or i == count - 1 and (count - 1) % every > every // 2:
                draw.text((left + step * (i + 0.5), bottom + 6 * scale * ss), label, font=small, fill=muted,
                          anchor="mt")
        y_of = lambda v: bottom - plot_h * (v / top_value)
        if self.kind == "bar":
            n = len(self.series)
            group = step * 0.72
            bar = group / n
            radius = min(bar * 0.3, 4 * scale * ss)
            for s, (_, color, vals) in enumerate(self.series):
                fill = _rgb(mode(color))
                for i, value in enumerate(vals):
                    if value <= 0:
                        continue
                    x0 = left + step * i + (step - group) / 2 + bar * s
                    y = y_of(value)
                    draw.rounded_rectangle((x0 + bar * 0.08, min(y, bottom - 2), x0 + bar * 0.92, bottom), radius,
                                           fill=fill)
        else:
            dot = 3 * scale * ss
            for _, color, vals in self.series:
                fill = _rgb(mode(color))
                points = [(left + step * (i + 0.5), y_of(v)) for i, v in enumerate(vals)]
                if len(points) > 1:
                    draw.line(points, fill=fill, width=max(2, round(2.2 * scale * ss)), joint="curve")
                if count <= 45:
                    for x, y in points:
                        draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=fill)

    def destroy(self):
        self._destroy_light()
        tkinter.Canvas.destroy(self)

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
from contextlib import contextmanager, nullcontext
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
    """Текст: одно окно вместо трёх у CTkLabel. color, bg — (светлая, тёмная); size — как у CTkFont.
    Выделяется мышью и копируется, как и CTkLabel, — см. install_label_selection."""

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

    def resize(self, size: int | None = None, wraplength: int | None = None) -> None:
        """Другой размер шрифта или ширина переноса (как у CTkFont и wraplength — до масштаба экрана)."""
        size = self._size if size is None else size
        wraplength = self._wrap if wraplength is None else wraplength
        if (size, wraplength) != (self._size, self._wrap):
            self._size, self._wrap = size, wraplength
            self._restyle()

    def text_width(self, text: str, size: int | None = None) -> int:
        """Ширина текста в пикселях этим шрифтом (size — другой размер шрифта)."""
        font = (_family(), -round((self._size if size is None else size) * self._get_widget_scaling()), self._weight)
        return int(self.tk.call("font", "measure", font, text))

    def destroy(self):
        self._destroy_light()
        tkinter.Label.destroy(self)


def _copy(widget: tkinter.Misc, text: str) -> None:
    widget.clipboard_clear()
    widget.clipboard_append(text)


# Правая кнопка мыши. На macOS в Tk это Button-2, а ещё там принят Ctrl+щелчок
MENU_BUTTONS = ("<Button-2>", "<Button-3>", "<Control-Button-1>") if sys.platform == "darwin" else ("<Button-3>",)


def _copy_menu(widget: tkinter.Misc, items) -> None:
    """Меню по правой кнопке: items — [(подпись, команда)]."""
    menu = tkinter.Menu(widget, tearoff=0)
    for label, command in items:
        menu.add_command(label=label, command=command)
    for button in MENU_BUTTONS:
        widget.bind(button, lambda e: menu.tk_popup(e.x_root, e.y_root), add="+")


def _wheel_to_page(widget: tkinter.Misc, event) -> str:
    """Колесо над текстом листает страницу, а не сдвигает строки самого текста."""
    parent = widget.master
    while parent is not None and not hasattr(parent, "_mouse_wheel_all"):
        parent = getattr(parent, "master", None)
    if parent is not None:
        parent._mouse_wheel_all(event)
    return "break"


def _is_copy_key(event) -> bool:
    """Ctrl+C в любой раскладке: в русской keysym другой, а keycode 67 — та же клавиша C (Windows)."""
    return event.keysym.lower() in ("c", "cyrillic_es") or event.keycode == 67


class SelectableText(_Light, tkinter.Text):
    """Текст, который можно выделить мышью и скопировать: Ctrl+C (в любой раскладке), правая кнопка →
    «Копировать». Для подсказок, путей и текста ошибок. Высота сама подстраивается под текст и ширину.

    width_px — ширина, по которой считать перенос строк, пока виджет ещё не на экране (как wraplength);
    quick — считать её грубо, по абзацам (для десятков текстов отчёта: точная высота всё равно посчитается,
    когда текст появится на экране, а пословный замер занимал полсекунды)."""

    def __init__(self, master, text: str = "", color=TEXT, size: int = 13, weight: str = "normal", bg=CARD_BG,
                 width_px: int = 600, quick: bool = False, **kwargs):
        tkinter.Text.__init__(self, master, bd=0, highlightthickness=0, relief="flat", wrap="word", height=1,
                              width=1, padx=0, pady=0, insertwidth=0, takefocus=0, cursor="xterm", **kwargs)
        self._color, self._bg, self._size, self._weight, self._width_px = color, bg, size, weight, width_px
        self._text = text
        self._quick, self._measure = quick, None
        self._init_light()
        self.bind("<Configure>", lambda _e: self._fit())
        # ширина могла смениться, пока текст был не на экране (окно ещё не показано, страница скрыта), — тогда
        # строки считались по width_px, и последняя пряталась («…дальше всё» без «автоматически»)
        self.bind("<Map>", lambda _e: self._fit(), add="+")
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
        self._measure = None
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
        lines = max(lines, 1) + self._extra_lines()
        if int(self.cget("height")) != lines:
            tkinter.Text.configure(self, height=lines)

    def _extra_lines(self) -> int:
        return 0

    def _estimate(self, width: int) -> int:
        import tkinter.font as tkfont

        if self._quick:
            if self._measure is None:
                self._measure = tkfont.Font(root=self, font=self._font)
            # ширина абзаца целиком на ширину строки, с запасом на перенос по словам
            return sum(max(1, -(-round(self._measure.measure(paragraph) * 1.08) // max(width, 1)))
                       for paragraph in self._text.split("\n"))
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
        return _wheel_to_page(self, event)

    def destroy(self):
        self._destroy_light()
        tkinter.Text.destroy(self)


class RichText(SelectableText):
    """Строки с колонками и цветами — таблица одним текстом: выделяется и копируется как обычный текст, столбцы
    разделены табуляцией (при вставке в Excel расходятся по ячейкам). И рисуется в разы быстрее сетки из
    десятков подписей.

    tabs — столбцы: [(x, "left" | "right")], x — в точках или доля ширины (меньше 1); indent — отступ
    перенесённых строк (длинный текст в последнем столбце продолжается под ним); wrap="none" — таблица
    не переносится: в узком окне правый край просто не виден; spacing — промежуток после каждой строки (точки).
    Шрифт у всех строк один — иначе высота, посчитанная по строкам, не совпала бы с настоящей."""

    def __init__(self, master, tabs=(), indent: int = 0, wrap: str = "word", size: int = 12, bg=CARD_BG,
                 width_px: int = 760, spacing: int = 0):
        self._tabs, self._indent, self._tab_spec, self._spacing = list(tabs), indent, None, spacing
        self._wrap_mode = wrap
        self._styles: dict[str, tuple] = {}  # тег → (цвет, жирный)
        self._link_tags: list[str] = []
        super().__init__(master, "", size=size, bg=bg, width_px=width_px, quick=True)
        tkinter.Text.configure(self, wrap=wrap)
        self.bind("<Configure>", lambda _e: self._apply_tabs(), add="+")

    def _restyle(self) -> None:
        super()._restyle()
        scale = self._get_widget_scaling()
        for tag, (color, bold) in self._styles.items():
            self.tag_configure(tag, foreground=self._apply_appearance_mode(color),
                               font=(_family(), -round(self._size * scale), "bold" if bold else "normal"))
        self.tag_configure("row", lmargin2=round(self._indent * scale), spacing3=round(self._spacing * scale))
        self._tab_spec = None
        self._apply_tabs()

    def _extra_lines(self) -> int:
        """Промежутки между строками — в строках (Tk считает высоту по строкам без них)."""
        if not self._spacing or not self._text:
            return 0
        import tkinter.font as tkfont

        rows = self._text.count("\n") + 1
        line = tkfont.Font(root=self, font=self._font).metrics("linespace")
        return -(-rows * round(self._spacing * self._get_widget_scaling()) // max(line, 1))

    def _apply_tabs(self) -> None:
        if not self._tabs:
            return
        scale = self._get_widget_scaling()
        width = self.winfo_width() if self.winfo_ismapped() and self.winfo_width() > 20 else self._width_px * scale
        spec, last = [], 0
        for x, align in self._tabs:
            px = max(round(x * width) if x < 1 else round(x * scale), last + 1)
            spec += [px, align]
            last = px
        if spec != self._tab_spec:
            self._tab_spec = spec
            tkinter.Text.configure(self, tabs=spec)

    def _style(self, color, bold: bool) -> str:
        for tag, style in self._styles.items():
            if style == (color, bold):
                return tag
        tag = f"style{len(self._styles)}"
        self._styles[tag] = (color, bold)
        size = -round(self._size * self._get_widget_scaling())
        self.tag_configure(tag, foreground=self._apply_appearance_mode(color),
                           font=(_family(), size, "bold" if bold else "normal"))
        return tag

    def set_rows(self, rows) -> None:
        """rows — строки, строка — ячейки: текст или (текст, цвет[, жирный[, ссылка]])."""
        tkinter.Text.configure(self, state="normal")
        self.delete("1.0", "end")
        for tag in self._link_tags:
            self.tag_delete(tag)
        self._link_tags = []
        plain = []
        for n, row in enumerate(rows):
            cells = []
            for i, cell in enumerate(row):
                text, color, bold, link = (((cell,) if isinstance(cell, str) else tuple(cell)) + (None,) * 3)[:4]
                text, bold = text or "", bool(bold)
                if i:
                    self.insert("end", "\t", "row")
                tags = ["row"]
                if color is not None or bold:
                    tags.append(self._style(color if color is not None else self._color, bold))
                if link:
                    tag = f"link{len(self._link_tags)}"
                    self._link_tags.append(tag)
                    tags.append(tag)
                    self.tag_bind(tag, "<Button-1>", lambda _e, url=link: _open_link(url))
                    self.tag_bind(tag, "<Enter>", lambda _e: tkinter.Text.configure(self, cursor="hand2"))
                    self.tag_bind(tag, "<Leave>", lambda _e: tkinter.Text.configure(self, cursor="xterm"))
                self.insert("end", text, tuple(tags))
                cells.append(text)
            if n + 1 < len(rows):
                self.insert("end", "\n", "row")
            plain.append("\t".join(cells))
        tkinter.Text.configure(self, state="disabled")
        self._text = "\n".join(plain)
        self._apply_tabs()
        self._fit()

    def _estimate(self, width: int) -> int:
        if self._wrap_mode == "none":
            return self._text.count("\n") + 1
        return super()._estimate(width)


def _open_link(url: str) -> None:
    import webbrowser

    webbrowser.open(url)


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
        values = {str(v) for v in values or []}
        if values == self.selected and self._photo is not None:
            return  # то же самое — не перерисовываем
        self.selected.clear()
        self.selected.update(values)
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
    # спрятанное окно и так не рисуется, а WM_SETREDRAW «рисовать» его показало бы: Windows ставит окну
    # видимость, и в углу страницы оставался белый квадратик 1×1 (вкладка настроек, открытая до показа страницы)
    if not user32.IsWindowVisible(hwnd):
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

class _Snapshot:
    """Картинка окна поверх него самого (только Windows): пока под ней перекрашиваются сотни элементов,
    видна старая картинка, а не промежуточные кадры с чёрными и белыми углами. Отдельное окно без рамки,
    не забирает фокус и пропускает щелчки мыши; снимок — только этого окна (PrintWindow), не экрана."""

    def __init__(self, root: tkinter.Misc, alpha: int):
        import ctypes
        from ctypes import wintypes

        self._ok = False
        self.alpha = alpha
        u, g = ctypes.windll.user32, ctypes.windll.gdi32
        self.u, self.g, self.ct = u, g, ctypes
        H, I = wintypes.HANDLE, ctypes.c_int
        for fn, args, res in (
            (u.GetParent, [H], H), (u.GetDC, [H], H), (u.ReleaseDC, [H, H], I),
            (u.GetClientRect, [H, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            (u.ClientToScreen, [H, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            (u.PrintWindow, [H, H, wintypes.UINT], wintypes.BOOL),
            (g.BitBlt, [H, I, I, I, I, H, I, I, wintypes.DWORD], wintypes.BOOL),
            (u.CreateWindowExW, [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, I, I, I, I,
                                 H, H, H, ctypes.c_void_p], H),
            (u.UpdateLayeredWindow, [H, H, ctypes.c_void_p, ctypes.c_void_p, H, ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            (u.ShowWindow, [H, I], wintypes.BOOL), (u.DestroyWindow, [H], wintypes.BOOL),
            (u.SetWindowRgn, [H, H, wintypes.BOOL], I),
            (g.CreateCompatibleDC, [H], H), (g.CreateCompatibleBitmap, [H, I, I], H),
            (g.SelectObject, [H, H], H), (g.DeleteObject, [H], wintypes.BOOL), (g.DeleteDC, [H], wintypes.BOOL),
            (g.CreateRoundRectRgn, [I, I, I, I, I, I], H),
        ):
            fn.argtypes, fn.restype = args, res

        class BLEND(ctypes.Structure):
            _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte),
                        ("format", ctypes.c_ubyte)]

        self.BLEND = BLEND
        self.frame = u.GetParent(root.winfo_id())  # настоящее окно Windows вокруг Tk-окна
        rect, origin = wintypes.RECT(), wintypes.POINT(0, 0)
        u.GetClientRect(self.frame, ctypes.byref(rect))
        u.ClientToScreen(self.frame, ctypes.byref(origin))
        self.size = (rect.right - rect.left, rect.bottom - rect.top)
        self.origin = (origin.x, origin.y)
        width, height = self.size
        if width <= 0 or height <= 0:
            return
        screen = u.GetDC(None)
        self.mem = g.CreateCompatibleDC(screen)
        self.bitmap = g.CreateCompatibleBitmap(screen, width, height)
        self.old = g.SelectObject(self.mem, self.bitmap)
        self.hwnd = None
        try:
            # копия того, что окно уже нарисовало (~1 мс); не вышло — PrintWindow (дольше, ~20–40 мс)
            client = root.winfo_id()
            source = u.GetDC(client)
            copied = bool(source) and g.BitBlt(self.mem, 0, 0, width, height, source, 0, 0, 0x00CC0020)  # SRCCOPY
            if source:
                u.ReleaseDC(client, source)
            if not copied and not u.PrintWindow(self.frame, self.mem, 0x1 | 0x2):  # CLIENTONLY | RENDERFULLCONTENT
                return
            # WS_EX_LAYERED | WS_EX_TRANSPARENT (щелчки — сквозь) | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE; WS_POPUP;
            # владелец — наше окно: снимок всегда над ним и только над ним
            self.hwnd = u.CreateWindowExW(0x80000 | 0x20 | 0x80 | 0x8000000, "Static", None, 0x80000000,
                                          origin.x, origin.y, width, height, self.frame, None, None, None)
            if not self.hwnd:
                return
            if sys.getwindowsversion().build >= 22000 and root.state() != "zoomed":
                # у Windows 11 нижние углы окна скруглены — у снимка тоже, иначе мелькнут квадратные уголки
                radius = round(8 * root.winfo_fpixels("1i") / 96) * 2
                u.SetWindowRgn(self.hwnd, g.CreateRoundRectRgn(0, -radius, width + 1, height + 1, radius, radius),
                               False)
            blend = BLEND(0, 0, alpha, 0)
            position, size, source = wintypes.POINT(*self.origin), wintypes.SIZE(width, height), wintypes.POINT(0, 0)
            if not u.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(position), ctypes.byref(size), self.mem,
                                         ctypes.byref(source), 0, ctypes.byref(blend), 0x2):  # ULW_ALPHA
                return
            u.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE — фокус остаётся у окна
            self._ok = True
        finally:
            u.ReleaseDC(None, screen)
            if not self._ok:
                self.close()

    def __bool__(self) -> bool:
        return self._ok

    def set_alpha(self, alpha: int) -> None:
        self.alpha = max(0, min(255, alpha))
        if self.hwnd:
            blend = self.BLEND(0, 0, self.alpha, 0)
            self.u.UpdateLayeredWindow(self.hwnd, None, None, None, None, None, 0, self.ct.byref(blend), 0x2)

    def close(self) -> None:
        if getattr(self, "hwnd", None):
            self.u.DestroyWindow(self.hwnd)
            self.hwnd = None
        if getattr(self, "mem", None):
            self.g.SelectObject(self.mem, self.old)
            self.g.DeleteObject(self.bitmap)
            self.g.DeleteDC(self.mem)
            self.mem = None
        self._ok = False


class transition:
    """Плавная смена вида окна: поверх кладётся снимок окна (begin), под ним меняется что угодно —
    страница, вкладка, тема — и дорисовывается целиком, потом снимок растворяется (fade). Недорисованных
    кусков и мелькающих углов не видно. Если новый переход начался, пока прошлый ещё растворяется (быстрые
    щелчки, страница и сразу вкладка), снимок остаётся тот же и растворяется уже в окончательный вид."""

    PEAK = 255  # непрозрачность снимка в начале (проверки ставят 0 — снимок не виден)
    _overlay: "_Snapshot | None" = None
    _root: tkinter.Misc | None = None
    _job = None
    _timer = False  # точный таймер Windows включён, пока снимок растворяется
    _idle: list = []  # кому сказать, что переход закончился (фоновая докраска темы ждёт его конца)

    @classmethod
    def active(cls) -> bool:
        return cls._overlay is not None

    @classmethod
    def begin(cls, root: tkinter.Misc) -> "_Snapshot | None":
        if cls._overlay is not None:  # прошлый переход ещё идёт — останавливаем растворение, снимок тот же
            cls._cancel_step()
            return cls._overlay
        if sys.platform != "win32":
            return None
        try:
            if not root.winfo_viewable() or root.state() in ("iconic", "withdrawn"):
                return None
            if cls.PEAK and float(root.attributes("-alpha")) < 0.05:
                return None  # окно невидимо — показывать нечего (и снимок не должен появиться сам по себе)
            overlay = _Snapshot(root, cls.PEAK)
        except Exception:  # нет снимка — меняем без перехода, как раньше
            return None
        if not overlay:
            return None
        cls._overlay, cls._root = overlay, root
        return overlay

    @classmethod
    def fade(cls, overlay: "_Snapshot", duration: float) -> None:
        import time

        if overlay is not cls._overlay:
            return
        if not cls._timer:
            cls._timer = True
            precise_timer.acquire()
        # с той непрозрачности, на которой снимок сейчас (прошлый переход мог уже начать растворять)
        share = overlay.alpha / cls.PEAK if cls.PEAK else 1
        cls._step(overlay, time.perf_counter(), max(duration * share, 0.001), overlay.alpha)

    @classmethod
    def _step(cls, overlay, started: float, duration: float, start_alpha: int) -> None:
        import time

        cls._job = None
        progress = (time.perf_counter() - started) / duration
        if progress >= 1 or overlay is not cls._overlay:
            if overlay is cls._overlay:
                cls.finish()
            return
        eased = progress * progress * (3 - 2 * progress)  # плавно в начале и в конце
        overlay.set_alpha(round(start_alpha * (1 - eased)))
        try:
            cls._job = cls._root.after(8, cls._step, overlay, started, duration, start_alpha)
        except (tkinter.TclError, AttributeError):
            cls.finish()

    @classmethod
    def _cancel_step(cls) -> None:
        if cls._job is not None:
            try:
                cls._root.after_cancel(cls._job)
            except (tkinter.TclError, AttributeError):
                pass
            cls._job = None

    @classmethod
    def finish(cls) -> None:
        """Убрать снимок сейчас же (конец перехода, закрытие окна)."""
        if cls._overlay is None:
            return
        overlay, cls._overlay = cls._overlay, None
        cls._cancel_step()
        overlay.close()
        if cls._timer:
            cls._timer = False
            precise_timer.release()
        for callback in list(cls._idle):
            try:
                callback()
            except Exception:
                pass


@contextmanager
def crossfade(root: tkinter.Misc, duration: float):
    """with crossfade(окно, 0.14): …перестройка… — внутри меняйте и дорисовывайте (frozen(…, flush=True)),
    по выходе снимок старого вида растворится за duration."""
    overlay = transition.begin(root)
    try:
        yield
    finally:
        if overlay is not None:
            transition.fade(overlay, duration)


class theme_switch:
    """Светлая ↔ тёмная плавно и без подвисания. customtkinter перекрашивает все ~500 элементов разом, и 90% из
    них — на скрытых страницах и вкладках (0,3–0,45 с), а Windows показывает перерисовку по кускам: вокруг
    скруглённых карточек мелькают чёрные и белые углы. Здесь поверх окна кладётся его снимок, видимое
    перекрашивается под ним, и снимок плавно растворяется (FADE) — старая тема перетекает в новую. Скрытое
    докрашивается понемногу в фоне, когда переход закончился; если скрытую страницу открыли раньше, она
    докрашивается перед показом (ensure)."""

    _root: tkinter.Misc | None = None
    _pending: list = []
    _job = None
    _timer = False  # точный таймер включён на время фоновой докраски
    BUDGET = 0.012  # сек на порцию фоновой перекраски — меньше кадра
    FADE = 0.22  # сек на переход (0 — без перехода)

    @classmethod
    def install(cls, root: tkinter.Misc) -> None:
        from customtkinter.windows.widgets.appearance_mode.appearance_mode_tracker import AppearanceModeTracker

        cls._root = root
        AppearanceModeTracker.update_callbacks = classmethod(lambda tracker: cls._switch(tracker))
        transition._idle.append(cls._schedule)  # скрытое докрашиваем, когда переход закончился

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
        cls._pending = hidden
        if alive:
            with crossfade(root, cls.FADE) if cls.FADE > 0 else nullcontext():
                with frozen(root, flush=True):  # под снимком: всё видимое перекрашивается и дорисовывается
                    for callback in visible:
                        cls._call(callback, mode)
        else:
            for callback in visible:
                cls._call(callback, mode)
        cls._schedule()

    @staticmethod
    def _call(callback, mode: str) -> None:
        try:
            callback(mode)
        except Exception:  # как в customtkinter: один сломанный элемент не мешает остальным
            pass

    @classmethod
    def _schedule(cls) -> None:
        if cls._pending and cls._job is None and cls._root is not None and not transition.active():
            if not cls._timer:  # без точного таймера after(1) срабатывает раз в 15,6 мс — фон шёл бы втрое дольше
                cls._timer = True
                precise_timer.acquire()
            try:
                cls._job = cls._root.after(1, cls._drain)
                return
            except tkinter.TclError:
                cls._pending = []
        if not cls._pending and cls._timer:
            cls._timer = False
            precise_timer.release()

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


class QuietToplevel:
    """Для окон на ctk.CTkToplevel (сообщения, мини-окно, выбор из списка). customtkinter красит заголовок,
    пряча окно и показывая снова: при смене темы и через 10 мс после resizable() — то есть сразу после
    появления окна-сообщения. На экране окно появлялось, пропадало и появлялось опять, а мини-окно мигало
    при каждой смене темы. Видимому окну хватает перерисовать рамку; спрятанное — как в customtkinter."""

    def _windows_set_titlebar_color(self, color_mode: str) -> None:
        try:
            shown = bool(self.winfo_ismapped())
        except tkinter.TclError:  # окно уже закрыли
            return
        if shown:
            set_titlebar_dark(self, color_mode.lower() == "dark")
        else:
            super()._windows_set_titlebar_color(color_mode)


# ───────────────────── Ctrl+C / V / X / A / Z в любой раскладке, отмена в полях ввода ─────────────────────

# В русской раскладке Tk не узнаёт букву под Ctrl (вместо «c» приходит «??»), и стандартные сочетания молчат.
# Узнаём клавишу по её коду: на Windows это виртуальный код клавиши, он от раскладки не зависит.
_KEYCODES = {67: "<<Copy>>", 86: "<<Paste>>", 88: "<<Cut>>", 65: "<<SelectAll>>", 90: "<<Undo>>", 89: "<<Redo>>"}
_CYRILLIC = {"cyrillic_es": "<<Copy>>", "cyrillic_em": "<<Paste>>", "cyrillic_che": "<<Cut>>",
             "cyrillic_ef": "<<SelectAll>>", "cyrillic_ya": "<<Undo>>", "cyrillic_en": "<<Redo>>"}  # Linux, macOS
# macOS, если keysym незнакомый: физические клавиши C, V, X, A, Z, Y
_MAC_KEYS = {8: "<<Copy>>", 9: "<<Paste>>", 7: "<<Cut>>", 0: "<<SelectAll>>", 6: "<<Undo>>", 16: "<<Redo>>"}
_SHORTCUT = "⌘" if sys.platform == "darwin" else "Ctrl+"


def shortcut_event(keysym: str, keycode: int, shift: bool, platform: str = sys.platform) -> str | None:
    """Какое действие вызвать вручную. None — ничего: стандартные сочетания Tk обрабатывает сам (латиница)."""
    low = keysym.lower()
    latin = len(keysym) == 1 and keysym.isascii()
    if platform == "win32":
        name = _KEYCODES.get(keycode)
    elif platform == "darwin":
        name = _CYRILLIC.get(low)
        if name is None and not latin:  # в keycode виртуальная клавиша: верхний байт, в старых Tk — третий
            name = _MAC_KEYS.get(keycode >> 24 or (keycode >> 16) & 0xFF)
    else:
        name = _CYRILLIC.get(low) or ("<<Undo>>" if low == "z" else None)
    if name == "<<Undo>>" and shift:
        return "<<Redo>>"  # Ctrl+Shift+Z — как в браузере и редакторах
    return None if latin else name


def install_editing(root: tkinter.Misc) -> None:
    """Для всех полей ввода приложения: сочетания в любой раскладке, Ctrl+Z / Ctrl+Y (и Ctrl+Shift+Z),
    меню правой кнопки «Отменить · Вырезать · Копировать · Вставить · Выделить всё». И для всего текста:
    выделение мышью и копирование — см. install_label_selection."""
    # На macOS сочетания идут с Command: по одному только Control их в другой раскладке никто не ловил, и в
    # русской раскладке не работали ни ⌘V, ни ⌘A, ни остальные. В английской работали — поэтому «иногда»
    root.bind_all("<Command-KeyPress>" if sys.platform == "darwin" else "<Control-KeyPress>", _layout_shortcut, add="+")
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
    # Tk отдаёт фокус по щелчку только изменяемому тексту: в окнах с текстом только для чтения
    # («Что получает нейросеть», новая версия) выделить было можно, а Ctrl+C уходил в другое поле
    root.bind_class("Text", "<Button-1>", lambda e: e.widget.focus_set(), add="+")
    for button in MENU_BUTTONS:
        root.bind_all(button, _edit_menu, add="+")
    install_label_selection(root)


def _layout_shortcut(event):
    widget = event.widget
    if not isinstance(widget, (tkinter.Entry, tkinter.Text)):
        return None
    name = shortcut_event(event.keysym or "", event.keycode, bool(event.state & 0x1))
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
    if not isinstance(widget, (tkinter.Entry, tkinter.Text)) or any(widget.bind(b) for b in MENU_BUTTONS):
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
        menu.add_command(label="Отменить", accelerator=f"{_SHORTCUT}Z", command=lambda: send("<<Undo>>"))
        menu.add_separator()
        menu.add_command(label="Вырезать", accelerator=f"{_SHORTCUT}X", command=lambda: send("<<Cut>>"),
                         state="normal" if has_selection else "disabled")
    menu.add_command(label="Копировать", accelerator=f"{_SHORTCUT}C", command=lambda: send("<<Copy>>"),
                     state="normal" if has_selection else "disabled")
    if editable:
        menu.add_command(label="Вставить", accelerator=f"{_SHORTCUT}V", command=lambda: send("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label="Выделить всё", accelerator=f"{_SHORTCUT}A", command=lambda: send("<<SelectAll>>"))
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()
    return "break"


# ───────────────────── выделение и копирование текста в подписях ─────────────────────

class _Selection:
    """Подпись, которую сейчас выделяют: текст поверх неё и точка, где нажали кнопку мыши."""

    box: tkinter.Text | None = None
    press: tuple | None = None  # (подпись, x, y)
    start: str = ""  # где в тексте начали выделять


def _is_text_label(widget) -> bool:
    """Подпись, а не часть кнопки или флажка: внутри них тоже tkinter.Label, но это функции, а не текст."""
    if isinstance(widget, TextLabel):
        return True
    return isinstance(widget, tkinter.Label) and isinstance(widget.master, ctk.CTkLabel)


def _selectable(widget) -> bool:
    """Подпись можно выделять мышью. Те, на которые нажимают (ссылки, строки, мини-окно), левую кнопку заняли."""
    return (_is_text_label(widget) and bool(str(widget.cget("text")).strip()) and not widget.bind("<Button-1>")
            and str(widget.cget("cursor")) != "hand2")


def install_label_selection(root: tkinter.Misc) -> None:
    """Любую подпись можно выделить мышью и скопировать: Ctrl+C или правая кнопка → «Копировать».

    Подпись — tkinter.Label: одно окно, и ширина по тексту, зато выделять в нём Tk не умеет. Менять сотни подписей
    на Text нельзя: страницы стали бы открываться заметно медленнее, а ширина «по тексту» потерялась бы. Поэтому Text
    с тем же шрифтом, цветом и расположением строк появляется поверх подписи, только когда её начали выделять.
    Кнопки, флажки, вкладки и варианты выбора не выделяются — это функции, а не текст."""
    root.bind_class("Label", "<Enter>", _label_enter, add="+")
    root.bind_class("Label", "<Button-1>", _label_press, add="+")
    root.bind_class("Label", "<B1-Motion>", _label_drag, add="+")
    root.bind_class("Label", "<ButtonRelease-1>", _label_release, add="+")
    root.bind_class("Label", "<Double-Button-1>", _label_double, add="+")
    for button in MENU_BUTTONS:
        root.bind_class("Label", button, _label_menu, add="+")
    root.bind_all("<Button-1>", _click_elsewhere, add="+")


def _label_enter(event) -> None:
    widget = event.widget
    if _selectable(widget) and not str(widget.cget("cursor")):
        tkinter.Label.configure(widget, cursor="xterm")


def _label_press(event) -> None:
    _Selection.press = (event.widget, event.x, event.y) if _selectable(event.widget) else None


def _label_drag(event) -> None:
    press = _Selection.press
    if press is None or press[0] is not event.widget:
        return
    box = _Selection.box
    if box is None or box.master is not event.widget:
        if abs(event.x - press[1]) + abs(event.y - press[2]) < 3:
            return  # простой щелчок: окно поверх подписи не создаём
        box = _open_box(event.widget)
        _Selection.start = _closest_gap(box, press[1], press[2])
    _select(box, _Selection.start, _closest_gap(box, event.x, event.y))


def _label_release(event) -> None:
    _Selection.press = None
    box = _Selection.box
    if box is not None and box.master is event.widget and not box.tag_ranges("sel"):
        _close_box()


def _label_double(event) -> None:
    """Двойной щелчок — слово."""
    if _selectable(event.widget):
        box = _open_box(event.widget)
        at = f"@{event.x},{event.y}"
        box.tag_add("sel", f"{at} wordstart", f"{at} wordend")
    _Selection.press = None


def _label_menu(event):
    label = event.widget
    if not _is_text_label(label) or not str(label.cget("text")).strip():
        return None
    text = str(label.cget("text"))
    menu = tkinter.Menu(label, tearoff=0)
    menu.add_command(label="Копировать", command=lambda: _copy(label, text))
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()
    return "break"


def _click_elsewhere(event) -> None:
    box = _Selection.box
    if box is not None and event.widget is not box and event.widget is not box.master:
        _close_box()


def _close_box(_event=None) -> None:
    box, _Selection.box = _Selection.box, None
    if box is not None:
        box.destroy()


def _closest_gap(box: tkinter.Text, x: int, y: int) -> str:
    """Промежуток между буквами под указателем. Ушли выше или ниже подписи — до начала или конца текста."""
    if y < 0:
        return "1.0"
    if y >= box.winfo_height():
        return "end-1c"
    return str(box.tk.call("tk::TextClosestGap", box, x, y))


def _select(box: tkinter.Text, start: str, end: str) -> None:
    box.tag_remove("sel", "1.0", "end")
    box.tag_add("sel", *((end, start) if box.compare(start, ">", end) else (start, end)))


def _open_box(label: tkinter.Label) -> tkinter.Text:
    """Text поверх подписи: те же шрифт, цвета и положение строк, что у tkinter.Label."""
    _close_box()
    text, font = str(label.cget("text")), label.cget("font")
    width, height = label.winfo_width(), label.winfo_height()
    wrap = int(str(label.cget("wraplength")) or 0)
    dark = 1 if ctk.get_appearance_mode() == "Dark" else 0
    box = tkinter.Text(label, bd=0, highlightthickness=0, relief="flat", padx=0, pady=0, width=1, height=1, font=font,
                       wrap="word" if wrap else "none", insertwidth=0, takefocus=0, cursor="xterm",
                       fg=label.cget("foreground"), bg=label.cget("background"), selectbackground=ACCENT_SOFT[dark],
                       selectforeground=TEXT[dark], inactiveselectbackground=ACCENT_SOFT[dark])
    measure = (int(box.tk.call("font", "measure", font, line)) for line in text.split("\n"))
    block = min(max(measure), wrap) if wrap else max(measure)
    free = max(0, width - block)
    anchor = str(label.cget("anchor"))
    left = 0 if "w" in anchor else free if "e" in anchor else free // 2
    # Label переносит слово, если вместе с пробелом за ним оно не влезает в wraplength, а Text пробелу разрешает
    # вылезать: с запасом на пробел строки ломаются там же, где в подписи
    space = int(box.tk.call("font", "measure", font, " ")) if wrap and block >= wrap else 0
    box.tag_configure("line", justify=str(label.cget("justify")), lmargin1=left, lmargin2=left,
                      rmargin=max(0, width - left - block + space))
    box.insert("1.0", text, "line")
    box.place(x=0, y=0, relwidth=1, relheight=1)
    box.update_idletasks()
    counted = box.count("1.0", "end-1c", "update", "displaylines")
    lines = (counted[0] if isinstance(counted, tuple) else counted or 0) + 1
    linespace = int(box.tk.call("font", "metrics", font, "-linespace"))
    if "n" not in anchor:
        box.configure(pady=max(0, (height - lines * linespace) // 2))
    box.configure(state="disabled")
    for wheel in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        box.bind(wheel, lambda e, widget=box: _wheel_to_page(widget, e))
    box.bind("<Escape>", _close_box)
    box.focus_set()
    _Selection.box = box
    _watch_box(box, label, text)
    return box


def _watch_box(box: tkinter.Text, label: tkinter.Label, text: str) -> None:
    """Подпись сменила текст (счётчик, состояние) или её больше не видно — поверх неё не должно остаться старого."""
    if _Selection.box is not box:
        return
    try:
        current = label.winfo_viewable() and str(label.cget("text")) == text
    except tkinter.TclError:
        current = False
    if current:
        box.after(400, _watch_box, box, label, text)
    else:
        _close_box()


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

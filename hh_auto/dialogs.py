"""Свои окна сообщений и вопросов в стиле приложения — вместо стандартных окон Windows (tkinter.messagebox).

showinfo / showwarning / showerror / askyesno принимают те же (заголовок, текст), что и tkinter.messagebox,
поэтому gui.py пользуется ими как раньше. Заголовок окна — всегда имя программы, а крупная строка внутри
берётся из текста: первый абзац, начало до двоеточия или первое предложение. Можно задать и явно: title=…
"""

from __future__ import annotations

import re
import tkinter

import customtkinter as ctk

from .theme import (ACCENT, ACCENT_HOVER, ACCENT_SOFT, APP_NAME, BORDER, CARD_BG, GREEN, GREEN_HOVER, MUTED, RED,
                    RED_HOVER, TEXT, apply_app_icon, badge)
from .widgets import SelectableText

_DEFAULT_TITLE = {"info": APP_NAME, "success": "Готово", "warning": "Обратите внимание",
                  "error": "Что-то пошло не так", "question": "Подтвердите"}


def _parent(parent: tkinter.Misc | None) -> tkinter.Misc | None:
    return parent if parent is not None else getattr(tkinter, "_default_root", None)


def _split(message: str) -> tuple[str, str]:
    """Крупная строка + пояснение из одного текста сообщения (пояснение — с заглавной буквы)."""
    head, text = _split_raw(message.strip())
    return head, text[:1].upper() + text[1:]


def _split_raw(message: str) -> tuple[str, str]:
    first, _, rest = message.partition("\n\n")
    if rest and len(first) <= 90:
        return first.rstrip(":"), rest.strip()
    head, sep, tail = message.partition(": ")  # «Не сохранено: …» → «Не сохранено» + причина
    if sep and len(head) <= 60 and "\n" not in head:
        return head, tail
    if len(message) <= 90 and "\n" not in message:
        return message, ""
    sentence = re.match(r"(.{10,90}?[.!?])\s+(.+)", message, re.S)
    if sentence:
        return sentence[1], sentence[2]
    return "", message


class Dialog(ctk.CTkToplevel):
    """Модальное окно: значок, крупная строка, текст, (прокручиваемые подробности), поле ввода, кнопки.

    buttons — [(подпись, значение, стиль)], стиль: primary | secondary | danger | success. Enter — первая
    кнопка, Escape — cancel."""

    def __init__(self, parent, *, kind: str = "info", title: str = "", message: str = "", details: str = "",
                 buttons=(("Понятно", True, "primary"),), cancel=None, entry: str | None = None,
                 placeholder: str = "", width: int = 470, link: tuple[str, str] | None = None):
        parent = _parent(parent)
        super().__init__(parent)
        self.withdraw()  # собираем невидимым, показываем уже по центру — без мигания
        self.title(APP_NAME)
        self.resizable(False, False)
        self.configure(fg_color=CARD_BG)
        apply_app_icon(self)
        self.result = cancel
        self._cancel = cancel

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=26, pady=(24, 0))
        head = ctk.CTkFrame(body, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text="", image=badge(kind, 44)).pack(side="left", anchor="n", padx=(0, 16))
        column = ctk.CTkFrame(head, fg_color="transparent")
        column.pack(side="left", fill="x", expand=True)
        wrap = width - 26 * 2 - 60
        if title:
            ctk.CTkLabel(column, text=title, font=ctk.CTkFont(size=17, weight="bold"), text_color=TEXT,
                         anchor="w", justify="left", wraplength=wrap).pack(fill="x", pady=(2, 0))
        if message:  # текст сообщения можно выделить и скопировать (например, текст ошибки)
            SelectableText(column, message, color=MUTED if title else TEXT, size=13,
                           width_px=wrap).pack(fill="x", pady=(6 if title else 2, 0))
        if link:
            text, command = link
            label = ctk.CTkLabel(column, text=text, text_color=ACCENT, cursor="hand2", anchor="w",
                                 font=ctk.CTkFont(size=13, underline=True))
            label.pack(fill="x", pady=(6, 0))
            label.bind("<Button-1>", lambda _e: command())

        if details:
            box = ctk.CTkTextbox(body, height=230, wrap="word", fg_color=("#F6F7FB", "#1A1C21"), border_width=1,
                                 border_color=BORDER, corner_radius=10, font=ctk.CTkFont(size=13))
            box.pack(fill="both", expand=True, pady=(16, 0))
            render_markdown(box, details)

        self.entry = None
        if entry is not None:
            self.entry = ctk.CTkEntry(body, height=38, placeholder_text=placeholder, font=ctk.CTkFont(size=13))
            self.entry.pack(fill="x", pady=(16, 0))
            if entry:
                self.entry.insert(0, entry)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=26, pady=(22, 22))
        self._buttons = []
        for i, (label, value, style) in enumerate(buttons):
            button = _button(row, label, style, lambda v=value: self._choose(v))
            button.pack(side="right", padx=(10 if i else 0, 0))
            self._buttons.append((button, value))

        self.bind("<Return>", lambda _e: self._choose(self._buttons[0][1]))
        self.bind("<KP_Enter>", lambda _e: self._choose(self._buttons[0][1]))
        self.bind("<Escape>", lambda _e: self._choose(self._cancel))
        self.protocol("WM_DELETE_WINDOW", lambda: self._choose(self._cancel))
        self._show(parent, width)

    def _show(self, parent, width: int) -> None:
        self.update_idletasks()
        scaling = ctk.ScalingTracker.get_window_scaling(self)
        w = max(round(width * scaling), self.winfo_reqwidth())
        h = self.winfo_reqheight()
        if parent is not None and parent.winfo_viewable():
            top = parent.winfo_toplevel()
            x = top.winfo_rootx() + (top.winfo_width() - w) // 2
            y = top.winfo_rooty() + max((top.winfo_height() - h) // 3, 20)
        else:
            x = (self.winfo_screenwidth() - w) // 2
            y = (self.winfo_screenheight() - h) // 3
        tkinter.Toplevel.geometry(self, f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")  # без пересчёта customtkinter
        if parent is not None and parent.winfo_viewable():
            self.transient(parent.winfo_toplevel())
        self.deiconify()
        self.lift()
        self.after(10, self._focus)
        self.after(40, self._settle)
        try:
            self.grab_set()
        except tkinter.TclError:  # окно ещё не показано — повторим чуть позже
            self.after(50, lambda: self.winfo_exists() and self.grab_set())

    def _settle(self) -> None:
        """На экране текст перенёсся по настоящей ширине — если строк вышло больше или меньше оценки,
        подгоняем высоту окна (верх остаётся на месте)."""
        if not self.winfo_exists():
            return
        self.update_idletasks()
        h = self.winfo_reqheight()
        if abs(h - self.winfo_height()) > 2:
            position = tkinter.Toplevel.geometry(self).split("+", 1)[1]
            tkinter.Toplevel.geometry(self, f"{self.winfo_width()}x{h}+{position}")

    def _focus(self) -> None:
        if self.winfo_exists():
            self.focus_force()
            if self.entry is not None:
                self.entry.focus_set()

    def _choose(self, value) -> None:
        if self.entry is not None and value is not self._cancel:
            value = self.entry.get().strip() if value is True else value
        self.result = value
        try:
            self.grab_release()
        except tkinter.TclError:
            pass
        self.destroy()

    def run(self):
        self.wait_window()
        return self.result


def _button(master, text: str, style: str, command) -> ctk.CTkButton:
    font = ctk.CTkFont(size=13, weight="bold" if style != "secondary" else "normal")
    if style == "secondary":
        return ctk.CTkButton(master, text=text, command=command, height=36, corner_radius=9, fg_color="transparent",
                             border_width=1, border_color=BORDER, text_color=TEXT, hover_color=ACCENT_SOFT, font=font)
    colors = {"primary": (ACCENT, ACCENT_HOVER), "danger": (RED, RED_HOVER), "success": (GREEN, GREEN_HOVER)}[style]
    return ctk.CTkButton(master, text=text, command=command, height=36, corner_radius=9, fg_color=colors[0],
                         hover_color=colors[1], text_color="#FFFFFF", font=font)


def render_markdown(box: ctk.CTkTextbox, text: str) -> None:
    """Описание релиза (markdown с GitHub) — заголовки жирным, списки с отступом, без звёздочек и решёток."""
    t = box._textbox  # у CTkTextbox нельзя задать шрифт тегу — настраиваем сам tk.Text
    scaling = ctk.ScalingTracker.get_widget_scaling(box)
    family = ctk.CTkFont(size=13).cget("family")
    t.tag_configure("h", font=(family, -round(15 * scaling), "bold"), spacing1=round(8 * scaling),
                    spacing3=round(4 * scaling))
    t.tag_configure("li", lmargin1=round(6 * scaling), lmargin2=round(20 * scaling), spacing3=round(3 * scaling))
    t.tag_configure("p", spacing3=round(5 * scaling))
    box.configure(state="normal")
    box.delete("1.0", "end")
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", raw)  # [текст](ссылка) → текст
        line = line.replace("**", "").replace("__", "").replace("`", "")
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue
        if stripped.startswith("#"):
            box.insert("end", stripped.lstrip("#").strip() + "\n", "h")
        elif re.match(r"^\s*[-*]\s+", line):
            indent = "    " if len(line) - len(line.lstrip()) >= 2 else ""
            item = re.sub(r"^\s*[-*]\s+", "", line)
            box.insert("end", f"{indent}•  {item}\n", "li")
        elif line.startswith("  ") and box.get("end-2c", "end-1c") == "\n":  # продолжение пункта списка
            box.delete("end-2c", "end-1c")
            box.insert("end", f" {stripped}\n", "li")
        else:
            box.insert("end", stripped + "\n", "p")
    box.configure(state="disabled")


# ───────────────────── замена tkinter.messagebox: те же имена и аргументы ─────────────────────

def _message(kind: str, title_or_app: str, message: str, title: str = "", buttons=None, parent=None, **kwargs):
    head, text = (title, message) if title else _split(message)
    if not head:
        head = _DEFAULT_TITLE[kind]
    buttons = buttons or (("Понятно", True, "primary"),)
    return Dialog(parent, kind=kind, title=head, message=text, buttons=buttons, cancel=True, **kwargs).run()


def showinfo(title_or_app: str = APP_NAME, message: str = "", **kwargs) -> None:
    _message(kwargs.pop("kind", "info"), title_or_app, message, **kwargs)


def showwarning(title_or_app: str = APP_NAME, message: str = "", **kwargs) -> None:
    _message("warning", title_or_app, message, **kwargs)


def showerror(title_or_app: str = APP_NAME, message: str = "", **kwargs) -> None:
    _message("error", title_or_app, message, buttons=(("Закрыть", True, "primary"),), **kwargs)


def askyesno(title_or_app: str = APP_NAME, message: str = "", yes: str = "Да", no: str = "Нет",
             danger: bool = False, kind: str = "question", **kwargs) -> bool:
    """Вопрос «да / нет». danger — красная кнопка для необратимого (удалить, выйти из аккаунта)."""
    buttons = ((yes, True, "danger" if danger else "primary"), (no, False, "secondary"))
    head, text = (kwargs.pop("title"), message) if kwargs.get("title") else _split(message)
    return bool(Dialog(kwargs.pop("parent", None), kind=kind, title=head or _DEFAULT_TITLE["question"],
                       message=text, buttons=buttons, cancel=False, **kwargs).run())


def whats_new(notes: str) -> str:
    """Из описания релиза — только про эту версию: до «Что было в …» или разделителя ---."""
    cut = re.search(r"^\s*(?:#+\s*Что было|---)", notes, re.M)
    notes = (notes[:cut.start()] if cut else notes).strip()
    return re.sub(r"\A#\s+[^\n]*\n+", "", notes)  # «# HH-Auto-Response 1.0.5-beta» — уже в заголовке окна


def ask_update(version: str, current: str, notes: str, size_mb: float, page_url: str, parent=None) -> bool:
    """Окно «вышла новая версия»: что нового (прокручиваемый список), ссылка на релиз, «Скачать и установить»."""
    import webbrowser

    size = f", архив {size_mb:.0f} МБ" if size_mb else ""
    return bool(Dialog(
        parent, kind="update", title=f"Вышла версия {version}",
        message=f"У вас {current}{size}. Программа скачает новую версию, проверит её и перезапустится. "
                "Настройки, логи и вход в hh останутся.",
        details=whats_new(notes) or "Подробности — на странице релиза.",
        link=("Открыть страницу релиза на GitHub", lambda: webbrowser.open(page_url)),
        buttons=(("Скачать и установить", True, "success"), ("Позже", False, "secondary")),
        cancel=False, width=620).run())


def askstring(title: str, message: str, placeholder: str = "", initial: str = "", ok: str = "Готово",
              parent=None) -> str | None:
    """Строка от пользователя (например, ссылка hh). None — передумал."""
    result = Dialog(parent, kind="question", title=title, message=message, entry=initial, placeholder=placeholder,
                    buttons=((ok, True, "primary"), ("Отмена", None, "secondary")), cancel=None, width=560).run()
    return result or None

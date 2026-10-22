"""Оконное приложение HH-Auto-Response: отклики, отказы в чатах, настройки и журналы — без консоли.

Работа с hh идёт в отдельном потоке (Playwright), окно только показывает лог и счётчики.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
import tkinter

import customtkinter as ctk

from . import __version__, control
from .app import EXIT_OK, EXIT_STOPPED, fetch_resume, run_chats, run_responses
from .chats import ChatStats
from . import hh_filters as hhf
from .hh_dicts import INDUSTRIES, INDUSTRY_NAMES, PROFESSIONAL_ROLE_CATEGORIES, PROFESSIONAL_ROLES
from .config import (AI_PROVIDERS, AREAS, WEEKDAYS, Config, ConfigError, config_values, load_config, save_config,
                     validate_config, validate_search_source)
from .ai import AiClient, AiError, shutdown_ollama
from .ai_playground import ROBOT, SAMPLE_FORM, SAMPLE_VACANCY, SAMPLES, Playground, fill_sample_form, write_sample_letter
from .results import HH_DAILY_LIMIT, Summary, load_summary
from .search import describe_search
from .stats import METRICS, SKIP_KINDS, StatsStore, buckets, totals
from .logger import SUCCESS, log, setup_logging
from .paths import app_root, bundled, is_frozen
from .runner import RunOptions, Stats
from . import browser, browser_window, updater
from .mini import BrowserPreview, MiniWindow
from . import dialogs as messagebox  # свои окна в стиле приложения вместо стандартных окон Windows
from .widgets import (Chart, ChipGroup, ItemList, crossfade, PlainFrame, SelectableText, TextLabel, flush_paint, frozen, install_editing,
                      precise_timer, set_titlebar_dark, theme_switch)
from .theme import (ACCENT, ACCENT_HOVER, ACCENT_SOFT, AMBER, APP_NAME, BORDER, CARD_BG, GREEN, GREEN_HOVER, MAIN_BG,
                    MUTED, RED, RED_HOVER, SIDEBAR_BG, TEXT, apply_app_icon, icon, sharp_windows_icon)

APP_VERSION = __version__
GITHUB_URL = "https://github.com/BlazeStudio/hh-auto-response"

LOG_COLORS = {  # тег → (светлая, тёмная)
    "debug": ("#9CA3AF", "#6B7280"),
    "info": ("#1F2937", "#D1D5DB"),
    "ok": ("#15803D", "#4ADE80"),
    "warn": ("#B45309", "#FBBF24"),
    "error": ("#B91C1C", "#F87171"),
}
LOG_MAX_LINES = 4000
APPEARANCE = {"Как в системе": "system", "Светлая": "light", "Тёмная": "dark"}


def _level_tag(levelno: int) -> str:
    if levelno >= logging.ERROR:
        return "error"
    if levelno >= logging.WARNING:
        return "warn"
    if levelno >= SUCCESS:
        return "ok"
    if levelno >= logging.INFO:
        return "info"
    return "debug"


def _line_tag(line: str) -> str:
    """Уровень строки из файла лога «дата | УРОВЕНЬ | текст»."""
    parts = line.split("|", 2)
    level = parts[1].strip() if len(parts) == 3 else ""
    return {"DEBUG": "debug", "OK": "ok", "WARNING": "warn", "ERROR": "error", "CRITICAL": "error"}.get(level, "info")


def open_path(path: Path) -> None:
    """Открыть файл или папку программой по умолчанию."""
    if not path.exists():
        messagebox.showinfo(APP_NAME, f"Пока нет: {path}")
        return
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 — открываем свой файл/папку
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class QueueLogHandler(logging.Handler):
    """Передаёт записи лога из рабочего потока в окно."""

    def __init__(self, events: queue.Queue):
        super().__init__(logging.DEBUG)
        self.events = events
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.events.put(("log", record.levelno, self.format(record)))
        except Exception:
            self.handleError(record)


# ─────────────────────────────── виджеты ───────────────────────────────


class Card(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=CARD_BG, corner_radius=14, border_width=1, border_color=BORDER, **kwargs)


class ScrollFrame(ctk.CTkScrollableFrame):
    """Плавная прокрутка, как в браузере, без «плывущего» и «размножившегося» текста и без застревания колеса.

    У CTkScrollableFrame щелчок колеса — рывок на 20 пикселей сразу, а Windows копирует старую картинку на новое
    место и дорисовать открывшееся не успевает: страница ползёт рывками, строки остаются на экране по нескольку
    раз. Здесь щелчок задаёт цель (около 110 пикселей), и страница доезжает до неё за несколько кадров
    с замедлением в конце; каждый кадр дорисовывается до следующего. Ползунок полосы прокрутки двигает
    страницу сразу, но тоже не чаще раза в кадр. Колесо над многострочным полем customtkinter отдаёт самому
    полю, даже если листать в нём нечего, — тогда листаем страницу.
    """

    FRAME_MS = 8  # ~120 кадров в секунду (на время анимации включается точный таймер Windows)
    WHEEL_PX = 110  # один щелчок колеса, при масштабе 100%
    EASE = 0.3  # доля оставшегося пути за кадр: быстро в начале, мягко в конце
    PAINT = "blit"  # blit — сдвиг и дорисовка открывшегося; frozen — заморозка и перерисовка целиком

    def __init__(self, *args, **kwargs):
        self._target: float | None = None  # куда доехать (пиксели от верха содержимого)
        self._moveto: float | None = None  # куда перетащили ползунок (доля)
        self._anim = None
        super().__init__(*args, **kwargs)
        self._parent_canvas.configure(yscrollincrement=1)  # по пикселю на любой системе — для плавности
        self._scrollbar.configure(command=self._scrollbar_command)

    def _top(self) -> float:
        return self._parent_canvas.canvasy(0)

    def _max_top(self) -> float:
        bbox = self._parent_canvas.bbox("all")
        total = (bbox[3] - bbox[1]) if bbox else 0
        return max(0.0, total - self._parent_canvas.winfo_height())

    def _scroll_by(self, pixels: float) -> None:
        base = self._target if self._target is not None else self._top()
        self._target = min(max(base + pixels, 0.0), self._max_top())
        self._start()

    def _start(self) -> None:
        if self._anim is None:
            precise_timer.acquire()
            self._anim = self.after(1, self._frame)

    def _frame(self) -> None:
        self._anim = None
        started = time.perf_counter()
        canvas = self._parent_canvas
        try:
            if self._moveto is not None:  # ползунок — сразу туда, без плавности
                moveto, self._moveto, self._target = self._moveto, None, None
                self._paint(lambda: canvas.yview_moveto(moveto))
                precise_timer.release()
                return
            if self._target is None:
                precise_timer.release()
                return
            top = self._top()
            remaining = self._target - top
            if abs(remaining) < 1:
                self._target = None
                precise_timer.release()
                return
            step = remaining * self.EASE
            step = max(1.0, abs(step)) * (1 if remaining > 0 else -1)
            if abs(step) > abs(remaining):
                step = remaining
            self._paint(lambda: canvas.yview_scroll(int(round(step)), "units"))
            if self._top() == top:  # упёрлись в край
                self._target = None
                precise_timer.release()
                return
        except tkinter.TclError:
            precise_timer.release()
            return
        # следующий кадр — через FRAME_MS от начала этого, а не от конца перерисовки
        spent = (time.perf_counter() - started) * 1000
        self._anim = self.after(max(1, round(self.FRAME_MS - spent)), self._frame)

    def _paint(self, move) -> None:
        if self.PAINT == "frozen":
            with frozen(self._parent_canvas, flush=True):
                move()
        else:
            move()
            flush_paint(self._parent_canvas)

    def _scrollbar_command(self, *args) -> None:
        """Ползунок полосы прокрутки: тянут — страница идёт следом сразу; щелчок по полосе — плавно на экран."""
        if args and args[0] == "moveto":
            self._moveto = float(args[1])
            self._start()
        elif args and args[0] == "scroll":
            scale = ctk.ScalingTracker.get_widget_scaling(self)
            amount = int(args[1]) * (self._parent_canvas.winfo_height() * 0.9 if args[2] == "pages"
                                     else self.WHEEL_PX * scale)
            self._scroll_by(amount)

    def _mouse_wheel_all(self, event):
        if self._shift_pressed:  # горизонтальной прокрутки у нас нет — как в customtkinter
            return super()._mouse_wheel_all(event)
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        if sys.platform == "win32":
            pixels = -event.delta / 120 * self.WHEEL_PX * scale  # тачпад даёт дробные щелчки — тоже плавно
        elif sys.platform == "darwin":
            pixels = -event.delta * 8
        else:
            pixels = (-1 if event.num == 4 else 1) * self.WHEEL_PX * scale
        if not pixels or not self._wheel_is_ours(event.widget, pixels < 0):
            return
        self._scroll_by(pixels)

    def _wheel_is_ours(self, widget, up: bool) -> bool:
        """Колесо крутят над этой областью, и листать нужно её (а не поле с текстом внутри)."""
        while isinstance(widget, tkinter.Misc):
            if widget == self._parent_canvas:
                return True
            if isinstance(widget, ctk.CTkTextbox):
                first, last = widget._textbox.yview()
                if (up and first > 0.0) or (not up and last < 1.0):
                    return False  # в поле есть что листать — его листает сам Tk
            elif isinstance(widget, (ctk.CTkScrollbar, ctk.CTkSlider)):
                return False
            elif isinstance(widget, ctk.CTkScrollableFrame):
                return widget._parent_canvas == self._parent_canvas
            widget = widget.master
        return False



class StatCard(Card):
    def __init__(self, master, title: str, color):
        super().__init__(master)
        ctk.CTkLabel(self, text=title, text_color=MUTED, font=ctk.CTkFont(size=13)).pack(anchor="w", padx=16, pady=(12, 0))
        self.value = ctk.CTkLabel(self, text="0", text_color=color, font=ctk.CTkFont(size=30, weight="bold"))
        self.value.pack(anchor="w", padx=16, pady=(0, 12))

    def set(self, value) -> None:
        text = str(value)
        if self.value.cget("text") != text:
            self.value.configure(text=text)


class LogView(ctk.CTkTextbox):
    """Цветной журнал только для чтения."""

    def __init__(self, master, **kwargs):
        super().__init__(master, font=ctk.CTkFont(family="Consolas", size=12), wrap="word", fg_color=CARD_BG,
                         border_width=1, border_color=BORDER, corner_radius=12, **kwargs)
        self.apply_colors()
        self.configure(state="disabled")
        self._menu = tkinter.Menu(self, tearoff=0)
        self._menu.add_command(label="Копировать", command=self.copy_selection)
        self._menu.add_command(label="Копировать всё", command=self.copy_all)
        self._menu.add_separator()
        self._menu.add_command(label="Выделить всё", command=self.select_all)
        text = self._textbox
        for button in ("<Button-3>", "<Button-2>") if sys.platform == "darwin" else ("<Button-3>",):
            text.bind(button, self._popup)
        text.bind("<Command-KeyPress>" if sys.platform == "darwin" else "<Control-KeyPress>", self._on_ctrl)
        text.bind("<Button-1>", lambda _e: text.focus_set(), add="+")  # иначе Ctrl+C уйдёт в другое поле

    def _popup(self, event) -> None:
        self._menu.tk_popup(event.x_root, event.y_root)

    def _on_ctrl(self, event):
        # keycode 67/65 — клавиши C/A на Windows в любой раскладке (в русской keysym другой)
        key = event.keysym.lower()
        if key in ("c", "cyrillic_es") or event.keycode == 67:
            self.copy_selection()
            return "break"
        if key in ("a", "cyrillic_ef") or event.keycode == 65:
            self.select_all()
            return "break"
        return None

    def selected_text(self) -> str:
        try:
            return self._textbox.get("sel.first", "sel.last")
        except tkinter.TclError:
            return ""

    def copy_selection(self) -> None:
        self._to_clipboard(self.selected_text() or self.get("1.0", "end-1c"))

    def copy_all(self) -> None:
        self._to_clipboard(self.get("1.0", "end-1c"))

    def select_all(self) -> None:
        self._textbox.tag_add("sel", "1.0", "end-1c")

    def _to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)

    def apply_colors(self) -> None:
        dark = ctk.get_appearance_mode() == "Dark"
        for tag, (light_color, dark_color) in LOG_COLORS.items():
            self.tag_config(tag, foreground=dark_color if dark else light_color)

    def append(self, text: str, tag: str) -> None:
        at_bottom = self.yview()[1] > 0.98
        self.configure(state="normal")
        self.insert("end", text + "\n", tag)
        lines = int(self.index("end-1c").split(".")[0])
        if lines > LOG_MAX_LINES:
            self.delete("1.0", f"{lines - LOG_MAX_LINES}.0")
        self.configure(state="disabled")
        if at_bottom:
            self.see("end")

    def set_lines(self, lines: list[str]) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        for line in lines:
            self.insert("end", line + "\n", _line_tag(line))
        self.configure(state="disabled")
        self.see("end")

    def clear(self) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.configure(state="disabled")


class Console(ctk.CTkFrame):
    """Журнал выполнения внизу страниц «Отклики» и «Чаты»."""

    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.records: list[tuple[int, str]] = []
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(header, text="Журнал выполнения", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        for text, command in (("Папка логов", self.open_folder), ("Файл лога", self.open_log), ("Очистить", self.clear),
                              ("Копировать", self.copy)):
            ctk.CTkButton(header, text=text, width=96, height=28, fg_color="transparent", border_width=1,
                          border_color=BORDER, text_color=TEXT, hover_color=ACCENT_SOFT,
                          command=command).pack(side="right", padx=(6, 0))
        self.verbose = ctk.CTkSwitch(header, text="Подробно", command=self.rerender)
        self.verbose.pack(side="right", padx=12)
        if app.prefs.get("verbose"):
            self.verbose.select()
        self.view = LogView(self)
        self.view.pack(fill="both", expand=True)

    def add(self, levelno: int, text: str) -> None:
        self.records.append((levelno, text))
        if len(self.records) > LOG_MAX_LINES * 2:
            del self.records[: LOG_MAX_LINES]
        if levelno >= logging.INFO or self.verbose.get():
            self.view.append(text, _level_tag(levelno))

    def rerender(self) -> None:
        self.app.save_prefs(verbose=bool(self.verbose.get()))
        show_debug = self.verbose.get()
        self.view.clear()
        for levelno, text in self.records[-LOG_MAX_LINES:]:
            if levelno >= logging.INFO or show_debug:
                self.view.append(text, _level_tag(levelno))

    def clear(self) -> None:
        self.records.clear()
        self.view.clear()

    def copy(self) -> None:
        """Выделенное — если есть, иначе весь журнал (то, что сейчас видно)."""
        self.view.copy_selection()
        self.app.toast("Скопировано в буфер обмена")

    def open_folder(self) -> None:
        open_path(self.app.root_dir / "logs")

    def open_log(self) -> None:
        if self.app.last_log_path:
            open_path(self.app.last_log_path)
        else:
            self.open_folder()


# ─────────────────────────────── страницы ───────────────────────────────


class Page(ctk.CTkFrame):
    def __init__(self, master, title: str, subtitle: str):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=24, weight="bold"), text_color=TEXT).pack(anchor="w")
        # выделяемый: в подзаголовке бывает путь (например, к config.toml) — его удобно скопировать
        self.subtitle = SelectableText(self, subtitle, color=MUTED, size=13, bg=MAIN_BG, width_px=860)
        self.subtitle.pack(fill="x", pady=(2, 14))


def _primary_button(master, text: str, command, **kwargs):
    kwargs.setdefault("height", 40)
    return ctk.CTkButton(master, text=text, command=command, corner_radius=10, fg_color=ACCENT,
                         hover_color=ACCENT_HOVER, font=ctk.CTkFont(size=14, weight="bold"), **kwargs)


def _secondary_button(master, text: str, command, **kwargs):
    kwargs.setdefault("height", 40)
    return ctk.CTkButton(master, text=text, command=command, corner_radius=10, fg_color="transparent",
                         border_width=1, border_color=BORDER, text_color=TEXT, hover_color=ACCENT_SOFT,
                         font=ctk.CTkFont(size=14), **kwargs)


def _stop_button(master, command):
    button = ctk.CTkButton(master, text="■  Остановить", command=command, height=40, width=150, corner_radius=10,
                           hover_color=RED_HOVER, font=ctk.CTkFont(size=14, weight="bold"))
    set_stop_enabled(button, False)
    return button


def _mini_button(master, app: "App") -> ctk.CTkButton:
    """«В уголок»: окно программы прячется, остаётся мини-окно в углу экрана (Ctrl+M)."""
    return _secondary_button(master, " В уголок", app.to_mini, width=130, image=icon("mini", 16, TEXT),
                             compound="left")


def set_stop_enabled(button: ctk.CTkButton, enabled: bool) -> None:
    button.configure(state="normal" if enabled else "disabled", fg_color=RED if enabled else BORDER,
                     text_color="white" if enabled else MUTED, text_color_disabled=MUTED)


class ResponsesPage(Page):
    def __init__(self, master, app: "App"):
        super().__init__(master, "Отклики на вакансии",
                         "Скрипт идёт по выдаче hh.ru и откликается с сопроводительным письмом (генерация hh, готовый текст или без письма — в «Настройках»). "
                         "При первом запуске откроется браузер — войдите в hh.ru сами, дальше всё автоматически.")
        self.app = app

        link_row = Card(self)
        link_row.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(link_row, text="Поиск:", text_color=MUTED).pack(side="left", padx=(16, 6), pady=10)
        self.search_label = ctk.CTkLabel(link_row, text="", text_color=TEXT, anchor="w")
        self.search_label.pack(side="left", fill="x", expand=True, pady=10)
        ctk.CTkButton(link_row, text="Изменить", width=90, height=28, fg_color="transparent", text_color=ACCENT,
                      hover_color=ACCENT_SOFT, command=lambda: app.show_page("settings")).pack(side="right", padx=10)

        totals = Card(self)
        totals.pack(fill="x", pady=(0, 12))
        self.totals = ctk.CTkLabel(totals, text="", text_color=TEXT, anchor="w")
        self.totals.pack(side="left", fill="x", expand=True, padx=16, pady=10)
        ctk.CTkButton(totals, text="Таблица Excel", width=120, height=28, fg_color="transparent", text_color=ACCENT,
                      hover_color=ACCENT_SOFT, command=lambda: open_path(app.xlsx_path)).pack(side="right", padx=10)

        cards = ctk.CTkFrame(self, fg_color="transparent")
        cards.pack(fill="x")
        self.cards = {
            "applied": StatCard(cards, "Отправлено за запуск", GREEN),
            "letters": StatCard(cards, "С письмом", ACCENT),
            "skipped": StatCard(cards, "Пропущено", AMBER),
            "failed": StatCard(cards, "Ошибок", RED),
        }
        for i, card in enumerate(self.cards.values()):
            cards.grid_columnconfigure(i, weight=1, uniform="cards")
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 10, 0))

        # прогресс: дорожка того же цвета, пока откликов нет, — без «точки» на нуле
        self.progress = ctk.CTkProgressBar(self, height=8, progress_color=GREEN)
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(14, 4))
        self.current = ctk.CTkLabel(self, text="Готов к работе", text_color=MUTED, anchor="w")
        self.current.pack(fill="x")

        options = Card(self)
        options.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(options, text="Откликов:", text_color=MUTED).pack(side="left", padx=(16, 6), pady=10)
        self.limit = ctk.CTkEntry(options, width=70, justify="center")
        self.limit.pack(side="left")
        ctk.CTkLabel(options, text="Со страницы:", text_color=MUTED).pack(side="left", padx=(20, 6))
        self.start_page = ctk.CTkEntry(options, width=56, justify="center")
        self.start_page.insert(0, "1")
        self.start_page.pack(side="left")
        self.retry = ctk.CTkCheckBox(options, text="Повторить пропущенные ранее",
                                     checkbox_width=20, checkbox_height=20)
        self.retry.pack(side="left", padx=(20, 16))

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.pack(fill="x", pady=(12, 0))
        self.start_btn = _primary_button(controls, "▶  Начать отклики", lambda: app.start_responses(False), width=190)
        self.start_btn.pack(side="left")
        self.dry_btn = _secondary_button(controls, "Пробный прогон", lambda: app.start_responses(True), width=150)
        self.dry_btn.pack(side="left", padx=10)
        self.stop_btn = _stop_button(controls, app.stop)
        self.stop_btn.pack(side="left")
        _mini_button(controls, app).pack(side="right")
        self._set_progress(0)

    def refresh_config(self, cfg: Config) -> None:
        try:
            validate_search_source(cfg)
            text, color = describe_search(cfg), TEXT
        except ConfigError:
            text, color = "не задан — откройте «Настройки»", RED
        self.search_label.configure(text=text if len(text) < 80 else text[:77] + "…", text_color=color)
        if not self.limit.get() or self.limit.cget("state") == "normal":
            self.limit.delete(0, "end")
            self.limit.insert(0, str(cfg.limits.max_responses))

    def update_totals(self, summary: Summary, running_applied: int = 0) -> None:
        """Сегодня / последний запуск / всего. Во время работы добавляем отклики текущего запуска."""
        today = summary.today.applied + running_applied
        last = running_applied if running_applied or not summary.last_run_at else summary.last_run.applied
        text = (f"Сегодня: {today} из ~{HH_DAILY_LIMIT}     •     Последний запуск: {last}     •     "
                f"Всего: {summary.total.applied + running_applied}")
        if self.totals.cget("text") != text:
            self.totals.configure(text=text, text_color=AMBER if today >= HH_DAILY_LIMIT * 0.9 else TEXT)

    def set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in (self.start_btn, self.dry_btn, self.limit, self.start_page, self.retry):
            widget.configure(state=state)
        set_stop_enabled(self.stop_btn, running)

    def update_stats(self, stats: Stats, limit: int) -> None:
        self.cards["applied"].set(stats.applied)
        self.cards["letters"].set(stats.with_letter)
        self.cards["skipped"].set(sum(stats.skipped.values()))
        self.cards["failed"].set(stats.failed)
        self._set_progress(min(stats.applied / limit, 1) if limit else 0)

    def _set_progress(self, value: float) -> None:
        if value == getattr(self, "_progress_value", None):  # то же значение — не перерисовываем (8 раз в секунду)
            return
        self._progress_value = value
        # на нуле скругление рисует зелёную точку — красим заполнение в цвет дорожки
        self.progress.configure(progress_color=GREEN if value > 0 else self.progress.cget("fg_color"))
        self.progress.set(value)


class ChatsPage(Page):
    def __init__(self, master, app: "App"):
        super().__init__(master, "Чаты",
                         "Включает в чатах hh «Только непрочитанные» и читает отказы. Если в «Настройках» включены "
                         "ИИ-ответы, отвечает «Роботу-рекрутеру» на анкеты по вашему резюме и читает уведомления. "
                         "Живым людям, приглашениям и просьбам о документах не отвечает — показывает их списком.")
        self.app = app
        cards = ctk.CTkFrame(self, fg_color="transparent")
        cards.pack(fill="x")
        self.cards = {
            "opened": StatCard(cards, "Прочитано отказов", GREEN),
            "answered": StatCard(cards, "Ответов ИИ", ACCENT),
            "other": StatCard(cards, "Ждут вашего ответа", AMBER),
            "failed": StatCard(cards, "Ошибок", RED),
        }
        for i, card in enumerate(self.cards.values()):
            cards.grid_columnconfigure(i, weight=1, uniform="cards")
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 10, 0))

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.pack(fill="x", pady=(16, 0))
        self.start_btn = _primary_button(controls, "✉  Обработать чаты", lambda: app.start_chats(False), width=200)
        self.start_btn.pack(side="left")
        self.dry_btn = _secondary_button(controls, "Только показать", lambda: app.start_chats(True), width=150)
        self.dry_btn.pack(side="left", padx=10)
        self.stop_btn = _stop_button(controls, app.stop)
        self.stop_btn.pack(side="left")
        _mini_button(controls, app).pack(side="right")
        self.ai_state = ctk.CTkLabel(self, text="", text_color=MUTED, anchor="w")
        self.ai_state.pack(fill="x", pady=(10, 0))

    def refresh_config(self, cfg: Config) -> None:
        if cfg.ai.enabled:
            model = cfg.ai.model or AI_PROVIDERS.get(cfg.ai.provider, ("", ""))[1]
            text = f"ИИ-ответы на анкеты: включены ({cfg.ai.provider}, {model})"
        else:
            text = "ИИ-ответы на анкеты выключены — только чтение отказов. Включить: «Настройки» → «ИИ-ответы в чатах»"
        self.ai_state.configure(text=text, text_color=GREEN if cfg.ai.enabled else MUTED)

    def set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.start_btn.configure(state=state)
        self.dry_btn.configure(state=state)
        set_stop_enabled(self.stop_btn, running)

    def update_stats(self, stats: ChatStats) -> None:
        self.cards["opened"].set(stats.opened)
        self.cards["answered"].set(stats.answered)
        self.cards["other"].set(len(stats.other_unread))
        self.cards["failed"].set(stats.failed)


class AiTestPage(Page):
    """Песочница ИИ: пишете от имени работодателя — нейросеть отвечает, в hh ничего не отправляется."""

    SENDERS = {"Робот-рекрутер": ROBOT, "Живой HR": "Анна, HR"}

    def __init__(self, master, app: "App"):
        super().__init__(master, "Проверка ИИ",
                         "Пишете от имени работодателя — нейросеть отвечает так, как ответила бы в чате hh, "
                         "с теми же страховками. В hh ничего не отправляется. Переписка копится, как в настоящем чате.")
        self.app = app
        self.pg: Playground | None = None
        self.busy = False

        info = Card(self)
        info.pack(fill="x")
        self.info = ctk.CTkLabel(info, text="", text_color=TEXT, anchor="w", justify="left")
        self.info.pack(fill="x", padx=16, pady=(10, 4))
        buttons = ctk.CTkFrame(info, fg_color="transparent")
        buttons.pack(fill="x", padx=12, pady=(0, 10))
        _secondary_button(buttons, "Что получает нейросеть", self.show_prompt, width=200, height=32).pack(side="left")
        self.fetch_btn = _secondary_button(buttons, "Загрузить резюме с hh", self.fetch_resume, width=190, height=32)
        self.fetch_btn.pack(side="left", padx=8)
        _secondary_button(buttons, "Новый диалог", self.new_dialog, width=130, height=32).pack(side="left", padx=(0, 8))
        _secondary_button(buttons, "Тест анкеты", self.test_form, width=130, height=32).pack(side="left")
        _secondary_button(buttons, "Тест письма", self.test_letter, width=130, height=32).pack(side="left", padx=8)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", pady=(10, 0))
        row = ctk.CTkFrame(bottom, fg_color="transparent")
        row.pack(fill="x")
        self.sender = ctk.CTkOptionMenu(row, values=list(self.SENDERS), width=160)
        self.sender.pack(side="left")
        self.entry = ctk.CTkEntry(row, placeholder_text="Сообщение работодателя, например: Какой у вас опыт с Python?",
                                  height=36)
        self.entry.pack(side="left", fill="x", expand=True, padx=8)
        self.entry.bind("<Return>", lambda _e: self.ask())
        self.ask_btn = _primary_button(row, "Спросить ИИ", self.ask, width=140, height=36)
        self.ask_btn.pack(side="left")
        samples = ctk.CTkFrame(bottom, fg_color="transparent")
        samples.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(samples, text="Примеры:", text_color=MUTED).pack(side="left")
        labels = [f"{'HR' if s != ROBOT else 'Робот'}: {t[:70]}{'…' if len(t) > 70 else ''}" for s, t in SAMPLES]
        self._samples = dict(zip(labels, SAMPLES))
        self.sample = ctk.CTkOptionMenu(samples, values=labels, command=self._use_sample, width=520,
                                        dynamic_resizing=False)
        self.sample.set("выберите готовое сообщение…")
        self.sample.pack(side="left", padx=8)
        self.state = ctk.CTkLabel(samples, text="", text_color=MUTED)
        self.state.pack(side="left", padx=8)

        self.view = LogView(self)
        self.view.pack(fill="both", expand=True, pady=(12, 0))

    # ---------- состояние ----------

    def refresh(self) -> None:
        cfg = self.app.cfg
        model = cfg.ai.model or AI_PROVIDERS.get(cfg.ai.provider, ("", ""))[1]
        resume_path = self.app.root_dir / "data" / "resume.txt"
        if resume_path.exists():
            when = datetime.fromtimestamp(resume_path.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
            resume = f"{len(resume_path.read_text(encoding='utf-8'))} симв., загружено {when}"
        else:
            resume = "не загружено — нажмите «Загрузить резюме с hh»"
        context = cfg.ai.context.strip()
        context = (context[:90] + "…" if len(context) > 90 else context) if context else "не заполнен («Настройки» → ИИ)"
        state = "включены" if cfg.ai.enabled else "выключены (здесь всё равно можно проверять)"
        self.info.configure(text=f"Нейросеть: {cfg.ai.provider}, {model}   •   ИИ-ответы в чатах: {state}\n"
                                 f"Резюме: {resume}\nВаш контекст: {context}")
        if not self.view.get("1.0", "end").strip():
            self.view.append("Напишите сообщение от имени работодателя или выберите пример ниже.", "debug")

    def invalidate(self) -> None:
        """Настройки или резюме поменялись — песочница пересоздастся при следующем вопросе."""
        self.pg = None

    def set_running(self, running: bool) -> None:
        self.fetch_btn.configure(state="disabled" if running else "normal")

    def _use_sample(self, label: str) -> None:
        sender, text = self._samples[label]
        self.sender.set("Робот-рекрутер" if sender == ROBOT else "Живой HR")
        self.entry.delete(0, "end")
        self.entry.insert(0, text)
        self.sample.set("выберите готовое сообщение…")

    # ---------- действия ----------

    def _run(self, action: str, job) -> None:
        """Запрос к нейросети в фоне; результат придёт в App._poll через очередь событий."""
        if self.busy:
            return
        self.busy = True
        self.ask_btn.configure(state="disabled")
        self.state.configure(text="нейросеть думает…", text_color=ACCENT)

        def work():
            try:
                if self.pg is None:
                    self.pg = Playground(self.app.read_config_file(check_search_url=False), self.app.root_dir)
                self.app.events.put(("ai_pg", action, job(self.pg)))
            except Exception as e:  # AiError и любые другие — показываем в переписке
                self.app.events.put(("ai_pg", "error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def ask(self) -> None:
        text = self.entry.get().strip()
        if not text or self.busy:
            return
        sender = self.SENDERS[self.sender.get()]
        self.entry.delete(0, "end")
        self.view.append("", "info")
        self.view.append(f"{sender}: {text}", "info")
        self._run("turn", lambda pg: pg.ask(sender, text))

    def show_prompt(self) -> None:
        self._run("prompt", lambda pg: pg.preview())

    def test_form(self) -> None:
        """Как ИИ заполнит пример анкеты при отклике: текст, один вариант, несколько вариантов."""
        self.view.append("", "info")
        self.view.append("Пример анкеты при отклике (ничего не отправляется):", "info")
        for n, q in enumerate(SAMPLE_FORM, 1):
            self.view.append(f"  {n}. {q.text}" + (f"  [{' | '.join(q.options)}]" if q.options else ""), "info")
        self._run("form", fill_sample_form)

    def test_letter(self) -> None:
        """Какое письмо ИИ напишет к примеру вакансии (режим «Генерировать нейросетью»)."""
        title, company, description = SAMPLE_VACANCY
        self.view.append("", "info")
        self.view.append(f"Пример вакансии: «{title}» — {company} (ничего не отправляется):", "info")
        self.view.append(description, "debug")
        self._run("letter", write_sample_letter)

    def new_dialog(self) -> None:
        if self.pg is not None:
            self.pg.reset()
        self.view.clear()
        self.view.append("Новый диалог.", "debug")

    def fetch_resume(self) -> None:
        cfg = self.app._config_for_run(need_search_url=False)
        if cfg is None:
            return
        self.view.append("Загружаю резюме с hh — откроется браузер, ход видно в «Журналах»…", "debug")
        self.app._start("resume", "Загрузка резюме", "resume",
                        lambda log_path: fetch_resume(cfg, self.app.root_dir, log_path))

    def on_result(self, action: str, payload) -> None:
        self.busy = False
        self.ask_btn.configure(state="normal")
        self.state.configure(text="")
        if action == "error":
            self.view.append(f"  ошибка: {payload}", "error")
        elif action == "prompt":
            PromptWindow(self, payload)
        elif action == "letter":
            text, seconds = payload
            self.view.append(f"  Письмо ИИ ({seconds:.1f} с, {len(text)} симв.):", "ok")
            self.view.append(text, "ok")
        elif action == "form":
            fill, seconds = payload
            if not fill.can_answer:
                self.view.append(f"  ИИ ({seconds:.1f} с): не стал бы отвечать — вакансия ушла бы вам: {fill.reason}", "warn")
            else:
                self.view.append(f"  ИИ заполнил анкету за {seconds:.1f} с:", "ok")
                for answer in fill.answers:
                    self.view.append(f"  {answer.question.text}", "info")
                    self.view.append(f"    → {answer.describe()}", "ok")
        else:
            turn = payload
            d = turn.decision
            tag = {"question": "ok", "info": "debug", "human": "warn", "wait": "debug"}[d.kind]
            self.view.append(f"  ИИ ({turn.seconds:.1f} с): {turn.action}", tag)
            if d.kind == "question":
                self.view.append(f"  Вы (ответ ИИ, НЕ отправлен): {d.reply}", "ok")
            elif d.reply:
                self.view.append(f"  черновик, который не был бы отправлен: {d.reply}", "debug")
            if turn.note:
                self.view.append(f"  страховка: {turn.note}", "warn")


class PromptWindow(ctk.CTkToplevel):
    """Отдельное окно: ровно то, что получает нейросеть."""

    def __init__(self, master, text: str):
        super().__init__(master)
        self.title("Что получает нейросеть")
        self.geometry("900x700")
        apply_app_icon(self)
        box = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        box.pack(fill="both", expand=True, padx=12, pady=12)
        box.insert("1.0", text)
        box.configure(state="disabled")
        self.after(100, self.lift)


# (раздел конфига, ключ, подпись, тип, подсказка). Раздел "" — верхний уровень.
SETTINGS = [
    ("Поиск и резюме", [
        ("search", "mode", "Где искать вакансии", "choice", ""),
        ("", "search_url", "Ссылка на поиск вакансий (режим «По ссылке»)", "str",
         "hh.ru → «Мои резюме» → «N подходящих вакансий» → скопируйте адрес из браузера"),
        ("search", "query", "Поисковые запросы (режим «По запросу»)", "text",
         "По одному на строку — за запуск пройдём все по очереди, лимит откликов общий, одна и та же вакансия "
         "дважды не попадётся. Как в строке поиска hh: python разработчик, backend OR бэкенд"),
        ("search", "area", "Регион (режим «По запросу»)", "choice", ""),
        ("search", "title_only", "Искать запрос только в названии вакансии", "bool", ""),
        ("", "resume_url", "Ссылка на резюме (необязательно)", "str",
         "Если пусто — ID резюме берётся из параметра resume= в ссылке поиска"),
        ("", "skip_on_resume_mismatch", "Пропускать вакансию, если hh подставил другое резюме", "bool", ""),
    ]),
    ("Лимиты и паузы", [
        ("limits", "max_responses", "Откликов за запуск", "int", "У hh лимит — около 200 откликов в сутки"),
        ("limits", "max_pages", "Страниц выдачи", "int", "На каждый поисковый запрос"),
        ("limits", "per_company", "Откликов в одну компанию, не больше", "int",
         "0 — без ограничения. У крупной компании бывает десяток похожих вакансий — откликнемся только на первые"),
        ("limits", "per_company_days", "…за последние, дней", "int", "Считаются отклики этой программы"),
        ("limits", "delay_min", "Пауза между вакансиями: от, сек", "float", ""),
        ("limits", "delay_max", "Пауза между вакансиями: до, сек", "float", ""),
    ]),
    ("Фильтры hh", [
        ("hh_filters", "order_by", "Сортировка", "choice", ""),
        ("hh_filters", "search_period", "Период публикации", "choice", ""),
        ("hh_filters", "work_schedule_by_days", "График работы", "multi", ""),
        ("hh_filters", "working_hours", "Рабочие часы в день", "multi", ""),
        ("hh_filters", "experience", "Опыт работы", "multi", ""),
        ("hh_filters", "work_format", "Формат работы", "multi", ""),
        ("hh_filters", "salary", "Уровень дохода от, ₽", "int", "0 — не важно"),
        ("hh_filters", "salary_mode", "Доход указан", "choice", ""),
        ("hh_filters", "salary_frequency", "Частота выплат", "multi", ""),
        ("hh_filters", "employment_form", "Тип занятости", "multi", ""),
        ("hh_filters", "accept_temporary", "Оформление по ГПХ или по совместительству", "bool", ""),
        ("hh_filters", "professional_role", "Специализации", "picker", ""),
        ("hh_filters", "employer_id", "Компании (фильтр hh)", "list",
         "Номера компаний или ссылки вида hh.ru/employer/1740 через запятую"),
        ("hh_filters", "industry", "Отрасль компании", "picker", ""),
        ("hh_filters", "excluded_text", "Слова-исключения", "str",
         "hh не покажет вакансии с этими словами. Пример: стажёр преподаватель"),
        ("hh_filters", "education", "Образование", "multi", ""),
        ("hh_filters", "label", "Другие параметры", "multi", ""),
        ("hh_filters", "inclusiveness_types", "Особенности здоровья", "multi", ""),
        ("hh_filters", "search_field", "Где искать слова запроса", "multi", "Для поиска «По запросу»"),
    ]),
    ("Фильтры по словам", [
        ("filters", "include_words", "Откликаться ТОЛЬКО на вакансии, где есть хотя бы одно слово", "list",
         "Через запятую, регистр не важен. Пусто — на все вакансии. Например: python, backend, django"),
        ("filters", "include_in_snippet", "Искать эти слова и в описании из карточки, не только в названии", "bool", ""),
        ("filters", "exclude_title_words", "Пропускать вакансии, в названии которых есть", "list",
         "Через запятую, регистр не важен. Например: преподаватель, куратор"),
        ("filters", "exclude_company_words", "Пропускать компании, в названии которых есть", "list",
         "Через запятую, регистр не важен. Например: сбер, тинькофф"),
        ("filters", "include_company_words", "Откликаться ТОЛЬКО в компании, в названии которых есть", "list",
         "Через запятую, регистр не важен. Пусто — в любые компании. Например: яндекс, тинькофф, ozon"),
    ]),
    ("Сопроводительное письмо", [
        ("letter", "mode", "Как писать письмо", "choice",
         "Кнопка hh требует подписку. Без подписки — «нейросетью» (настройки нейросети — в «ИИ-ответы в чатах»), "
         "«Готовый текст» или «Без письма». В режиме «кнопкой hh» без кнопки письмо пишет нейросеть, если ИИ включён"),
        ("letter", "template_text", "Готовый текст письма", "text",
         "Для режима «Готовый текст». {vacancy} и {company} заменятся на название вакансии и компанию"),
        ("letter", "generate_timeout", "Ждать генерацию письма, сек", "float", ""),
        ("letter", "ai_max_chars", "Длина письма нейросети, символов", "int", "Обычно 600–1200"),
        ("letter", "fallback_text", "Запасной текст, если генерация не сработала", "text",
         "Для режима «Генерировать». Пусто — такая вакансия пропускается"),
    ]),
    ("Вопросы работодателя", [
        ("questions", "answer_salary", "Отвечать, если вопрос один и он о зарплате", "bool", ""),
        ("questions", "salary_answer", "Ответ о зарплате", "str", "Например: Рассматриваю от 100 000 ₽ на руки"),
        ("questions", "max_ai_questions", "С ИИ: максимум вопросов в анкете", "int",
         "Если включены ИИ-ответы, анкеты (текст и варианты) заполняет нейросеть; анкеты длиннее — пропускаются"),
    ]),
    ("Вакансии в другом регионе", [
        ("relocation", "confirm_other_region", "Соглашаться на предупреждение hh о другом регионе", "bool", ""),
        ("relocation", "decline_if_not_remote", "Отказываться, если нет удалёнки (нужен переезд)", "bool", ""),
        ("relocation", "decline_keywords", "Слова о переезде в карточке → отказ", "list", ""),
    ]),
    ("ИИ-ответы в чатах", [
        ("ai", "enabled", "Отвечать на анкеты «Робота-рекрутера» с помощью нейросети", "bool",
         "Нейросеть получает ваше резюме с hh, контекст ниже и переписку и отвечает коротко от вашего имени"),
        ("ai", "provider", "Нейросеть", "choice", ""),
        ("ai", "api_key", "API-ключ", "secret", ""),
        ("ai", "model", "Модель (необязательно)", "str", "Пусто — модель по умолчанию для выбранной нейросети"),
        ("ai", "base_url", "Адрес API (для «Другой сервис» или Ollama на другом компьютере)", "str",
         "Ollama на другом компьютере: http://192.168.1.50:11434 (или http://localhost:11434 через SSH-туннель). "
         "Пусто — Ollama на этом компьютере. Подробно — docs/ollama.md"),
        ("ai", "context", "Что ещё знать о вас, кроме резюме", "text",
         "Зарплатные ожидания, формат работы, город, когда готовы выйти, готовность к переезду и т.п."),
        ("ai", "only_robot", "Отвечать только ботам hh (Робот-рекрутер, ИИ-помощник), живым людям — вы", "bool",
         "Выключено — отвечает всем. Встречи, собеседования и звонки в любом случае остаются вам"),
        ("ai", "max_answer_chars", "Максимальная длина ответа, символов", "int", ""),
        ("ai", "timeout", "Сколько ждать ответа нейросети, сек", "float",
         "Обычно 60. Ollama на процессоре без видеокарты или на медленном сервере — 180 и больше"),
    ]),
    ("ИИ-фильтр вакансий", [
        ("ai_filter", "enabled", "Оценивать каждую вакансию нейросетью перед откликом", "bool",
         "Нейросеть читает название и сниппет карточки и решает, подходит ли вакансия под ваше описание ниже. "
         "Использует ту же нейросеть, что и «ИИ-ответы в чатах». Если вакансия не подходит — отклика не будет"),
        ("ai_filter", "criteria", "Что вы ищете и чего не хотите (своими словами)", "text",
         "Например: «Ищу удалённую работу Python-разработчиком. Не хочу продажи, поддержку по телефону, 1С и вакансии "
         "в офисе в Москве». Чем конкретнее описание, тем точнее отбор"),
        ("ai_filter", "max_snippet_chars", "Сколько символов описания вакансии отправлять нейросети", "int",
         "Обычно 800. Меньше — экономит токены, но нейросеть видит меньше деталей карточки"),
        ("ai_filter", "skip_on_error", "Пропускать вакансию, если нейросеть не ответила", "bool",
         "Выключено — при сбое нейросети вакансия обрабатывается как обычно (безопаснее, без потери вакансий)"),
    ]),
    ("Расписание запусков", [
        ("schedule", "enabled", "Запускать отклики по расписанию", "bool",
         "Пока приложение открыто (можно свернуть) и компьютер не спит. Если в это время уже идёт работа — "
         "запустится сразу после неё. Лимит — «Откликов за запуск»"),
        ("schedule", "times", "Во сколько", "list", "Через запятую, по часам компьютера: 10:00, 15:30"),
        ("schedule", "days", "По каким дням", "multi", ""),
        ("schedule", "chats_after", "После откликов прочитать отказы в чатах", "bool",
         "И ответить ботам hh, если включены «ИИ-ответы в чатах»"),
    ]),
    ("Уведомления по итогам", [
        ("notify", "desktop", "Показывать уведомление на компьютере, когда запуск закончился", "bool",
         "Отправлено, пропущено, ошибки. Приложение спросит разрешение после первого запуска"),
        ("notify", "telegram", "Присылать итоги в Telegram", "bool",
         "Удобно, если программа работает на сервере. Нужен свой бот: @BotFather → /newbot"),
        ("notify", "telegram_token", "Токен бота", "secret", ""),
        ("notify", "telegram_chat_id", "Ваш chat id", "str",
         "Напишите боту что-нибудь и откройте api.telegram.org/bot<токен>/getUpdates — там будет chat.id"),
    ]),
    ("Статистика", [
        ("stats", "disabled", "Не вести статистику", "bool",
         "Вкладка «Статистика» хранит только числа по дням — без названий вакансий, компаний и текстов. "
         "Включено — новые запуски не записываются; собранное раньше удаляется на самой вкладке"),
        ("stats", "keep_days", "Хранить статистику, дней", "int", "Более старые дни удаляются сами. 0 — хранить всё"),
    ]),
    ("Логи и место на диске", [
        ("logs", "snapshots", "Сохранять скриншот и HTML страницы при ошибках", "bool",
         "По ним можно разобраться, если hh поменял вёрстку. Один снимок — 1–3 МБ (папка logs/snapshots). "
         "Удалить старые — «Журналы» → «Удалить снимки»"),
        ("logs", "keep_days", "Хранить снимки, дней", "int",
         "Более старые удаляются сами при запуске. 0 — хранить всё"),
    ]),
    ("Браузер и сеть", [
        ("browser", "channel", "Браузер", "choice", ""),
        ("browser", "hide_window", "Прятать окно браузера (Windows)", "bool",
         "Браузер работает за краем экрана и не мешает, а что он делает — видно в превью в меню слева. "
         "Для входа в hh и капчи окно покажется само; «Показать» — в любой момент. Действует со следующего запуска"),
        ("geo", "proxy", "Российский прокси (если вы не в РФ или с VPN)", "str",
         "Формат: http://user:pass@host:port. Пусто — прямое подключение"),
        ("geo", "check_ip", "Показывать страну IP перед стартом", "bool", ""),
        ("geo", "require_ru_ip", "Не запускаться с нероссийского IP", "bool", ""),
    ]),
]
# Вкладки настроек: на экране одна вкладка, а не все 13 разделов разом (на ленте было 915 окон Windows,
# и при прокрутке и переключении страниц они перерисовывались кусками)
PAGE_FADE = 0.14  # сек: плавный переход между страницами
TAB_FADE = 0.12  # и между вкладками настроек
SETTINGS_TABS = [
    ("Поиск", ("Поиск и резюме", "Фильтры по словам")),
    ("Фильтры hh", ("Фильтры hh",)),
    ("Отклики", ("Лимиты и паузы", "Сопроводительное письмо", "Вопросы работодателя", "Вакансии в другом регионе")),
    ("Нейросеть", ("ИИ-ответы в чатах", "ИИ-фильтр вакансий")),
    ("Расписание", ("Расписание запусков", "Уведомления по итогам")),
    ("Прочее", ("Логи и место на диске", "Статистика", "Браузер и сеть", "Данные приложения")),
]
# Варианты для полей с несколькими галочками: значение в config → подпись
MULTI = {("hh_filters", name): options for name, options in hhf.LIST_OPTIONS.items() if options is not None}
MULTI[("schedule", "days")] = WEEKDAYS
# Большие справочники hh — выбор в отдельном окне с поиском: (группы, id → название)
PICKERS = {
    ("hh_filters", "professional_role"): (PROFESSIONAL_ROLE_CATEGORIES, PROFESSIONAL_ROLES),
    ("hh_filters", "industry"): (
        [(name, [(iid, f"{name} — вся отрасль")] + subs) for iid, name, subs in INDUSTRIES], INDUSTRY_NAMES),
}

# Варианты для полей-списков выбора: подпись → значение в config
CHOICES = {
    ("hh_filters", "order_by"): {label: value for value, label in hhf.ORDER_BY.items()},
    ("hh_filters", "search_period"): {label: value for value, label in hhf.SEARCH_PERIOD.items()},
    ("hh_filters", "salary_mode"): {label: value for value, label in hhf.SALARY_MODE.items()},
    ("ai", "provider"): {
        "OpenRouter — есть бесплатные модели": "openrouter",
        "DeepSeek API — платно, но копейки": "deepseek",
        "Ollama — на вашем компьютере, бесплатно (qwen3:8b)": "ollama",
        "Другой сервис (OpenAI-совместимый)": "custom",
    },
    ("search", "mode"): {"По ссылке (подходящие к резюме)": "resume", "По запросу (общий поиск hh)": "query"},
    ("search", "area"): {label: code for code, label in AREAS.items()},
    ("browser", "channel"): {"Google Chrome": "chrome", "Microsoft Edge": "msedge"},
    ("letter", "mode"): {"Генерировать кнопкой hh (подписка)": "generate",
                         "Генерировать нейросетью (без подписки)": "ai", "Готовый текст": "template",
                         "Без письма": "none"},
}


class PickerField(ctk.CTkFrame):
    """Поле настроек для большого справочника hh: «Выбрать…» + краткая сводка выбранного."""

    def __init__(self, master, title: str, groups, names: dict[str, str]):
        super().__init__(master, fg_color="transparent")
        self.title, self.groups, self.names = title, groups, names
        self.values: list[str] = []
        _secondary_button(self, "Выбрать…", self._open, width=120, height=30).pack(side="left")
        self.summary = TextLabel(self, "", color=MUTED, wraplength=620)
        self.summary.pack(side="left", fill="x", expand=True, padx=10)

    def set(self, values) -> None:
        self.values = [str(v) for v in values or []]
        if not self.values:
            text = "Не выбрано — все (как на hh)"
        else:
            shown = ", ".join(self.names.get(v, v) for v in self.values[:3])
            text = f"Выбрано {len(self.values)}: {shown}" + ("…" if len(self.values) > 3 else "")
        self.summary.set(text, TEXT if self.values else MUTED)

    def get(self) -> list[str]:
        return list(self.values)

    def _open(self) -> None:
        PickerDialog(self, self.title, self.groups, self.values, self.set)


class PickerDialog(ctk.CTkToplevel):
    """Выбор из справочника hh: поиск по названию, группы, «все в группе», счётчик выбранного.

    Каждая группа — «чипы» на одном холсте: у отраслей 326 вариантов, и флажками это было ~1300 окон Windows,
    которые при прокрутке и поиске перерисовывались кусками."""

    def __init__(self, master, title: str, groups, selected: list[str], on_done):
        super().__init__(master, fg_color=MAIN_BG)
        self.title(title)
        self.geometry("820x700")
        self.transient(master.winfo_toplevel())
        apply_app_icon(self)
        self.on_done = on_done
        known = {item_id for _, items in groups for item_id, _ in items}
        self.order: list[str] = list(dict.fromkeys(i for _, items in groups for i, _ in items))  # порядок справочника
        self.selected: set[str] = {str(v) for v in selected if str(v) in known}
        self.extra = [v for v in selected if str(v) not in known]  # неизвестные id сохраняем как были

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(14, 8))
        self.search = ctk.CTkEntry(top, placeholder_text="Поиск по названию…", height=36)
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind("<KeyRelease>", lambda _e: self._schedule_filter())
        self.counter = TextLabel(top, "", color=MUTED, bg=MAIN_BG)
        self.counter.pack(side="left", padx=12)

        self.list = ScrollFrame(self, fg_color=CARD_BG, corner_radius=12)
        self.list.pack(fill="both", expand=True, padx=14)
        self.groups: list[tuple[PlainFrame, ChipGroup, list[tuple[str, str]]]] = []
        for group, items in groups:
            header = PlainFrame(self.list)
            TextLabel(header, group, size=14, weight="bold").pack(side="left")
            ids = [item_id for item_id, _ in items]
            for label, color, value in (("все", ACCENT, True), ("снять", MUTED, False)):
                link = TextLabel(header, label, color=color, size=12, cursor="hand2")
                link.pack(side="left", padx=(10, 0))
                link.bind("<Button-1>", lambda _e, ids=ids, value=value: self._mark(ids, value))
            chips = ChipGroup(self.list, items, selected=self.selected, on_change=self._count)
            self.groups.append((header, chips, list(items)))

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=14, pady=12)
        _primary_button(bottom, "Готово", self._done, width=130, height=36).pack(side="left")
        _secondary_button(bottom, "Снять всё", lambda: self._mark(self.order, False), width=120,
                          height=36).pack(side="left", padx=8)
        _secondary_button(bottom, "Отмена", self.destroy, width=110, height=36).pack(side="right")
        self._after = None
        self._filter()
        self._count()
        self.after(150, self.grab_set)

    def _schedule_filter(self) -> None:
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(200, self._filter)

    def _filter(self) -> None:
        query = self.search.get().strip().lower()
        with frozen(self):  # поиск перестраивает весь список — показываем уже готовый
            for header, chips, _ in self.groups:
                header.pack_forget()
                chips.pack_forget()
            for header, chips, items in self.groups:
                matched = [(item_id, name) for item_id, name in items if query in name.lower()]
                if matched:
                    header.pack(fill="x", padx=14, pady=(12, 6))
                    chips.set_options(matched)
                    chips.pack(fill="x", padx=14)

    def _mark(self, ids, value: bool) -> None:
        if value:
            self.selected.update(ids)
        else:
            self.selected.difference_update(ids)
        for _, chips, _ in self.groups:
            chips._render()
        self._count()

    def _count(self) -> None:
        chosen = len(self.selected)
        self.counter.set(f"выбрано {chosen}" if chosen else "ничего не выбрано — все")

    def _done(self) -> None:
        self.on_done([item_id for item_id in self.order if item_id in self.selected] + self.extra)
        self.destroy()


class SettingsPage(Page):
    def __init__(self, master, app: "App"):
        super().__init__(master, "Настройки", f"Сохраняются в {app.config_path}")
        self.app = app
        self.widgets: dict[tuple[str, str], tuple[str, object]] = {}
        self._loaded: dict | None = None  # что уже разложено по полям формы

        # вкладки — как пункты меню слева: выбранная подсвечена
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 10))
        self.tab_buttons: dict[str, ctk.CTkButton] = {}
        for name, _ in SETTINGS_TABS:
            button = ctk.CTkButton(bar, text=name, height=34, width=0, corner_radius=9, fg_color="transparent",
                                   text_color=TEXT, hover_color=ACCENT_SOFT, font=ctk.CTkFont(size=14),
                                   command=lambda n=name: self.show_tab(n))
            button.pack(side="left", padx=(0, 6))
            self.tab_buttons[name] = button
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)
        self.tabs = {name: ScrollFrame(body, fg_color="transparent") for name, _ in SETTINGS_TABS}
        tab_of = {section: name for name, sections in SETTINGS_TABS for section in sections}
        self.field_tab: dict[tuple[str, str], str] = {}

        for section_title, items in SETTINGS:
            card = Card(self.tabs[tab_of[section_title]])
            card.pack(fill="x", pady=(0, 12), padx=(0, 6))
            TextLabel(card, section_title, size=15, weight="bold").pack(anchor="w", padx=16, pady=(12, 4))
            if section_title == "Фильтры hh":
                SelectableText(card, "Пусто — фильтр не задан, на hh это «выбрано всё». Проще всего настроить "
                                     "фильтры на hh.ru, скопировать адрес страницы и нажать «Заполнить из ссылки».",
                               color=MUTED, size=12, width_px=760).pack(fill="x", padx=16)
                row = PlainFrame(card)
                row.pack(fill="x", padx=16, pady=(8, 8))
                _secondary_button(row, "Заполнить из ссылки hh…", self.import_hh_filters, width=220,
                                  height=32).pack(side="left")
                _secondary_button(row, "Сбросить фильтры hh", self.reset_hh_filters, width=190,
                                  height=32).pack(side="left", padx=8)
            for section, key, label, kind, hint in items:
                self._add_field(card, section, key, label, kind, hint)
                self.field_tab[(section, key)] = tab_of[section_title]
            if section_title == "ИИ-ответы в чатах":
                row = PlainFrame(card)
                row.pack(fill="x", padx=16, pady=(8, 0))
                self.ai_test_btn = _secondary_button(row, "Проверить подключение к ИИ", self.test_ai, width=240, height=32)
                self.ai_test_btn.pack(side="left")
                SelectableText(row, "Где взять ключ: OpenRouter — openrouter.ai → Keys; DeepSeek — "
                                    "platform.deepseek.com → API keys; Ollama — ключ не нужен", color=MUTED, size=12,
                               width_px=480).pack(side="left", fill="x", expand=True, padx=12)
            if section_title == "ИИ-фильтр вакансий":
                row = PlainFrame(card)
                row.pack(fill="x", padx=16, pady=(8, 0))
                self.filter_test_btn = _secondary_button(row, "Проверить фильтр на примере вакансии",
                                                         self.test_vacancy_filter, width=300, height=32)
                self.filter_test_btn.pack(side="left")
                SelectableText(row, "Отправит нейросети тестовую вакансию и покажет, что она решила",
                               color=MUTED, size=12, width_px=420).pack(side="left", fill="x", expand=True, padx=12)
            if section_title == "Уведомления по итогам":
                row = PlainFrame(card)
                row.pack(fill="x", padx=16, pady=(8, 0))
                self.notify_btn = _secondary_button(row, "Отправить тестовое уведомление", self.test_notify,
                                                    width=260, height=32)
                self.notify_btn.pack(side="left")
            PlainFrame(card, height=8).pack()

        danger = Card(self.tabs["Прочее"])
        danger.pack(fill="x", pady=(0, 12), padx=(0, 6))
        TextLabel(danger, "Данные приложения", size=15, weight="bold").pack(anchor="w", padx=16, pady=(12, 4))
        SelectableText(danger, f"Папка: {app.root_dir}", color=MUTED, width_px=760).pack(fill="x", padx=16)
        row = PlainFrame(danger)
        row.pack(fill="x", padx=16, pady=(8, 14))
        _secondary_button(row, "Открыть папку", lambda: open_path(app.root_dir), width=150, height=32).pack(side="left")
        ctk.CTkButton(row, text="Выйти из аккаунта hh (сбросить вход)", height=32, fg_color="transparent",
                      border_width=1, border_color=RED, text_color=RED, hover_color=ACCENT_SOFT,
                      command=app.reset_login).pack(side="left", padx=10)
        row = PlainFrame(danger)
        row.pack(fill="x", padx=16, pady=(0, 14))
        self.check_updates_switch = ctk.CTkSwitch(row, text="Проверять обновления на GitHub",
                                                  command=lambda: app.save_prefs(
                                                      check_updates=bool(self.check_updates_switch.get())))
        if app.prefs.get("check_updates", True):
            self.check_updates_switch.select()
        self.check_updates_switch.pack(side="left")
        _secondary_button(row, "Проверить сейчас", lambda: app.check_updates(manual=True), width=160,
                          height=32).pack(side="left", padx=12)
        SelectableText(row, "Новая версия скачивается и ставится по кнопке в меню слева, настройки остаются",
                       color=MUTED, size=12, width_px=420).pack(side="left", fill="x", expand=True)
        row = PlainFrame(danger)
        row.pack(fill="x", padx=16, pady=(0, 14))
        self.mini_switch = ctk.CTkSwitch(row, text="Сворачивать в мини-окно в углу экрана, если идёт работа",
                                         command=lambda: app.save_prefs(mini_on_minimize=bool(self.mini_switch.get())))
        if app.prefs.get("mini_on_minimize", True):
            self.mini_switch.select()
        self.mini_switch.pack(side="left")
        self.current_tab = ""
        saved = app.prefs.get("settings_tab")
        self.show_tab(saved if saved in self.tabs else SETTINGS_TABS[0][0])

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", pady=(10, 0))
        _primary_button(footer, "Сохранить", self.save, width=150).pack(side="left")
        _secondary_button(footer, "Отменить изменения", lambda: self.load(force=True), width=180).pack(
            side="left", padx=10)
        _secondary_button(footer, "Открыть config.toml", lambda: open_path(app.config_path), width=180).pack(side="right")

    def show_tab(self, name: str) -> None:
        """Вкладка настроек: на экране только она — остальные не рисуются вовсе."""
        if name == self.current_tab:
            return
        # снимок прежней вкладки поверх, новая дорисовывается под ним целиком и плавно проступает
        with crossfade(self.winfo_toplevel(), TAB_FADE), frozen(self, flush=True):
            if self.current_tab:
                self.tabs[self.current_tab].pack_forget()
            theme_switch.ensure(self.tabs[name])  # тему сменили, а фон ещё не докрасил эту вкладку
            self.tabs[name].pack(fill="both", expand=True)
            for tab, button in self.tab_buttons.items():
                button.configure(fg_color=ACCENT_SOFT if tab == name else "transparent",
                                 text_color=ACCENT if tab == name else TEXT)
        self.current_tab = name
        self.app.save_prefs(settings_tab=name)

    def show_field(self, section: str, key: str) -> None:
        """Ошибка в поле — открыть вкладку, где оно лежит."""
        tab = self.field_tab.get((section, key)) or next(
            (t for (s, _k), t in self.field_tab.items() if s == section), None)
        if tab:
            self.show_tab(tab)

    def _add_field(self, card, section, key, label, kind, hint) -> None:
        row = PlainFrame(card)
        row.pack(fill="x", padx=16, pady=4)
        if kind == "bool":
            widget = ctk.CTkSwitch(row, text=label)
            widget.pack(anchor="w")
        else:
            TextLabel(row, label).pack(anchor="w", pady=(0, 3))
            if kind == "text":
                widget = ctk.CTkTextbox(row, height=70, border_width=1, border_color=BORDER)
                widget.pack(fill="x")
            elif kind == "multi":  # «чипы» на одном холсте вместо десятка флажков
                widget = ChipGroup(row, MULTI[(section, key)])
                widget.pack(fill="x", pady=(2, 0))
            elif kind == "picker":
                groups, names = PICKERS[(section, key)]
                widget = PickerField(row, label, groups, names)
                widget.pack(fill="x", pady=(2, 0))
            elif kind == "choice":
                widget = ctk.CTkOptionMenu(row, values=list(CHOICES[(section, key)]), width=300)
                widget.pack(anchor="w")
            elif kind in ("int", "float"):
                widget = ctk.CTkEntry(row, width=120)
                widget.pack(anchor="w")
            elif kind == "secret":
                widget = ctk.CTkEntry(row, show="•")
                widget.pack(fill="x")
            else:
                widget = ctk.CTkEntry(row)
                widget.pack(fill="x")
        if hint:  # подсказку можно выделить и скопировать
            SelectableText(row, hint, color=MUTED, size=12, width_px=760).pack(fill="x", pady=(3, 0))
        self.widgets[(section, key)] = (kind, widget)

    def load(self, force: bool = False) -> None:
        """Значения из настроек — в форму. Если настройки с прошлого раза не менялись, форма не трогается
        (переход на страницу быстрее, а несохранённые правки остаются до «Отменить изменения»)."""
        cfg = self.app.cfg
        values = config_values(cfg)
        if not force and values == self._loaded:
            return
        self._loaded = values
        for (section, key), (kind, widget) in self.widgets.items():
            self._set_value(section, key, getattr(getattr(cfg, section) if section else cfg, key))

    def _set_value(self, section: str, key: str, value) -> None:
        kind, widget = self.widgets[(section, key)]
        if kind == "picker":
            widget.set(value)
            return
        # то же значение — не трогаем: переключатель и «чипы» перерисовывались бы зря
        if kind == "bool":
            if bool(widget.get()) != bool(value):
                widget.select() if value else widget.deselect()
        elif kind == "text":
            if widget.get("1.0", "end-1c") != value:
                widget.delete("1.0", "end")
                widget.insert("1.0", value)
        elif kind == "multi":
            widget.set(value)
        elif kind == "choice":
            options = CHOICES[(section, key)]
            if value not in options.values():  # например, регион, которого нет в списке
                options[f"{value}"] = value
                widget.configure(values=list(options))
            label = next((k for k, v in options.items() if v == value), next(iter(options)))
            if widget.get() != label:
                widget.set(label)
        else:
            text = ", ".join(value) if kind == "list" else str(value)
            if widget.get() != text:
                widget.delete(0, "end")
                widget.insert(0, text)

    def save(self) -> None:
        cfg = self.collect()
        if cfg is None:
            return
        save_config(cfg, self.app.config_path)
        self.app.reload_config()
        self.app.toast("Настройки сохранены")

    def collect(self) -> Config | None:
        """Значения формы → Config с проверкой. None — ошибка уже показана пользователю."""
        cfg = self.app.read_config_file(check_search_url=False)
        try:
            for (section, key), (kind, widget) in self.widgets.items():
                target = getattr(cfg, section) if section else cfg
                if kind == "bool":
                    value = bool(widget.get())
                elif kind == "text":
                    value = widget.get("1.0", "end").strip()
                elif kind == "multi":
                    value = widget.get()
                elif kind == "picker":
                    value = widget.get()
                elif kind == "choice":
                    value = CHOICES[(section, key)][widget.get()]
                elif kind == "list":
                    value = [w.strip() for w in widget.get().split(",") if w.strip()]
                elif kind in ("str", "secret"):
                    value = widget.get().strip()
                else:  # int / float
                    raw = widget.get().strip().replace(",", ".")
                    value = int(raw) if kind == "int" else float(raw)
                    if value < 0:
                        raise ValueError
                setattr(target, key, value)
            validate_config(cfg)
            if cfg.search.mode == "query":
                validate_search_source(cfg)
        except ValueError:
            self.show_field(section, key)
            messagebox.showerror(APP_NAME, f"Неверное число в поле «{self._label(section, key)}»")
            return None
        except ConfigError as e:
            bad = re.match(r"\[(\w+)\]", str(e))
            if bad:
                self.show_field(bad[1], "")
            messagebox.showerror(APP_NAME, f"Не сохранено: {e}")
            return None
        return cfg

    def test_ai(self) -> None:
        """Короткий запрос к нейросети с текущими (даже несохранёнными) настройками — в фоне."""
        cfg = self.collect()
        if cfg is None:
            return
        self.ai_test_btn.configure(state="disabled", text="Проверяю…")

        def work():
            try:
                client = AiClient(cfg.ai)
                answer = client.ping()
                result = (True, f"Нейросеть отвечает ✓\n\n{client.describe()}\nОтвет: «{answer[:100]}»")
            except AiError as e:
                result = (False, f"Не получилось: {e}")
            self.app.events.put(("ai_test", *result))  # окно трогаем только из главного потока (_poll)

        threading.Thread(target=work, daemon=True).start()

    def import_hh_filters(self) -> None:
        """Фильтры из ссылки hh: настроили на сайте, скопировали адрес — все поля заполнились."""
        url = messagebox.askstring("Фильтры из ссылки hh", "Настройте фильтры на hh.ru, скопируйте адрес страницы "
                                   "из браузера и вставьте сюда — поля заполнятся сами.",
                                   placeholder="https://hh.ru/search/vacancy?…", ok="Заполнить")
        if not url:
            return
        if "hh.ru/search/vacancy" not in url:
            messagebox.showerror(APP_NAME, "Это не ссылка поиска вакансий hh\n\nНужна ссылка вида "
                                           "https://hh.ru/search/vacancy?… — со страницы, где выбраны фильтры.")
            return
        found = hhf.from_url(url)
        for name in hhf.MANAGED:
            self._set_value("hh_filters", name, getattr(found, name))
        note = ""
        if "resume=" in url:  # ссылка «подходящие к резюме» — её же без фильтров ставим ссылкой поиска
            self._set_value("", "search_url", hhf.strip(url))
            note = " Ссылка поиска тоже обновлена (без фильтров — они теперь в полях ниже)."
        messagebox.showinfo(APP_NAME, f"Заполнено фильтров: {hhf.count(found)}\n\nПроверьте поля и нажмите "
                                      f"«Сохранить».{note}", kind="success")

    def reset_hh_filters(self) -> None:
        empty = hhf.HhFilters()
        for name in hhf.MANAGED:
            self._set_value("hh_filters", name, getattr(empty, name))
        self.app.toast("Фильтры hh сброшены — нажмите «Сохранить»")

    def test_notify(self) -> None:
        """Тестовое уведомление с текущими (даже несохранёнными) настройками — в фоне."""
        cfg = self.collect()
        if cfg is None:
            return
        if not (cfg.notify.desktop or cfg.notify.telegram):
            messagebox.showinfo(APP_NAME, "Сначала включите уведомление на компьютере или в Telegram.")
            return
        from . import notify

        threading.Thread(target=lambda: notify.send(cfg.notify, f"{APP_NAME} — проверка",
                                                    "Так будут выглядеть итоги запуска."), daemon=True).start()
        self.app.toast("Тестовое уведомление отправлено")

    def _ai_tested(self, ok: bool, text: str) -> None:
        self.ai_test_btn.configure(state="normal", text="Проверить подключение к ИИ")
        if ok:
            messagebox.showinfo(APP_NAME, text.replace(" ✓", ""), kind="success")
        else:
            messagebox.showerror(APP_NAME, text.replace("Не получилось: ", "Нейросеть не отвечает\n\n", 1))

    def test_vacancy_filter(self) -> None:
        """ИИ-фильтр на тестовой вакансии с текущими (даже несохранёнными) настройками — в фоне."""
        cfg = self.collect()
        if cfg is None:
            return
        self.filter_test_btn.configure(state="disabled", text="Проверяю…")

        def work():
            from .ai_playground import SAMPLE_VACANCY

            title, company, description = SAMPLE_VACANCY
            try:
                client = AiClient(cfg.ai)
                verdict = client.evaluate_vacancy(cfg.ai_filter.criteria, title, company, description,
                                                  cfg.ai_filter.max_snippet_chars)
                head = ("Подходит — откликнулись бы" if verdict.interesting
                        else "Не подходит — вакансию пропустили бы")
                text = f"{head}\n\nТестовая вакансия: «{title}» — {company}"
                if verdict.reason:
                    text += f"\nПричина: {verdict.reason}"
                result = (True, text)
            except AiError as e:
                result = (False, f"Не получилось: {e}")
            self.app.events.put(("ai_filter_test", *result))  # окно трогаем только из главного потока (_poll)

        threading.Thread(target=work, daemon=True).start()

    def _vacancy_filter_tested(self, ok: bool, text: str) -> None:
        self.filter_test_btn.configure(state="normal", text="Проверить фильтр на примере вакансии")
        if ok:
            messagebox.showinfo(APP_NAME, text, kind="info")
        else:
            messagebox.showerror(APP_NAME, text.replace("Не получилось: ", "Нейросеть не ответила\n\n", 1))

    @staticmethod
    def _label(section: str, key: str) -> str:
        for _, items in SETTINGS:
            for s, k, label, _, _ in items:
                if (s, k) == (section, key):
                    return label
        return key


class JournalsPage(Page):
    def __init__(self, master, app: "App"):
        super().__init__(master, "Журналы", "Логи прошлых запусков, таблица откликов и снимки страниц при ошибках.")
        self.app = app
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", pady=(0, 10))
        _primary_button(toolbar, "Таблица откликов (Excel)", lambda: open_path(app.xlsx_path),
                        width=220, height=34).pack(side="left")
        _secondary_button(toolbar, "Папка логов", lambda: open_path(app.root_dir / "logs"), width=130,
                          height=34).pack(side="left", padx=8)
        _secondary_button(toolbar, "Снимки ошибок", lambda: open_path(app.root_dir / "logs" / "snapshots"),
                          width=140, height=34).pack(side="left")
        _secondary_button(toolbar, "Обновить", self.refresh, width=110, height=34).pack(side="right")
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", pady=(0, 10))
        _secondary_button(actions, "Копировать", self.copy, width=120, height=30).pack(side="left")
        for text, command in (("Удалить этот лог", self.delete_current), ("Удалить все логи", self.delete_all),
                              ("Удалить снимки", self.delete_snapshots)):
            ctk.CTkButton(actions, text=text, command=command, height=30, corner_radius=10, fg_color="transparent",
                          border_width=1, border_color=RED, text_color=RED, hover_color=ACCENT_SOFT).pack(
                side="left", padx=(8, 0))
        self.usage = ctk.CTkLabel(actions, text="", text_color=MUTED)
        self.usage.pack(side="right")
        self.current: Path | None = None

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)
        # список логов — одно окно на весь список (раньше кнопка на каждый лог, до 600 окон Windows,
        # и все пересоздавались при каждом открытии страницы и после удаления — экран мигал)
        files = Card(body, width=250)
        files.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        files.grid_propagate(False)
        files.grid_rowconfigure(0, weight=1)
        files.grid_columnconfigure(0, weight=1)
        self.files = ItemList(files, self.show, width=1, height=1)
        self.files.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=8)
        bar = ctk.CTkScrollbar(files, command=self.files.yview, width=12)
        bar.grid(row=0, column=1, sticky="ns", padx=(0, 4), pady=8)
        self.files.configure(yscrollcommand=bar.set)
        self.view = LogView(body)
        self.view.grid(row=0, column=1, sticky="nsew")

    def refresh(self) -> None:
        logs = sorted((self.app.root_dir / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        self._show_usage()
        if not logs:
            self.current = None
            self.files.set_items([])
            self.view.set_lines(["Запусков ещё не было."])
            return
        items = []
        for path in logs[:200]:
            kind = "Чаты" if path.name.startswith("chats_") else "Отклики"
            when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m %H:%M")
            items.append((path, f"{kind}   {when}"))
        current = self.current if self.current in logs else logs[0]
        self.files.set_items(items, current)
        self.show(current)

    def show(self, path: Path) -> None:
        self.current = path
        self.files.select(path)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-5000:]
        except OSError as e:
            lines = [f"Не удалось прочитать {path}: {e}"]
        self.view.set_lines(lines)

    # ---------- копирование и удаление ----------

    def copy(self) -> None:
        self.view.copy_selection()
        self.app.toast("Скопировано в буфер обмена")

    def _busy_log(self) -> Path | None:
        """Лог, в который сейчас пишет идущий запуск: его не удаляем."""
        worker = self.app.worker
        return self.app.last_log_path if worker and worker.is_alive() else None

    def _show_usage(self) -> None:
        logs = self.app.root_dir / "logs"
        size = lambda paths: sum(f.stat().st_size for f in paths if f.is_file()) / 1024 / 1024  # noqa: E731
        self.usage.configure(text=f"логи: {size(logs.glob('*.log')):.1f} МБ   •   снимки: "
                                  f"{size((logs / 'snapshots').glob('*')):.1f} МБ")

    def delete_current(self) -> None:
        path = self.current
        if path is None or not path.exists():
            return
        if path == self._busy_log():
            messagebox.showinfo(APP_NAME, "В этот лог сейчас пишет идущий запуск\n\nУдалить его можно, когда "
                                          "запуск закончится.")
            return
        if messagebox.askyesno(APP_NAME, f"Удалить лог {path.name}?", yes="Удалить", no="Отмена", danger=True):
            self._unlink([path])

    def delete_all(self) -> None:
        busy = self._busy_log()
        logs = [p for p in (self.app.root_dir / "logs").glob("*.log") if p != busy]
        if not logs:
            messagebox.showinfo(APP_NAME, "Удалять нечего: логов прошлых запусков нет.")
            return
        if messagebox.askyesno(APP_NAME, f"Удалить все логи ({len(logs)} шт.)?\n\nТаблица откликов (Excel) "
                                         "и статистика останутся — они хранятся отдельно.",
                               yes="Удалить все", no="Отмена", danger=True):
            self._unlink(logs)

    def delete_snapshots(self) -> None:
        files = [p for p in (self.app.root_dir / "logs" / "snapshots").glob("*") if p.is_file()]
        if not files:
            messagebox.showinfo(APP_NAME, "Снимков страниц нет — удалять нечего.")
            return
        if messagebox.askyesno(APP_NAME, f"Удалить снимки страниц ({len(files)} файлов)?\n\nОни нужны, только чтобы "
                                         "разобраться в ошибке, если hh поменял вёрстку.",
                               yes="Удалить", no="Отмена", danger=True):
            self._unlink(files)

    def _unlink(self, paths: list[Path]) -> None:
        failed = 0
        for path in paths:
            try:
                path.unlink()
            except OSError:
                failed += 1
        with frozen(self):
            self.refresh()
        self.app.toast(f"Удалено: {len(paths) - failed}" + (f", не удалось: {failed}" if failed else ""))


# ─────────────────────────────── приложение ───────────────────────────────


# ---------- статистика ----------

STAT_COLORS = {
    "applied": GREEN, "letters": ACCENT, "skipped": AMBER, "failed": RED,
    "skip_filters": ("#64748B", "#94A3B8"), "skip_ai": ("#9333EA", "#C084FC"),
    "skip_questions": ("#EA580C", "#FB923C"), "skip_relocation": ("#0D9488", "#2DD4BF"),
    "skip_company": ("#CA8A04", "#FACC15"), "skip_other": ("#78716C", "#A8A29E"),
    "rejections": ("#DB2777", "#F472B6"), "ai_answers": ("#7C3AED", "#A78BFA"),
    "waiting": ("#0891B2", "#22D3EE"), "runs": ("#475569", "#CBD5E1"), "minutes": ("#2563EB", "#60A5FA"),
}
STAT_PERIODS = {"7 дней": 7, "30 дней": 30, "90 дней": 90, "Год": 365, "Всё время": None}
STAT_STEPS = {"По дням": "day", "По неделям": "week", "По месяцам": "month"}
CHART_KINDS = {"Столбцы": "bar", "Линии": "line", "Итоги": "total"}
DEFAULT_CHARTS = [
    {"title": "Отклики", "metrics": ["applied", "letters"], "kind": "bar"},
    {"title": "Чаты: отказы и ответы ИИ", "metrics": ["rejections", "ai_answers"], "kind": "line"},
    {"title": "Почему вакансии пропущены", "metrics": list(SKIP_KINDS), "kind": "total"},
]


def _num(value: float) -> str:
    return f"{value:.0f}" if abs(value - round(value)) < 0.05 else f"{value:.1f}".replace(".", ",")


class Segments(ctk.CTkFrame):
    """Выбор одного из нескольких вариантов — как вкладки настроек: выбранный подсвечен мягким акцентом.
    (У CTkSegmentedButton в светлой теме невыбранное серое на сером.)"""

    def __init__(self, master, values, command=None, height: int = 30, size: int = 13):
        super().__init__(master, fg_color="transparent")
        self.command = command
        self.value = ""
        self.buttons: dict[str, ctk.CTkButton] = {}
        for value in values:
            button = ctk.CTkButton(self, text=value, width=0, height=height, corner_radius=8, fg_color="transparent",
                                   text_color=TEXT, hover_color=ACCENT_SOFT, font=ctk.CTkFont(size=size),
                                   command=lambda v=value: self._clicked(v))
            button.pack(side="left", padx=(0, 2))
            self.buttons[value] = button

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        if value == self.value or value not in self.buttons:
            return
        self.value = value
        for name, button in self.buttons.items():
            button.configure(fg_color=ACCENT_SOFT if name == value else "transparent",
                             text_color=ACCENT if name == value else TEXT)

    def _clicked(self, value: str) -> None:
        if value != self.value:
            self.set(value)
            if self.command:
                self.command(value)


class ChartCard(Card):
    """Один график на вкладке «Статистика»: показатели, вид и название настраиваются («Настроить»),
    наведение на столбец показывает числа за этот период."""

    def __init__(self, master, page: "StatsPage", config: dict):
        super().__init__(master)
        self.page = page
        kind = config.get("kind") if config.get("kind") in CHART_KINDS.values() else "bar"
        metrics = [m for m in config.get("metrics", []) if m in METRICS]
        self.config = {"title": str(config.get("title", "")), "metrics": metrics, "kind": kind}
        self.rows: list[tuple[str, dict]] = []

        head = PlainFrame(self)
        head.pack(fill="x", padx=16, pady=(12, 0))
        self.title = TextLabel(head, "", size=15, weight="bold")
        self.title.pack(side="left")
        self.readout = TextLabel(head, "", color=MUTED, size=12)
        self.readout.pack(side="left", padx=12)
        ctk.CTkButton(head, text="Убрать", width=70, height=26, fg_color="transparent", text_color=MUTED,
                      hover_color=ACCENT_SOFT, command=lambda: page.remove_chart(self)).pack(side="right")
        self.edit_btn = ctk.CTkButton(head, text="Настроить", width=96, height=26, fg_color="transparent",
                                      text_color=ACCENT, hover_color=ACCENT_SOFT, command=self.toggle_editor)
        self.edit_btn.pack(side="right", padx=(0, 4))

        # настройки графика — спрятаны, пока не нажали «Настроить»
        self.editor = PlainFrame(self)
        row = PlainFrame(self.editor)
        row.pack(fill="x", pady=(10, 6))
        TextLabel(row, "Название", color=MUTED, size=12).pack(side="left")
        self.name = ctk.CTkEntry(row, width=260, height=30, placeholder_text="по показателям")
        if self.config["title"]:
            self.name.insert(0, self.config["title"])
        self.name.pack(side="left", padx=(8, 16))
        self.name.bind("<KeyRelease>", lambda _e: self._edited())
        TextLabel(row, "Вид", color=MUTED, size=12).pack(side="left")
        self.kind = Segments(row, list(CHART_KINDS), command=lambda _v: self._edited(), height=28, size=12)
        self.kind.set(next(label for label, value in CHART_KINDS.items() if value == kind))
        self.kind.pack(side="left", padx=8)
        TextLabel(self.editor, "Показатели", color=MUTED, size=12).pack(anchor="w")
        self.metrics = ChipGroup(self.editor, METRICS, selected=set(metrics), on_change=self._edited)
        self.metrics.pack(fill="x", pady=(4, 4))

        self.legend = PlainFrame(self)
        self.legend.pack(fill="x", padx=16, pady=(4, 0))
        self.legend_labels: dict[str, TextLabel] = {}
        self.chart = Chart(self, on_hover=self._hover)
        self.chart.pack(fill="x", padx=12, pady=(6, 14))

    def toggle_editor(self) -> None:
        with frozen(self.page):
            if self.editor.winfo_manager():
                self.editor.pack_forget()
                self.edit_btn.configure(text="Настроить")
            else:
                self.editor.pack(fill="x", padx=16, after=self.title.master)
                self.edit_btn.configure(text="Готово")

    def _edited(self) -> None:
        self.config = {"title": self.name.get().strip(), "metrics": self.metrics.get(),
                       "kind": CHART_KINDS[self.kind.get()]}
        self.refresh(self.rows)
        self.page.save_charts()

    def refresh(self, rows: list[tuple[str, dict]]) -> None:
        self.rows = rows
        metrics, kind = self.config["metrics"], self.config["kind"]
        title = self.config["title"] or (" · ".join(METRICS[m] for m in metrics[:2]) + ("…" if len(metrics) > 2 else "")
                                         if metrics else "Новый график")
        if self.title.cget("text") != title:
            self.title.configure(text=title)
        if list(self.legend_labels) != (metrics if kind != "total" else []):
            for label in self.legend_labels.values():
                label.destroy()
            self.legend_labels = {}
            if kind != "total":  # у «итогов» подписи уже на полосах
                for metric in metrics:
                    label = TextLabel(self.legend, "", color=STAT_COLORS[metric], size=12)
                    label.pack(side="left", padx=(0, 14))
                    self.legend_labels[metric] = label
        series = [(METRICS[m], STAT_COLORS[m], [row.get(m, 0) for _, row in rows]) for m in metrics]
        empty = "Выберите показатели — «Настроить»" if not metrics else "Пока нет данных за этот период"
        self.chart.set_data([label for label, _ in rows], series, kind, empty)
        self._hover(None)

    def _hover(self, index) -> None:
        for metric, label in self.legend_labels.items():
            if index is None:
                text = f"●  {METRICS[metric]}"
            else:
                text = f"●  {METRICS[metric]}: {_num(self.rows[index][1].get(metric, 0))}"
            if label.cget("text") != text:
                label.configure(text=text)
        readout = self.rows[index][0] if index is not None and index < len(self.rows) else ""
        if self.readout.cget("text") != readout:
            self.readout.configure(text=readout)


class StatsPage(Page):
    def __init__(self, master, app: "App"):
        super().__init__(master, "Статистика", "Сколько отправлено и пропущено, отказы и ответы ИИ в чатах — по дням. "
                                               "Хранятся только числа, без названий вакансий и компаний.")
        self.app = app
        self.days: dict[str, dict] = {}

        self.notice = Card(self)  # «статистика не ведётся» — видна, только если её выключили
        TextLabel(self.notice, "Статистика не ведётся: новые запуски не записываются ([stats] disabled). "
                               "Собранное раньше показано ниже.", color=AMBER).pack(side="left", padx=16, pady=10)
        _secondary_button(self.notice, "Вести статистику", self.enable, width=160, height=30).pack(
            side="right", padx=10)

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", pady=(0, 12))
        self.toolbar = toolbar
        self.period = Segments(toolbar, list(STAT_PERIODS), command=lambda _v: self._view_changed(), height=32,
                               size=14)
        self.period.set(app.prefs.get("stats_period") if app.prefs.get("stats_period") in STAT_PERIODS else "30 дней")
        self.period.pack(side="left")
        self.step = ctk.CTkOptionMenu(toolbar, values=list(STAT_STEPS), width=140, height=32,
                                      command=lambda _v: self._view_changed())
        self.step.set(app.prefs.get("stats_step") if app.prefs.get("stats_step") in STAT_STEPS else "По дням")
        self.step.pack(side="left", padx=12)
        _secondary_button(toolbar, "+  График", self.add_chart, width=120, height=32).pack(side="right")

        cards = ctk.CTkFrame(self, fg_color="transparent")
        cards.pack(fill="x", pady=(0, 12))
        self.cards = {
            "applied": StatCard(cards, "Отправлено", GREEN),
            "letters": StatCard(cards, "С письмом", ACCENT),
            "rejections": StatCard(cards, "Отказов в чатах", STAT_COLORS["rejections"]),
            "share": StatCard(cards, "Отказов на 100 откликов", AMBER),
        }
        for i, card in enumerate(self.cards.values()):
            cards.grid_columnconfigure(i, weight=1, uniform="stats")
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 10, 0))

        self.scroll = ScrollFrame(self, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True)
        self.charts: list[ChartCard] = []
        saved = app.prefs.get("stats_charts")
        for config in saved if isinstance(saved, list) else DEFAULT_CHARTS:
            if isinstance(config, dict):
                self._add_card(config)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", pady=(10, 0))
        ctk.CTkButton(footer, text="Очистить статистику…", height=30, corner_radius=10, fg_color="transparent",
                      border_width=1, border_color=RED, text_color=RED, hover_color=ACCENT_SOFT,
                      command=self.clear).pack(side="right")
        self.info = SelectableText(footer, "", color=MUTED, size=12, bg=MAIN_BG, width_px=620)
        self.info.pack(side="left", fill="x", expand=True, padx=(0, 12))

    # ---------- данные ----------

    def refresh(self) -> None:
        cfg = self.app.cfg
        store = StatsStore(self.app.root_dir, cfg.stats.keep_days)
        self.days = store.load()  # в первый раз — посчитает дни по уже накопленной истории откликов
        period = STAT_PERIODS[self.period.get()]
        rows = buckets(self.days, period, STAT_STEPS[self.step.get()])
        total = totals(self.days, period)
        with frozen(self):
            if cfg.stats.disabled and not self.notice.winfo_manager():
                self.notice.pack(fill="x", pady=(0, 12), before=self.toolbar)
            elif not cfg.stats.disabled and self.notice.winfo_manager():
                self.notice.pack_forget()
            applied = total.get("applied", 0)
            self.cards["applied"].set(_num(applied))
            self.cards["letters"].set(f"{_num(total.get('letters', 0))}" + (
                f"  ({total.get('letters', 0) * 100 / applied:.0f}%)" if applied else ""))
            self.cards["rejections"].set(_num(total.get("rejections", 0)))
            self.cards["share"].set(f"{total.get('rejections', 0) * 100 / applied:.0f}" if applied else "—")
            for card in self.charts:
                card.refresh(rows)
        keep = f"старше {cfg.stats.keep_days} дн. удаляется само" if cfg.stats.keep_days else "хранится без срока"
        size = f"{store.size() / 1024:.1f}".replace(".", ",")
        self.info.set(f"Дней в статистике: {len(self.days)} · {size} КБ · data/stats.json в папке данных · {keep}")

    def _view_changed(self) -> None:
        self.app.save_prefs(stats_period=self.period.get(), stats_step=self.step.get())
        self.refresh()

    # ---------- графики ----------

    def _add_card(self, config: dict) -> ChartCard:
        card = ChartCard(self.scroll, self, config)
        card.pack(fill="x", pady=(0, 12), padx=(0, 6))
        self.charts.append(card)
        return card

    def add_chart(self) -> None:
        with frozen(self):
            card = self._add_card({"title": "", "metrics": ["applied"], "kind": "bar"})
            card.refresh(buckets(self.days, STAT_PERIODS[self.period.get()], STAT_STEPS[self.step.get()]))
            card.toggle_editor()
        self.save_charts()
        self.after(50, lambda: self.scroll._parent_canvas.yview_moveto(1.0))

    def remove_chart(self, card: ChartCard) -> None:
        if card in self.charts:
            with frozen(self):
                self.charts.remove(card)
                card.destroy()
            self.save_charts()

    def save_charts(self) -> None:
        self.app.save_prefs(stats_charts=[card.config for card in self.charts])

    # ---------- очистка и включение ----------

    def clear(self) -> None:
        if not messagebox.askyesno(APP_NAME, "Удалить всю статистику?\n\nГрафики начнутся с нуля. Таблица "
                                             "откликов (Excel) и журналы останутся.",
                                   yes="Удалить статистику", no="Отмена", danger=True):
            return
        StatsStore(self.app.root_dir, self.app.cfg.stats.keep_days).clear()
        self.refresh()
        self.app.toast("Статистика очищена")

    def enable(self) -> None:
        try:
            cfg = self.app.read_config_file(check_search_url=False)
            cfg.stats.disabled = False
            save_config(cfg, self.app.config_path)
        except ConfigError as e:
            messagebox.showerror(APP_NAME, f"Не сохранено: {e}")
            return
        self.app.reload_config()
        self.refresh()


class App(ctk.CTk):
    def __init__(self):
        self.root_dir = app_root()
        self.config_path = self.root_dir / "config.toml"
        self.prefs_path = self.root_dir / "app_prefs.json"
        self.prefs = self._load_prefs()
        ctk.set_appearance_mode(self.prefs.get("appearance", "system"))
        ctk.set_default_color_theme("blue")
        _quiet_timers()
        super().__init__(fg_color=MAIN_BG)
        theme_switch.install(self)  # смена темы одним кадром, скрытые страницы — в фоне
        install_editing(self)  # Ctrl+C/V/X/A/Z в любой раскладке, отмена в полях, меню правой кнопки

        self.title(f"{APP_NAME} — автоотклики hh.ru")
        self.geometry("1180x800")
        self.minsize(1000, 680)
        apply_app_icon(self)  # .ico — Windows (заголовок, панель задач), .png — macOS и Linux
        self.after(800, lambda: sharp_windows_icon(self))  # ещё раз, когда окно точно появилось

        self.events: queue.Queue = queue.Queue()
        self.log_handler = QueueLogHandler(self.events)
        self.worker: threading.Thread | None = None
        self.running_kind: str | None = None
        self.started_at = 0.0
        self.last_log_path: Path | None = None
        self.stats = Stats()
        self.chat_stats = ChatStats()
        self.run_limit = 0
        self.closing = False
        self.mini: MiniWindow | None = None  # одно на сеанс: создаётся заранее, потом только показывается
        self.mini_mode = False  # сейчас вместо окна программы — мини-окно
        self._frames_wanted = True  # кадры браузера кому-то видны (окно программы или мини-окно)
        self._poll_count = 0
        self.last_frame: bytes | None = None  # последний кадр браузера (для превью и мини-окна)
        self._browser_check = 0.0  # когда в последний раз смотрели на окно браузера
        self._crash_shown = False
        self.waiting_captcha = False  # hh показал капчу, работа ждёт человека
        self.update_release: updater.Release | None = None  # найденная новая версия
        self.update_root: Path | None = None  # скачанная и распакованная новая версия
        self.update_busy = False
        self.chain_chats = False  # после откликов по расписанию — прочитать чаты
        self.schedule_since = datetime.now()  # время до этой минуты по расписанию уже не запускаем
        self.cfg = self.read_config_file(check_search_url=False)
        self.xlsx_path = self.root_dir / "logs" / "responses.xlsx"
        self.summary = load_summary(self.root_dir)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()
        self.show_page("responses")
        self.reload_config()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(120, self._poll)
        browser.frame_sink = lambda jpeg: self.events.put(("frame", jpeg))  # кадры браузера для превью
        browser.frame_wanted = lambda: self._frames_wanted  # окно свёрнуто — кадры не снимаем зря
        self.after(2500, self._ensure_mini)  # мини-окно — заранее, пока программа простаивает
        self.bind("<Unmap>", self._on_unmap, add="+")
        self.bind_all("<Control-KeyPress>", self._ctrl_key, add="+")
        self.pages["responses"].update_totals(self.summary)
        try:
            validate_search_source(self.cfg)
        except ConfigError:
            self.after(400, self._first_run_hint)
        if "--updated" in sys.argv:
            self.after(1200, lambda: self.toast(f"Обновлено до версии {APP_VERSION}"))
        if is_frozen():  # остатки прошлого обновления — когда программа обновления точно закрылась
            threading.Thread(target=lambda: (time.sleep(20), updater.cleanup()), daemon=True).start()
        self.after(4000, self.check_updates)
        self.after(3000, self._schedule_tick)

    # ---------- построение окна ----------

    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self, width=230, corner_radius=0, fg_color=SIDEBAR_BG)
        bar.grid(row=0, column=0, sticky="nsw")
        bar.grid_propagate(False)
        ctk.CTkLabel(bar, text=APP_NAME, font=ctk.CTkFont(size=21, weight="bold"), text_color=ACCENT).pack(
            anchor="w", padx=22, pady=(26, 0))
        ctk.CTkLabel(bar, text="автоотклики на hh.ru", text_color=MUTED).pack(anchor="w", padx=22, pady=(0, 22))

        self.nav: dict[str, ctk.CTkButton] = {}
        # значки нарисованы (theme.py), а не символами шрифта: одного размера и толщины, у выбранного — цвет акцента
        self.nav_icons: dict[str, tuple[ctk.CTkImage, ctk.CTkImage]] = {}
        for key, name, text in (("responses", "send", "Отклики"), ("chats", "chat", "Чаты"),
                                ("stats", "chart", "Статистика"), ("ai", "sparkle", "Проверка ИИ"),
                                ("settings", "gear", "Настройки"), ("journals", "journal", "Журналы")):
            self.nav_icons[key] = (icon(name, 18, TEXT), icon(name, 18, ACCENT))
            button = ctk.CTkButton(bar, text=f" {text}", image=self.nav_icons[key][0], compound="left", anchor="w",
                                   height=42, corner_radius=10, fg_color="transparent", text_color=TEXT,
                                   hover_color=ACCENT_SOFT, font=ctk.CTkFont(size=15),
                                   command=lambda k=key: self.show_page(k))
            button.pack(fill="x", padx=12, pady=2)
            self.nav[key] = button

        spacer = ctk.CTkFrame(bar, fg_color="transparent")
        spacer.pack(fill="both", expand=True)
        # что сейчас в окне браузера (сам браузер спрятан за краем экрана) — появляется с первым кадром
        self.preview = BrowserPreview(bar, self)
        # «Автозапуск сегодня в 15:00» — видно с любой страницы; щелчок — к настройкам расписания
        self.schedule_label = ctk.CTkLabel(bar, text="", image=icon("clock", 14, MUTED), compound="left",
                                           text_color=MUTED, anchor="w", cursor="hand2", font=ctk.CTkFont(size=12))
        self.schedule_label.bind("<Button-1>", lambda _e: self._open_schedule_settings())

        self.status = ctk.CTkLabel(bar, text="●  Готово", text_color=MUTED, anchor="w",
                                   font=ctk.CTkFont(size=14, weight="bold"))
        self.status.pack(fill="x", padx=22)
        self.status_detail = ctk.CTkLabel(bar, text="", text_color=MUTED, anchor="w", font=ctk.CTkFont(size=12))
        self.status_detail.pack(fill="x", padx=22, pady=(0, 16))

        ctk.CTkLabel(bar, text="Оформление", text_color=MUTED, anchor="w").pack(fill="x", padx=22)
        current = next((k for k, v in APPEARANCE.items() if v == self.prefs.get("appearance", "system")),
                       "Как в системе")
        mode = ctk.CTkOptionMenu(bar, values=list(APPEARANCE), command=self._set_appearance)
        mode.set(current)
        mode.pack(fill="x", padx=22, pady=(4, 10))
        # появляется, когда на GitHub вышла новая версия
        self._icon_download = icon("download", 16)  # свой значок: «⬇» есть не в каждом шрифте
        self._icon_restart = icon("restart", 16)
        self.update_btn = ctk.CTkButton(bar, text="", image=self._icon_download, compound="left", height=36,
                                        corner_radius=10, fg_color=GREEN, hover_color=GREEN_HOVER,
                                        text_color="#FFFFFF", text_color_disabled="#FFFFFF",
                                        font=ctk.CTkFont(size=13, weight="bold"), command=self._update_clicked)
        self.update_progress = ctk.CTkProgressBar(bar, height=4, corner_radius=2, progress_color=GREEN)
        about = ctk.CTkFrame(bar, fg_color="transparent")
        about.pack(fill="x", padx=22, pady=(0, 16))
        self._about = about
        ctk.CTkLabel(about, text=f"версия {APP_VERSION}", text_color=MUTED, font=ctk.CTkFont(size=11)).pack(side="left")
        github = ctk.CTkLabel(about, text="GitHub ↗", text_color=ACCENT, cursor="hand2",
                              font=ctk.CTkFont(size=11, underline=True))
        github.pack(side="left", padx=(10, 0))
        github.bind("<Button-1>", lambda _e: webbrowser.open(GITHUB_URL))

    def _build_main(self) -> None:
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=28, pady=24)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        self.pages = {
            "responses": ResponsesPage(main, self),
            "chats": ChatsPage(main, self),
            "ai": AiTestPage(main, self),
            "settings": SettingsPage(main, self),
            "journals": JournalsPage(main, self),
            "stats": StatsPage(main, self),
        }
        self.console = Console(main, self)
        self.main = main

    def show_page(self, key: str) -> None:
        # снимок окна поверх: пока новая страница собирается и дорисовывается под ним, не видно ни кусков
        # старой и новой страницы, ни белых и чёрных прямоугольников; потом снимок плавно растворяется
        with crossfade(self, PAGE_FADE), frozen(self.main, flush=True):
            for name, page in self.pages.items():
                if name != key:
                    page.grid_forget()
                self.nav[name].configure(fg_color=ACCENT_SOFT if name == key else "transparent",
                                         text_color=ACCENT if name == key else TEXT,
                                         image=self.nav_icons[name][name == key])
            page = self.pages[key]
            theme_switch.ensure(page)
            theme_switch.ensure(self.console)
            if key in ("responses", "chats"):
                page.grid(row=0, column=0, sticky="new")
                self.console.grid(row=1, column=0, sticky="nsew", pady=(18, 0))
            else:
                self.console.grid_forget()
                page.grid(row=0, column=0, rowspan=2, sticky="nsew")
            if key == "settings":
                page.load()
            elif key in ("journals", "ai", "stats"):
                page.refresh()

    # ---------- настройки ----------

    def read_config_file(self, check_search_url: bool = True) -> Config:
        if not self.config_path.exists():
            if check_search_url:
                raise ConfigError("не указана ссылка поиска — откройте «Настройки»")
            return Config(search_url="")
        return load_config(self.config_path, check_search_url=check_search_url)

    def reload_config(self) -> None:
        before = self.cfg.schedule
        try:
            self.cfg = self.read_config_file(check_search_url=False)
        except ConfigError as e:
            messagebox.showerror(APP_NAME, f"Ошибка в config.toml: {e}")
            return
        if self.cfg.schedule != before:  # включили или поменяли время — уже прошедшее сегодня не запускаем
            self.schedule_since = datetime.now()
        self._show_next_run()
        self.pages["responses"].refresh_config(self.cfg)
        self.pages["chats"].refresh_config(self.cfg)
        self.pages["ai"].invalidate()

    def _load_prefs(self) -> dict:
        try:
            return json.loads(self.prefs_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_prefs(self, **values) -> None:
        self.prefs.update(values)
        try:
            self.prefs_path.write_text(json.dumps(self.prefs, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _windows_set_titlebar_color(self, color_mode: str) -> None:
        """customtkinter перекрашивает заголовок окна, пряча окно и показывая его снова: окно мигает и
        перерисовывается целиком — это и было «подвисание» при смене темы. Хватает перерисовать рамку."""
        if not getattr(self, "_window_exists", False):  # окно ещё не показано — как в customtkinter
            super()._windows_set_titlebar_color(color_mode)
            return
        set_titlebar_dark(self, color_mode.lower() == "dark")

    def _set_appearance(self, label: str) -> None:
        mode = APPEARANCE[label]
        ctk.set_appearance_mode(mode)
        self.save_prefs(appearance=mode)
        self.after(50, self._recolor_logs)

    def _recolor_logs(self) -> None:
        self.console.view.apply_colors()
        self.pages["journals"].view.apply_colors()

    def _first_run_hint(self) -> None:
        messagebox.showinfo(APP_NAME, "Добро пожаловать!\n\nСначала укажите в «Настройках», где искать вакансии: "
                                      "ссылку hh.ru (из «Мои резюме» → «N подходящих вакансий») или поисковый "
                                      "запрос, — и нажмите «Сохранить».")
        self.show_page("settings")

    def reset_login(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning(APP_NAME, "Сначала остановите текущую работу.")
            return
        profile = self.root_dir / self.cfg.browser.profile_dir
        if not messagebox.askyesno(APP_NAME, "Выйти из аккаунта hh?\n\nСохранённый вход удалится, "
                                             "при следующем запуске нужно будет войти заново.",
                                   yes="Выйти из аккаунта", no="Отмена", danger=True):
            return
        shutil.rmtree(profile, ignore_errors=True)
        self.toast("Вход сброшен")

    # ---------- обновление из релизов GitHub ----------

    def check_updates(self, manual: bool = False) -> None:
        """Есть ли новая версия на GitHub — в фоне; ответ придёт в _poll событием update_found."""
        if not manual:  # пока программа открыта — заглядываем раз в 12 часов
            self.after(12 * 3600 * 1000, self.check_updates)
            if not self.prefs.get("check_updates", True):
                return
        if self.update_busy:
            return

        def work():
            try:
                self.events.put(("update_found", updater.check(), "", manual))
            except updater.UpdateError as e:
                self.events.put(("update_found", None, str(e), manual))

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _on_update_found(self, release: updater.Release | None, error: str, manual: bool) -> None:
        if release is not None and not self.update_busy and self.update_root is None:
            self.update_release = release
            self.update_btn.configure(text=f"Обновить до {release.version}", image=self._icon_download,
                                      state="normal")
            self.update_btn.pack(fill="x", padx=22, pady=(0, 10), before=self._about)
        if not manual:
            return
        if error:
            messagebox.showwarning(APP_NAME, f"Не получилось проверить обновления: {error}")
        elif release is None:
            messagebox.showinfo(APP_NAME, f"У вас последняя версия\n\nСейчас установлена {APP_VERSION} — "
                                          "новее на GitHub пока нет.", kind="success")
        else:
            self._update_clicked()

    def _update_clicked(self) -> None:
        release = self.update_release
        if release is None or self.update_busy:
            return
        if self.update_root is not None:
            self._restart_into_update()
            return
        reason = updater.can_install()
        if reason:
            hint = ("\n\nСкачайте архив со страницы релиза и распакуйте его поверх старой папки." if is_frozen()
                    else "")
            if messagebox.askyesno(APP_NAME, f"Обновить прямо из приложения не получится: {reason}.{hint}",
                                   title=f"Вышла версия {release.version}", yes="Открыть страницу релиза",
                                   no="Позже", kind="update"):
                webbrowser.open(release.page)
            return
        if not messagebox.ask_update(release.version, APP_VERSION, release.notes, release.size / 1024 / 1024,
                                     release.page):
            return
        self.update_busy = True
        self.update_btn.configure(state="disabled", text="Скачиваю…")
        self.update_progress.set(0)
        self.update_progress.pack(fill="x", padx=24, pady=(0, 10), before=self._about)

        def work():
            try:
                root = updater.download(release, lambda done, total: self.events.put(("update_progress", done, total)))
                self.events.put(("update_ready", root, ""))
            except updater.UpdateError as e:
                self.events.put(("update_ready", None, str(e)))

        threading.Thread(target=work, name="update-download", daemon=True).start()

    def _on_update_progress(self, done: int, total: int) -> None:
        text = f"{done * 100 // total}%" if total else f"{done / 1024 / 1024:.0f} МБ"
        self.update_btn.configure(text=f"Скачиваю… {text}")
        if total:
            self.update_progress.set(done / total)

    def _on_update_ready(self, root: Path | None, error: str) -> None:
        self.update_busy = False
        self.update_progress.pack_forget()
        if root is None:
            self.update_btn.configure(state="normal", text=f"Обновить до {self.update_release.version}")
            messagebox.showerror(APP_NAME, f"Обновление не скачалось: {error}")
            return
        self.update_root = root
        self.update_btn.configure(state="normal", text="Перезапустить и обновить", image=self._icon_restart)
        if messagebox.askyesno(APP_NAME, "Приложение закроется на несколько секунд и откроется уже новой версией.",
                               title=f"Версия {self.update_release.version} скачана и проверена",
                               yes="Перезапустить сейчас", no="Позже", kind="update"):
            self._restart_into_update()

    def _restart_into_update(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning(APP_NAME, "Идёт работа. Дождитесь конца или остановите её — после этого нажмите "
                                             "«Перезапустить и обновить».")
            return
        try:
            updater.launch(self.update_root)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Не удалось запустить обновление: {e}")
            return
        self.destroy()  # новая версия ждёт, пока это окно закроется, и ставит себя на место старой

    # ---------- расписание ----------

    SCHEDULE_CATCH_UP = 3 * 3600  # запуск, отложенный из-за другой работы, догоняем в течение 3 часов

    def _schedule_tick(self) -> None:
        """Раз в 15 секунд: не пора ли запустить отклики по расписанию ([schedule])."""
        self.after(15_000, self._schedule_tick)
        self._show_next_run()
        if not self.cfg.schedule.enabled or self.closing:
            return
        due = self._due_slot(datetime.now())
        if due is None or (self.worker and self.worker.is_alive()):
            return  # идёт работа — запустим, когда она закончится
        self.schedule_since = due
        self.toast("Запуск по расписанию")
        self.start_responses(False, scheduled=True)

    def _due_slot(self, now: datetime) -> datetime | None:
        """Самое позднее время по расписанию между schedule_since и сейчас (не старше SCHEDULE_CATCH_UP)."""
        sched, due = self.cfg.schedule, None
        day = self.schedule_since.date()
        while day <= now.date():
            if str(day.isoweekday()) in sched.days:
                for hour, minute in sched.slots():
                    moment = datetime(day.year, day.month, day.day, hour, minute)
                    if self.schedule_since < moment <= now and (now - moment).total_seconds() <= self.SCHEDULE_CATCH_UP:
                        due = moment
            day += timedelta(days=1)
        return due

    def _next_slot(self, now: datetime) -> datetime | None:
        sched = self.cfg.schedule
        for offset in range(8):
            day = (now + timedelta(days=offset)).date()
            if str(day.isoweekday()) not in sched.days:
                continue
            for hour, minute in sched.slots():
                moment = datetime(day.year, day.month, day.day, hour, minute)
                if moment > now:
                    return moment
        return None

    def _show_next_run(self) -> None:
        """Строка в меню слева: когда следующий запуск по расписанию."""
        text = ""
        if self.cfg.schedule.enabled:
            now = datetime.now()
            if self.worker and self.worker.is_alive() and self._due_slot(now):
                text = "Автозапуск — после этой работы"
            else:
                moment = self._next_slot(now)
                if moment is not None:
                    days = (moment.date() - now.date()).days
                    when = ("сегодня" if days == 0 else "завтра" if days == 1
                            else WEEKDAYS[str(moment.isoweekday())].lower())
                    text = f"Автозапуск {when} в {moment:%H:%M}"
        if text and self.schedule_label.cget("text") != f" {text}":
            self.schedule_label.configure(text=f" {text}")
        if bool(text) != bool(self.schedule_label.winfo_manager()):
            if text:
                self.schedule_label.pack(fill="x", padx=22, pady=(0, 12), before=self.status)
            else:
                self.schedule_label.pack_forget()

    def _open_schedule_settings(self) -> None:
        self.show_page("settings")
        self.pages["settings"].show_tab("Расписание")

    # ---------- запуск и остановка ----------

    def start_responses(self, dry_run: bool, scheduled: bool = False) -> None:
        page: ResponsesPage = self.pages["responses"]
        cfg = self._config_for_run()
        if cfg is None:
            return
        if scheduled:  # без человека: лимит и прочее — из настроек, а не из полей на странице
            limit, start_page, retry = cfg.limits.max_responses, 1, False
        else:
            try:
                limit = int(page.limit.get()) if page.limit.get().strip() else cfg.limits.max_responses
                start_page = max(int(page.start_page.get() or 1), 1)
            except ValueError:
                messagebox.showerror(APP_NAME, "«Откликов» и «со страницы» должны быть числами")
                return
            retry = bool(page.retry.get())
        opts = RunOptions(dry_run=dry_run, limit=limit, start_page=start_page - 1, retry_skipped=retry)
        self.stats = Stats()
        self.run_limit = limit
        self.chain_chats = scheduled and cfg.schedule.chats_after
        page.update_stats(self.stats, limit)
        title = "Пробный прогон" if dry_run else "Автозапуск" if scheduled else "Отклики"
        self._start("responses", title, "run", lambda log_path: run_responses(cfg, opts, self.root_dir, self.stats,
                                                                               log_path))

    def start_chats(self, dry_run: bool) -> None:
        cfg = self._config_for_run(need_search_url=False)
        if cfg is None:
            return
        self.chat_stats = ChatStats()
        self.pages["chats"].update_stats(self.chat_stats)
        self._start("chats", "Чаты", "chats",
                    lambda log_path: run_chats(cfg, dry_run, self.root_dir, self.chat_stats, log_path))

    def _config_for_run(self, need_search_url: bool = True) -> Config | None:
        try:
            return self.read_config_file(check_search_url=need_search_url)
        except ConfigError as e:
            messagebox.showwarning(APP_NAME, f"Проверьте настройки: {e}")
            self.show_page("settings")
            return None

    def _start(self, kind: str, title: str, log_name: str, job) -> None:
        if self.worker and self.worker.is_alive():
            return
        control.reset()
        self.running_kind = kind
        self.started_at = time.monotonic()
        self.console.clear()
        self._set_running(True)
        self._set_status(f"●  {title}", ACCENT, "запуск…")

        def work():
            code = EXIT_OK
            try:
                log_path = setup_logging(self.root_dir / "logs", verbose=True, name=log_name,
                                         extra_handlers=(self.log_handler,))
                self.last_log_path = log_path
                log.info(f"{APP_NAME} {APP_VERSION}: {title.lower()} • данные: {self.root_dir}")
                code = job(log_path)
            except Exception:
                log.error("Непредвиденная ошибка:\n" + traceback.format_exc())
                code = 1
            finally:
                self.events.put(("done", kind, code))

        self.worker = threading.Thread(target=work, name="worker", daemon=True)
        self.worker.start()

    def stop(self) -> None:
        if self.worker and self.worker.is_alive():
            control.request_stop()
            self._set_status("●  Останавливаюсь…", AMBER, "дождитесь завершения шага")
            for page in (self.pages["responses"], self.pages["chats"]):
                set_stop_enabled(page.stop_btn, False)

    def _set_running(self, running: bool) -> None:
        self.pages["responses"].set_running(running)
        self.pages["chats"].set_running(running)
        self.pages["ai"].set_running(running)

    def _set_status(self, text: str, color, detail: str = "") -> None:
        self.status.configure(text=text, text_color=color)
        self.status_detail.configure(text=detail)

    def _finish(self, kind: str, code: int) -> None:
        self._set_running(False)
        self.running_kind = None
        self.waiting_captcha = False
        self.summary = load_summary(self.root_dir)
        self.pages["responses"].update_totals(self.summary)
        minutes, seconds = divmod(int(time.monotonic() - self.started_at), 60)
        if kind == "responses":
            summary = f"отправлено {self.stats.applied}, пропущено {sum(self.stats.skipped.values())}, " \
                      f"ошибок {self.stats.failed}"
        elif kind == "resume":
            summary = "резюме загружено" if code == EXIT_OK else "резюме не загружено — см. «Журналы»"
            self.pages["ai"].invalidate()
            self.pages["ai"].refresh()
            self.pages["ai"].view.append(f"  {summary}", "ok" if code == EXIT_OK else "error")
        else:
            summary = (f"прочитано отказов {self.chat_stats.opened}, ответов ИИ {self.chat_stats.answered}, "
                       f"ждут вас {len(self.chat_stats.other_unread)}")
        if code == EXIT_OK:
            self._set_status("●  Готово", GREEN, f"{summary} • {minutes} мин {seconds} с")
        elif code == EXIT_STOPPED:
            self._set_status("●  Остановлено", AMBER, summary)
        else:
            self._set_status("●  Остановлено с ошибкой", RED, "подробности в журнале")
        self.pages["responses"].current.configure(text=f"Последний запуск: {summary}")
        if self.pages["stats"].winfo_ismapped():  # статистику смотрят прямо сейчас — с итогами этого запуска
            self.pages["stats"].refresh()
        if self.closing:
            self.destroy()
            return
        if kind == "responses" and self.chain_chats:  # по расписанию: следом — чаты (если отклики не остановили)
            self.chain_chats = False
            if code != EXIT_STOPPED:
                self.after(3000, lambda: self.start_chats(False))
        self._show_next_run()
        if kind in ("responses", "chats") and not self.prefs.get("notify_asked"):
            title = "Отклики" if kind == "responses" else "Чаты"
            self.after(400, lambda: self._ask_notify(f"{title}: {summary}"))

    def _ask_notify(self, summary: str) -> None:
        """Один раз спрашиваем, можно ли показывать уведомление по итогам. Без согласия ничего не шлём."""
        self.save_prefs(notify_asked=True)
        if self.cfg.notify.desktop or self.cfg.notify.telegram:
            return
        if not messagebox.askyesno(APP_NAME, "Показывать уведомление, когда запуск закончился?\n\n"
                                             "Например: «Отправлено 14, пропущено 161, ошибок 0». "
                                             "Изменить можно в «Настройки» → «Уведомления по итогам».",
                                   yes="Показывать", no="Не нужно", kind="info"):
            return
        try:
            cfg = self.read_config_file(check_search_url=False)
            cfg.notify.desktop = True
            save_config(cfg, self.config_path)
        except ConfigError as e:
            messagebox.showerror(APP_NAME, f"Не сохранено: {e}")
            return
        self.reload_config()
        from . import notify

        threading.Thread(target=lambda: notify.desktop(APP_NAME, summary), daemon=True).start()

    def _poll(self) -> None:
        """Раз в 120 мс (без работы — раз в 250 мс) забирает записи лога и события из рабочего потока."""
        handled = 0
        try:
            for _ in range(400):
                event = self.events.get_nowait()
                handled += 1
                if event[0] == "log":
                    _, levelno, text = event
                    self.console.add(levelno, text)
                    if "▶" in text and self.running_kind == "responses":
                        self.pages["responses"].current.configure(text=text.split("▶", 1)[1].strip())
                    if "показал капчу" in text and self.running_kind:  # ждём человека — видно и в меню слева
                        self.waiting_captcha = True
                        self._set_status("●  Нужна капча", AMBER, "решите её в окне браузера")
                        self.bell()
                    elif "капча пройдена" in text and self.waiting_captcha:
                        self.waiting_captcha = False
                        self._set_status("●  Отклики" if self.running_kind == "responses" else "●  Чаты", ACCENT)
                elif event[0] == "ai_pg":
                    self.pages["ai"].on_result(event[1], event[2])
                elif event[0] == "ai_test":
                    self.pages["settings"]._ai_tested(event[1], event[2])
                elif event[0] == "ai_filter_test":
                    self.pages["settings"]._vacancy_filter_tested(event[1], event[2])
                elif event[0] == "done":
                    self._finish(event[1], event[2])
                elif event[0] == "frame":
                    self._on_frame(event[1])
                elif event[0] == "update_found":
                    self._on_update_found(*event[1:])
                elif event[0] == "update_progress":
                    self._on_update_progress(*event[1:])
                elif event[0] == "update_ready":
                    self._on_update_ready(*event[1:])
        except queue.Empty:
            pass
        if self.running_kind == "responses":
            self.pages["responses"].update_stats(self.stats, self.run_limit)
            self.pages["responses"].update_totals(self.summary, self.stats.applied)
        elif self.running_kind == "chats":
            self.pages["chats"].update_stats(self.chat_stats)
        if self.running_kind and not control.stop_requested():
            minutes, seconds = divmod(int(time.monotonic() - self.started_at), 60)
            what = "решите капчу в окне браузера • " if self.waiting_captcha else "идёт "
            self.status_detail.configure(text=f"{what}{minutes:02d}:{seconds:02d}")
        if self.running_kind and time.monotonic() - self._browser_check > 0.4:
            self._browser_check = time.monotonic()
            self._watch_browser()
        self._poll_count += 1
        if self.mini_mode and self.mini is not None and self._poll_count % 3 == 0:  # мини-окну хватает 3 раз в с
            self.mini.refresh()
        self._frames_wanted = (self.mini.visible and not self.mini.compact) if self.mini_mode and self.mini \
            else self.state() == "normal"
        self.after(120 if self.running_kind or handled else 250, self._poll)  # без работы — реже, незаметно

    # ---------- окно браузера и мини-окно ----------

    def _hwnd(self) -> int | None:
        try:
            return int(self.wm_frame(), 16)
        except (tkinter.TclError, ValueError):
            return None

    def _on_frame(self, jpeg: bytes) -> None:
        self.last_frame = jpeg
        if not self.preview.winfo_manager():
            self.preview.pack(fill="x", padx=22, pady=(0, 14), before=self.status)
        self.preview.set_frame(jpeg)
        if self.mini is not None and not self.mini.compact:
            self.mini.set_frame(jpeg)

    def _watch_browser(self) -> None:
        """Во время работы: спрятанное окно браузера открыли значком на панели задач — вывести на экран."""
        if browser_window.opened_by_user():
            browser_window.show(near=self._hwnd())
        hwnd = browser_window.find()
        self.preview.set_state(browser_window.is_hidden(hwnd) if hwnd else None)

    def show_browser(self) -> None:
        if browser_window.find():
            browser_window.show(near=self._hwnd())
            self.preview.set_state(False)
        else:
            self.toast("Браузер сейчас не открыт")

    def toggle_browser(self) -> None:
        hwnd = browser_window.find()
        if not hwnd:
            self.toast("Браузер сейчас не открыт")
            return
        if browser_window.is_hidden(hwnd):
            browser_window.show(near=self._hwnd())
        else:
            browser_window.hide()
        self.preview.set_state(browser_window.is_hidden(hwnd))

    FULL_ALPHA = 0.999  # непрозрачность окна после плавного появления (1.0 Tk перерисовал бы окно ещё раз)
    REVEAL_STEPS = 9  # шаги появления окна по 14 мс

    def _ensure_mini(self) -> MiniWindow:
        if self.mini is None or not self.mini.winfo_exists():
            self.mini = MiniWindow(self)
        return self.mini

    def to_mini(self) -> None:
        """Окно программы прячется, в углу экрана — мини-окно с состоянием работы."""
        if self.mini_mode:
            return
        self.mini_mode = True
        mini = self._ensure_mini()
        if self.last_frame and mini._photo is None:
            mini.set_frame(self.last_frame)
        mini.show()
        self.withdraw()

    def from_mini(self) -> None:
        """Обратно в окно программы: мини-окно растворяется, окно программы плавно проявляется."""
        self.mini_mode = False
        if self.mini is not None and self.mini.visible:
            self.mini.hide()
        self._reveal()

    def _reveal(self) -> None:
        """Показать окно программы невидимым, дать ему дорисоваться целиком и только потом плавно проявить —
        иначе сотни элементов появлялись на глазах по кускам («пролагивает» при разворачивании)."""
        try:
            self.attributes("-alpha", 0.0)
        except tkinter.TclError:
            pass
        self.deiconify()
        if self.state() != "normal":  # после «свернуть → мини-окно» Windows помнит окно свёрнутым
            self.state("normal")
        self.update_idletasks()
        flush_paint(self)
        self.lift()
        self.focus_force()
        target = self.FULL_ALPHA

        def step(i: int) -> None:
            t = i / self.REVEAL_STEPS
            try:
                self.attributes("-alpha", target * t * t * (3 - 2 * t))
            except tkinter.TclError:
                return
            if i < self.REVEAL_STEPS:
                self.after(14, step, i + 1)

        step(1)

    def _on_unmap(self, event) -> None:
        """Свернули окно, пока идёт работа — вместо панели задач мини-окно в углу (если так настроено)."""
        if (event.widget is self and not self.mini_mode and self.running_kind
                and self.prefs.get("mini_on_minimize", True) and self.state() == "iconic"):
            self.after(10, self._minimize_to_mini)

    def _minimize_to_mini(self) -> None:
        """Свёрнутое окно Windows держит в стороне (-32000); если спрятать его прямо таким, при разворачивании
        Windows считает, что окно переезжает на монитор с другим масштабом, и растягивает рамку в 2,5 раза —
        «после разворачивания всё багается». Поэтому сначала невидимо возвращаем обычное окно, потом прячем."""
        if self.mini_mode or self.state() != "iconic":
            return
        try:
            self.attributes("-alpha", 0.0)
        except tkinter.TclError:
            pass
        self.state("normal")
        self.update_idletasks()
        self.to_mini()

    def _ctrl_key(self, event) -> None:
        if event.keycode == 77 or (event.keysym or "").lower() in ("m", "cyrillic_softsign"):  # Ctrl+M (Ctrl+Ь)
            self.from_mini() if self.mini_mode else self.to_mini()

    def report_callback_exception(self, exc, value, tb) -> None:
        """Ошибка в окне программы: в logs/crash.log, человеку — одно сообщение за сеанс; работа продолжается."""
        path = record_crash(self.root_dir, "ошибка в окне программы", exc, value, tb)
        if not self._crash_shown:
            self._crash_shown = True
            messagebox.showerror(APP_NAME, f"В окне программы произошла ошибка: {value}\n\nРабота продолжается. "
                                           f"Подробности — в {path}; если повторится, пришлите этот файл.")

    def toast(self, text: str) -> None:
        self._set_status(f"●  {text}", GREEN)
        self.after(4000, lambda: self.running_kind or self._set_status("●  Готово", MUTED))

    def on_close(self) -> None:
        if self.mini_mode:
            self.from_mini()
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "Идёт работа. Остановить и закрыть приложение?\n\nТекущий "
                                                 "шаг доделается, итоги сохранятся.", yes="Остановить и закрыть",
                                       no="Продолжить работу", kind="warning"):
                return
            self.closing = True
            self.stop()
            # если шаг завис — через 25 с закрываемся принудительно
            self.after(25_000, force_exit)
            return
        self.destroy()


def force_exit() -> None:
    """Жёсткий выход, когда рабочий шаг завис. os._exit пропускает уборку — поэтому сами закрываем
    браузер с драйвером Playwright (node.exe → chrome) и Ollama, если её запускали мы."""
    try:
        shutdown_ollama()
        if sys.platform == "win32":
            for pid in _child_pids({"node.exe", "ollama.exe"}):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:  # macOS / Linux: драйвер node и его браузер — всё дерево наших дочерних процессов
            _kill_tree_posix(os.getpid(), include_self=False)
    finally:
        os._exit(0)


def _kill_tree_posix(pid: int, include_self: bool = True) -> None:
    import signal

    try:
        children = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    except OSError:
        children = []
    for child in children:
        _kill_tree_posix(int(child))
    if include_self:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _child_pids(names: set[str]) -> list[int]:
    """PID прямых дочерних процессов с такими именами (Windows; Excel и проводник, открытые из приложения, не трогаем)."""
    if sys.platform != "win32":
        return []
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return []
    me, found = os.getpid(), []
    entry = Entry()
    entry.dwSize = ctypes.sizeof(Entry)
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.th32ParentProcessID == me and entry.szExeFile.lower() in names:
                found.append(entry.th32ProcessID)
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(wintypes.HANDLE(snapshot))
    return found


def self_test() -> int:
    """HH-Auto-Response.exe --self-test: запускает драйвер Playwright и браузер без окна, итог — в logs/self_test.txt."""
    from playwright.sync_api import sync_playwright

    out = app_root() / "logs" / "self_test.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{APP_NAME} {APP_VERSION}, данные: {app_root()}"]
    code = 1
    try:
        with sync_playwright() as pw:
            for channel in ("chrome", "msedge", ""):  # "" — встроенный Chromium Playwright (Linux, Docker)
                try:
                    browser = pw.chromium.launch(channel=channel or None, headless=True)
                    page = browser.new_page()
                    page.set_content("<title>ok</title>")
                    lines.append(f"{channel or 'chromium'}: OK (версия {browser.version}, страница: {page.title()})")
                    browser.close()
                    code = 0
                except Exception as e:
                    lines.append(f"{channel or 'chromium'}: нет ({(str(e).splitlines() or [''])[0]})")
    except Exception:
        lines.append("драйвер Playwright не запустился:\n" + traceback.format_exc())
    try:  # Excel-отчёт (openpyxl внутри сборки)
        from .report import build_workbook
        from .results import Summary

        build_workbook([], Summary()).save(out.with_name("self_test.xlsx"))
        lines.append("Excel-отчёт: OK")
    except Exception as e:
        lines.append(f"Excel-отчёт: ОШИБКА ({e})")
        code = 1
    lines.append("ИТОГ: " + ("всё работает" if code == 0 else "ОШИБКА — см. строки выше"))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code


def main() -> None:
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    if "--apply-update" in sys.argv:  # запущены из скачанного обновления — ставим себя на место старой версии
        sys.exit(updater.run_apply(sys.argv))
    if sys.platform == "win32":
        try:  # своя иконка на панели задач вместо иконки Python
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HH-Auto-Response")
        except Exception:
            pass
    try:
        app = App()
    except Exception:  # окно не открылось (например, «... does not exist») — объяснить и сохранить подробности
        path = record_crash(app_root(), "программа не запустилась", *sys.exc_info())
        _native_error(f"Программа не запустилась: {sys.exc_info()[1]}\n\nЗапустите её ещё раз. Если повторится — "
                      f"пришлите файл {path}")
        sys.exit(1)
    app.mainloop()


def _quiet_timers() -> None:
    """Таймеры customtkinter будят программу десятки раз в секунду, даже когда она ничего не делает:
    смена темы Windows — 33 раза в секунду, масштаб монитора — 10, полосы прокрутки всех многострочных полей,
    даже спрятанных, — по 5 на каждое. Реже — незаметно для глаза, а процессор простаивает."""
    from customtkinter.windows.widgets.appearance_mode.appearance_mode_tracker import AppearanceModeTracker
    from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker

    AppearanceModeTracker.update_loop_interval = 250
    ScalingTracker.update_loop_interval = 300
    if getattr(ctk.CTkTextbox, "_hh_quiet", False):
        return
    original = ctk.CTkTextbox._check_if_scrollbars_needed

    def check(self, event=None, continue_loop: bool = False):
        if continue_loop and event is None:
            try:
                hidden = not self.winfo_ismapped()
            except tkinter.TclError:
                return
            if hidden:  # спрятанному полю полосы прокрутки не нужны — проверим, когда покажут
                self.after(self._scrollbar_update_time, lambda: self._check_if_scrollbars_needed(continue_loop=True))
                return
        original(self, event, continue_loop)

    ctk.CTkTextbox._check_if_scrollbars_needed = check
    ctk.CTkTextbox._hh_quiet = True


def record_crash(root: Path, title: str, exc, value, tb) -> Path:
    """Сбой — в logs/crash.log (дописывается): когда, версия, что случилось, полная трассировка."""
    path = root / "logs" / "crash.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(f"===== {datetime.now():%Y-%m-%d %H:%M:%S} {APP_NAME} {APP_VERSION}: {title}\n")
            file.write("".join(traceback.format_exception(exc, value, tb)) + "\n")
    except OSError:
        pass
    return path


def _native_error(text: str) -> None:
    """Сообщение без Tk (он мог и не запуститься)."""
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10)
            return
        except Exception:
            pass
    print(text, file=sys.stderr)

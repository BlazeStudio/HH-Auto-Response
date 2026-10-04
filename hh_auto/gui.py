"""Оконное приложение HH-Auto-Response: отклики, отказы в чатах, настройки и журналы — без консоли.

Работа с hh идёт в отдельном потоке (Playwright), окно только показывает лог и счётчики.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from . import control
from .app import EXIT_OK, EXIT_STOPPED, fetch_resume, run_chats, run_responses
from .chats import ChatStats
from .config import (AI_PROVIDERS, AREAS, EXPERIENCE, WORK_FORMAT, Config, ConfigError, load_config, save_config,
                     validate_config, validate_search_source)
from .ai import AiClient, AiError, shutdown_ollama
from .ai_playground import ROBOT, SAMPLE_FORM, SAMPLES, Playground, fill_sample_form
from .results import HH_DAILY_LIMIT, Summary, load_summary
from .search import describe_search
from .logger import SUCCESS, log, setup_logging
from .paths import app_root, bundled
from .runner import RunOptions, Stats

APP_NAME = "HH-Auto-Response"
APP_VERSION = "1.0.2-beta"

# --- палитра: (светлая тема, тёмная тема) ---
ACCENT = ("#2563EB", "#3B82F6")
ACCENT_HOVER = ("#1D4ED8", "#2563EB")
ACCENT_SOFT = ("#DBEAFE", "#1E3A5F")
SIDEBAR_BG = ("#EDF0F5", "#131519")
MAIN_BG = ("#F6F7FB", "#1A1C21")
CARD_BG = ("#FFFFFF", "#23262D")
BORDER = ("#E3E6EC", "#2F333B")
TEXT = ("#111827", "#F3F4F6")
MUTED = ("#6B7280", "#9CA3AF")
GREEN = ("#16A34A", "#22C55E")
AMBER = ("#D97706", "#F59E0B")
RED = ("#DC2626", "#EF4444")
RED_HOVER = ("#B91C1C", "#DC2626")

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
        for text, command in (("Папка логов", self.open_folder), ("Файл лога", self.open_log), ("Очистить", self.clear)):
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
        self.subtitle = ctk.CTkLabel(self, text=subtitle, text_color=MUTED, font=ctk.CTkFont(size=13),
                                     justify="left", anchor="w", wraplength=700)
        self.subtitle.pack(fill="x", pady=(2, 14))
        self.bind("<Configure>", self._rewrap)

    def _rewrap(self, event) -> None:
        """Перенос подзаголовка по фактической ширине страницы (с учётом масштаба Windows)."""
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        width = max(int(event.width / scaling) - 16, 300)
        if self.subtitle.cget("wraplength") != width:
            self.subtitle.configure(wraplength=width)


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
        if value == self.progress.get() and value:
            return
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
        ("search", "query", "Поисковый запрос (режим «По запросу»)", "str",
         "Как в строке поиска hh: python разработчик, backend OR бэкенд"),
        ("search", "area", "Регион (режим «По запросу»)", "choice", ""),
        ("search", "title_only", "Искать запрос только в названии вакансии", "bool", ""),
        ("", "resume_url", "Ссылка на резюме (необязательно)", "str",
         "Если пусто — ID резюме берётся из параметра resume= в ссылке поиска"),
        ("", "skip_on_resume_mismatch", "Пропускать вакансию, если hh подставил другое резюме", "bool", ""),
    ]),
    ("Лимиты и паузы", [
        ("limits", "max_responses", "Откликов за запуск", "int", "У hh лимит — около 200 откликов в сутки"),
        ("limits", "max_pages", "Страниц выдачи", "int", ""),
        ("limits", "delay_min", "Пауза между вакансиями: от, сек", "float", ""),
        ("limits", "delay_max", "Пауза между вакансиями: до, сек", "float", ""),
    ]),
    ("Фильтры hh: опыт и формат работы", [
        ("filters", "experience", "Опыт работы", "multi",
         "Ничего не отмечено — как в ссылке поиска. Отмеченное заменяет опыт в ссылке"),
        ("filters", "work_format", "Формат работы", "multi",
         "Ничего не отмечено — как в ссылке поиска. Отмеченное заменяет формат в ссылке"),
    ]),
    ("Фильтры по словам", [
        ("filters", "include_words", "Откликаться ТОЛЬКО на вакансии, где есть хотя бы одно слово", "list",
         "Через запятую, регистр не важен. Пусто — на все вакансии. Например: python, backend, django"),
        ("filters", "include_in_snippet", "Искать эти слова и в описании из карточки, не только в названии", "bool", ""),
        ("filters", "exclude_title_words", "Пропускать вакансии, в названии которых есть", "list",
         "Через запятую, регистр не важен. Например: преподаватель, куратор"),
        ("filters", "exclude_company_words", "Пропускать компании, в названии которых есть", "list",
         "Через запятую, регистр не важен. Например: сбер, тинькофф"),
    ]),
    ("Сопроводительное письмо", [
        ("letter", "mode", "Как писать письмо", "choice",
         "«Генерировать» требует подписку hh. Без подписки — «Готовый текст» или «Без письма»"),
        ("letter", "template_text", "Готовый текст письма", "text",
         "Для режима «Готовый текст». {vacancy} и {company} заменятся на название вакансии и компанию"),
        ("letter", "generate_timeout", "Ждать генерацию письма, сек", "float", ""),
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
        ("ai", "base_url", "Адрес API (только для «Другой сервис»)", "str", "Например: https://api.example.com/v1"),
        ("ai", "context", "Что ещё знать о вас, кроме резюме", "text",
         "Зарплатные ожидания, формат работы, город, когда готовы выйти, готовность к переезду и т.п."),
        ("ai", "only_robot", "Отвечать только «Роботу-рекрутеру» (живым людям отвечаете вы)", "bool", ""),
        ("ai", "max_answer_chars", "Максимальная длина ответа, символов", "int", ""),
    ]),
    ("Браузер и сеть", [
        ("browser", "channel", "Браузер", "choice", ""),
        ("geo", "proxy", "Российский прокси (если вы не в РФ или с VPN)", "str",
         "Формат: http://user:pass@host:port. Пусто — прямое подключение"),
        ("geo", "check_ip", "Показывать страну IP перед стартом", "bool", ""),
        ("geo", "require_ru_ip", "Не запускаться с нероссийского IP", "bool", ""),
    ]),
]
# Варианты для полей с несколькими галочками: значение в config → подпись
MULTI = {("filters", "experience"): EXPERIENCE, ("filters", "work_format"): WORK_FORMAT}

# Варианты для полей-списков выбора: подпись → значение в config
CHOICES = {
    ("ai", "provider"): {
        "OpenRouter — есть бесплатные модели": "openrouter",
        "DeepSeek API — платно, но копейки": "deepseek",
        "Ollama — на вашем компьютере, бесплатно (qwen3:8b)": "ollama",
        "Другой сервис (OpenAI-совместимый)": "custom",
    },
    ("search", "mode"): {"По ссылке (подходящие к резюме)": "resume", "По запросу (общий поиск hh)": "query"},
    ("search", "area"): {label: code for code, label in AREAS.items()},
    ("browser", "channel"): {"Google Chrome": "chrome", "Microsoft Edge": "msedge"},
    ("letter", "mode"): {"Генерировать кнопкой hh (подписка)": "generate", "Готовый текст": "template",
                         "Без письма": "none"},
}


class SettingsPage(Page):
    def __init__(self, master, app: "App"):
        super().__init__(master, "Настройки", f"Сохраняются в {app.config_path}")
        self.app = app
        self.widgets: dict[tuple[str, str], tuple[str, object]] = {}

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True)
        for section_title, items in SETTINGS:
            card = Card(scroll)
            card.pack(fill="x", pady=(0, 12), padx=(0, 6))
            ctk.CTkLabel(card, text=section_title, font=ctk.CTkFont(size=15, weight="bold")).pack(
                anchor="w", padx=16, pady=(12, 4))
            for section, key, label, kind, hint in items:
                self._add_field(card, section, key, label, kind, hint)
            if section_title == "ИИ-ответы в чатах":
                row = ctk.CTkFrame(card, fg_color="transparent")
                row.pack(fill="x", padx=16, pady=(8, 0))
                self.ai_test_btn = _secondary_button(row, "Проверить подключение к ИИ", self.test_ai, width=240, height=32)
                self.ai_test_btn.pack(side="left")
                ctk.CTkLabel(row, text="Где взять ключ: OpenRouter — openrouter.ai → Keys; DeepSeek — "
                                       "platform.deepseek.com → API keys; Ollama — ключ не нужен",
                             text_color=MUTED, font=ctk.CTkFont(size=12), anchor="w", justify="left",
                             wraplength=480).pack(side="left", padx=12)
            ctk.CTkFrame(card, height=8, fg_color="transparent").pack()

        danger = Card(scroll)
        danger.pack(fill="x", pady=(0, 12), padx=(0, 6))
        ctk.CTkLabel(danger, text="Данные приложения", font=ctk.CTkFont(size=15, weight="bold")).pack(
            anchor="w", padx=16, pady=(12, 4))
        ctk.CTkLabel(danger, text=f"Папка: {app.root_dir}", text_color=MUTED).pack(anchor="w", padx=16)
        row = ctk.CTkFrame(danger, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(8, 14))
        _secondary_button(row, "Открыть папку", lambda: open_path(app.root_dir), width=150, height=32).pack(side="left")
        ctk.CTkButton(row, text="Выйти из аккаунта hh (сбросить вход)", height=32, fg_color="transparent",
                      border_width=1, border_color=RED, text_color=RED, hover_color=ACCENT_SOFT,
                      command=app.reset_login).pack(side="left", padx=10)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", pady=(10, 0))
        _primary_button(footer, "Сохранить", self.save, width=150).pack(side="left")
        _secondary_button(footer, "Отменить изменения", self.load, width=180).pack(side="left", padx=10)
        _secondary_button(footer, "Открыть config.toml", lambda: open_path(app.config_path), width=180).pack(side="right")

    def _add_field(self, card, section, key, label, kind, hint) -> None:
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=4)
        if kind == "bool":
            widget = ctk.CTkSwitch(row, text=label)
            widget.pack(anchor="w")
        else:
            ctk.CTkLabel(row, text=label, anchor="w").pack(anchor="w")
            if kind == "text":
                widget = ctk.CTkTextbox(row, height=70, border_width=1, border_color=BORDER)
                widget.pack(fill="x")
            elif kind == "multi":
                box = ctk.CTkFrame(row, fg_color="transparent")
                box.pack(anchor="w", pady=(2, 0))
                widget = {value: ctk.CTkCheckBox(box, text=label, checkbox_width=20, checkbox_height=20)
                          for value, label in MULTI[(section, key)].items()}
                for i, checkbox in enumerate(widget.values()):
                    checkbox.grid(row=0, column=i, padx=(0, 18), sticky="w")
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
        if hint:
            ctk.CTkLabel(row, text=hint, text_color=MUTED, font=ctk.CTkFont(size=12), anchor="w",
                         justify="left", wraplength=760).pack(anchor="w")
        self.widgets[(section, key)] = (kind, widget)

    def load(self) -> None:
        cfg = self.app.cfg
        for (section, key), (kind, widget) in self.widgets.items():
            value = getattr(getattr(cfg, section) if section else cfg, key)
            if kind == "bool":
                widget.select() if value else widget.deselect()
            elif kind == "text":
                widget.delete("1.0", "end")
                widget.insert("1.0", value)
            elif kind == "multi":
                for item, checkbox in widget.items():
                    checkbox.select() if item in value else checkbox.deselect()
            elif kind == "choice":
                options = CHOICES[(section, key)]
                if value not in options.values():  # например, регион, которого нет в списке
                    options[f"{value}"] = value
                    widget.configure(values=list(options))
                widget.set(next((k for k, v in options.items() if v == value), next(iter(options))))
            else:
                widget.delete(0, "end")
                widget.insert(0, ", ".join(value) if kind == "list" else str(value))

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
                    value = [item for item, checkbox in widget.items() if checkbox.get()]
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
            messagebox.showerror(APP_NAME, f"Неверное число в поле «{self._label(section, key)}»")
            return None
        except ConfigError as e:
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

    def _ai_tested(self, ok: bool, text: str) -> None:
        self.ai_test_btn.configure(state="normal", text="Проверить подключение к ИИ")
        (messagebox.showinfo if ok else messagebox.showerror)(APP_NAME, text)

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

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.files = ctk.CTkScrollableFrame(body, width=250, fg_color=CARD_BG, corner_radius=12,
                                            border_width=1, border_color=BORDER)
        self.files.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        self.view = LogView(body)
        self.view.grid(row=0, column=1, sticky="nsew")
        self.buttons: list[ctk.CTkButton] = []

    def refresh(self) -> None:
        for button in self.buttons:
            button.destroy()
        self.buttons.clear()
        logs = sorted((self.app.root_dir / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not logs:
            self.view.set_lines(["Запусков ещё не было."])
            return
        for path in logs[:200]:
            kind = "Чаты" if path.name.startswith("chats_") else "Отклики"
            when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m %H:%M")
            button = ctk.CTkButton(self.files, text=f"{kind}   {when}", anchor="w", height=30, fg_color="transparent",
                                   text_color=TEXT, hover_color=ACCENT_SOFT, command=lambda p=path: self.show(p))
            button.pack(fill="x", pady=1)
            self.buttons.append(button)
        self.show(logs[0])

    def show(self, path: Path) -> None:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-5000:]
        except OSError as e:
            lines = [f"Не удалось прочитать {path}: {e}"]
        self.view.set_lines(lines)


# ─────────────────────────────── приложение ───────────────────────────────


class App(ctk.CTk):
    def __init__(self):
        self.root_dir = app_root()
        self.config_path = self.root_dir / "config.toml"
        self.prefs_path = self.root_dir / "app_prefs.json"
        self.prefs = self._load_prefs()
        ctk.set_appearance_mode(self.prefs.get("appearance", "system"))
        ctk.set_default_color_theme("blue")
        super().__init__(fg_color=MAIN_BG)

        self.title(f"{APP_NAME} — автоотклики hh.ru")
        self.geometry("1180x800")
        self.minsize(1000, 680)
        icon = bundled("packaging/icon.ico")
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except Exception:
                pass

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
        self.pages["responses"].update_totals(self.summary)
        try:
            validate_search_source(self.cfg)
        except ConfigError:
            self.after(400, self._first_run_hint)

    # ---------- построение окна ----------

    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self, width=230, corner_radius=0, fg_color=SIDEBAR_BG)
        bar.grid(row=0, column=0, sticky="nsw")
        bar.grid_propagate(False)
        ctk.CTkLabel(bar, text=APP_NAME, font=ctk.CTkFont(size=21, weight="bold"), text_color=ACCENT).pack(
            anchor="w", padx=22, pady=(26, 0))
        ctk.CTkLabel(bar, text="автоотклики на hh.ru", text_color=MUTED).pack(anchor="w", padx=22, pady=(0, 22))

        self.nav: dict[str, ctk.CTkButton] = {}
        for key, text in (("responses", "➤   Отклики"), ("chats", "✉   Чаты"), ("ai", "✦   Проверка ИИ"),
                          ("settings", "⚙   Настройки"), ("journals", "☰   Журналы")):
            button = ctk.CTkButton(bar, text=text, anchor="w", height=42, corner_radius=10, fg_color="transparent",
                                   text_color=TEXT, hover_color=ACCENT_SOFT, font=ctk.CTkFont(size=15),
                                   command=lambda k=key: self.show_page(k))
            button.pack(fill="x", padx=12, pady=2)
            self.nav[key] = button

        spacer = ctk.CTkFrame(bar, fg_color="transparent")
        spacer.pack(fill="both", expand=True)

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
        ctk.CTkLabel(bar, text=f"версия {APP_VERSION}", text_color=MUTED, font=ctk.CTkFont(size=11)).pack(
            anchor="w", padx=22, pady=(0, 16))

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
        }
        self.console = Console(main, self)
        self.main = main

    def show_page(self, key: str) -> None:
        for name, page in self.pages.items():
            page.grid_forget()
            self.nav[name].configure(fg_color=ACCENT_SOFT if name == key else "transparent")
        page = self.pages[key]
        if key in ("responses", "chats"):
            page.grid(row=0, column=0, sticky="new")
            self.console.grid(row=1, column=0, sticky="nsew", pady=(18, 0))
        else:
            self.console.grid_forget()
            page.grid(row=0, column=0, rowspan=2, sticky="nsew")
        if key == "settings":
            page.load()
        elif key in ("journals", "ai"):
            page.refresh()

    # ---------- настройки ----------

    def read_config_file(self, check_search_url: bool = True) -> Config:
        if not self.config_path.exists():
            if check_search_url:
                raise ConfigError("не указана ссылка поиска — откройте «Настройки»")
            return Config(search_url="")
        return load_config(self.config_path, check_search_url=check_search_url)

    def reload_config(self) -> None:
        try:
            self.cfg = self.read_config_file(check_search_url=False)
        except ConfigError as e:
            messagebox.showerror(APP_NAME, f"Ошибка в config.toml: {e}")
            return
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
        if not messagebox.askyesno(APP_NAME, "Удалить сохранённый вход в hh.ru?\n\n"
                                             "При следующем запуске нужно будет войти заново."):
            return
        shutil.rmtree(profile, ignore_errors=True)
        self.toast("Вход сброшен")

    # ---------- запуск и остановка ----------

    def start_responses(self, dry_run: bool) -> None:
        page: ResponsesPage = self.pages["responses"]
        cfg = self._config_for_run()
        if cfg is None:
            return
        try:
            limit = int(page.limit.get()) if page.limit.get().strip() else cfg.limits.max_responses
            start_page = max(int(page.start_page.get() or 1), 1)
        except ValueError:
            messagebox.showerror(APP_NAME, "«Откликов» и «со страницы» должны быть числами")
            return
        opts = RunOptions(dry_run=dry_run, limit=limit, start_page=start_page - 1, retry_skipped=bool(page.retry.get()))
        self.stats = Stats()
        self.run_limit = limit
        page.update_stats(self.stats, limit)
        title = "Пробный прогон" if dry_run else "Отклики"
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
        if self.closing:
            self.destroy()

    def _poll(self) -> None:
        """Раз в 120 мс забирает записи лога и события из рабочего потока."""
        try:
            for _ in range(400):
                event = self.events.get_nowait()
                if event[0] == "log":
                    _, levelno, text = event
                    self.console.add(levelno, text)
                    if "▶" in text and self.running_kind == "responses":
                        self.pages["responses"].current.configure(text=text.split("▶", 1)[1].strip())
                elif event[0] == "ai_pg":
                    self.pages["ai"].on_result(event[1], event[2])
                elif event[0] == "ai_test":
                    self.pages["settings"]._ai_tested(event[1], event[2])
                elif event[0] == "done":
                    self._finish(event[1], event[2])
        except queue.Empty:
            pass
        if self.running_kind == "responses":
            self.pages["responses"].update_stats(self.stats, self.run_limit)
            self.pages["responses"].update_totals(self.summary, self.stats.applied)
        elif self.running_kind == "chats":
            self.pages["chats"].update_stats(self.chat_stats)
        if self.running_kind and not control.stop_requested():
            minutes, seconds = divmod(int(time.monotonic() - self.started_at), 60)
            self.status_detail.configure(text=f"идёт {minutes:02d}:{seconds:02d}")
        self.after(120, self._poll)

    def toast(self, text: str) -> None:
        self._set_status(f"●  {text}", GREEN)
        self.after(4000, lambda: self.running_kind or self._set_status("●  Готово", MUTED))

    def on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "Идёт работа. Остановить и закрыть приложение?"):
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
        for pid in _child_pids({"node.exe", "ollama.exe"}):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        os._exit(0)


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
            for channel in ("chrome", "msedge"):
                try:
                    browser = pw.chromium.launch(channel=channel, headless=True)
                    page = browser.new_page()
                    page.set_content("<title>ok</title>")
                    lines.append(f"{channel}: OK (версия {browser.version}, страница: {page.title()})")
                    browser.close()
                    code = 0
                except Exception as e:
                    lines.append(f"{channel}: нет ({(str(e).splitlines() or [''])[0]})")
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
    if sys.platform == "win32":
        try:  # своя иконка на панели задач вместо иконки Python
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HH-Auto-Response")
        except Exception:
            pass
    app = App()
    app.mainloop()

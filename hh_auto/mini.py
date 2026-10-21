"""Превью браузера в меню слева и мини-окно в углу экрана.

Превью — кадр окна браузера раз в полторы секунды, пока идёт работа (браузер при этом спрятан за краем
экрана и не мешает). Мини-окно — маленькое окно поверх остальных: состояние, счётчики, текущая вакансия,
«Начать / Остановить», «Браузер», «Развернуть». Его можно перетащить, место запоминается.
"""

from __future__ import annotations

import io
import sys
import tkinter
from typing import TYPE_CHECKING

import customtkinter as ctk
from PIL import Image, ImageDraw

from . import browser_window
from .theme import (ACCENT, ACCENT_HOVER, ACCENT_SOFT, BORDER, CARD_BG, GREEN, MAIN_BG, MUTED, RED, RED_HOVER,
                    SIDEBAR_BG, TEXT, icon)
from .widgets import PlainFrame, TextLabel

if TYPE_CHECKING:
    from .gui import App


def _thumbnail(jpeg: bytes, size: tuple[int, int], scale: float, radius: int) -> Image.Image:
    """Кадр браузера → картинка нужного размера со скруглёнными углами (углы прозрачные)."""
    width, height = round(size[0] * scale * 2), round(size[1] * scale * 2)  # ×2 — чёткость на любом масштабе
    image = Image.open(io.BytesIO(jpeg))
    image.draft("RGB", (width, height))  # JPEG сразу уменьшенным — в разы быстрее
    image = image.convert("RGB")
    # заполнить рамку, лишнее обрезать по центру (пропорции окна браузера и превью чуть разные)
    ratio = max(width / image.width, height / image.height)
    image = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))), Image.LANCZOS)
    left, top = (image.width - width) // 2, (image.height - height) // 2
    image = image.crop((left, top, left + width, top + height)).convert("RGBA")
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=round(radius * scale * 2), fill=255)
    image.putalpha(mask)
    return image


class BrowserPreview(ctk.CTkFrame):
    """Меню слева: что сейчас в окне браузера и «Показать / Спрятать»."""

    SIZE = (186, 116)

    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        head = PlainFrame(self, bg=SIDEBAR_BG)
        head.pack(fill="x")
        self.caption = TextLabel(head, "Браузер", color=MUTED, size=12, bg=SIDEBAR_BG)
        self.caption.pack(side="left")
        self.toggle = TextLabel(head, "", color=ACCENT, size=12, bg=SIDEBAR_BG, cursor="hand2")
        self.toggle.pack(side="right")
        self.toggle.bind("<Button-1>", lambda _e: app.toggle_browser())
        self.image = ctk.CTkLabel(self, text="", width=self.SIZE[0], height=self.SIZE[1], cursor="hand2")
        self.image.pack(pady=(4, 0))
        self.image.bind("<Button-1>", lambda _e: app.show_browser())
        self._photo = None

    def set_frame(self, jpeg: bytes) -> None:
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        try:
            picture = _thumbnail(jpeg, self.SIZE, scale, 8)
        except Exception:  # битый кадр — оставляем прежний
            return
        self._photo = ctk.CTkImage(light_image=picture, dark_image=picture, size=self.SIZE)
        self.image.configure(image=self._photo)

    def set_state(self, hidden: bool | None) -> None:
        """hidden: True — спрятан, False — на экране, None — браузер закрыт (кадр — последний)."""
        toggle = "" if hidden is None else ("Показать" if hidden else "Спрятать")
        caption = "Браузер · последний кадр" if hidden is None else "Браузер"
        if self.toggle.cget("text") != toggle:
            self.toggle.configure(text=toggle)
        if self.caption.cget("text") != caption:
            self.caption.configure(text=caption)


class MiniWindow(ctk.CTkToplevel):
    """Мини-окно в углу экрана, поверх остальных окон."""

    SIZE = (380, 0)  # высота — по содержимому
    PREVIEW = (128, 80)
    ALPHA = 0.97  # проверки ставят 0 — окно не видно
    MARGIN = 16

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.withdraw()
        self.overrideredirect(True)  # без рамки Windows — своё оформление
        self.attributes("-topmost", True)
        self.configure(fg_color=CARD_BG)
        self._drag: tuple[int, int] | None = None
        self._photo = None
        self._fade_job = None

        body = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=0, border_width=1, border_color=BORDER)
        body.pack(fill="both", expand=True)
        head = PlainFrame(body)
        head.pack(fill="x", padx=12, pady=(8, 0))
        title = TextLabel(head, "HH-Auto-Response", color=ACCENT, size=12, weight="bold")
        title.pack(side="left")
        ctk.CTkButton(head, text="", image=icon("expand", 14, TEXT), width=28, height=24, corner_radius=6,
                      fg_color="transparent", hover_color=ACCENT_SOFT, command=app.from_mini).pack(side="right")

        row = PlainFrame(body)
        row.pack(fill="x", padx=12, pady=(6, 0))
        self.preview = ctk.CTkLabel(row, text="нет кадра", text_color=MUTED, width=self.PREVIEW[0],
                                    height=self.PREVIEW[1], fg_color=MAIN_BG, corner_radius=6, cursor="hand2",
                                    font=ctk.CTkFont(size=11))
        self.preview.pack(side="left", padx=(0, 10))
        self.preview.bind("<Button-1>", lambda _e: app.show_browser())
        column = PlainFrame(row)
        column.pack(side="left", fill="both", expand=True)
        self.status = TextLabel(column, "", size=13, weight="bold")
        self.status.pack(anchor="w")
        self.counts = TextLabel(column, "", color=TEXT, size=12)
        self.counts.pack(anchor="w", pady=(2, 0))
        self.counts2 = TextLabel(column, "", color=MUTED, size=12)
        self.counts2.pack(anchor="w")
        self.current = TextLabel(column, "", color=MUTED, size=11, wraplength=self.SIZE[0] - self.PREVIEW[0] - 40)
        self.current.pack(anchor="w", pady=(2, 0))

        self.progress = ctk.CTkProgressBar(body, height=5, corner_radius=3, progress_color=GREEN)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=12, pady=(8, 0))
        buttons = PlainFrame(body)
        buttons.pack(fill="x", padx=12, pady=(8, 10))
        self.action = ctk.CTkButton(buttons, text="", height=28, width=128, corner_radius=8,
                                    font=ctk.CTkFont(size=12, weight="bold"), command=self._action)
        self.action.pack(side="left")
        self.browser_btn = ctk.CTkButton(buttons, text=" Браузер", image=icon("eye", 14, TEXT), compound="left",
                                         height=28, width=96, corner_radius=8, fg_color="transparent",
                                         border_width=1, border_color=BORDER, text_color=TEXT,
                                         hover_color=ACCENT_SOFT, font=ctk.CTkFont(size=12),
                                         command=app.toggle_browser)
        self.browser_btn.pack(side="left", padx=8)

        for widget in (body, head, title, row, column, self.status, self.counts, self.counts2, self.current, buttons):
            widget.bind("<ButtonPress-1>", self._press, add="+")
            widget.bind("<B1-Motion>", self._move, add="+")
            widget.bind("<ButtonRelease-1>", self._release, add="+")
        self.bind("<Enter>", lambda _e: self._set_alpha(1.0 if self.ALPHA else 0))
        self.bind("<Leave>", lambda _e: self._set_alpha(self.ALPHA))

    # ---------- появление и место ----------

    def appear(self) -> None:
        self.refresh()
        self.update_idletasks()
        scale = ctk.ScalingTracker.get_window_scaling(self)
        width, height = round(self.SIZE[0] * scale), max(round(self.SIZE[1] * scale), self.winfo_reqheight())
        x, y = self._position(width, height)
        tkinter.Toplevel.geometry(self, f"{width}x{height}+{x}+{y}")
        self.attributes("-alpha", 0.0)
        self.deiconify()
        self._round_corners()
        self._fade(0.0, self.ALPHA, on_done=None)

    def _position(self, width: int, height: int) -> tuple[int, int]:
        saved = self.app.prefs.get("mini_pos")
        if isinstance(saved, list) and len(saved) == 2:
            x, y = int(saved[0]), int(saved[1])
            if -width < x < self.winfo_screenwidth() and 0 <= y < self.winfo_screenheight():
                return x, y
        if browser_window.supported():  # правый нижний угол рабочей области монитора, где окно программы
            near = int(self.app.wm_frame(), 16)
            left, top, right, bottom = browser_window._work_area(near)
        else:
            left, top, right, bottom = 0, 0, self.winfo_screenwidth(), self.winfo_screenheight() - 48
        margin = round(self.MARGIN * ctk.ScalingTracker.get_window_scaling(self))
        return right - width - margin, bottom - height - margin

    def _round_corners(self) -> None:
        """Windows 11: скруглённые углы и тень, как у обычных окон."""
        if sys.platform != "win32":
            return
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            value = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def vanish(self) -> None:
        self._fade(self._alpha(), 0.0, on_done=self.destroy)

    def _alpha(self) -> float:
        try:
            return float(self.attributes("-alpha"))
        except tkinter.TclError:
            return 0.0

    def _set_alpha(self, value: float) -> None:
        if self._fade_job is None:
            try:
                self.attributes("-alpha", value)
            except tkinter.TclError:
                pass

    def _fade(self, start: float, end: float, on_done, steps: int = 9) -> None:
        if self._fade_job is not None:
            self.after_cancel(self._fade_job)
            self._fade_job = None

        def step(i: int) -> None:
            self._fade_job = None
            t = i / steps
            value = start + (end - start) * (t * t * (3 - 2 * t))
            try:
                self.attributes("-alpha", value)
            except tkinter.TclError:
                return
            if i < steps:
                self._fade_job = self.after(14, step, i + 1)
            elif on_done:
                on_done()

        step(0)

    # ---------- перетаскивание ----------

    def _press(self, event) -> None:
        self._drag = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _move(self, event) -> None:
        if self._drag is not None:
            tkinter.Toplevel.geometry(self, f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    def _release(self, _event) -> None:
        if self._drag is not None:
            self._drag = None
            self.app.save_prefs(mini_pos=[self.winfo_x(), self.winfo_y()])

    # ---------- содержимое ----------

    def _action(self) -> None:
        if self.app.running_kind:
            self.app.stop()
        else:
            self.app.start_responses(False)

    def set_frame(self, jpeg: bytes) -> None:
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        try:
            picture = _thumbnail(jpeg, self.PREVIEW, scale, 6)
        except Exception:
            return
        self._photo = ctk.CTkImage(light_image=picture, dark_image=picture, size=self.PREVIEW)
        self.preview.configure(image=self._photo, text="")

    def refresh(self) -> None:
        """Состояние из окна программы (раз в 120 мс): меняется только то, что изменилось."""
        app = self.app
        status = app.status.cget("text").replace("●", "").strip() or "Готово"
        color = app.status.cget("text_color")
        detail = app.status_detail.cget("text")
        if app.running_kind == "responses":
            stats = app.stats
            counts = f"Отправлено {stats.applied} из {app.run_limit}"
            counts2 = f"пропущено {sum(stats.skipped.values())}" + (f" · ошибок {stats.failed}" if stats.failed else "")
            current = app.pages["responses"].current.cget("text")
            progress = min(stats.applied / app.run_limit, 1) if app.run_limit else 0
        elif app.running_kind == "chats":
            chats = app.chat_stats
            counts = f"Отказов прочитано {chats.opened}"
            counts2 = f"ответов ИИ {chats.answered}"
            current, progress = detail, 0
        else:
            counts = f"Сегодня откликов: {app.summary.today.applied}"
            counts2 = detail
            current, progress = app.pages["responses"].current.cget("text"), 0
        current = current if len(current) <= 70 else current[:69] + "…"  # не больше двух строк
        for label, text in ((self.status, status), (self.counts, counts), (self.counts2, counts2),
                            (self.current, current)):
            if label.cget("text") != text:
                label.configure(text=text)
        if self.status._color != color:
            self.status.set(color=color)
        if abs(self.progress.get() - progress) > 0.001:
            self.progress.set(progress)
        running = bool(app.running_kind)
        action = "■  Остановить" if running else "▶  Начать отклики"
        if self.action.cget("text") != action:
            self.action.configure(text=action, fg_color=RED if running else ACCENT,
                                  hover_color=RED_HOVER if running else ACCENT_HOVER)
        state = "normal" if browser_window.find() else "disabled"
        if self.browser_btn.cget("state") != state:
            self.browser_btn.configure(state=state)

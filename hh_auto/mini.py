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
from .theme import (ACCENT, ACCENT_FILL, ACCENT_HOVER, ACCENT_SOFT, APP_NAME, BORDER, CARD_BG, FILL, FILL_HOVER, GREEN,
                    MAIN_BG, MUTED, RED_FILL, RED_HOVER, SIDEBAR_BG, TEXT, apply_app_icon, icon)
from .widgets import PlainFrame, QuietToplevel, TextLabel

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


class MiniWindow(QuietToplevel, ctk.CTkToplevel):
    """Мини-окно в углу экрана, поверх остальных окон. Одно на весь сеанс: создаётся заранее и только
    показывается и прячется (раньше создавалось заново при каждом «В уголок» — это и было «долго
    открывается», а обращение к уже закрытому окну роняло программу). Без рамки Windows, но со своей
    кнопкой на панели задач («HH-Auto-Response»): «—» сворачивает его туда, щелчок по кнопке — обратно."""

    SIZE = (380, 0)  # ширина карточки; высота — по содержимому
    PREVIEW = (128, 80)
    ALPHA = 0.97  # проверки ставят 0 — окно не видно
    MARGIN = 16

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.withdraw()
        self.title(APP_NAME)  # так оно называется на панели задач (раньше — «CTkToplevel»)
        apply_app_icon(self)
        self.overrideredirect(True)  # без рамки Windows — своё оформление
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.0)
        # «Закрыть окно» у кнопки на панели задач — закрыть программу (раньше закрывалось одно мини-окно,
        # а программа оставалась работать без окон и падала, обращаясь к нему)
        self.protocol("WM_DELETE_WINDOW", app.on_close)
        self.configure(fg_color=CARD_BG)
        self.visible = False
        self._drag: tuple[int, int, int, int] | None = None
        self._photo = None
        self._fade_job = None
        self._placed = False

        # ---- карточка ----
        self.card = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=0, border_width=1, border_color=BORDER)
        head = PlainFrame(self.card)
        head.pack(fill="x", padx=12, pady=(8, 0))
        title = TextLabel(head, "HH-Auto-Response", color=ACCENT, size=12, weight="bold")
        title.pack(side="left")
        ctk.CTkButton(head, text="", image=icon("expand", 14, TEXT), width=28, height=24, corner_radius=6,
                      fg_color="transparent", hover_color=ACCENT_SOFT, command=app.from_mini).pack(side="right")
        ctk.CTkButton(head, text="—", width=28, height=24, corner_radius=6, fg_color="transparent",
                      hover_color=ACCENT_SOFT, text_color=TEXT, font=ctk.CTkFont(size=13, weight="bold"),
                      command=self.minimize).pack(side="right", padx=(0, 2))
        row = PlainFrame(self.card)
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
        self.progress = ctk.CTkProgressBar(self.card, height=5, corner_radius=3, progress_color=GREEN)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=12, pady=(8, 0))
        buttons = PlainFrame(self.card)
        buttons.pack(fill="x", padx=12, pady=(8, 10))
        self.action = ctk.CTkButton(buttons, text="", height=28, width=128, corner_radius=14,
                                    font=ctk.CTkFont(size=12, weight="bold"), command=self._action)
        self.action.pack(side="left")
        self.browser_btn = ctk.CTkButton(buttons, text=" Браузер", image=icon("eye", 14, TEXT), compound="left",
                                         height=28, width=96, corner_radius=14, fg_color=FILL,
                                         border_width=0, text_color=TEXT, text_color_disabled=MUTED,
                                         hover_color=FILL_HOVER, font=ctk.CTkFont(size=12),
                                         command=app.toggle_browser)
        self.browser_btn.pack(side="left", padx=8)

        for widget in (self.card, head, title, row, column, self.status, self.counts, self.counts2, self.current,
                       buttons):
            widget.bind("<ButtonPress-1>", self._press, add="+")
            widget.bind("<B1-Motion>", self._move, add="+")
            widget.bind("<ButtonRelease-1>", self._release, add="+")
        self.bind("<Enter>", lambda _e: self._set_alpha(1.0 if self.ALPHA else 0))
        self.bind("<Leave>", lambda _e: self._set_alpha(self.ALPHA))
        self.card.pack(fill="both", expand=True)
        self._taskbar_button()

    # ---------- показать и спрятать ----------

    def show(self) -> None:
        self.refresh(force=True)
        self.update_idletasks()
        if not self._placed:
            width, height = self._size()
            x, y = self._position(width, height)
            tkinter.Toplevel.geometry(self, f"{width}x{height}+{x}+{y}")
            self._placed = True
        self.visible = True
        self.deiconify()
        self._round_corners()
        self._fade(self._alpha(), self.ALPHA, on_done=None)

    def hide(self) -> None:
        self.visible = False
        if self.minimized():  # свёрнутое — сначала невидимо вернуть, иначе Windows потом исказит размер
            self._set_alpha_now(0.0)
            self._show_window(9)  # SW_RESTORE

        def gone() -> None:
            if not self.visible:  # пока растворялось, могли снова показать
                self.withdraw()

        self._fade(self._alpha(), 0.0, on_done=gone)

    def _size(self) -> tuple[int, int]:
        scale = ctk.ScalingTracker.get_window_scaling(self)
        return round(self.SIZE[0] * scale), max(round(self.SIZE[1] * scale), self.card.winfo_reqheight())

    # ---------- панель задач ----------

    def _hwnd(self) -> int | None:
        if sys.platform != "win32":
            return None
        import ctypes

        return ctypes.windll.user32.GetParent(self.winfo_id()) or None

    def _taskbar_button(self) -> None:
        """Окну без рамки Tk не даёт кнопки на панели задач — даём (без неё свернуть его было бы некуда)."""
        hwnd = self._hwnd()
        if not hwnd:
            return
        import ctypes

        user32 = ctypes.windll.user32
        self.update_idletasks()
        style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        user32.SetWindowLongW(hwnd, -20, (style & ~0x80) | 0x40000)  # без WS_EX_TOOLWINDOW, с WS_EX_APPWINDOW

    def _show_window(self, command: int) -> None:
        hwnd = self._hwnd()
        if hwnd:
            import ctypes

            ctypes.windll.user32.ShowWindow(hwnd, command)

    def minimize(self) -> None:
        """«—»: на панель задач; щелчок по кнопке «HH-Auto-Response» там — снова мини-окно."""
        if sys.platform == "win32":
            self._show_window(6)  # SW_MINIMIZE (Tk не сворачивает окна без рамки)
        else:
            self.iconify()

    def minimized(self) -> bool:
        hwnd = self._hwnd()
        if not hwnd:
            return self.state() == "iconic"
        import ctypes

        return bool(ctypes.windll.user32.IsIconic(hwnd))

    def _set_alpha_now(self, value: float) -> None:
        if self._fade_job is not None:
            self.after_cancel(self._fade_job)
            self._fade_job = None
        try:
            self.attributes("-alpha", value)
        except tkinter.TclError:
            pass

    def _position(self, width: int, height: int) -> tuple[int, int]:
        saved = self.app.prefs.get("mini_corner")  # правый нижний угол окна — его место не зависит от вида
        if isinstance(saved, list) and len(saved) == 2:
            right, bottom = int(saved[0]), int(saved[1])
            if 0 < right <= self.winfo_screenwidth() + width and 0 < bottom <= self.winfo_screenheight() + height:
                return right - width, bottom - height
        if browser_window.supported():  # правый нижний угол рабочей области монитора, где окно программы
            near = int(self.app.wm_frame(), 16)
            left, top, right, bottom = browser_window._work_area(near)
        else:
            left, top, right, bottom = 0, 0, self.winfo_screenwidth(), self.winfo_screenheight() - 48
        margin = round(self.MARGIN * ctk.ScalingTracker.get_window_scaling(self))
        return right - width - margin, bottom - height - margin

    def _save_position(self) -> None:
        self.app.save_prefs(mini_corner=[self.winfo_x() + self.winfo_width(), self.winfo_y() + self.winfo_height()])

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

    def _alpha(self) -> float:
        try:
            return float(self.attributes("-alpha"))
        except tkinter.TclError:
            return 0.0

    def _set_alpha(self, value: float) -> None:
        if self._fade_job is None and self.visible:
            try:
                self.attributes("-alpha", value)
            except tkinter.TclError:
                pass

    def _fade(self, start: float, end: float, on_done, steps: int = 8) -> None:
        if self._fade_job is not None:
            self.after_cancel(self._fade_job)
            self._fade_job = None

        def step(i: int) -> None:
            self._fade_job = None
            t = i / steps
            try:
                self.attributes("-alpha", start + (end - start) * (t * t * (3 - 2 * t)))
            except tkinter.TclError:
                return
            if i < steps:
                self._fade_job = self.after(14, step, i + 1)
            elif on_done:
                on_done()

        step(0)

    # ---------- перетаскивание и щелчок ----------

    def _press(self, event) -> None:
        self._drag = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y(), event.x_root, event.y_root)

    def _move(self, event) -> None:
        if self._drag is not None:
            tkinter.Toplevel.geometry(self, f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    def _release(self, event) -> None:
        if self._drag is None:
            return
        moved = abs(event.x_root - self._drag[2]) + abs(event.y_root - self._drag[3]) > 4 if event else True
        self._drag = None
        if moved:
            self._save_position()

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

    def refresh(self, force: bool = False) -> None:
        """Состояние из окна программы: меняется только то, что изменилось."""
        if not (self.visible or force) or not self.winfo_exists():
            return
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
            counts, counts2 = f"Отказов прочитано {chats.opened}", f"ответов ИИ {chats.answered}"
            current, progress = detail, 0
        else:
            counts, counts2 = f"Сегодня откликов: {app.summary.today.applied}", detail
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
            self.action.configure(text=action, fg_color=RED_FILL if running else ACCENT_FILL,
                                  hover_color=RED_HOVER if running else ACCENT_HOVER)
        state = "normal" if browser_window.find() else "disabled"
        if self.browser_btn.cget("state") != state:
            self.browser_btn.configure(state=state)

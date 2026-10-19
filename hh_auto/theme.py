"""Оформление окон: палитра, иконка программы в заголовке, значки, нарисованные Pillow.

Значки рисуем сами, а не символами шрифта: «⬇» или «↻» есть не в каждом шрифте, и Windows показывает
вместо них пустой прямоугольник. Рисуем в 4 раза крупнее и уменьшаем — края получаются гладкими.
"""

from __future__ import annotations

import sys
import tkinter
from functools import lru_cache

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

from .paths import bundled

APP_NAME = "HH-Auto-Response"

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
GREEN_HOVER = ("#15803D", "#16A34A")
AMBER = ("#D97706", "#F59E0B")
RED = ("#DC2626", "#EF4444")
RED_HOVER = ("#B91C1C", "#DC2626")

# цвет значка для каждого вида сообщения
KIND_COLOR = {"info": ACCENT, "question": ACCENT, "success": GREEN, "warning": AMBER, "error": RED,
              "update": GREEN}


# ─────────────────────────── иконка программы в заголовке окна ───────────────────────────

def apply_app_icon(window: tkinter.Misc) -> None:
    """Наша иконка в заголовке и на панели задач — вместо пера tkinter и значка customtkinter."""
    try:
        if sys.platform == "win32":
            ico = bundled("packaging/icon.ico")
            if not ico.exists():
                return
            window.iconbitmap(str(ico))  # заодно говорит customtkinter не ставить свою иконку через 200 мс
            window.after(60, lambda: sharp_windows_icon(window))
            window.after(400, lambda: sharp_windows_icon(window))
        else:
            png = bundled("packaging/icon.png")
            if png.exists():
                image = tkinter.PhotoImage(file=str(png))
                window.iconphoto(False, image)
                window._hh_icon = image  # иначе картинку соберёт сборщик мусора
    except (tkinter.TclError, OSError):
        pass


def sharp_windows_icon(window: tkinter.Misc) -> None:
    """Иконки окна из .ico ровно под масштаб экрана: 32 px при 100%, 40 при 125%, 48 при 150%… — все эти размеры
    есть в icon.ico, и Windows ничего не растягивает (Tk сам ставит 32 px, и на 150% выходит мыло)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int,
                                      ctypes.c_int, wintypes.UINT]
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        if not window.winfo_exists():
            return
        hwnd = int(window.wm_frame(), 16)  # настоящее окно Windows вокруг Tk-окна
        dpi = user32.GetDpiForWindow(hwnd) or 96
        path = str(bundled("packaging/icon.ico"))
        for which, base in ((1, 32), (0, 16)):  # ICON_BIG — панель задач и Alt+Tab, ICON_SMALL — заголовок
            size = round(base * dpi / 96)
            icon = user32.LoadImageW(None, path, 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if icon:
                user32.SendMessageW(hwnd, 0x80, which, icon)  # WM_SETICON
    except Exception:
        pass


# ─────────────────────────────────── значки ───────────────────────────────────

_SCALE = 4  # рисуем крупнее и уменьшаем — сглаженные края


def _rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), alpha


@lru_cache(maxsize=None)
def _bold_font(size: int) -> ImageFont.ImageFont:
    for name in ("segoeuib.ttf", "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # старый Pillow
        return ImageFont.load_default()


def _draw(name: str, px: int, fg: str, bg: str | None) -> Image.Image:
    """Значок name размером px×px. bg — цвет круга-подложки (None — без неё), fg — цвет рисунка."""
    s = px * _SCALE
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    w = max(2, round(s * 0.09))  # толщина линий
    if bg is not None:
        d.ellipse((0, 0, s - 1, s - 1), fill=_rgba(bg))
        inset = s * 0.26
    else:
        inset = s * 0.12
    box = (inset, inset, s - inset, s - inset)
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    color = _rgba(fg)
    if name == "download":  # стрелка вниз в лоток
        d.line((cx, y0, cx, y1 - (y1 - y0) * 0.32), fill=color, width=w)
        head = (y1 - y0) * 0.30
        d.line((cx - head, y1 - (y1 - y0) * 0.32 - head, cx, y1 - (y1 - y0) * 0.32), fill=color, width=w,
               joint="curve")
        d.line((cx, y1 - (y1 - y0) * 0.32, cx + head, y1 - (y1 - y0) * 0.32 - head), fill=color, width=w,
               joint="curve")
        d.line((x0, y1 - (y1 - y0) * 0.18, x0, y1, x1, y1, x1, y1 - (y1 - y0) * 0.18), fill=color, width=w,
               joint="curve")
    elif name == "restart":  # круговая стрелка
        d.arc(box, start=-60, end=250, fill=color, width=w)
        tip_x, tip_y = cx + (x1 - x0) / 2 * 0.5, y0 + (y1 - y0) * 0.067
        head = (y1 - y0) * 0.28
        d.polygon([(tip_x + head * 0.55, tip_y), (tip_x - head * 0.5, tip_y - head * 0.62),
                   (tip_x - head * 0.35, tip_y + head * 0.62)], fill=color)
    elif name in ("success", "check"):
        d.line((x0, cy, x0 + (x1 - x0) * 0.38, y1 - (y1 - y0) * 0.12, x1, y0 + (y1 - y0) * 0.12), fill=color,
               width=w, joint="curve")
    elif name == "error":  # крестик
        d.line((x0, y0, x1, y1), fill=color, width=w)
        d.line((x0, y1, x1, y0), fill=color, width=w)
    elif name == "info":  # «i»: точка и черта
        r = w * 0.75
        d.ellipse((cx - r, y0 - r * 0.2, cx + r, y0 + r * 1.8), fill=color)
        d.line((cx, y0 + (y1 - y0) * 0.38, cx, y1), fill=color, width=w)
    elif name == "warning":  # «!»: черта и точка
        r = w * 0.75
        d.line((cx, y0, cx, y1 - (y1 - y0) * 0.32), fill=color, width=w)
        d.ellipse((cx - r, y1 - r * 1.8, cx + r, y1 + r * 0.2), fill=color)
    elif name == "question":
        font = _bold_font(round((y1 - y0) * 1.35))
        d.text((cx, cy + (y1 - y0) * 0.04), "?", font=font, fill=color, anchor="mm")
    elif name == "update":  # стрелка вниз без лотка — на зелёном круге
        d.line((cx, y0, cx, y1), fill=color, width=w)
        head = (y1 - y0) * 0.38
        d.line((cx - head, y1 - head, cx, y1, cx + head, y1 - head), fill=color, width=w, joint="curve")
    return image.resize((px * 2, px * 2), Image.LANCZOS)  # ×2 — запас для экранов с масштабом 150–200%


def icon(name: str, size: int = 18, color: str | tuple[str, str] = "#FFFFFF") -> ctk.CTkImage:
    """Значок без подложки (для кнопок): download, restart, check… color — один цвет или (светлая, тёмная)."""
    light, dark = color if isinstance(color, tuple) else (color, color)
    return ctk.CTkImage(light_image=_draw(name, size, light, None), dark_image=_draw(name, size, dark, None),
                        size=(size, size))


def badge(kind: str, size: int = 44) -> ctk.CTkImage:
    """Круглый значок окна сообщения: info, question, success, warning, error, update."""
    light, dark = KIND_COLOR.get(kind, ACCENT)
    return ctk.CTkImage(light_image=_draw(kind, size, "#FFFFFF", light),
                        dark_image=_draw(kind, size, "#FFFFFF", dark), size=(size, size))

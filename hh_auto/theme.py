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
    elif name in _MENU:
        _MENU[name](d, image, box, w, color)
    return image.resize((px * 2, px * 2), Image.LANCZOS)  # ×2 — запас для экранов с масштабом 150–200%


# --- значки меню слева: тонкие линии, как у системных значков, одинаковые по размеру ---

def _send(d, image, box, w, color) -> None:  # бумажный самолётик
    x0, y0, x1, y1 = box
    size = x1 - x0
    nose, tail_top, tail_bottom = (x1, y0 + size * 0.06), (x0, y0 + size * 0.42), (x0 + size * 0.42, y1)
    d.line((*tail_top, *nose, *tail_bottom, x0 + size * 0.47, y0 + size * 0.53, *tail_top), fill=color, width=w,
           joint="curve")
    d.line((x0 + size * 0.47, y0 + size * 0.53, *nose), fill=color, width=w)


def _chat(d, image, box, w, color) -> None:  # облачко с хвостиком
    x0, y0, x1, y1 = box
    size = x1 - x0
    mask = Image.new("L", image.size, 0)
    m = ImageDraw.Draw(mask)
    top, bottom = y0 + size * 0.04, y1 - size * 0.2
    m.rounded_rectangle((x0, top, x1, bottom), radius=size * 0.26, fill=255)
    m.polygon([(x0 + size * 0.2, bottom - size * 0.1), (x0 + size * 0.12, y1), (x0 + size * 0.5, bottom - 1)],
              fill=255)
    m.rounded_rectangle((x0 + w, top + w, x1 - w, bottom - w), radius=max(1, size * 0.26 - w), fill=0)
    for i in (-1, 0, 1):  # три точки — «печатает»
        cx, cy, r = (x0 + x1) / 2 + i * size * 0.22, (top + bottom) / 2, w * 0.62
        m.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    image.paste(Image.new("RGBA", image.size, color), mask=mask)


def _sparkle(d, image, box, w, color) -> None:  # большая и маленькая звёздочки
    x0, y0, x1, y1 = box
    size = x1 - x0

    def star(cx, cy, r):
        k = r * 0.22
        d.polygon([(cx, cy - r), (cx + k, cy - k), (cx + r, cy), (cx + k, cy + k), (cx, cy + r), (cx - k, cy + k),
                   (cx - r, cy), (cx - k, cy - k)], fill=color)

    star(x0 + size * 0.42, y0 + size * 0.58, size * 0.42)
    star(x0 + size * 0.84, y0 + size * 0.16, size * 0.18)


def _gear(d, image, box, w, color) -> None:  # шестерёнка: 8 зубцов и отверстие
    import math

    x0, y0, x1, y1 = box
    cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2
    mask = Image.new("L", image.size, 0)
    m = ImageDraw.Draw(mask)
    body = r * 0.74
    m.ellipse((cx - body, cy - body, cx + body, cy + body), fill=255)
    for i in range(8):
        a = math.pi / 4 * i
        half = 0.2
        points = [(cx + math.cos(a + s * half) * rr, cy + math.sin(a + s * half) * rr)
                  for s, rr in ((-1, body * 0.9), (-0.72, r), (0.72, r), (1, body * 0.9))]
        m.polygon(points, fill=255)
    hole = r * 0.3
    m.ellipse((cx - hole, cy - hole, cx + hole, cy + hole), fill=0)
    image.paste(Image.new("RGBA", image.size, color), mask=mask)


def _journal(d, image, box, w, color) -> None:  # список: точки и строки
    x0, y0, x1, y1 = box
    size = x1 - x0
    for i in range(3):
        y = y0 + size * (0.14 + i * 0.36)
        r = w * 0.75
        d.ellipse((x0 + size * 0.06 - r, y - r, x0 + size * 0.06 + r, y + r), fill=color)
        d.line((x0 + size * 0.3, y, x1, y), fill=color, width=w)


def _clock(d, image, box, w, color) -> None:  # часы — расписание
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    d.ellipse(box, outline=color, width=w)
    size = x1 - x0
    d.line((cx, y0 + size * 0.24, cx, cy, cx + size * 0.22, cy + size * 0.14), fill=color, width=w, joint="curve")


def _chart(d, image, box, w, color) -> None:  # столбики — статистика
    x0, y0, x1, y1 = box
    size = x1 - x0
    for i, height in enumerate((0.45, 0.8, 0.6)):
        left = x0 + size * (0.08 + i * 0.32)
        d.rounded_rectangle((left, y1 - size * height, left + size * 0.22, y1), radius=w * 0.45, fill=color)


def _mini(d, image, box, w, color) -> None:  # окно и маленькое окошко в углу — «в уголок»
    x0, y0, x1, y1 = box
    size = x1 - x0
    d.rounded_rectangle((x0, y0 + size * 0.08, x1, y1 - size * 0.08), radius=size * 0.12, outline=color, width=w)
    d.rounded_rectangle((x0 + size * 0.48, y0 + size * 0.48, x1 - size * 0.16, y1 - size * 0.22),
                        radius=size * 0.06, fill=color)


def _expand(d, image, box, w, color) -> None:  # стрелки наружу — «развернуть окно»
    x0, y0, x1, y1 = box
    size = x1 - x0
    arm = size * 0.34
    for (cx, cy, sx, sy) in ((x0, y0, 1, 1), (x1, y1, -1, -1)):
        d.line((cx + sx * arm, cy, cx, cy, cx, cy + sy * arm), fill=color, width=w, joint="curve")
        d.line((cx, cy, cx + sx * size * 0.42, cy + sy * size * 0.42), fill=color, width=w)


def _eye(d, image, box, w, color) -> None:  # глаз — «показать браузер»
    x0, y0, x1, y1 = box
    size = x1 - x0
    import math

    cy = (y0 + y1) / 2
    lid = size * 0.34  # насколько «открыт» глаз
    steps = [i / 24 for i in range(25)]
    top = [(x0 + t * size, cy - lid * math.sin(math.pi * t)) for t in steps]
    bottom = [(x0 + t * size, cy + lid * math.sin(math.pi * t)) for t in steps]
    d.line(top, fill=color, width=w, joint="curve")
    d.line(bottom, fill=color, width=w, joint="curve")
    r = size * 0.15
    d.ellipse(((x0 + x1) / 2 - r, cy - r, (x0 + x1) / 2 + r, cy + r), fill=color)


_MENU = {"send": _send, "chat": _chat, "sparkle": _sparkle, "gear": _gear, "journal": _journal, "clock": _clock,
         "chart": _chart, "mini": _mini, "expand": _expand, "eye": _eye}


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

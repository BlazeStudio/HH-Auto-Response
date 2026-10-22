"""Окно браузера, которым управляет программа: спрятать за край экрана, показать, узнать, что его открыли.

Только Windows (на других системах функции ничего не делают). Окно ищется среди процессов, запущенных этой
программой (её браузер — внук: программа → драйвер Playwright → браузер), поэтому ваш обычный Chrome
не задевается никогда. Вызывать можно из любого потока: это функции Windows, не Playwright.
"""

from __future__ import annotations

import os
import sys
import threading

HIDDEN_AT = -32000  # там окно не видно ни на одном мониторе
WORK_SIZE = (1460, 1000)  # размер окна при запуске (в точках) — страница ~1440 шириной, как раньше
_lock = threading.Lock()
_cached: int | None = None

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _u, _k = ctypes.windll.user32, ctypes.windll.kernel32

    class _Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    _k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k.Process32FirstW.argtypes = _k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Entry)]
    _k.CloseHandle.argtypes = [wintypes.HANDLE]
    _ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _u.EnumWindows.argtypes = [_ENUM, wintypes.LPARAM]
    for _name, _args in (("IsWindowVisible", [wintypes.HWND]), ("IsWindow", [wintypes.HWND]),
                         ("IsIconic", [wintypes.HWND]), ("GetForegroundWindow", [])):
        getattr(_u, _name).argtypes = _args
    _u.GetForegroundWindow.restype = wintypes.HWND
    _u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
    _u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u.SetForegroundWindow.argtypes = [wintypes.HWND]
    _u.MonitorFromWindow.restype = wintypes.HANDLE
    _u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _u.MonitorFromPoint.restype = wintypes.HANDLE
    _u.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    _u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]


def supported() -> bool:
    return sys.platform == "win32"


def _descendants(root_pid: int) -> set[int]:
    """Все процессы, запущенные этой программой (дети, внуки…)."""
    snapshot = _k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return set()
    children: dict[int, list[int]] = {}
    try:
        entry = _Entry()
        entry.dwSize = ctypes.sizeof(_Entry)
        ok = _k.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            ok = _k.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _k.CloseHandle(snapshot)
    found, todo = set(), [root_pid]
    while todo:
        for child in children.get(todo.pop(), []):
            if child not in found and child != root_pid:
                found.add(child)
                todo.append(child)
    return found


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    _u.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def _ours(hwnd: int, pids: set[int]) -> bool:
    pid = wintypes.DWORD()
    _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value not in pids:
        return False
    name = ctypes.create_unicode_buffer(64)
    _u.GetClassNameW(hwnd, name, 64)
    return name.value == "Chrome_WidgetWin_1" and _u.GetWindowTextLengthW(hwnd) > 0


def find(refresh: bool = False) -> int | None:
    """Главное окно браузера, запущенного программой (или None)."""
    global _cached
    if not supported():
        return None
    with _lock:
        if _cached and not refresh and _u.IsWindow(_cached) and _u.IsWindowVisible(_cached):
            return _cached
        pids = _descendants(os.getpid())
        if not pids:
            _cached = None
            return None
        best: list[tuple[int, int]] = []

        def visit(hwnd, _param):
            if _u.IsWindowVisible(hwnd) and _ours(hwnd, pids):
                left, top, right, bottom = _rect(hwnd)
                best.append(((right - left) * (bottom - top), hwnd))
            return True

        _u.EnumWindows(_ENUM(visit), 0)
        _cached = max(best)[1] if best else None
        return _cached


def is_hidden(hwnd: int | None = None) -> bool:
    """Окно за краем экрана (спрятано программой)."""
    hwnd = hwnd or find()
    if not hwnd or _u.IsIconic(hwnd):
        return False
    left, top, _right, _bottom = _rect(hwnd)
    return left < -10000 or top < -10000


def hide() -> bool:
    hwnd = find()
    if not hwnd:
        return False
    if _u.IsIconic(hwnd):
        _u.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE: из свёрнутого — иначе «спрятать» не сработает
    # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
    return bool(_u.SetWindowPos(hwnd, None, HIDDEN_AT, HIDDEN_AT, 0, 0, 0x0001 | 0x0004 | 0x0010))


def _work_area(near: int | None) -> tuple[int, int, int, int]:
    """Рабочая область монитора (без панели задач): где окно программы или главный монитор."""
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    monitor = _u.MonitorFromWindow(near, 2) if near else _u.MonitorFromPoint(wintypes.POINT(0, 0), 1)
    _u.GetMonitorInfoW(monitor, ctypes.byref(info))
    work = info.rcWork
    return work.left, work.top, work.right, work.bottom


def show(near: int | None = None, activate: bool = True, rect: tuple[int, int, int, int] | None = None) -> bool:
    """Вывести окно на экран: по центру монитора, где окно программы (near), на 80% его рабочей области.
    rect — (x, y, ширина, высота), если нужно точное место."""
    hwnd = find()
    if not hwnd:
        return False
    if _u.IsIconic(hwnd):
        _u.ShowWindow(hwnd, 9)  # SW_RESTORE
    if rect is None:
        left, top, right, bottom = _work_area(near)
        width, height = round((right - left) * 0.8), round((bottom - top) * 0.88)
        rect = (left + (right - left - width) // 2, top + (bottom - top - height) // 2, width, height)
    flags = 0x0040 | (0 if activate else 0x0010)  # SWP_SHOWWINDOW (+ SWP_NOACTIVATE)
    # дважды: на мониторе с другим масштабом Chrome после первого переноса сам меняет размер
    for _ in range(2):
        _u.SetWindowPos(hwnd, None, *rect, flags | 0x0004)  # SWP_NOZORDER
    if activate:
        _u.SetForegroundWindow(hwnd)
    return True


def opened_by_user() -> bool:
    """Спрятанное окно активировали (щелчок по значку на панели задач, Alt+Tab) — его хотят увидеть."""
    hwnd = find()
    return bool(hwnd) and _u.GetForegroundWindow() == hwnd and is_hidden(hwnd)

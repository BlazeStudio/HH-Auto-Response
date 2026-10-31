"""Окно браузера, которым управляет программа: спрятать за край экрана, показать, узнать, что его открыли.

Только Windows (на других системах функции ничего не делают). Окно ищется среди процессов, запущенных этой
программой (её браузер — внук: программа → драйвер Playwright → браузер), поэтому ваш обычный Chrome
не задевается никогда. Вызывать можно из любого потока: это функции Windows, не Playwright.
"""

from __future__ import annotations

import os
import sys
import threading
import time

HIDDEN_AT = -32000  # там окно не видно ни на одном мониторе
WORK_SIZE = (1460, 1000)  # размер окна при запуске (в точках) — страница ~1440 шириной, как раньше
_lock = threading.Lock()
_cached: int | None = None
_missing_until = 0.0  # окна нет — не ищем заново до этого времени: перебор процессов Windows ~17 мс
MISSING_TTL = 2.0

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
    _u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _k.OpenProcess.restype = wintypes.HANDLE
    _k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]


def supported() -> bool:
    return sys.platform == "win32"


def _snapshot() -> tuple[dict[int, list[int]], dict[int, str]]:
    """Все процессы Windows за один проход: кто кого запустил (родитель → дети) и имена файлов."""
    children: dict[int, list[int]] = {}
    names: dict[int, str] = {}
    snapshot = _k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return children, names
    try:
        entry = _Entry()
        entry.dwSize = ctypes.sizeof(_Entry)
        ok = _k.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            names[entry.th32ProcessID] = entry.szExeFile
            ok = _k.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _k.CloseHandle(snapshot)
    return children, names


def _below(children: dict[int, list[int]], root_pid: int) -> set[int]:
    """Все процессы, запущенные root_pid (дети, внуки…)."""
    found, todo = set(), [root_pid]
    while todo:
        for child in children.get(todo.pop(), []):
            if child not in found and child != root_pid:
                found.add(child)
                todo.append(child)
    return found


def _driver_tree() -> tuple[set[int], dict[int, str]]:
    """Драйвер Playwright, запущенный программой, и всё, что запустил он, — браузер работы; и имена процессов.
    Chrome, который программа открыла человеку по ссылке («Открыть чаты»), — тоже её потомок, но не драйвера:
    раньше его окно могло оказаться «окном браузера» (спрятаться за край экрана, закрыться по «Остановить»),
    а сам он — завершиться вместе с процессами работы."""
    children, names = _snapshot()
    tree: set[int] = set()
    for pid in _below(children, os.getpid()):
        if names.get(pid, "").lower() == "node.exe":
            tree |= {pid} | _below(children, pid)
    return tree, names


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
    global _cached, _missing_until
    if not supported():
        return None
    with _lock:
        if _cached and not refresh and _u.IsWindow(_cached) and _u.IsWindowVisible(_cached):
            return _cached
        if not refresh and time.monotonic() < _missing_until:
            return None
        pids, _names = _driver_tree()
        if not pids:
            _cached = None
            _missing_until = time.monotonic() + MISSING_TTL
            return None
        best: list[tuple[int, int]] = []

        def visit(hwnd, _param):
            if _u.IsWindowVisible(hwnd) and _ours(hwnd, pids):
                left, top, right, bottom = _rect(hwnd)
                best.append(((right - left) * (bottom - top), hwnd))
            return True

        _u.EnumWindows(_ENUM(visit), 0)
        _cached = max(best)[1] if best else None
        if _cached is None:
            _missing_until = time.monotonic() + MISSING_TTL
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


def processes() -> dict[int, str]:
    """Процессы, запущенные программой сейчас (драйвер Playwright, браузер и его части): pid → имя файла."""
    if not supported():
        return {}
    ours, names = _driver_tree()
    return {pid: names[pid] for pid in ours if names.get(pid, "").lower()
            in ("chrome.exe", "msedge.exe", "node.exe", "chrome-headless-shell.exe")}


def still_running(recorded: dict[int, str]) -> list[int]:
    """Какие из запомненных процессов ещё живы (тот же pid и то же имя — не чужой процесс с тем же номером)."""
    if not recorded or not supported():
        return []
    _children, names = _snapshot()
    return [pid for pid, name in recorded.items() if names.get(pid, "").lower() == name.lower()]


def terminate(pids: list[int]) -> None:
    for pid in pids:
        handle = _k.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if handle:
            _k.TerminateProcess(handle, 1)
            _k.CloseHandle(handle)


def close() -> bool:
    """Закрыть окно браузера, как крестиком (для «Остановить», если шаг затянулся)."""
    hwnd = find(refresh=True)
    if not hwnd:
        return False
    return bool(_u.PostMessageW(hwnd, 0x0010, 0, 0))  # WM_CLOSE


def opened_by_user() -> bool:
    """Спрятанное окно активировали (щелчок по значку на панели задач, Alt+Tab) — его хотят увидеть."""
    hwnd = find()
    return bool(hwnd) and _u.GetForegroundWindow() == hwnd and is_hidden(hwnd)

"""Окно браузера, которым управляет программа: спрятать, показать, узнать, что его открыли.

Windows: окно уезжает за край экрана. Оно ищется среди процессов, запущенных этой программой (её браузер —
внук: программа → драйвер Playwright → браузер), поэтому ваш обычный Chrome не задевается никогда.
Вызывать можно из любого потока: это функции Windows, не Playwright.

macOS и Linux: за край экрана окно не убрать (система возвращает его обратно), и оно сворачивается — в Dock
или на панель задач — силами самого браузера (CDP: Browser.setWindowBounds). Страница в свёрнутом окне
работает как на экране: пока Playwright подключён, она «видима», анимация и таймеры не замедляются, щелчки
проходят, снимки для превью снимаются (замерено на Linux; на своей системе — tests/check_hidden_window.py).
Где это не так, программа замечает сама и оставляет окно на экране (_Cdp.check): страница «уснула» или
система не сворачивает окно. Linux — только с X11 (XWayland): окно Wayland браузер сворачивает, но обратно
не разворачивает (замерено). С браузером можно говорить только из рабочего потока (Playwright): он связывается
с окном (bind) и в паузах выполняет поручения (pump), а окно приложения (поток Tk) оставляет поручения
и читает последнее известное состояние окна.
"""

from __future__ import annotations

import os
import sys
import threading
import time

from ..core.logger import log

HIDDEN_AT = -32000  # там окно не видно ни на одном мониторе
WORK_SIZE = (1460, 1000)  # размер окна при запуске (в точках) — страница ~1440 шириной, как раньше
# Как прятать окно: "win32" — за край экрана, функциями Windows; "cdp" — свернуть через сам браузер
MODE = "win32" if sys.platform == "win32" else "cdp"
_WIN32 = sys.platform == "win32"  # процессы и окна Windows (завершить зависший браузер, закрыть окно)
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
    """Умеет ли программа прятать окно браузера на этой системе. Linux — только с X11 (в том числе XWayland
    в сеансе Wayland): свёрнутое окно Wayland браузер сам обратно не развернёт, и для капчи оно не выехало бы.
    Не в WSL: там свёрнутое окно не разворачивается ни в X11, ни в Wayland (замерено в WSLg). И не в контейнере
    (Docker): экран там виртуальный, прятать окно не от кого, а для входа его смотрят по VNC."""
    if sys.platform.startswith("linux"):
        in_container = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")
        return bool(os.environ.get("DISPLAY")) and not os.environ.get("WSL_DISTRO_NAME") and not in_container
    return sys.platform in ("win32", "darwin")


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
    """Главное окно браузера, запущенного программой (или None). macOS, Linux — номер окна в браузере,
    пока рабочий поток с ним связан; refresh в рабочем потоке — заодно узнать его состояние заново."""
    if MODE == "cdp":
        return _Cdp.find(refresh)
    return _find_hwnd(refresh)


def _find_hwnd(refresh: bool = False) -> int | None:
    global _cached, _missing_until
    if not _WIN32:
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
    """Окно спрятано: за краем экрана (Windows) или свёрнуто (macOS, Linux; поручение окна приложения
    спрятать или показать, которое рабочий поток ещё не выполнил, уже считается)."""
    if MODE == "cdp":
        return _Cdp.hidden()
    hwnd = hwnd or find()
    if not hwnd or _u.IsIconic(hwnd):
        return False
    left, top, _right, _bottom = _rect(hwnd)
    return left < -10000 or top < -10000


def hide() -> bool:
    if MODE == "cdp":
        return _Cdp.request("hide")
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
    rect — (x, y, ширина, высота), если нужно точное место. macOS, Linux: развернуть окно туда, где оно было
    (near и rect не нужны), и вывести вперёд."""
    if MODE == "cdp":
        return _Cdp.request("show" if activate else "restore")
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
    if not _WIN32:
        return {}
    ours, names = _driver_tree()
    return {pid: names[pid] for pid in ours if names.get(pid, "").lower()
            in ("chrome.exe", "msedge.exe", "node.exe", "chrome-headless-shell.exe")}


def still_running(recorded: dict[int, str]) -> list[int]:
    """Какие из запомненных процессов ещё живы (тот же pid и то же имя — не чужой процесс с тем же номером)."""
    if not recorded or not _WIN32:
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
    """Закрыть окно браузера, как крестиком (для «Остановить», если шаг затянулся). Только Windows."""
    hwnd = _find_hwnd(refresh=True)
    if not hwnd:
        return False
    return bool(_u.PostMessageW(hwnd, 0x0010, 0, 0))  # WM_CLOSE


def opened_by_user() -> bool:
    """Спрятанное окно активировали (щелчок по значку на панели задач, Alt+Tab) — его хотят увидеть.
    macOS, Linux: свёрнутое окно, развёрнутое щелчком по значку, и так на экране — делать ничего не нужно."""
    if MODE == "cdp":
        return False
    hwnd = find()
    return bool(hwnd) and _u.GetForegroundWindow() == hwnd and is_hidden(hwnd)


# ---------- macOS и Linux: свернуть окно через сам браузер ----------

def bind(page) -> bool:
    """macOS, Linux (рабочий поток, сразу после запуска браузера): связаться с окном, где вкладка page,
    и поставить его посередине экрана размером, как на Windows. На Windows ничего не делает."""
    return MODE == "cdp" and _Cdp.bind(page)


def unbind() -> None:
    """Работа закончилась (рабочий поток): браузер сейчас закроется."""
    if MODE == "cdp":
        _Cdp.unbind()


def pump(fresh: bool = False) -> None:
    """Рабочий поток, в паузах: выполнить поручение окна приложения («Показать», «Спрятать») и узнать, что
    с окном сейчас (его могли развернуть щелчком по значку). fresh — узнать сразу, без «раз в секунду».
    На Windows ничего не делает."""
    if MODE == "cdp":
        _Cdp.pump(fresh)


def kept_shown() -> str:
    """Почему окно браузера здесь не сворачивается (выяснилось в этом сеансе программы); "" — сворачивается."""
    return _Cdp.kept if MODE == "cdp" else ""


# Свёрнутая страница «уснула», если она «скрыта» или за PROBE_WAIT с не пришло ни одного кадра анимации: тогда
# работа на ней встала бы (щелчок Playwright ждёт кадров). Кадры реже обычного — не сон: так бывает со страницей,
# где что-то движется (на Windows свёрнутое окно с анимацией даёт ~1 кадр в секунду), — работа идёт, только
# щелчки дольше. Поэтому на Windows окно не сворачивается, а уезжает за край экрана
PROBE_WAIT = 2.5
PROBE_START = ("(() => { globalThis.hhFrames = 0; requestAnimationFrame(() => globalThis.hhFrames++); "
               "return document.visibilityState; })()")


class _Cdp:
    """Окно браузера на macOS и Linux. Методы с CDP — только из рабочего потока (thread); из других потоков —
    поручение (wanted) и последнее известное состояние (state)."""

    lock = threading.Lock()
    session = None  # CDP-сессия вкладки работы
    page = None
    thread = 0  # рабочий поток, который связался с окном
    window = 0  # номер окна в браузере
    state = ""  # minimized | normal | maximized | fullscreen; "" — связи нет или окно закрыто
    wanted = ""  # поручение из другого потока: hide | show | restore
    checked = False  # свёрнутую страницу уже проверили: не «спит» ли
    misses = 0  # проверок подряд, когда свёрнутая страница «спала»
    kept = ""  # почему окно здесь не сворачивается (до конца сеанса программы); "" — сворачивается
    next_state = 0.0
    next_check = 0.0
    STATE_EVERY = 1.0  # с: как часто узнавать, не развернули ли окно щелчком по значку
    CHECK_EVERY = 30.0  # с: как часто проверять, что свёрнутая страница не «уснула»
    MINIMIZE_WAIT = 1.5  # с: сколько ждать, пока система свернёт окно (на macOS — с анимацией)

    @classmethod
    def mine(cls) -> bool:
        return cls.session is not None and threading.get_ident() == cls.thread

    @classmethod
    def send(cls, method: str, params: dict | None = None) -> dict:
        return cls.session.send(method, params or {})

    @classmethod
    def bind(cls, page) -> bool:
        cls.unbind()
        try:
            session = page.context.new_cdp_session(page)
            found = session.send("Browser.getWindowForTarget")
        except Exception as e:  # noqa: BLE001 — нет окна (браузер без окна) или он уже закрывается
            log.debug(f"окно браузера: не связаться ({(str(e).splitlines() or [''])[0]})")
            return False
        with cls.lock:
            cls.session, cls.page, cls.thread = session, page, threading.get_ident()
            cls.window, cls.state = found["windowId"], found["bounds"].get("windowState", "normal")
            cls.wanted, cls.checked, cls.misses = "", False, 0
            cls.next_state = cls.next_check = 0.0
        cls.fit()
        return True

    @classmethod
    def unbind(cls) -> None:
        with cls.lock:
            session, cls.session, cls.page = cls.session, None, None
            cls.thread, cls.window, cls.state, cls.wanted = 0, 0, "", ""
        if session is not None:
            try:
                session.detach()
            except Exception:  # noqa: BLE001 — браузер уже закрыт
                pass

    @classmethod
    def fit(cls) -> None:
        """Окно — размером, как на Windows (страница ~1440 шириной: по ней настроена работа с hh), но не больше
        экрана, и посередине: развёрнутое для капчи, оно целиком на виду."""
        if cls.state != "normal":
            return  # развёрнуто на весь экран — так и оставить
        try:
            left, top, width, height = cls.send("Runtime.evaluate", {
                "expression": "[screen.availLeft || 0, screen.availTop || 0, screen.availWidth, screen.availHeight]",
                "returnByValue": True})["result"]["value"]
            w, h = min(WORK_SIZE[0], width), min(WORK_SIZE[1], height)
            cls.send("Browser.setWindowBounds", {"windowId": cls.window, "bounds": {
                "left": left + (width - w) // 2, "top": top + (height - h) // 2, "width": w, "height": h}})
        except Exception as e:  # noqa: BLE001 — не вышло — окно остаётся как открылось
            log.debug(f"окно браузера: размер не задан ({(str(e).splitlines() or [''])[0]})")

    @classmethod
    def find(cls, refresh: bool) -> int | None:
        if refresh and cls.mine():
            cls.pump(fresh=True)
        return cls.window if cls.session is not None and cls.state else None

    @classmethod
    def hidden(cls) -> bool:
        wanted = cls.wanted
        if wanted:
            return wanted == "hide"
        return cls.state == "minimized"

    @classmethod
    def request(cls, want: str) -> bool:
        if cls.session is None or not cls.state or want == "hide" and cls.kept:
            return False
        if cls.mine():
            return cls.apply(want)
        with cls.lock:
            cls.wanted = want
        return True

    @classmethod
    def pump(cls, fresh: bool = False) -> None:
        if not cls.mine():
            return
        with cls.lock:
            want, cls.wanted = cls.wanted, ""
        if want:
            cls.apply(want)
            return
        now = time.monotonic()
        if fresh or now >= cls.next_state:
            cls.read_state()
        if cls.state == "minimized" and now >= cls.next_check:
            cls.next_check = now + (cls.CHECK_EVERY if cls.checked else cls.STATE_EVERY)
            cls.check()

    @classmethod
    def read_state(cls) -> str:
        cls.next_state = time.monotonic() + cls.STATE_EVERY
        try:
            cls.state = cls.send("Browser.getWindowBounds", {"windowId": cls.window})["bounds"].get(
                "windowState", "normal")
        except Exception:  # noqa: BLE001 — окно закрыли (это заметит рабочий поток) или вкладку закрыли
            cls.state = ""
        return cls.state

    @classmethod
    def set_state(cls, state: str) -> None:
        cls.send("Browser.setWindowBounds", {"windowId": cls.window, "bounds": {"windowState": state}})

    @classmethod
    def minimized(cls) -> bool:
        """Дождаться, пока система свернёт окно (и сообщит об этом браузеру)."""
        deadline = time.monotonic() + cls.MINIMIZE_WAIT
        while cls.read_state() != "minimized" and cls.state and time.monotonic() < deadline:
            time.sleep(0.1)
        return cls.state == "minimized"

    @classmethod
    def apply(cls, want: str) -> bool:
        """Рабочий поток: hide — свернуть; show — развернуть и вывести вперёд; restore — только развернуть."""
        try:
            if want == "hide":
                if cls.kept:
                    return False
                cls.set_state("minimized")
                if cls.checked:
                    cls.read_state()
                    return True
                # впервые: убедиться, что система окно свернула, и проверить свёрнутую страницу. Сразу после
                # запуска окно может быть ещё не на экране, и просьбу свернуть система пропускает — повторяем
                if not cls.minimized():
                    cls.set_state("minimized")
                    if not cls.minimized():
                        cls.keep("система не сворачивает его по просьбе браузера (бывает без оконного менеджера "
                                 "или с неполным)", unsure=True)
                        return False
                cls.next_check = time.monotonic() + cls.STATE_EVERY
                cls.check()
                return not cls.kept
            if cls.read_state() == "minimized":
                cls.set_state("normal")
            if want == "show":
                cls.send("Page.bringToFront")  # вкладку работы — вперёд, а с ней и окно
            if cls.read_state() == "minimized":
                log.warning("  ! окно браузера не разворачивается само — откройте его щелчком по значку браузера "
                            "в Dock или на панели задач")
                return False
            return True
        except Exception as e:  # noqa: BLE001 — окно закрыли: это заметит рабочий поток
            log.debug(f"окно браузера: {want} не вышло ({(str(e).splitlines() or [''])[0]})")
            cls.read_state()
            return False

    @classmethod
    def check(cls) -> None:
        """Свёрнутая страница не «уснула»? На Windows и Linux — нет, пока Playwright подключён; где «уснёт»,
        работа на ней встала бы — тогда окно возвращается на экран и больше не сворачивается. Первая проверка —
        сразу после первого сворачивания (страница пустая, ответ точный), дальше — раз в CHECK_EVERY: вдруг
        система «усыпит» окно позже."""
        if cls.state != "minimized":
            return
        if cls.page is not None and len(cls.page.context.pages) > 1:
            return  # открыта ещё вкладка — вкладка работы сейчас может быть в фоне, где кадров и нет
        result = cls.probe()
        if result is None:
            return  # страница как раз сменилась — проверим в следующий раз
        first, cls.checked = not cls.checked, True
        visibility, frames = result
        if visibility == "visible" and frames:
            cls.misses = 0
            return
        cls.misses += 1
        log.debug(f"окно браузера: свёрнутая страница {visibility}, кадров {frames} (раз подряд: {cls.misses})")
        if first or cls.misses >= 2:  # тяжёлая страница может задержать кадр — нужен второй раз подряд
            cls.keep(f"в свёрнутом окне страница здесь «засыпает» ({visibility}, кадров анимации нет)")
            log.warning("  ! окно браузера возвращено на экран: свёрнутым оно здесь не работает. Чтобы оно не "
                        "сворачивалось совсем — «Настройки» → «Прочее» → «Браузер и сеть» → «Прятать окно "
                        "браузера» ([browser] hide_window = false)")

    @classmethod
    def probe(cls) -> tuple[str, int] | None:
        """Кадры анимации на вкладке работы — в своём, невидимом странице мире (страница не видит ни проверки,
        ни её переменных). Без ожидания обещаний: «уснувшая» страница не подвесит рабочий поток."""
        try:
            frame = cls.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
            world = cls.send("Page.createIsolatedWorld", {"frameId": frame, "worldName": "hh-auto-window"})[
                "executionContextId"]
            visibility = cls.send("Runtime.evaluate", {"expression": PROBE_START, "contextId": world,
                                                       "returnByValue": True})["result"]["value"]
            frames, deadline = 0, time.monotonic() + PROBE_WAIT
            while not frames and time.monotonic() < deadline:
                time.sleep(0.05)
                frames = cls.send("Runtime.evaluate", {"expression": "globalThis.hhFrames", "contextId": world,
                                                       "returnByValue": True})["result"].get("value") or 0
            return visibility, frames
        except Exception:  # noqa: BLE001 — страница перезагрузилась посреди проверки
            return None

    @classmethod
    def keep(cls, reason: str, unsure: bool = False) -> None:
        """Окно здесь не сворачивается — до конца сеанса программы оставить его на экране. unsure — система могла
        свернуть окно, не сказав браузеру (так в WSLg с X11): развернуть его наверняка — на весь экран и обратно
        (просьбу «развернуть» браузер, считающий окно развёрнутым, пропустил бы)."""
        cls.kept = reason
        log.info(f"  окно браузера остаётся на экране: {reason}")
        try:
            if cls.read_state() == "minimized":
                cls.set_state("normal")
            elif unsure and cls.state == "normal":
                cls.set_state("maximized")
                deadline = time.monotonic() + cls.MINIMIZE_WAIT
                while cls.read_state() == "normal" and time.monotonic() < deadline:
                    time.sleep(0.1)
                cls.set_state("normal")
        except Exception:  # noqa: BLE001
            pass
        cls.read_state()

"""Компьютер не засыпает, пока программа работает: экран гаснет как обычно, а сам компьютер — нет.

Раньше отклики, начатые перед уходом, вставали, когда Windows уходила в сон через полчаса без мыши и клавиатуры,
а запуск по расписанию в 10:00 не случался, если компьютер к этому времени спал. Теперь:

- пока идёт работа (отклики, чаты, фоновая проверка) — всегда;
- пока приложение открыто и включено расписание или фоновые задачи — если так настроено ([schedule] keep_awake).

Windows — SetThreadExecutionState(ES_SYSTEM_REQUIRED) без ES_DISPLAY_REQUIRED: экран выключается, сон — нет.
macOS — caffeinate -i (тоже только сон, не экран). Linux — systemd-inhibit --what=sleep: блокировка сна через
logind, её уважают GNOME, KDE, XFCE и другие; экран гаснет как обычно. Нет systemd-inhibit (Docker, сервер без
logind) — ничего: там компьютер и так не засыпает. Закрытую крышку ноутбука это не отменяет нигде.
Помощник (caffeinate, systemd-inhibit) сам заканчивается вместе с программой, даже если она упала.
Причины считаются по именам: hold("работа") и hold("расписание") независимы, сон разрешается, когда снят последний.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager

from .logger import log

_reasons: set[str] = set()
_lock = threading.Lock()
_changed = threading.Condition(_lock)
_thread: threading.Thread | None = None
_helper: subprocess.Popen | None = None  # macOS и Linux: процесс, который держит запрет сна
_helper_failed = False

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def hold(reason: str) -> None:
    """Не засыпать, пока не снят release(reason)."""
    with _lock:
        if reason in _reasons:
            return
        _reasons.add(reason)
        _apply()


def release(reason: str) -> None:
    with _lock:
        if reason not in _reasons:
            return
        _reasons.discard(reason)
        _apply()


def set_held(reason: str, on: bool) -> None:
    hold(reason) if on else release(reason)


def held() -> bool:
    with _lock:
        return bool(_reasons)


@contextmanager
def awake(reason: str):
    """Пока выполняется блок — компьютер не засыпает."""
    hold(reason)
    try:
        yield
    finally:
        release(reason)


def _apply() -> None:
    """Под _lock: передать новое состояние системе."""
    if sys.platform == "win32":
        _start_keeper()
        _changed.notify_all()
    else:
        _helper_set(bool(_reasons))


def _start_keeper() -> None:
    """Windows помнит запрет сна за потоком, который его поставил (и снимает, когда поток кончился). Рабочие
    потоки программы живут недолго, поэтому запрет ставит и снимает свой поток, который живёт до выхода."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_keeper, name="power", daemon=True)
    _thread.start()


def _keeper() -> None:
    import ctypes

    kernel = ctypes.windll.kernel32
    kernel.SetThreadExecutionState.restype = ctypes.c_uint
    kernel.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    current = None
    with _lock:
        while True:
            wanted = bool(_reasons)
            if wanted != current:
                flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if wanted else 0)
                if not kernel.SetThreadExecutionState(flags):
                    log.debug("  не удалось запретить сон компьютера (SetThreadExecutionState)")
                current = wanted
            _changed.wait()


def _helper_command() -> list[str] | None:
    pid = os.getpid()
    if sys.platform == "darwin":
        return ["caffeinate", "-i", "-w", str(pid)]  # -w: закончится вместе с программой
    inhibit = shutil.which("systemd-inhibit")
    if not inhibit:
        return None
    # держит блокировку, пока жива программа: упала — блокировка снимется сама, а не повиснет до выхода из системы
    return [inhibit, "--what=sleep", "--mode=block", "--who=HH-Auto-Response",
            "--why=Идут отклики или ждёт запуск по расписанию",
            "sh", "-c", f"while kill -0 {pid} 2>/dev/null; do sleep 15; done"]


def _helper_set(on: bool) -> None:
    global _helper, _helper_failed
    if on and _helper is not None and _helper.poll() not in (None, 0) and not _helper_failed:
        # прошлый помощник сразу закончился с ошибкой: система не разрешила (Linux вне сеанса — WSL, ssh, сервер)
        log.debug(f"  запрет сна: система не разрешила (код {_helper.returncode}) — компьютер может уснуть "
                  "по своим настройкам")
        _helper_failed = True
    if on and (_helper is None or _helper.poll() is not None):
        command = _helper_command()
        if command is None:
            if not _helper_failed:
                log.debug("  запрет сна: нет systemd-inhibit — компьютер может уснуть по настройкам системы")
            _helper_failed = True
            return
        try:  # своя группа процессов: снять запрет — завершить и systemd-inhibit, и то, что он держит
            _helper = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            if not _helper_failed:
                log.debug(f"  запрет сна не включился: {e}")
            _helper_failed = True
            _helper = None
    elif not on and _helper is not None:
        helper, _helper = _helper, None
        try:
            os.killpg(helper.pid, signal.SIGTERM)
        except (OSError, AttributeError):  # уже закончился
            helper.terminate()

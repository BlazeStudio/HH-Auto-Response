"""Остановка по запросу (кнопка «Остановить» в приложении) и закрытое окно браузера.

Все паузы в коде идут через control.sleep: при запросе остановки она сразу бросает
StopRequested. Он наследует BaseException, как KeyboardInterrupt, чтобы его не
перехватывали обработчики `except Exception` внутри отклика на вакансию.

Окно браузера закрыли — BrowserClosed (вид StopRequested): раньше циклы ожидания (вход, капча,
окно отклика) принимали ошибку «браузер закрыт» за временную и ждали до конца своего срока,
и запуск «висел». Теперь первая же пауза после закрытия останавливает работу.

Пока идёт пауза, рабочий поток выполняет поручения окна приложения (on_idle): показать или спрятать
браузер, снять кадр для превью — с Playwright можно работать только из его потока.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

_stop = threading.Event()
_closed = threading.Event()
_closed_reason = ""
_idle: Callable[[], None] | None = None
_in_idle = False
SLICE = 0.25  # как часто во время паузы выполнять поручения окна, сек


class StopRequested(BaseException):
    pass


class BrowserClosed(StopRequested):
    """Окно браузера (или вкладку с hh) закрыли — работать дальше не на чем."""


def request_stop() -> None:
    _stop.set()


def browser_closed(reason: str = "окно браузера закрыто") -> None:
    global _closed_reason
    if not _closed.is_set():
        _closed_reason = reason
        _closed.set()
    _stop.set()  # разбудить паузу


def reset() -> None:
    _stop.clear()
    _closed.clear()


def stop_requested() -> bool:
    return _stop.is_set()


def check() -> None:
    if _closed.is_set():
        raise BrowserClosed(_closed_reason)
    if _stop.is_set():
        raise StopRequested


def set_idle(callback: Callable[[], None] | None) -> None:
    """Что делать рабочему потоку во время пауз (None — ничего)."""
    global _idle
    _idle = callback


def _run_idle() -> None:
    global _in_idle
    if _idle is None or _in_idle:
        return
    _in_idle = True
    try:
        _idle()
    except StopRequested:
        raise
    except Exception:  # поручение окна не должно ломать работу
        pass
    finally:
        _in_idle = False


def sleep(seconds: float) -> None:
    end = time.monotonic() + max(seconds, 0)
    while True:
        _run_idle()
        left = end - time.monotonic()
        if left <= 0:
            check()
            return
        if _stop.wait(min(left, SLICE) if _idle is not None else left):
            check()

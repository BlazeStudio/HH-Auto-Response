"""Остановка по запросу (кнопка «Остановить» в приложении).

Все паузы в коде идут через control.sleep: при запросе остановки она сразу бросает
StopRequested. Он наследует BaseException, как KeyboardInterrupt, чтобы его не
перехватывали обработчики `except Exception` внутри отклика на вакансию.
"""

from __future__ import annotations

import threading

_stop = threading.Event()


class StopRequested(BaseException):
    pass


def request_stop() -> None:
    _stop.set()


def reset() -> None:
    _stop.clear()


def stop_requested() -> bool:
    return _stop.is_set()


def check() -> None:
    if _stop.is_set():
        raise StopRequested


def sleep(seconds: float) -> None:
    if _stop.wait(max(seconds, 0)):
        raise StopRequested

"""Остановка по запросу (кнопка «Остановить» в приложении) и закрытое окно браузера.

Все паузы в коде идут через control.sleep: при запросе остановки она сразу бросает
StopRequested. Он наследует BaseException, как KeyboardInterrupt, чтобы его не
перехватывали обработчики `except Exception` внутри отклика на вакансию.

Окно браузера закрыли — BrowserClosed (вид StopRequested): раньше циклы ожидания (вход, капча,
окно отклика) принимали ошибку «браузер закрыт» за временную и ждали до конца своего срока,
и запуск «висел». Теперь первая же пауза после закрытия останавливает работу.

Пока идёт пауза, рабочий поток выполняет поручения окна приложения (on_idle): показать или спрятать
браузер, снять кадр для превью — с Playwright можно работать только из его потока.

Пауза («Пауза» в приложении): работа встаёт в ближайшей паузе или проверке между шагами (вакансия, начатая
до нажатия, доделывается) и стоит, пока не нажмут «Продолжить» или «Остановить». Поручения окна при этом
выполняются — превью браузера живое.

Пока браузер запускается и идут проверки перед работой (starting), терять при остановке нечего: окно
приложения закрывает браузер почти сразу, не дожидаясь шага. Раньше «Запустить» и сразу «Остановить»
ждали, пока откроется hh.ru (до 45 с), а то и заканчивались ошибкой «hh.ru не открывается».
"""

from __future__ import annotations

import threading
import time
from typing import Callable

_stop = threading.Event()
_user = threading.Event()  # «Остановить» нажал человек (а не браузер закрылся сам)
_closed = threading.Event()
_starting = threading.Event()  # браузер запускается или идут проверки перед работой
_pause = threading.Event()  # нажали «Пауза»
_holding = threading.Event()  # работа действительно стоит на паузе (дошла до ближайшей проверки)
_closed_reason = ""
_idle: Callable[[], None] | None = None
_in_idle = False
SLICE = 0.25  # как часто во время паузы выполнять поручения окна, сек


class StopRequested(BaseException):
    pass


class BrowserClosed(StopRequested):
    """Окно браузера (или вкладку с hh) закрыли — работать дальше не на чем."""


def request_stop() -> None:
    _user.set()
    _stop.set()


def user_stopped() -> bool:
    return _user.is_set()


def browser_closed(reason: str = "окно браузера закрыто") -> None:
    global _closed_reason
    if not _closed.is_set():
        _closed_reason = reason
        _closed.set()
    _stop.set()  # разбудить паузу


def reset() -> None:
    _stop.clear()
    _user.clear()
    _closed.clear()
    _starting.clear()
    _pause.clear()
    _holding.clear()


def request_pause() -> None:
    _pause.set()


def resume() -> None:
    _pause.clear()


def paused() -> bool:
    """Нажали «Пауза» (работа встанет в ближайшей паузе между шагами)."""
    return _pause.is_set()


def holding() -> bool:
    """Работа уже стоит на паузе."""
    return _holding.is_set()


def _hold() -> None:
    """Пауза: ждём «Продолжить» или «Остановить», выполняя поручения окна."""
    _holding.set()
    try:
        while _pause.is_set():
            _run_idle()
            if _stop.wait(SLICE):
                break
    finally:
        _holding.clear()


def set_starting(on: bool) -> None:
    (_starting.set if on else _starting.clear)()


def starting() -> bool:
    """Работа ещё не началась (запуск браузера, вход, проверки): остановить можно сразу, ничего не оборвав."""
    return _starting.is_set()


def stop_requested() -> bool:
    return _stop.is_set()


def check() -> None:
    if _pause.is_set() and not _in_idle:
        _hold()
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
        if _pause.is_set() and not _in_idle:
            _hold()  # после паузы досыпать нечего: она и так была длиннее
        _run_idle()
        left = end - time.monotonic()
        if left <= 0:
            check()
            return
        if _stop.wait(min(left, SLICE) if _idle is not None else left):
            check()

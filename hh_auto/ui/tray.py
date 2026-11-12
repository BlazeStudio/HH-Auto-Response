"""Значок в трее: свернули окно программы — оно уходит туда, а не на панель задач (или в Dock).

Программа при этом работает как обычно: расписание, фоновая проверка, отклики. Где значок:

- Windows — у часов. Щелчок — окно программы; правая кнопка — меню: открыть, начать отклики или остановить,
  показать браузер, выйти. Подсказка на значке — что сейчас происходит («Отклики • идёт 12:03», «Нужна капча»).
- macOS — в строке меню справа вверху. Щелчок — то же меню. Щелчок по значку в Dock тоже возвращает окно.
- Linux — в области уведомлений панели (KDE, XFCE, Cinnamon, MATE, LXQt; GNOME — с расширением AppIndicator).
  Щелчок — окно программы (меню у таких значков в pystray нет). Нет области уведомлений (чистый GNOME,
  Wayland без XWayland-трея) — трея нет, окно сворачивается как раньше: иначе свёрнутое окно было бы не вернуть.

Окно программы (Tk) трогать можно только из его потока: пункты меню кладут поручение в очередь событий окна,
его забирает App._poll. Windows и Linux — значок в своём потоке (pystray сам переносит вызовы в него).
macOS — в главном потоке: строку меню обслуживает тот же цикл событий Cocoa, что и окно Tk, свой поток там нельзя.
"""

from __future__ import annotations

import functools
import os
import queue
import sys
import threading
from typing import Callable

from ..core.logger import log
from ..core.paths import bundled
from .theme import APP_NAME

TIP_MAX = 120  # подсказка значка в Windows — до 127 символов
MAC = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")
# где искать значок — для подписей в настройках и подсказки при первом сворачивании
PLACE = "в строке меню" if MAC else "в области уведомлений" if LINUX else "у часов"


@functools.lru_cache(maxsize=1)
def available() -> bool:
    """Есть ли куда свернуть: библиотека значков и (на Linux) область уведомлений на панели."""
    if LINUX:
        # Значок по протоколу X11 (XEmbed): работает в отдельном потоке рядом с Tk и не требует GTK. Варианты
        # pystray через GTK/AppIndicator хотят свой главный цикл и библиотеки системы — их не берём
        os.environ.setdefault("PYSTRAY_BACKEND", "xorg")
    try:
        import pystray  # noqa: F401
    except Exception as e:  # noqa: BLE001 — нет библиотеки (старая установка из исходников): без трея
        log.debug(f"значка в трее не будет: {e}")
        return False
    if LINUX and os.environ.get("PYSTRAY_BACKEND") == "xorg":
        return x11_tray_present()
    return sys.platform in ("win32", "darwin") or LINUX


def x11_tray_present() -> bool:
    """На экране есть область уведомлений: у выделения _NET_SYSTEM_TRAY_S<экран> есть владелец (панель)."""
    try:
        from Xlib import X, display

        screen = display.Display()
        try:
            atom = screen.intern_atom(f"_NET_SYSTEM_TRAY_S{screen.get_default_screen()}")
            return screen.get_selection_owner(atom) != X.NONE
        finally:
            screen.close()
    except Exception as e:  # noqa: BLE001 — нет X11 (чистый Wayland, сервер без экрана)
        log.debug(f"области уведомлений нет: {e}")
        return False


class Tray:
    def __init__(self, events: queue.Queue, running: Callable[[], bool]):
        self.events = events
        self.running = running  # идёт ли работа — от этого зависят пункты меню
        self.icon = None
        self._tip = APP_NAME

    def start(self) -> bool:
        """Показать значок (из потока окна программы). False — не вышло."""
        try:
            import pystray
            from PIL import Image

            item, separator = pystray.MenuItem, pystray.Menu.SEPARATOR
            menu = pystray.Menu(
                item(f"Открыть {APP_NAME}", self._emit("open"), default=True),
                separator,
                item("Начать отклики", self._emit("start"), visible=lambda _i: not self.running()),
                item("Остановить", self._emit("stop"), visible=lambda _i: self.running()),
                item("Показать браузер", self._emit("browser"), enabled=lambda _i: self.running()),
                separator,
                item("Выйти", self._emit("quit")),
            )
            image = Image.open(bundled("packaging/icon.png"))
            self.icon = pystray.Icon("hh-auto-response", image, self._tip, menu)
            if MAC:
                # строку меню обслуживает цикл событий окна (Tk на Cocoa): pystray только готовит значок,
                # а показываем его отсюда же — из главного потока, как требует macOS
                self.icon.run_detached(setup=lambda _icon: None)
                self.icon.visible = True
                return True
        except Exception as e:  # noqa: BLE001
            log.debug(f"значок в трее не создан: {e}")
            self.icon = None
            return False
        # свой поток-демон: run_detached у pystray запускает обычный поток, и он не дал бы программе закрыться
        threading.Thread(target=self._run, name="tray", daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            self.icon.run()
        except Exception as e:  # noqa: BLE001 — нет области уведомлений (сеанс без проводника): живём без значка
            log.debug(f"значок в трее не работает: {e}")

    def _emit(self, action: str):
        return lambda: self.events.put(("tray", action))

    def set_tip(self, text: str) -> None:
        text = text if len(text) <= TIP_MAX else text[:TIP_MAX - 1] + "…"
        if self.icon is None or text == self._tip:
            return
        self._tip = text
        try:
            self.icon.title = text
        except Exception:  # noqa: BLE001 — значок ещё не показан
            pass

    def refresh(self) -> None:
        """Работа началась или закончилась — пересобрать меню («Начать» ↔ «Остановить»)."""
        if self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:  # noqa: BLE001
                pass

    def notify(self, text: str) -> None:
        """Всплывающая подсказка у значка (один раз — что программа теперь здесь). На Linux у значков X11
        подсказок нет — тогда без неё."""
        if self.icon is not None and getattr(self.icon, "HAS_NOTIFICATION", False):
            try:
                self.icon.notify(text, APP_NAME)
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        """Убрать значок (иначе после выхода он висит в трее, пока на него не наведут мышь)."""
        icon, self.icon = self.icon, None
        if icon is None:
            return
        try:
            if MAC:
                # icon.stop() на macOS останавливает цикл событий приложения — тот же, что у окна Tk
                icon.visible = False
                icon._status_bar.removeStatusItem_(icon._status_item)
            else:
                icon.stop()
        except Exception:  # noqa: BLE001
            pass

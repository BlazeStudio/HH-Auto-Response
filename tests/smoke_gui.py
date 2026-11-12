"""Окно программы целиком, как его открывает человек: все страницы и вкладки настроек, обе темы, мини-окно,
окна сообщений, выделение и копирование текста, сочетания клавиш в русской раскладке, «Проверка ИИ», отклики
и чаты из окна — на макете hh (tests/mock_hh.py), без сети и без настоящих откликов.

    python tests/smoke_gui.py

Нужен экран (на Linux без него — xvfb-run -a python tests/smoke_gui.py) и браузер, как для e2e_mock_hh.py.
Данные — во временной папке: ваши настройки и вход в hh не трогаются. Отчёт — строками ok/BAD, код возврата 1,
если что-то не так или в окне случилась ошибка.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import tkinter
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта
sys.path.insert(0, str(Path(__file__).resolve().parent))

for stream in (sys.stdout, sys.stderr):  # кириллица в консоли Windows
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

HOME = Path(tempfile.mkdtemp(prefix="hh-gui-"))
os.environ["HH_AUTO_HOME"] = str(HOME)

from mock_hh import RESUME_TEXT, FakeLlm, MockHh, use_mock, write_config  # noqa: E402

LLM = FakeLlm()
write_config(HOME / "config.toml", LLM.url)
(HOME / "data").mkdir()
(HOME / "data" / "resume.txt").write_text(f"Проживает: Москва\n{RESUME_TEXT}", encoding="utf-8")
# «Показывать уведомление по итогам?» спрашивается после первого запуска — проверяем его отдельно (part_runs)
(HOME / "app_prefs.json").write_text('{"check_updates": false}', encoding="utf-8")
MOCK = MockHh()
use_mock(MOCK)

from hh_auto.core.config import load_config  # noqa: E402
from hh_auto.ui import dialogs, gui  # noqa: E402
from hh_auto.ui import widgets as W  # noqa: E402

results: list[str] = []
errors: list[str] = []


def check(name: str, ok, detail="") -> None:
    line = ("ok " if ok else "BAD") + f" {name}" + (f" — {detail}" if detail and not ok else "")
    results.append(line)
    print(line, flush=True)


def report_error(self, exc, value, tb) -> None:  # ошибка в окне — не окно с сообщением, а строка в отчёте
    errors.append("".join(traceback.format_exception(exc, value, tb)))
    print("ОШИБКА В ОКНЕ:\n" + errors[-1], flush=True)


gui.App.report_callback_exception = report_error
app = gui.App()


def pump(seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(0.01)


def wait(condition, timeout: float, step: float = 0.1) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        pump(step)
    return bool(condition())


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def labels_on(widget) -> list:
    return [w for w in walk(widget) if W._selectable(w) and w.winfo_viewable()]


def choose_later(value=True, after: int = 300) -> None:
    """Ответить на ближайшее окно сообщения, как человек: нажать кнопку."""
    def press():
        for dialog in [w for w in walk(app) if isinstance(w, dialogs.Dialog)]:
            dialog._choose(value)
            return
        app.after(100, press)
    app.after(after, press)


def part_pages() -> None:
    pump(1.0)
    for key, page in app.pages.items():
        app.show_page(key)
        pump(0.4)
        check(f"страница «{key}» открылась", page.winfo_ismapped())
        shown = labels_on(page)
        for label in shown[:30]:  # выделение в подписях этой страницы
            W._open_box(label)
            W._close_box()
        check(f"страница «{key}»: подписи выделяются", True, len(shown))
    settings = app.pages["settings"]
    app.show_page("settings")
    for name in settings.tabs:
        settings.show_tab(name)
        pump(0.3)
        check(f"настройки: вкладка «{name}»", settings.tabs[name].winfo_ismapped())
    missing = [f"{s}.{k}" for (s, k) in settings.widgets if not hasattr(getattr(app.cfg, s) if s else app.cfg, k)]
    check("настройки: у каждого поля есть параметр", not missing, missing)


def part_settings_save() -> None:
    settings = app.pages["settings"]
    app.show_page("settings")
    pump(0.3)
    kind, entry = settings.widgets[("", "search_url")]
    url = "https://hh.ru/search/vacancy?resume=0123456789abcdef0123&from=resumelist"
    entry.delete(0, "end")
    app.clipboard_clear()
    app.clipboard_append(url)
    entry._entry.focus_force()
    pump(0.1)
    entry._entry.event_generate("<<Paste>>")
    pump(0.1)
    check("настройки: ссылка вставляется из буфера", entry.get() == url, entry.get())
    settings.widgets[("ai", "documents_ready")][1].select()
    context = settings.widgets[("ai", "context")][1]
    context.delete("1.0", "end")
    context.insert("1.0", "Живу в Москве. Готов предоставить документы.")
    settings.save()
    pump(0.5)
    cfg = load_config(HOME / "config.toml")
    check("настройки: сохранились", cfg.search_url == url and cfg.ai.documents_ready and "Москве" in cfg.ai.context,
          (cfg.search_url, cfg.ai.documents_ready, cfg.ai.context))
    settings.load(force=True)
    check("настройки: снова в форме после загрузки", entry.get() == url)


def part_shortcuts() -> None:
    """Сочетания в русской раскладке — тем путём, каким их получает программа на этой системе."""
    page = app.pages["ai"]
    app.show_page("ai")
    pump(0.3)
    field = page.entry._entry
    field.delete(0, "end")
    app.clipboard_clear()
    app.clipboard_append("Текст из буфера")
    field.focus_force()
    pump(0.1)
    if sys.platform == "win32":
        event = SimpleNamespace(widget=field, keysym="??", keycode=86, state=0)
    elif sys.platform == "darwin":
        event = SimpleNamespace(widget=field, keysym="Cyrillic_em", keycode=9 << 24 | ord("м"), state=0)
    else:
        event = SimpleNamespace(widget=field, keysym="Cyrillic_em", keycode=55, state=0)
    W._layout_shortcut(event)
    pump(0.1)
    check(f"сочетания ({sys.platform}): вставка в русской раскладке", field.get() == "Текст из буфера", field.get())
    binding = "<Command-KeyPress>" if sys.platform == "darwin" else "<Control-KeyPress>"
    check(f"сочетания ({sys.platform}): обработчик стоит на {binding}", bool(app.bind_all(binding)))


def part_ai_page() -> None:
    page = app.pages["ai"]
    app.show_page("ai")
    pump(0.3)
    page.entry.delete(0, "end")
    page.entry.insert(0, "В каком городе вы проживаете на постоянной основе?")
    page.ask()
    ok = wait(lambda: "НЕ отправлен" in page.view.get("1.0", "end") or "ошибка" in page.view.get("1.0", "end"), 30)
    text = page.view.get("1.0", "end")
    check("«Проверка ИИ»: ответ в переписке", ok and "НЕ отправлен" in text, text[-300:])
    page.test_form()
    wait(lambda: not page.busy, 60)
    check("«Проверка ИИ»: тест анкеты", "ИИ заполнил анкету" in page.view.get("1.0", "end"))
    page.test_letter()
    wait(lambda: not page.busy, 60)
    check("«Проверка ИИ»: тест письма", "Письмо ИИ" in page.view.get("1.0", "end"))
    page.view.copy_all()
    check("«Проверка ИИ»: журнал копируется", "Письмо ИИ" in app.clipboard_get())
    page.show_prompt()
    wait(lambda: any(isinstance(w, gui.PromptWindow) for w in walk(app)), 30)
    window = next((w for w in walk(app) if isinstance(w, gui.PromptWindow)), None)
    check("«Что получает нейросеть»: окно открылось", window is not None)
    if window is not None:
        text = next(w for w in walk(window) if isinstance(w, tkinter.Text))
        text.event_generate("<Button-1>", x=5, y=5)
        text.event_generate("<ButtonRelease-1>", x=5, y=5)
        pump(0.2)
        check("«Что получает нейросеть»: текст берёт фокус для Ctrl+C", window.focus_get() is text, window.focus_get())
        text.tag_add("sel", "1.0", "1.20")
        text.event_generate("<<Copy>>")
        check("«Что получает нейросеть»: копируется", app.clipboard_get() == text.get("1.0", "1.20"))
        window.destroy()


def part_themes_and_mini() -> None:
    for label in ("Тёмная", "Светлая", "Авто"):
        app._set_appearance(label)
        pump(0.3)
        for key in app.pages:
            app.show_page(key)
            pump(0.15)
        check(f"тема «{label}»: все страницы", not errors)
    app.to_mini()
    pump(0.6)
    check("мини-окно: открылось", app.mini is not None and app.mini_mode)
    app.from_mini()
    pump(0.6)
    check("мини-окно: вернулись в окно программы", not app.mini_mode)


def part_tray() -> None:
    """Значок в трее (у часов, в строке меню, в области уведомлений): свернули окно — оно в трее; щелчок по
    значку — снова на экране. Linux без области уведомлений (xvfb в проверках) — трея нет, всё как раньше."""
    from hh_auto.ui import tray as tray_icon

    if not tray_icon.available():
        check("трей: здесь его нет (нет области уведомлений) — окно сворачивается как раньше", True)
        return
    check("трей: значок появился", wait(lambda: app.tray is not None and app.tray.icon is not None, 5))
    app.iconify()
    check("трей: свёрнутое окно ушло в трей", wait(lambda: app.state() == "withdrawn", 3), app.state())
    check("трей: подсказка о трее запомнена", app.prefs.get("tray_hint_shown") is True)
    app.events.put(("tray", "open"))  # так приходит щелчок по значку
    check("трей: щелчок по значку вернул окно", wait(lambda: app.state() == "normal", 3), app.state())
    pump(0.4)
    tip = app._tray_tip()
    check("трей: подсказка на значке", tip.startswith(gui.APP_NAME) and len(tip) > len(gui.APP_NAME), tip)
    app.tray.refresh()  # «Начать» ↔ «Остановить»
    settings = app.pages["settings"]
    settings.tray_switch.deselect()
    settings._tray_switched()
    check("трей: выключили — значка нет", app.tray is None and app.prefs.get("tray") is False)
    app.iconify()
    pump(0.6)
    check("трей: без него окно сворачивается на панель задач", app.state() == "iconic", app.state())
    app.deiconify()
    pump(0.4)
    settings.tray_switch.select()
    settings._tray_switched()
    check("трей: включили снова", wait(lambda: app.tray is not None, 3))


def part_dialogs() -> None:
    choose_later(True)
    dialogs.showinfo(gui.APP_NAME, "Проверка\n\nТекст сообщения можно выделить", parent=app)
    check("сообщение: показалось и закрылось", True)
    choose_later(False)
    check("вопрос: «Нет»", dialogs.askyesno(gui.APP_NAME, "Проверка?", parent=app) is False)
    choose_later(True)
    dialog_value = dialogs.askstring("Ссылка", "Вставьте ссылку", initial="https://hh.ru", parent=app)
    check("ввод строки", dialog_value == "https://hh.ru", dialog_value)


def part_runs() -> None:
    app.show_page("responses")
    pump(0.3)
    app.reload_config()
    page = app.pages["responses"]
    page.limit.delete(0, "end")
    page.limit.insert(0, "50")
    asked = []
    real_ask = dialogs.askyesno

    def ask(*args, **kwargs):  # один раз после первого запуска: «Показывать уведомление по итогам?»
        asked.append(args[1] if len(args) > 1 else kwargs.get("message", ""))
        return False

    gui.messagebox.askyesno = ask
    app.start_responses(dry_run=False)
    done = wait(lambda: app.running_kind is None and not (app.worker and app.worker.is_alive()), 240, 0.2)
    pump(1.0)
    gui.messagebox.askyesno = real_ask
    check("отклики из окна: закончились", done)
    check("после первого запуска: вопрос об уведомлениях", asked and "уведомление" in asked[0], asked)
    check("отклики из окна: ушли на макет", len({r["id"] for r in MOCK.sent}) >= 6, len(MOCK.sent))
    check("отклики из окна: счётчик на странице", app.stats.applied >= 6, app.stats.applied)
    check("отклики из окна: журнал в окне", "Итоги запуска" in app.console.view.get("1.0", "end"))
    check("отклики из окна: наружу ни одного запроса", not MOCK.outside, MOCK.outside[:3])
    app.show_page("chats")
    pump(0.3)
    app.start_chats(dry_run=False)
    done = wait(lambda: app.running_kind is None and not (app.worker and app.worker.is_alive()), 240, 0.2)
    check("чаты из окна: закончились", done)
    check("чаты из окна: робот получил ответы", any(r["chat"] == "2" for r in MOCK.chat_sent), MOCK.chat_sent)
    check("чаты из окна: отказ прочитан", app.chat_stats.opened == 1, app.chat_stats.opened)
    for key in ("journals", "stats", "analysis"):
        app.show_page(key)
        pump(1.5)
        check(f"после работы: страница «{key}»", app.pages[key].winfo_ismapped() and not errors)
    check("журналы: логи видны", any((HOME / "logs").glob("run_*.log")))


def part_stop() -> None:
    """«Пауза», «Продолжить» и «Остановить» посреди откликов."""
    from mock_hh import vacancy

    MOCK.sent.clear()
    MOCK.pages = [[vacancy(900 + i, f"Python {i}", "modal") for i in range(12)]]
    path = HOME / "config.toml"  # паузы между вакансиями — чтобы было где встать на паузу
    path.write_text(path.read_text(encoding="utf-8").replace("delay_min = 0.0\ndelay_max = 0.0",
                                                             "delay_min = 1.5\ndelay_max = 1.5"), encoding="utf-8")
    app.reload_config()
    app.start_responses(dry_run=False)
    started = wait(lambda: MOCK.sent, 120, 0.2)
    check("третий запуск подряд: браузер открылся", started, "в журнале — ошибка запуска?")
    if not started:
        app.stop()
        wait(lambda: not (app.worker and app.worker.is_alive()), 60, 0.2)
        return
    app.pause()
    wait(control_paused_holding, 20, 0.2)
    paused_at = len(MOCK.sent)
    pump(4)
    check("пауза: работа стоит", len(MOCK.sent) == paused_at, (paused_at, len(MOCK.sent)))
    app.resume()
    wait(lambda: len(MOCK.sent) > paused_at, 60, 0.2)
    check("продолжить: работа пошла дальше", len(MOCK.sent) > paused_at)
    app.stop()
    done = wait(lambda: not (app.worker and app.worker.is_alive()), 60, 0.2)
    check("остановить: работа закончилась", done)
    check("остановить: остальные вакансии не тронуты", len(MOCK.sent) < 12, len(MOCK.sent))
    pump(0.5)
    check("остановить: в меню «Остановлено»", "Остановлено" in str(app.status.cget("text")), app.status.cget("text"))


def control_paused_holding() -> bool:
    from hh_auto.core import control

    return control.holding()


def main() -> int:
    for part in (part_pages, part_settings_save, part_shortcuts, part_ai_page, part_themes_and_mini, part_tray,
                 part_dialogs, part_runs, part_stop):
        print(f"--- {part.__name__}", flush=True)
        try:
            part()
        except Exception as e:
            check(f"{part.__name__}: упало", False, f"{type(e).__name__}: {e}")
            traceback.print_exc()
    check("в окне не было ошибок", not errors, len(errors))
    crash = HOME / "logs" / "crash.log"
    check("crash.log пуст", not crash.exists() or not crash.read_text(encoding="utf-8").strip())
    app.on_close()
    pump(1.0)
    bad = [line for line in results if line.startswith("BAD")]
    print(f"\nИтог: {len(results) - len(bad)} ok, {len(bad)} BAD")
    LLM.close()
    os._exit(1 if bad else 0)


if __name__ == "__main__":
    main()

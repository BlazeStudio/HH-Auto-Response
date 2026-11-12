"""Спрятанный браузер на этом компьютере: работает ли страница, пока окно спрятано (web/window.py).

    python tests/check_hidden_window.py              # минута в спрятанном окне
    python tests/check_hidden_window.py --minutes 10 # дольше: вдруг система «усыпит» окно не сразу
    python tests/check_hidden_window.py --cdp        # на Windows — как на macOS и Linux (свернуть через CDP)

Браузер запускается так же, как в работе ([browser] hide_window = true), с временным профилем и без сети:
страница — своя, с кнопкой. Проверяется, что окно спрятано сразу после запуска, страница «видима», кадры
анимации и таймеры идут с обычной скоростью, щелчок проходит, кадры для превью снимаются; «Показать»
и «Спрятать» из другого потока (как из окна приложения) выполняются в паузе рабочего потока; вход и капча
выводят окно на экран и прячут обратно. Нужен экран. Отчёт — строками ok/BAD, код возврата 1, если что-то
не так. Окно браузера несколько раз появится и спрячется — так и задумано.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта

for stream in (sys.stdout, sys.stderr):  # кириллица в консоли Windows
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright  # noqa: E402

from hh_auto.core import control  # noqa: E402
from hh_auto.core.config import Config  # noqa: E402
from hh_auto.core.logger import log  # noqa: E402
from hh_auto.web import browser, window as browser_window  # noqa: E402

failures: list[str] = []

PAGE = """<!doctype html><title>check</title>
<button id="b" onclick="this.textContent = 'нажата'" style="font-size: 40px">кнопка</button>
<div id="spin" style="width: 80px; height: 80px; background: #e33; animation: s 1s linear infinite"></div>
<style>@keyframes s { to { transform: rotate(360deg) } }</style>"""

# за секунду: сколько раз пришёл кадр анимации и сработал таймер на 100 мс; видна ли страница самой себе
MEASURE = """() => new Promise(done => {
  let frames = 0, ticks = 0;
  const tick = () => { frames++; requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  const timer = setInterval(() => ticks++, 100);
  setTimeout(() => { clearInterval(timer); done({visibility: document.visibilityState, frames, ticks}); }, 1000);
})"""


def check(name: str, ok: bool, detail="") -> None:
    print(f"{'ok ' if ok else 'BAD'} {name}" + (f" — {detail}" if detail not in ("", None) else ""), flush=True)
    if not ok:
        failures.append(name)


def measure_ok(m: dict) -> bool:
    """Как на экране: страница видима, кадров анимации ≥20 в секунду (обычно 60), таймер на 100 мс — ≥8 раз.
    Кадров меньше — работа идёт, но щелчки дольше (Playwright перед щелчком ждёт два кадра)."""
    return m["visibility"] == "visible" and m["frames"] >= 20 and m["ticks"] >= 8


class Window:
    """Что с окном на самом деле (не со слов web/window.py): своя связь с браузером или функции Windows."""

    def __init__(self, page):
        self.session = None
        if browser_window.MODE == "cdp":
            self.session = page.context.new_cdp_session(page)
            self.id = self.session.send("Browser.getWindowForTarget")["windowId"]

    def state(self) -> str:
        if self.session is None:
            return "hidden" if browser_window.is_hidden() else "shown"
        state = self.session.send("Browser.getWindowBounds", {"windowId": self.id})["bounds"]["windowState"]
        return "hidden" if state == "minimized" else "shown"


def from_other_thread(action) -> object:
    """Как кнопка в окне приложения: вызов не из рабочего потока."""
    result = []
    thread = threading.Thread(target=lambda: result.append(action()))
    thread.start()
    thread.join()
    return result[0]


def valid_jpeg(data: bytes) -> bool:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            return image.size[0] > 10 and image.size[1] > 10
    except ImportError:
        return data[:2] == b"\xff\xd8"
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=1.0, help="сколько держать окно спрятанным (по умолчанию 1)")
    parser.add_argument("--cdp", action="store_true", help="на Windows — сворачивать через CDP, как на macOS и Linux")
    parser.add_argument("--channel", default="chrome", help='chrome | msedge | "" (встроенный Chromium)')
    args = parser.parse_args()
    if args.cdp:
        browser_window.MODE = "cdp"
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("    · %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)

    profile = Path(tempfile.mkdtemp(prefix="hh-window-"))
    cfg = Config(search_url="")
    cfg.browser.channel, cfg.browser.headless, cfg.browser.hide_window = args.channel, False, True
    cfg.browser.profile_dir = str(profile)
    print(f"Система: {sys.platform}, как прятать: {browser_window.MODE}, браузер: {args.channel or 'chromium'}")
    check("прятать окно здесь умеем", browser.hides_window(cfg))
    frames: list[bytes] = []
    browser.frame_sink, browser.frame_wanted = frames.append, None
    control.reset()
    with sync_playwright() as pw:
        context = browser.launch(pw, cfg)
        try:
            page = context.pages[0]
            browser.attach(page)
            real = Window(page)
            check("окно спрятано сразу после запуска", real.state() == "hidden" and browser_window.is_hidden(),
                  real.state())
            page.set_content(PAGE)
            m = page.evaluate(MEASURE)
            check("спрятанная страница видима, кадры анимации и таймеры идут в полную силу", measure_ok(m), m)
            control.sleep(2 * browser.FRAME_EVERY + 0.5)
            check("кадры для превью снимаются", len(frames) >= 1 and valid_jpeg(frames[-1]),
                  f"{len(frames)} кадр., {len(frames[-1]) if frames else 0} байт")
            page.click("#b", timeout=5000)
            check("щелчок в спрятанном окне проходит", page.inner_text("#b") == "нажата")

            check("«Показать» из окна приложения принято", from_other_thread(browser_window.show))
            check("превью сразу говорит «на экране»", not browser_window.is_hidden())
            control.sleep(1)
            check("«Показать» выполнено в паузе", real.state() == "shown", real.state())
            check("«Спрятать» из окна приложения принято", from_other_thread(browser_window.hide))
            check("превью сразу говорит «спрятано»", browser_window.is_hidden())
            control.sleep(1)
            check("«Спрятать» выполнено в паузе", real.state() == "hidden", real.state())

            was_hidden = browser.show_for_user(page)
            check("вход и капча: окно выведено на экран", was_hidden and real.state() == "shown",
                  f"было спрятано: {was_hidden}, сейчас: {real.state()}")
            m = page.evaluate(MEASURE)
            check("на экране страница работает", measure_ok(m), m)
            browser.hide_after_user(was_hidden)
            check("после входа и капчи окно спрятано снова", real.state() == "hidden", real.state())

            worst, seen, deadline = None, 0, time.monotonic() + args.minutes * 60
            frames.clear()
            print(f"Держу окно спрятанным {args.minutes:g} мин…", flush=True)
            while time.monotonic() < deadline:
                control.sleep(min(10.0, max(deadline - time.monotonic(), 0)))
                m = page.evaluate(MEASURE)
                seen += 1
                if worst is None or m["frames"] < worst["frames"] or m["ticks"] < worst["ticks"]:
                    worst = m
            check(f"за {args.minutes:g} мин страница не «уснула» и не замедлилась",
                  worst is not None and measure_ok(worst), f"худший замер из {seen}: {worst}")
            check("окно всё это время спрятано", real.state() == "hidden" and browser_window.is_hidden())
            check("превью всё это время обновлялось",
                  len(frames) >= args.minutes * 60 / browser.FRAME_EVERY * 0.5, f"{len(frames)} кадр.")
            check("программа не оставила окно на экране", not browser_window.kept_shown(),
                  browser_window.kept_shown())
        finally:
            browser.detach()
            context.close()
    check("после закрытия окна браузера нет", browser_window.find() is None if browser_window.MODE == "cdp" else True)
    print("ВСЁ В ПОРЯДКЕ" if not failures else f"НЕ В ПОРЯДКЕ: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

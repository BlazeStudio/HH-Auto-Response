"""Обновление из релизов GitHub: кнопка «Обновить» в окне → скачать → заменить файлы → перезапустить.

Установщик для этого не нужен. Собранное приложение — это папка (HH-Auto-Response.exe + _internal на Windows,
HH-Auto-Response + _internal на Linux, HH-Auto-Response.app на macOS), и обновление кладёт на её место те же
файлы из архива нового релиза:

1. check()    — список релизов GitHub; берём самую новую версию, у которой есть архив под эту систему.
2. download() — архив в папку данных (update/), сверка SHA256 с SHA256SUMS.txt релиза, распаковка.
3. launch()   — запускаем НОВУЮ версию прямо из распакованной папки с --apply-update, старая закрывается.
4. apply()    — новая версия ждёт, пока старая закроется, откладывает старые файлы в *.old-<время>, копирует
                себя на их место и запускает обновлённое приложение. Не вышло — возвращает старые файлы.
5. cleanup()  — следующий запуск удаляет update/ и *.old-*.

Настройки, логи и вход в hh лежат в папке данных пользователя — обновление их не трогает. Нужно только право
записи в папку программы: если его нет (например, Program Files), окно предложит скачать архив вручную.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import __version__
from .paths import app_root, is_frozen

APP_NAME = "HH-Auto-Response"
REPO = "BlazeStudio/hh-auto-response"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
SUMS = "SHA256SUMS.txt"
# что откладывает apply(): только наши файлы, чужое в папке программы не трогаем
_LEFTOVER = re.compile(rf"^(?:{re.escape(APP_NAME)}(?:\.exe|\.app)?|_internal)\.old-\d+$")


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str  # «1.0.5-beta»
    page: str  # страница релиза на GitHub
    notes: str  # описание релиза (markdown)
    asset: str  # имя архива под эту систему
    url: str
    size: int
    sums_url: str


_STAGES = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "rc": 2}


def parse_version(text: str) -> tuple[int, int, int, int, int] | None:
    """«1.0.4-beta» → (1, 0, 4, 1, 0). Без пометки — финальный релиз: он новее любой беты той же версии."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-.]?(alpha|beta|rc|a|b)\.?(\d+)?)?", text.strip(), re.I)
    if not m:
        return None
    stage = _STAGES[m[4].lower()] if m[4] else 3
    return int(m[1]), int(m[2]), int(m[3] or 0), stage, int(m[5] or 0)


def asset_suffix() -> str:
    """Конец имени архива под эту систему — как их называет packaging/build.py."""
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
    if sys.platform == "win32":
        return "-windows.zip"
    if sys.platform == "darwin":
        return f"-macos-{arch}.zip"
    return f"-linux-{arch}.tar.gz"


def _request(url: str, accept: str = "application/octet-stream") -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{__version__}", "Accept": accept})


def check(current: str = __version__, timeout: float = 10) -> Release | None:
    """Самый новый релиз новее current с архивом под эту систему, или None. Нет связи — UpdateError."""
    api = os.environ.get("HH_UPDATE_API") or RELEASES_API  # свой адрес — чтобы проверить обновление без GitHub
    try:
        with urllib.request.urlopen(_request(api, "application/vnd.github+json"), timeout=timeout) as response:
            releases = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UpdateError(f"не удалось узнать о новых версиях: {e}") from e
    mine, suffix = parse_version(current), asset_suffix()
    best: tuple[tuple, Release] | None = None
    for rel in releases if isinstance(releases, list) else []:
        version = parse_version(str(rel.get("tag_name", "")))
        if rel.get("draft") or version is None or (mine is not None and version <= mine):
            continue
        assets = {a.get("name", ""): a for a in rel.get("assets") or []}
        archive = next((a for name, a in assets.items() if name.startswith(APP_NAME) and name.endswith(suffix)), None)
        if archive is None or SUMS not in assets:
            continue  # под эту систему архива нет или релиз ещё собирается
        if best is None or version > best[0]:
            best = (version, Release(version=str(rel["tag_name"]).lstrip("v"), page=rel.get("html_url") or RELEASES_PAGE,
                                     notes=(rel.get("body") or "").strip(), asset=archive["name"],
                                     url=archive["browser_download_url"], size=int(archive.get("size") or 0),
                                     sums_url=assets[SUMS]["browser_download_url"]))
    return best[1] if best else None


def install_dir() -> Path:
    """Что заменяет обновление: .app на macOS, папку с программой на Windows и Linux."""
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
    return exe.parent


def executable(root: Path) -> Path:
    if sys.platform == "darwin":
        return root / "Contents" / "MacOS" / APP_NAME
    return root / (f"{APP_NAME}.exe" if sys.platform == "win32" else APP_NAME)


def can_install() -> str:
    """Пусто — можно обновиться прямо из приложения; иначе — почему нельзя."""
    if not is_frozen():
        return "программа запущена из исходников — обновите их сами (git pull)"
    target = install_dir()
    folder = target.parent if sys.platform == "darwin" else target
    probe = folder / f".hh-update-check-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return f"нет прав на запись в папку {folder}"
    return ""


def update_dir() -> Path:
    return app_root() / "update"


def download(release: Release, progress: Callable[[int, int], None] | None = None, timeout: float = 30) -> Path:
    """Скачать архив, сверить контрольную сумму, распаковать. Возвращает новую версию (папку или .app).

    Каждая загрузка — в свою новую папку update/<версия>-<время>. Раньше это была всегда update/new, и если
    в ней что-то было занято (антивирус проверял свежие файлы, ещё работал установщик прошлого обновления
    или второй копии программы), распаковка поверх падала с «Permission denied»."""
    base = update_dir()
    folder = base / f"{release.version}-{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}"
    with _folders_lock:
        _our_folders.clear()  # защищена только последняя загрузка; прежние этого же запуска — убрать
        _our_folders.add(folder.resolve())  # фоновая чистка (cleanup) эту папку не тронет
        _remove_leftovers(base)  # прошлые загрузки — что получится; занятое уберётся в другой раз
        folder.mkdir(parents=True, exist_ok=True)
    archive = folder / release.asset
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(_request(release.sums_url), timeout=timeout) as response:
            sums = response.read().decode("utf-8", errors="replace")
        expected = next((parts[0].lower() for parts in (line.split() for line in sums.splitlines())
                         if len(parts) >= 2 and parts[-1].lstrip("*") == release.asset), "")
        if not expected:
            raise UpdateError(f"в {SUMS} релиза нет суммы для {release.asset}")
        with urllib.request.urlopen(_request(release.url), timeout=timeout) as response, open(archive, "wb") as file:
            total = int(response.headers.get("Content-Length") or release.size or 0)
            done = 0
            while chunk := response.read(256 * 1024):
                file.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"не удалось скачать обновление: {e}") from e
    if digest.hexdigest() != expected:
        raise UpdateError("архив скачался с ошибкой (не сходится контрольная сумма) — попробуйте ещё раз")
    unpacked = folder / "new"
    for attempt in range(3):  # антивирус может на секунду занять только что распакованный файл
        try:
            _extract(archive, unpacked)
            break
        except UpdateError:
            if attempt == 2:
                raise
            time.sleep(1.5)
            unpacked = folder / f"new{attempt + 2}"  # не поверх занятого — в соседнюю папку
    try:
        archive.unlink(missing_ok=True)
    except OSError:
        pass
    root = unpacked / (f"{APP_NAME}.app" if sys.platform == "darwin" else APP_NAME)
    if not executable(root).exists():
        raise UpdateError(f"в архиве {release.asset} нет приложения")
    return root


def _extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        if archive.name.endswith(".tar.gz"):
            with tarfile.open(archive) as tar:
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(dest, filter="data")
                else:
                    tar.extractall(dest)  # noqa: S202 — свой архив, сумма уже сверена
        elif sys.platform == "darwin":  # родной распаковщик: права и ссылки внутри .app
            subprocess.run(["ditto", "-x", "-k", str(archive), str(dest)], check=True)
        else:
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(dest)
    except PermissionError as e:
        raise UpdateError(f"не удалось распаковать обновление: файл занят другой программой ({e.filename}). "
                          "Чаще всего это антивирус проверяет новые файлы или открыта вторая копия программы — "
                          "закройте её и нажмите «Обновить» ещё раз") from e
    except (OSError, tarfile.TarError, zipfile.BadZipFile, subprocess.CalledProcessError) as e:
        raise UpdateError(f"не удалось распаковать обновление: {e}") from e


def _detached(args: list[str]) -> None:
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(args, creationflags=flags, close_fds=True)
    else:
        subprocess.Popen(args, start_new_session=True, close_fds=True)


def launch(root: Path) -> None:
    """Запустить новую версию из распакованной папки: она дождётся нашего выхода и заменит программу."""
    _detached([str(executable(root)), "--apply-update", str(install_dir()), "--wait-pid", str(os.getpid())])


def _wait_exit(pid: int, timeout: float = 90) -> bool:
    if pid <= 0:
        return True
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return True  # процесса уже нет
        try:
            return kernel.WaitForSingleObject(wintypes.HANDLE(handle), int(timeout * 1000)) == 0
        finally:
            kernel.CloseHandle(wintypes.HANDLE(handle))
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        try:  # Linux: завершившийся, но ещё не убранный родителем процесс («зомби») — уже не мешает
            with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
                if stat.read().rsplit(")", 1)[-1].split()[0] == "Z":
                    return True
        except (OSError, IndexError):
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.3)


def _retry(action: Callable[[], object], timeout: float = 20) -> None:
    """Windows ещё пару секунд после выхода программы держит её файлы (антивирус, проводник) — повторяем."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            action()
            return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def _long(path: Path) -> str:
    """Путь, который Windows примет и длиннее 260 символов. Отложенные «_internal.old-…» на 15 символов длиннее
    настоящих, и при длинном пути к программе обычное удаление их не берёт (если длинные пути выключены)."""
    text = str(path.resolve())
    return "\\\\?\\" + text if sys.platform == "win32" and not text.startswith("\\\\") else text


# Загрузки этого запуска программы: фоновая чистка через 20 с после старта удаляла и их — если «Обновить»
# нажали сразу, архив исчезал прямо во время распаковки («в архиве … нет приложения»; со второго раза — ок)
_our_folders: set[Path] = set()
_folders_lock = threading.Lock()


def _remove_leftovers(base: Path) -> None:
    """Убрать прошлые загрузки обновлений; занятые (установщик ещё работает) оставить — уберутся потом,
    загрузки этого запуска (идёт или готова к установке) — не трогать."""
    try:
        items = list(base.iterdir())
    except OSError:
        return
    for item in items:
        try:
            if item.resolve() in _our_folders:
                continue
        except OSError:
            continue
        _remove(item)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(_long(path), ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink(missing_ok=True)


def apply(target: Path, wait_pid: int, status: Callable[[str], None] = lambda _text: None,
          source: Path | None = None) -> None:
    """Заменить программу в target этой (новой) версией. Запускается из распакованного обновления.
    source — откуда копировать (по умолчанию — папка, из которой запущена эта версия)."""
    if source is None:
        if not is_frozen():  # из исходников «папка программы» — это папка Python: её не трогаем никогда
            raise UpdateError("обновление ставится только в собранное приложение")
        source = install_dir()
    if source.resolve() == target.resolve():
        raise UpdateError("обновление запущено не из папки обновления")
    status("Жду, пока закроется старая версия…")
    if not _wait_exit(wait_pid):
        raise UpdateError("старая версия не закрылась — закройте её и повторите обновление")
    status("Устанавливаю новую версию…")
    if sys.platform == "darwin":
        pairs = [(source, target)]
    else:
        pairs = [(item, target / item.name) for item in source.iterdir()]
    stamp = int(time.time())
    done: list[tuple[Path, Path | None]] = []
    try:
        for new, old in pairs:
            backup = None
            if old.exists() or old.is_symlink():
                backup = old.with_name(f"{old.name}.old-{stamp}")
                _retry(lambda: old.rename(backup))
            done.append((old, backup))
            if new.is_dir() and not new.is_symlink():
                shutil.copytree(new, old, symlinks=True)
            else:
                shutil.copy2(new, old, follow_symlinks=False)
    except Exception as e:
        for old, backup in reversed(done):  # вернуть как было
            _remove(old)
            if backup is not None:
                try:
                    backup.rename(old)
                except OSError:
                    pass
        raise UpdateError(f"не удалось заменить файлы программы ({e}); старая версия оставлена как была") from e


def restart(target: Path, *args: str) -> None:
    if sys.platform == "darwin":
        _detached(["open", "-n", str(target), "--args", *args])
    else:
        _detached([str(executable(target)), *args])


def cleanup() -> None:
    """Убрать остатки прошлого обновления: распакованный архив и отложенные старые файлы."""
    if not is_frozen():
        return
    with _folders_lock:
        _remove_leftovers(update_dir())
    target = install_dir()
    folder = target.parent if sys.platform == "darwin" else target
    try:
        leftovers = [item for item in folder.iterdir() if _LEFTOVER.match(item.name)]
    except OSError:
        return
    for item in leftovers:
        _remove(item)


def _log(text: str) -> None:
    try:
        path = app_root() / "logs" / "update.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {__version__}: {text}\n")
    except OSError:
        pass


def run_apply(argv: list[str]) -> int:
    """HH-Auto-Response --apply-update <папка программы> --wait-pid <pid старой версии>.

    Небольшое окно «Обновляю…», замена файлов, запуск обновлённого приложения. При ошибке — сообщение
    и запуск прежней версии (файлы к этому времени уже возвращены). HH_UPDATE_QUIET=1 — без окна
    и без перезапуска (для проверки обновления в тестах)."""
    target = Path(argv[argv.index("--apply-update") + 1])
    wait_pid = int(argv[argv.index("--wait-pid") + 1]) if "--wait-pid" in argv else 0
    quiet = bool(os.environ.get("HH_UPDATE_QUIET"))
    _log(f"обновляю {target} (ждём закрытия pid {wait_pid})")
    result: dict[str, str] = {}

    def work(status: Callable[[str], None]) -> None:
        try:
            apply(target, wait_pid, status)
            result["ok"] = "1"
            _log("готово")
        except Exception as e:  # noqa: BLE001 — любую ошибку показываем человеку и пишем в журнал
            result["error"] = str(e)
            _log(f"ошибка: {e}")

    if quiet:
        work(lambda _text: None)
        return 0 if "ok" in result else 1

    _progress_window(work, result)
    if "error" in result:
        if _wait_exit(wait_pid, timeout=0):  # файлы возвращены — запускаем прежнюю версию (если она закрыта)
            restart(target)
        return 1
    restart(target, "--updated")
    return 0


def _progress_window(work: Callable[[Callable[[str], None]], None], result: dict[str, str]) -> None:
    """Окно «Устанавливаю…» в стиле приложения (иконка программы, полоса прогресса) на время замены файлов.
    Ошибка — окно с объяснением; закрыли его — закрылось и это."""
    import tkinter

    import customtkinter as ctk

    from . import dialogs
    from .theme import CARD_BG, GREEN, MUTED, TEXT, apply_app_icon, badge

    try:  # светлое или тёмное оформление — как выбрано в приложении
        prefs = json.loads((app_root() / "app_prefs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prefs = {}
    ctk.set_appearance_mode(prefs.get("appearance", "system"))
    window = ctk.CTk(fg_color=CARD_BG)
    window.title(f"Обновление {APP_NAME}")
    window.resizable(False, False)
    apply_app_icon(window)
    frame = ctk.CTkFrame(window, fg_color="transparent")
    frame.pack(fill="both", expand=True, padx=26, pady=24)
    head = ctk.CTkFrame(frame, fg_color="transparent")
    head.pack(fill="x")
    ctk.CTkLabel(head, text="", image=badge("update", 44)).pack(side="left", padx=(0, 16))
    column = ctk.CTkFrame(head, fg_color="transparent")
    column.pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(column, text=f"Устанавливаю {APP_NAME} {__version__}", text_color=TEXT, anchor="w",
                 font=ctk.CTkFont(size=17, weight="bold")).pack(fill="x")
    status = ctk.CTkLabel(column, text="Готовлюсь…", text_color=MUTED, anchor="w", font=ctk.CTkFont(size=13))
    status.pack(fill="x", pady=(4, 0))
    bar = ctk.CTkProgressBar(frame, mode="indeterminate", height=6, corner_radius=3, progress_color=GREEN)
    bar.pack(fill="x", pady=(20, 0))
    bar.start()
    ctk.CTkLabel(frame, text="Настройки, логи и вход в hh остаются на месте", text_color=MUTED, anchor="w",
                 font=ctk.CTkFont(size=12)).pack(fill="x", pady=(12, 0))

    window.update_idletasks()
    scaling = ctk.ScalingTracker.get_window_scaling(window)
    w, h = max(round(460 * scaling), window.winfo_reqwidth()), window.winfo_reqheight()
    x, y = (window.winfo_screenwidth() - w) // 2, (window.winfo_screenheight() - h) // 3
    tkinter.Tk.geometry(window, f"{w}x{h}+{x}+{y}")

    statuses: list[str] = []
    thread = threading.Thread(target=work, args=(statuses.append,), daemon=True)
    thread.start()

    def poll() -> None:
        if statuses:
            status.configure(text=statuses[-1])
        if thread.is_alive():
            window.after(150, poll)
            return
        bar.stop()
        if "error" in result:
            status.configure(text="Не получилось — старая версия на месте")
            dialogs.showerror(APP_NAME, f"Обновление не установилось\n\n{result['error']}\n\n"
                                        f"Скачать архив вручную можно на странице релизов: {RELEASES_PAGE}",
                              parent=window)
        window.destroy()

    window.after(150, poll)
    window.mainloop()

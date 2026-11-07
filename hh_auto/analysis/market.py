"""Рынок вакансий для анализа резюме — data/market.json.

Что hh отдаёт вместе со страницей выдачи (vacancy_meta.py): название, ключевые навыки, требования,
зарплату, опыт, формат работы — по каждой вакансии. Вакансии приходят двумя путями:
  • при обычных откликах — страница выдачи и так открыта, отдельно ничего не загружается;
  • «Проверить рынок» на вкладке «Анализ резюме» — общий поиск hh по должности из резюме, без фильтров и
    подбора «подходящие к резюме»: так видно, что просят в профессии вообще. Смотрится вся выдача, а не
    первые страницы. Ни на что не откликается.
Хранится KEEP_DAYS дней и не больше LIMIT вакансий: рынок меняется, старое только мешает.
"""

from __future__ import annotations

import json
import math
import random
import re
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..core import control
from ..core.config import Config
from ..core.logger import log
from ..hh.filters import EXPERIENCE, WORK_FORMAT
from ..hh.vacancy_meta import VacancyMeta, read_meta

FILE = "market.json"
KEEP_DAYS = 45
LIMIT = 15_000  # полная проверка широкого запроса («Python» — 5000+) не должна вытеснять сама себя
HH_CAP = 2000  # больше hh по одному поиску не показывает: дальше 20-й страницы по 100 — пусто
SCAN_LIMIT = 10_000  # больше за одну проверку не смотрим (100 страниц — минут 7)
PER_PAGE = 100
# Чем делить поиск, в котором больше HH_CAP: сначала опытом (он у вакансии один — части не пересекаются),
# потом форматом работы (их бывает несколько — одна вакансия попадёт в две части, но считается один раз)
SPLITS = (("experience", "опыт", EXPERIENCE), ("work_format", "формат", WORK_FORMAT))
# Темп как у человека, листающего выдачу: пауза между страницами и передышка каждые BREAK_EVERY страниц.
# Полная проверка — десятки страниц подряд: быстрее hh начинал показывать капчу
PAGE_PAUSE = (3.0, 6.0)
BREAK_EVERY, BREAK = 20, (20.0, 40.0)
POOL_DAYS = 30  # анализ — по вакансиям, которые видели за последний месяц
NET = 0.87  # «до вычета налогов» → на руки (НДФЛ 13%)
_lock = threading.Lock()
_cache: dict[Path, tuple] = {}  # файл → (отметка изменения, разобранные данные)

_TOTAL_JS = """() => {
  const el = document.getElementById('HH-Lux-InitialState');
  if (!el) return null;
  try {
    const state = JSON.parse(el.content ? el.content.textContent : el.textContent);
    return ((state || {}).vacancySearchResult || {}).totalResults ?? null;
  } catch (e) { return null; }
}"""


@dataclass
class MarketVacancy:
    id: str
    title: str
    skills: tuple[str, ...]  # ключевые навыки как написаны в карточке
    text: str  # требования и обязанности из карточки
    experience: str  # noExperience, between1And3, between3And6, moreThan6 или ""
    salary: int | None  # ₽ в месяц на руки (середина вилки); другая валюта или почасовая — None
    formats: tuple[str, ...]
    english: str
    area: str
    seen: str  # когда видели в последний раз, ГГГГ-ММ-ДД
    queries: tuple[str, ...]  # в каких выдачах встречалась
    match: int | None  # «подходит по навыкам на N%» от hh

    @property
    def url(self) -> str:
        return f"https://hh.ru/vacancy/{self.id}"


def net_salary(meta: VacancyMeta) -> int | None:
    """Зарплата для сравнения: ₽ в месяц на руки, середина вилки. Иначе (валюта, за час) — None."""
    if meta.currency not in ("RUR", "RUB") or meta.salary_mode not in ("", "MONTH"):
        return None
    low, high = meta.salary_from, meta.salary_to
    value = (low + high) / 2 if low and high else low or high
    if not value:
        return None
    if meta.gross:
        value *= NET
    return round(value) if 10_000 <= value <= 3_000_000 else None


class MarketStore:
    def __init__(self, root: Path):
        self.path = root / "data" / FILE
        self._own: tuple | None = None  # (отметка файла, данные) — что этот же объект записал в прошлый раз

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("vacancies", {})
        data.setdefault("found", {})  # выдача → [день, сколько вакансий нашёл hh]
        return data

    def record(self, metas: Iterable[VacancyMeta], label: str, total: int | None = None) -> int:
        """Записать вакансии со страницы выдачи label. Возвращает, сколько новых."""
        today = date.today().isoformat()
        with _lock:
            # тот же запуск пишет страницу за страницей: файл в мегабайты не перечитываем, если его не трогали
            data = self._own[1] if self._own and self._own[0] == self.stamp() else self.load()
            vacancies = data["vacancies"]
            fresh = 0
            for meta in metas:
                if not meta.id or not (meta.title or meta.skills):
                    continue
                old = vacancies.get(meta.id)
                fresh += old is None
                queries = list(dict.fromkeys((old or {}).get("q", []) + [label]))[-5:]
                vacancies[meta.id] = {
                    "t": meta.title, "s": list(meta.skills), "r": meta.requirement, "e": meta.experience,
                    "p": net_salary(meta), "f": list(meta.formats), "l": meta.english, "a": meta.area, "d": today,
                    "q": queries, "m": meta.match,
                }
            if total is not None:
                data["found"][label] = [today, total]
            border = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
            kept = sorted(((k, v) for k, v in vacancies.items() if v.get("d", "") >= border),
                          key=lambda item: item[1].get("d", ""), reverse=True)[:LIMIT]
            data["vacancies"] = dict(kept)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temp.replace(self.path)
            self._own = (self.stamp(), data)
            return fresh

    def _read(self) -> dict:
        """Для чтения: тот же разобранный файл, пока он не менялся (вкладка и расчёт читают его несколько раз
        подряд, а это мегабайты). Не изменять: record() берёт свою свежую копию."""
        stamp = self.stamp()
        with _lock:
            cached = _cache.get(self.path)
            if cached is not None and cached[0] == stamp:
                return cached[1]
        data = self.load()
        with _lock:
            _cache[self.path] = (stamp, data)
        return data

    def count(self, days: int = POOL_DAYS) -> int:
        border = (date.today() - timedelta(days=days)).isoformat()
        return sum(1 for v in self._read()["vacancies"].values() if v.get("d", "") >= border)

    def vacancies(self, days: int = POOL_DAYS, label: str | None = None) -> list[MarketVacancy]:
        border = (date.today() - timedelta(days=days)).isoformat()
        result = []
        for vid, v in self._read()["vacancies"].items():
            if v.get("d", "") < border or (label and label not in v.get("q", [])):
                continue
            result.append(MarketVacancy(
                id=vid, title=v.get("t", ""), skills=tuple(v.get("s") or ()), text=v.get("r", ""),
                experience=v.get("e", ""), salary=v.get("p"), formats=tuple(v.get("f") or ()), english=v.get("l", ""),
                area=v.get("a", ""), seen=v.get("d", ""), queries=tuple(v.get("q") or ()), match=v.get("m")))
        return result

    def labels(self, days: int = POOL_DAYS) -> list[tuple[str, int]]:
        """Выдачи, из которых есть вакансии, и сколько из каждой — частые первыми."""
        border = (date.today() - timedelta(days=days)).isoformat()
        counts = Counter(q for v in self._read()["vacancies"].values() if v.get("d", "") >= border
                         for q in v.get("q", []))
        return counts.most_common()

    def found(self) -> dict[str, tuple[str, int]]:
        return {label: (day, total) for label, (day, total) in self._read()["found"].items()}

    def stamp(self) -> tuple | None:
        """Изменился ли файл (чтобы не пересчитывать анализ зря)."""
        try:
            info = self.path.stat()
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size

    def clear(self) -> None:
        with _lock:
            try:
                self.path.unlink()
            except OSError:
                pass


# ─────────────────────────── «Проверить рынок» ───────────────────────────

def _with_params(url: str, **params: str) -> str:
    u = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k not in params]
    query += list(params.items())
    return urlunparse(u._replace(query=urlencode(query)))


# уровень и «ведущий» в названии сужают поиск — рынок профессии ищем без них
_LEVEL = re.compile(r"(?<!\w)(?:junior|middle|senior|team ?lead|lead|младший|старший|ведущий|главный|стажер|стажёр|"
                    r"intern|trainee|head|principal|chief)(?!\w)\+?", re.I)


def market_query(title: str) -> str:
    """Что искать, чтобы увидеть рынок профессии: должность из резюме без уточнений в скобках, второй должности
    через «/» и уровня. «Senior Python-разработчик (Backend, LLM / AI-агенты)» → «Python разработчик»."""
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", title)
    text = re.split(r"[/,;|•]|\s+или\s+", text)[0]
    text = _LEVEL.sub(" ", text)
    text = re.sub(r"(?<=\w)-(?=\w)", " ", text)  # «Python-разработчик» — hh найдёт и «Python разработчик»
    return " ".join(text.split()).strip(" -—–:")[:80]


def query_url(cfg: Config, text: str) -> str:
    """Общий поиск hh по запросу — в вашем регионе (из настроек или из ссылки поиска), без остальных фильтров
    и без подбора под резюме: чтобы видеть рынок целиком, а не только то, на что вы откликаетесь."""
    from ..hh.search import SEARCH_PAGE

    areas = [cfg.search.area] if cfg.search.area else \
        [v for k, v in parse_qsl(urlparse(cfg.search_url or "").query) if k == "area"]
    query = [("text", text)] + [("area", a) for a in areas] + [("enable_snippets", "true")]
    return f"{SEARCH_PAGE}?{urlencode(query)}"


def scan(page, cfg: Config, root: Path, query: str, result: dict | None = None) -> None:
    """Общий поиск hh по запросу query (обычно — должность из резюме, market_query): записать для анализа всю
    выдачу. hh показывает по одному поиску не больше HH_CAP вакансий — такой поиск делится по опыту, а часть,
    где и так больше, — ещё по формату работы. Ни на что не откликается. result: "seen" — сколько разных вакансий
    просмотрено, "added" — из них новых, "total" — сколько нашёл hh, "label" — выдача, под которой они записаны
    (её и стоит выбрать для сравнения)."""
    from ..hh.search import results_page_url
    from ..web.auth import is_auth_url
    from ..web.browser import FatalError, open_url, slowdown, wait_captcha

    result = result if result is not None else {}
    result.setdefault("added", 0)
    result.setdefault("seen", 0)
    query = " ".join(query.split())
    if not query:
        raise FatalError("не задано, что искать: впишите должность в «Проверить по запросу»")
    label = f"проверка рынка: «{query}»"
    result["label"] = label
    store = MarketStore(root)
    seen: set[str] = set()
    opened = 0

    def open_page(url: str, page_num: int) -> tuple[dict[str, VacancyMeta], int | None]:
        nonlocal opened
        control.check()
        if opened and opened % BREAK_EVERY == 0:
            rest = random.uniform(*BREAK) * slowdown()
            log.info(f"  ⏸ передышка {rest:.0f} с — просмотрено {opened} стр. подряд")
            control.sleep(rest)
        elif opened:  # пауза между страницами, как у человека; после капчи — дольше
            control.sleep(random.uniform(*PAGE_PAUSE) * slowdown())
        opened += 1
        page_url = results_page_url(url, page_num)
        log.debug(f"  открываю: {page_url}")
        open_url(page, page_url)
        wait_captcha(page)
        if is_auth_url(page.url):
            raise FatalError("hh просит войти — запустите «Отклики» и войдите в открывшемся окне")
        total = None
        if page_num == 0:
            try:
                total = page.evaluate(_TOTAL_JS)
            except Exception:  # noqa: BLE001 — сколько всего, необязательно
                pass
        return read_meta(page), total

    def save(metas: dict[str, VacancyMeta], total: int | None = None) -> int:
        added = store.record(metas.values(), label, total)
        seen.update(metas)
        result["added"] += added
        result["seen"] = len(seen)
        return added

    def walk(url: str, part: str, level: int) -> None:
        """Одна выдача целиком; больше HH_CAP — делим её на части (SPLITS[level]) и проходим каждую."""
        metas, total = open_page(url, 0)
        if level == 0:
            result["total"] = total
            log.info(f"  hh нашёл вакансий: {total if total is not None else 'не сказал сколько'}")
        if not metas:
            log.info(f"  {part}: вакансий нет")
            return
        if total and total > HH_CAP and level < len(SPLITS):
            key, what, values = SPLITS[level]
            log.info(f"  {part}: {total} — больше {HH_CAP}, которые hh показывает по одному поиску; делю по «{what}»")
            save(metas, total if level == 0 else None)  # первая страница уже открыта — её вакансии тоже в счёт
            for value, title in values.items():
                walk(_with_params(url, **{key: value}), f"{part} · {title.lower()}", level + 1)
            return
        if total and total > HH_CAP:
            log.warning(f"  ! {part}: {total} — hh покажет только первые {HH_CAP}")
        pages = math.ceil(min(total or len(metas), HH_CAP) / PER_PAGE)
        added = save(metas, total if level == 0 else None)
        for page_num in range(1, pages):
            if len(seen) >= SCAN_LIMIT:
                log.warning(f"  ! просмотрено {len(seen)} вакансий — на одну проверку хватит, остальное пропускаю")
                return
            metas, _ = open_page(url, page_num)
            if not metas:
                break
            added += save(metas)
        log.success(f"  ✓ {part}: {pages} стр., новых для анализа {added} · всего просмотрено {len(seen)}")

    log.info("")
    log.info(f"Проверка рынка: общий поиск hh «{query}» — вся выдача, без фильтров и откликов")
    walk(_with_params(query_url(cfg, query), items_on_page=str(PER_PAGE)), f"«{query}»", 0)
    log.info("")
    log.success(f"Проверка рынка закончена: просмотрено {len(seen)} вакансий, новых {result['added']}"
                + (f" (hh нашёл {result['total']})" if result.get("total") else ""))

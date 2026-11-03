"""Рынок вакансий для анализа резюме — data/market.json.

Что hh отдаёт вместе со страницей выдачи (vacancy_meta.py): название, ключевые навыки, требования,
зарплату, опыт, формат работы — по каждой вакансии. Вакансии приходят двумя путями:
  • при обычных откликах — страница выдачи и так открыта, отдельно ничего не загружается;
  • «Проверить рынок» на вкладке «Анализ резюме» — программа листает выдачу, ни на что не откликаясь.
Хранится KEEP_DAYS дней и не больше LIMIT вакансий: рынок меняется, старое только мешает.
"""

from __future__ import annotations

import json
import random
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
from ..hh.vacancy_meta import VacancyMeta, read_meta

FILE = "market.json"
KEEP_DAYS = 45
LIMIT = 6000
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
            data = self.load()
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


def query_url(cfg: Config, text: str) -> str:
    """Общий поиск hh по запросу — в вашем регионе (из настроек или из ссылки поиска), без остальных фильтров:
    чтобы видеть рынок целиком, а не только то, на что вы откликаетесь."""
    from ..hh.search import SEARCH_PAGE

    areas = [cfg.search.area] if cfg.search.area else \
        [v for k, v in parse_qsl(urlparse(cfg.search_url or "").query) if k == "area"]
    query = [("text", text)] + [("area", a) for a in areas] + [("enable_snippets", "true")]
    return f"{SEARCH_PAGE}?{urlencode(query)}"


def scan(page, cfg: Config, root: Path, query: str = "", pages: int = 5, result: dict | None = None) -> None:
    """Пройти pages страниц выдачи — по запросу query или (пусто) по поиску из настроек — и записать вакансии.
    Ни на что не откликается. result["added"], result["seen"] — сколько новых и всего просмотрено."""
    from ..hh.search import results_page_url, search_urls
    from ..web.auth import is_auth_url
    from ..web.browser import FatalError, open_url, wait_captcha

    result = result if result is not None else {}
    result.setdefault("added", 0)
    result.setdefault("seen", 0)
    searches = [(f"проверка рынка: «{query}»", query_url(cfg, query))] if query else search_urls(cfg)
    store = MarketStore(root)
    log.info("")
    log.info(f"Проверка рынка: {len(searches)} выдач(и), до {pages} стр. в каждой — без откликов")
    for number, (label, url) in enumerate(searches, 1):
        log.info("")
        log.info(f"━━━━━━━━━━ {label} ━━━━━━━━━━")
        url = _with_params(url, items_on_page="100")  # меньше страниц — меньше запросов к hh
        seen_here, total = 0, None
        for page_num in range(pages):
            control.check()
            page_url = results_page_url(url, page_num)
            log.info(f"  страница {page_num + 1}: {page_url}")
            open_url(page, page_url)
            wait_captcha(page)
            if is_auth_url(page.url):
                raise FatalError("hh просит войти — запустите «Отклики» и войдите в открывшемся окне")
            metas = read_meta(page)
            if page_num == 0:
                try:
                    total = page.evaluate(_TOTAL_JS)
                except Exception:  # noqa: BLE001 — сколько всего, необязательно
                    total = None
            if not metas:
                log.info("  вакансий на странице нет — выдача закончилась")
                break
            added = store.record(metas.values(), label, total if page_num == 0 else None)
            result["added"] += added
            result["seen"] += len(metas)
            seen_here += len(metas)
            log.success(f"  ✓ вакансий: {len(metas)}, новых для анализа: {added}"
                        + (f" (всего по запросу hh нашёл {total})" if total and page_num == 0 else ""))
            if total is not None and seen_here >= total:
                log.info("  это последняя страница выдачи")
                break
            if page_num + 1 < pages:
                control.sleep(random.uniform(2.0, 4.0))
        if number < len(searches):
            control.sleep(random.uniform(2.0, 4.0))
    log.info("")
    log.success(f"Проверка рынка закончена: просмотрено {result['seen']}, новых {result['added']}")

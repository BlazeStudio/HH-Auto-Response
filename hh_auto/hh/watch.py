"""Фоновая проверка: что пора сделать, пока приложение открыто (или по расписанию из консоли).

  • подъём резюме ([raising]) — раз в 4 часа, сроки в data/raising.json;
  • приглашения в чатах ([watch] invitations) — раз в [watch] every_minutes;
  • «компании мечты» ([dream]) — их свежие вакансии, раз в [watch] every_minutes.
Когда что проверяли — data/watch.json. Всё, чему подошло время, делается за один запуск браузера.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from . import raising
from ..core.config import Config, employer_id

FILE = "watch.json"
TASKS = {"raise": "подъём резюме", "invitations": "приглашения в чатах", "dream": "компании мечты"}


def _path(root: Path) -> Path:
    return root / "data" / FILE


def _load(root: Path) -> dict:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def last_run(root: Path, task: str) -> datetime | None:
    try:
        return datetime.fromisoformat(_load(root).get(task, ""))
    except (TypeError, ValueError):
        return None


def mark(root: Path, task: str, when: datetime | None = None) -> None:
    data = _load(root)
    data[task] = (when or datetime.now()).isoformat(timespec="seconds")
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def next_check(root: Path, cfg: Config, task: str, now: datetime | None = None) -> datetime | None:
    """Когда пора проверять чаты или «компании мечты» (None — выключено)."""
    if task == "invitations" and not cfg.watch.invitations:
        return None
    if task == "dream" and not (cfg.dream.enabled and cfg.dream.companies):
        return None
    last = last_run(root, task)
    now = now or datetime.now()
    return now if last is None else last + timedelta(minutes=max(cfg.watch.every_minutes, 15))


def due_tasks(root: Path, cfg: Config, now: datetime | None = None) -> list[str]:
    """Чему подошло время — по порядку: подъём резюме, приглашения, компании мечты."""
    now = now or datetime.now()
    tasks = []
    due = raising.next_due(root, cfg, now)
    if due is not None and due <= now:
        tasks.append("raise")
    for task in ("invitations", "dream"):
        moment = next_check(root, cfg, task, now)
        if moment is not None and moment <= now:
            tasks.append(task)
    return tasks


def next_any(root: Path, cfg: Config, now: datetime | None = None) -> datetime | None:
    """Ближайшая фоновая проверка любого вида (для подписи в настройках)."""
    now = now or datetime.now()
    moments = [raising.next_due(root, cfg, now)] + [next_check(root, cfg, t, now) for t in ("invitations", "dream")]
    moments = [m for m in moments if m is not None]
    return min(moments) if moments else None


def _with_params(url: str, **params: str) -> str:
    u = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k not in params and k != "page"]
    query += list(params.items())
    return urlunparse(u._replace(query=urlencode(query)))


def dream_searches(cfg: Config) -> list[tuple[str, str]]:
    """Выдачи «компаний мечты»: ваш поиск (запросы или ссылка «подходящие к резюме») + фильтры hh, только
    эта компания, свежие сверху, за последние 3 дня (каждую проверку смотрим только новое)."""
    from .search import SEARCH_PAGE, build_search_url

    if cfg.search.mode == "query":
        queries = [q.strip() for q in cfg.search.queries if q.strip()]
        if not queries:
            return []
        # несколько запросов — одним поиском через OR (язык запросов hh)
        text = queries[0] if len(queries) == 1 else " OR ".join(f"({q})" for q in queries)
        base = build_search_url(cfg, text)
    else:
        base = build_search_url(cfg) if cfg.search_url else ""
    if not base or not base.startswith(SEARCH_PAGE):
        return []
    period = dict(parse_qsl(urlparse(base).query)).get("search_period")
    searches = []
    for company in cfg.dream.companies:
        number = employer_id(company)
        if number:
            url = _with_params(base, employer_id=number, order_by="publication_time",
                               search_period="1" if period == "1" else "3")
            searches.append((f"компания мечты hh.ru/employer/{number}", url))
    return searches

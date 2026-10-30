"""Поднятие резюме в поиске hh — бесплатно, раз в 4 часа на каждое резюме ([raising]).

Программа делает то же, что кнопка «Поднять в поиске» на странице «Мои резюме»: тот же запрос с той же
страницы, в вашем браузере и под вашим входом. Логин и пароль ей не нужны и нигде не хранятся.

Когда поднимали и когда снова можно — в data/raising.json: по нему приложение решает, пора ли открывать
браузер, не трогая hh лишний раз.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import Page

from .auth import RESUMES_URL, on_resumes_page
from .config import Config
from .logger import log

EVERY = timedelta(hours=4)  # hh поднимает бесплатно не чаще раза в 4 часа
RETRY_EARLY = timedelta(hours=1)  # hh ответил «рано», а когда будет можно — со страницы не понять
RETRY_ERROR = timedelta(minutes=30)  # сбой сети или hh — попробуем позже, а не каждые 15 секунд

_ID = re.compile(r"/resume/([0-9a-f]{16,})", re.I)
# «Обновлено сегодня в 15:46» — поднимали (здесь или на hh вручную); «Поднять можно в 19:46» — когда снова можно
_UPDATED = re.compile(r"(?:обновлен\w*|поднят\w*)\s+(?:сегодня\s+)?в\s+(\d{1,2}):(\d{2})", re.I)
_AVAILABLE = re.compile(r"подн\w*[^\n]{0,40}?(?:в|после)\s+(\d{1,2}):(\d{2})", re.I)

# Тот же запрос, что делает кнопка «Поднять в поиске» на странице «Мои резюме»
_TOUCH_JS = """async ({id, xsrf}) => {
    const body = new FormData();
    body.append("resume", id);
    body.append("undirectable", "true");
    try {
        const response = await fetch("/applicant/resumes/touch", {method: "POST", body, credentials: "same-origin",
            headers: {"X-Xsrftoken": xsrf, "X-Requested-With": "XMLHttpRequest"}});
        return response.status;
    } catch (e) {
        return 0;
    }
}"""

# Резюме на странице «Мои резюме»: id из ссылки, название и текст карточки (по нему — когда поднимали)
_LIST_JS = """() => {
    const seen = new Map();
    for (const link of document.querySelectorAll('a[href*="/resume/"]')) {
        const m = link.getAttribute("href").match(/\\/resume\\/([0-9a-f]{16,})/i);
        if (!m || seen.has(m[1])) continue;
        const card = link.closest('[data-qa="resume"]') || link.closest("article, li, section") || link.parentElement;
        const title = (card && card.getAttribute("data-qa-title")) || link.innerText || "";
        seen.set(m[1], {id: m[1], title: title.trim().split("\\n")[0], text: card ? card.innerText : ""});
    }
    return [...seen.values()];
}"""


@dataclass
class Resume:
    id: str
    title: str = ""
    text: str = ""  # текст карточки на странице «Мои резюме»


@dataclass
class RaiseResult:
    raised: list[str] = field(default_factory=list)  # названия поднятых
    early: list[str] = field(default_factory=list)  # hh: ещё не прошло 4 часа
    failed: list[str] = field(default_factory=list)
    next_at: datetime | None = None  # когда снова пора

    def summary(self) -> str:
        parts = []
        if self.raised:
            parts.append("резюме поднято" if len(self.raised) == 1 else f"поднято резюме: {len(self.raised)}")
        if self.early:
            parts.append("рано — hh поднимает раз в 4 часа" if not self.raised else f"рано: {len(self.early)}")
        if self.failed:
            parts.append(f"не получилось: {len(self.failed)}")
        if self.next_at is not None:
            parts.append(f"следующее в {self.next_at:%H:%M}")
        return ", ".join(parts) or "поднимать нечего"


def _state_path(root: Path) -> Path:
    return root / "data" / "raising.json"


def load_state(root: Path) -> dict:
    try:
        data = json.loads(_state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(root: Path, state: dict) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(path)


def last_raised(root: Path) -> datetime | None:
    """Когда в последний раз подняли (любое резюме)."""
    resumes = load_state(root).get("resumes") or {}
    moments = [m for m in (_when(e.get("raised")) for e in resumes.values() if isinstance(e, dict)) if m]
    return max(moments) if moments else None


def forget_due(root: Path) -> None:
    """«Поднять сейчас»: забыть сроки — пора для всех резюме, а рано ли, решит сам hh."""
    state = load_state(root)
    for entry in (state.get("resumes") or {}).values():
        entry.pop("next", None)
    state.pop("retry", None)
    _save_state(root, state)


def _when(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def _targets(cfg: Config, known: list[str]) -> list[str]:
    """Какие резюме поднимать: из ссылки (resume_url или resume= в ссылке поиска), а если её нет или
    включено all_resumes — все с прошлой проверки."""
    if cfg.raising.all_resumes or not cfg.resume_id:
        return known
    return [cfg.resume_id]


def next_due(root: Path, cfg: Config, now: datetime | None = None) -> datetime | None:
    """Когда пора поднимать (None — выключено). Ни разу не проверяли — прямо сейчас (now)."""
    if not cfg.raising.enabled:
        return None
    now = now or datetime.now()
    state = load_state(root)
    resumes = state.get("resumes") if isinstance(state.get("resumes"), dict) else {}
    targets = _targets(cfg, list(resumes))
    if not targets:  # ещё не знаем, какие резюме есть
        return _when(state.get("retry")) or now
    moments = [_when((resumes.get(rid) or {}).get("next")) for rid in targets]
    return now if None in moments else min(moments)


def list_resumes(page: Page) -> list[Resume]:
    if not on_resumes_page(page):
        page.goto(RESUMES_URL, wait_until="domcontentloaded")
    try:
        page.locator('a[href*="/resume/"]').first.wait_for(state="attached", timeout=10_000)
    except Exception:
        return []
    return [Resume(**item) for item in page.evaluate(_LIST_JS)]


def _next_from_card(text: str, now: datetime) -> datetime | None:
    """Когда снова можно поднять — по карточке резюме: «Обновлено сегодня в 15:46» → 19:46,
    «Поднять можно в 19:46» → 19:46. Не нашли — None."""
    m = _UPDATED.search(text)
    if m:
        moment = now.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
        if moment <= now:
            return moment + EVERY
    m = _AVAILABLE.search(text)
    if m:
        moment = now.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
        return moment if moment > now else moment + timedelta(days=1)
    return None


def raise_due(page: Page, cfg: Config, root: Path, force: bool = False) -> RaiseResult | None:
    """Поднять резюме, у которых подошло время (force — не глядя на data/raising.json). Ничего не пора —
    None, браузер не трогаем."""
    now = datetime.now()
    due = next_due(root, cfg, now)
    if not force and (due is None or due > now):
        return None
    log.info("Поднятие резюме в поиске")
    found = list_resumes(page)
    state = load_state(root)
    old = state.get("resumes") if isinstance(state.get("resumes"), dict) else {}
    resumes = {r.id: (old.get(r.id) or {}) | {"title": r.title or (old.get(r.id) or {}).get("title", "")}
               for r in found}  # удалённые с hh резюме забываем
    by_id = {r.id: r for r in found}
    targets = _targets(cfg, list(resumes))
    result = RaiseResult()
    if not targets:
        log.warning("  ! на странице «Мои резюме» не нашёл ни одного резюме — поднимать нечего")
        state.update(resumes={}, retry=(now + RETRY_EARLY).isoformat(timespec="seconds"))
        _save_state(root, state)
        result.next_at = now + RETRY_EARLY
        return result
    xsrf = next((c["value"] for c in page.context.cookies("https://hh.ru") if c["name"] == "_xsrf"), "")
    for rid in targets:
        entry = resumes.setdefault(rid, dict(old.get(rid) or {}))  # резюме из ссылки, которого нет в списке
        card = by_id.get(rid)
        title = (card.title if card else "") or entry.get("title") or f"резюме {rid[:8]}…"
        last = _when(entry.get("raised"))
        if not force and _when(entry.get("next")) and _when(entry["next"]) > now:
            continue  # это резюме ещё рано, а другое — уже пора
        status = page.evaluate(_TOUCH_JS, {"id": rid, "xsrf": xsrf})
        log.debug(f"  /applicant/resumes/touch {rid}: HTTP {status}")
        if status == 200:
            entry["raised"] = now.isoformat(timespec="seconds")
            entry["next"] = (now + EVERY).isoformat(timespec="seconds")
            result.raised.append(title)
            log.success(f"  ✓ «{title}» поднято в поиске — следующее в {now + EVERY:%H:%M}")
        elif status == 409:  # hh: не прошло 4 часа (подняли здесь или вручную на hh)
            moment = _next_from_card(card.text if card else "", now) or max(
                last + EVERY if last else now, now + RETRY_EARLY)
            entry["next"] = moment.isoformat(timespec="seconds")
            result.early.append(title)
            log.info(f"  «{title}»: hh поднимает раз в 4 часа, ещё рано — попробую в {moment:%H:%M}")
        else:
            reason = {0: "нет связи с hh", 400: "hh не нашёл такое резюме — проверьте ссылку на резюме",
                      403: "hh не пустил (вход устарел?) — войдите заново через «Отклики»",
                      404: "hh не нашёл такое резюме — проверьте ссылку на резюме"}.get(status, f"hh ответил {status}")
            entry["next"] = (now + RETRY_ERROR).isoformat(timespec="seconds")
            result.failed.append(title)
            log.warning(f"  ! «{title}» поднять не получилось: {reason}")
    state.update(resumes=resumes, checked=now.isoformat(timespec="seconds"))
    state.pop("retry", None)
    _save_state(root, state)
    moments = [m for m in (_when(resumes.get(rid, {}).get("next")) for rid in targets) if m]
    result.next_at = min(moments) if moments else None
    return result

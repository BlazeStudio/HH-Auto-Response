"""Что стало с откликами: статусы из чатов hh, уведомление о приглашении и воронка.

Статусы берутся из списка чатов hh (hh.ru/chat), который программа и так читает: у отклика там свой чат
с названием вакансии и компанией, а последнее сообщение — «Отказ», «Приглашение на собеседование» и т.п.
Файл data/negotiations.json: чат → вакансия, компания, статус, когда его увидели, сообщили ли о приглашении.
Воронка — отклики из data/responses.jsonl, к которым по вакансии и компании приложен статус чата:
сколько приглашений и отказов по каждому запросу, виду письма и совпадению по навыкам.
"""

from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .state import company_key

INVITED, REJECTED = "invited", "rejected"
FILE = "negotiations.json"
FULL_SYNC_EVERY = timedelta(hours=24)  # весь список чатов (и прочитанные) — не чаще раза в сутки
# Статус «Собеседование» (hh выделяет его зелёным), «Приглашение на собеседование», «Приглашаем вас на интервью»,
# «Вас пригласили…». Вопрос робота «Готовы ли вы пройти собеседование?» приглашением не считается
_INVITE = re.compile(r"^собеседовани|^приглашени|пригла\w*\s+(?:вас\s+)?на\s+(?:собеседовани|интервью|встреч)"
                     r"|вас\s+пригласил|^приглашаем|вы\s+приглашен", re.I)
_lock = threading.Lock()


def classify(last: str, color: str = "", rejection_text: str = "Отказ") -> str | None:
    """Статус по последнему сообщению чата (и цвету, которым hh его выделил). None — ни то, ни другое."""
    text = " ".join(last.split()).lower()
    rejection = rejection_text.strip().lower()
    if (rejection and text.startswith(rejection)) or re.search(r"color_red|_red\b|negative", color, re.I):
        return REJECTED
    if _INVITE.search(text) or re.search(r"color_green|_green\b|positive", color, re.I):
        return INVITED
    return None


def match_key(title: str, company: str) -> tuple[str, str]:
    """Вакансия и компания одинаково для отклика и для чата: регистр, кавычки и лишние пробелы не важны."""
    return " ".join(re.sub(r"[«»\"'“”]", "", title).lower().replace("ё", "е").split()), company_key(company)


class Negotiations:
    def __init__(self, root: Path):
        self.path = root / "data" / FILE

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get("chats"), dict):
            data["chats"] = {}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temp.replace(self.path)

    def update(self, chats, rejection_text: str = "Отказ") -> list[dict]:
        """Запомнить статусы чатов из списка. Возвращает приглашения, о которых ещё не сообщали, — только
        из непрочитанных чатов: старые приглашения, которые вы уже видели, уведомлений не вызывают.
        chats — объекты с id, title, company, last, unread и (необязательно) color."""
        today = date.today().isoformat()
        fresh = []
        with _lock:
            data = self.load()
            changed = False
            for chat in chats:
                status = classify(chat.last, getattr(chat, "color", ""), rejection_text)
                entry = data["chats"].get(chat.id)
                if entry is None:
                    if status is None:
                        continue  # обычная переписка — воронке не нужна
                    entry = data["chats"][chat.id] = {"title": chat.title, "company": chat.company}
                    changed = True
                if status and entry.get("status") != status:
                    entry.update(status=status, at=today)
                    changed = True
                if entry.get("status") == INVITED and not entry.get("notified"):
                    entry["notified"] = True  # прочитанное старое приглашение — молча
                    changed = True
                    if chat.unread:
                        fresh.append({"id": chat.id, "title": chat.title, "company": chat.company})
            if changed:
                self._save(data)
        return fresh

    def full_sync_due(self, now: datetime | None = None) -> bool:
        last = self.load().get("full_sync")
        try:
            return not last or (now or datetime.now()) - datetime.fromisoformat(last) >= FULL_SYNC_EVERY
        except ValueError:
            return True

    def mark_full_sync(self) -> None:
        with _lock:
            data = self.load()
            data["full_sync"] = datetime.now().isoformat(timespec="seconds")
            self._save(data)

    def statuses(self) -> dict[tuple[str, str], str]:
        """(вакансия, компания) → статус. Приглашение важнее отказа (по одной вакансии бывают оба чата)."""
        result: dict[tuple[str, str], str] = {}
        for entry in self.load()["chats"].values():
            status = entry.get("status")
            if status not in (INVITED, REJECTED):
                continue
            key = match_key(entry.get("title", ""), entry.get("company", ""))
            if result.get(key) != INVITED:
                result[key] = status
        return result

    def clear(self) -> None:
        with _lock:
            try:
                self.path.unlink()
            except OSError:
                pass


# ─────────────────────────── воронка ───────────────────────────

LETTER_NAMES = {"hh": "письмо hh («Сгенерировать»)", "ai": "письмо нейросети", "template": "шаблон письма",
                "fallback": "запасной текст", "none": "без письма"}
OLD = "до версии 1.0.11"  # записи откликов, сделанные до воронки, — без запроса и вида письма


@dataclass
class FunnelRow:
    label: str
    sent: int = 0
    invited: int = 0
    rejected: int = 0

    @property
    def silent(self) -> int:
        return self.sent - self.invited - self.rejected

    def rate(self, value: int) -> float:
        return value / self.sent * 100 if self.sent else 0.0


def _match_bucket(match) -> str:
    if not isinstance(match, (int, float)):
        return "hh не показал совпадение"
    if match >= 70:
        return "по навыкам 70% и больше"
    if match >= 50:
        return "по навыкам 50–69%"
    return "по навыкам меньше 50%"


def funnel(root: Path, period_days: int | None, today: date | None = None) -> dict[str, list[FunnelRow]]:
    """Отклики за период (None — всё время) и что с ними стало. {"total": [строка], "query": […],
    "letter": […], "match": […]} — строки по убыванию числа откликов."""
    since = ((today or date.today()) - timedelta(days=period_days - 1)).isoformat() if period_days else ""
    statuses = Negotiations(root).statuses()
    groups: dict[str, dict[str, FunnelRow]] = defaultdict(dict)
    total = FunnelRow("Все отклики")
    path = root / "data" / "responses.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("category") != "applied" or str(record.get("at", ""))[:10] < since:
            continue
        status = statuses.get(match_key(str(record.get("title", "")), str(record.get("company", ""))))
        kind = record.get("letter_kind")
        letter = LETTER_NAMES.get(kind) if kind else ("с письмом" if record.get("letter") else "без письма")
        labels = {"query": record.get("query") or OLD, "letter": letter,
                  "match": _match_bucket(record.get("match")) if "query" in record else OLD}
        for row in [total] + [groups[group].setdefault(label, FunnelRow(label)) for group, label in labels.items()]:
            row.sent += 1
            row.invited += status == INVITED
            row.rejected += status == REJECTED
    result = {"total": [total]}
    for group in ("query", "letter", "match"):
        result[group] = sorted(groups[group].values(), key=lambda row: -row.sent)
    return result

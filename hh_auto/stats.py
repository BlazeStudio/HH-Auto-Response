"""Статистика для вкладки «Статистика»: только счётчики по дням.

Ни названий вакансий, ни компаний, ни текстов писем и ответов — только числа: сколько отправлено, с письмом,
пропущено (и по какой причине — одним из нескольких видов), отказов и ответов ИИ в чатах, запусков и минут
работы. Файл data/stats.json, ~200 байт на день. Не вести статистику — [stats] disabled = true; дни старше
[stats] keep_days удаляются сами.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

# показатель → подпись; порядок — как в списке выбора на вкладке
METRICS = {
    "applied": "Отправлено откликов",
    "letters": "С письмом",
    "skipped": "Пропущено всего",
    "skip_filters": "Отсеяли фильтры по словам",
    "skip_ai": "Отсеял ИИ-фильтр",
    "skip_questions": "Пропущены из-за анкет",
    "skip_relocation": "Пропущены: переезд, другой регион",
    "skip_company": "Лимит откликов в компанию",
    "skip_other": "Пропущены по другим причинам",
    "failed": "Ошибок",
    "rejections": "Отказов в чатах",
    "ai_answers": "Ответов ИИ в чатах",
    "waiting": "Чатов ждали вашего ответа",
    "runs": "Запусков",
    "minutes": "Минут работы",
}
SKIP_KINDS = ("skip_filters", "skip_ai", "skip_questions", "skip_relocation", "skip_company", "skip_other")
SNAPSHOT = {"waiting"}  # не сумма за день, а сколько было на момент проверки — за неделю берём наибольшее
FILE = "stats.json"
_lock = threading.Lock()


def skip_kind(reason: str) -> str | None:
    """Вид причины пропуска. None — не пропуск (hh подвинул вакансию — к ней ещё вернёмся)."""
    r = reason.lower()
    if r.startswith("hh пересортировал выдачу"):
        return None
    if r.startswith("ии-фильтр") or r.startswith("ии: не интересна") or r.startswith("ии: не удалось проверить"):
        return "skip_ai"
    if "одну компанию" in r:
        return "skip_company"
    if "анкет" in r or "вопрос" in r:
        return "skip_questions"
    if "регион" in r or "переезд" in r or "релокац" in r:
        return "skip_relocation"
    if r.startswith("в названии") or r.startswith("компания") or "нужных слов" in r or "include_words" in r:
        return "skip_filters"
    return "skip_other"


class StatsStore:
    def __init__(self, root: Path, keep_days: int = 365):
        self.root = root
        self.path = root / "data" / FILE
        self.keep_days = keep_days

    # ---------- чтение и запись ----------

    def load(self) -> dict[str, dict[str, float]]:
        """{«ГГГГ-ММ-ДД»: {показатель: число}}. При первом чтении — из истории откликов (data/responses.jsonl)."""
        with _lock:
            if not self.path.exists():
                days = self._from_history()
                if days:
                    self._write(days)
                return days
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                days = data.get("days", {}) if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError):
                return {}
            pruned = self._prune(days)
            if pruned != days:
                self._write(pruned)
            return pruned

    def add(self, counts: dict[str, float], day: str | None = None) -> None:
        """Прибавить счётчики к дню (по умолчанию — сегодня)."""
        counts = {k: v for k, v in counts.items() if k in METRICS and v}
        if not counts:
            return
        day = day or date.today().isoformat()
        days = self.load()
        with _lock:
            row = days.setdefault(day, {})
            for key, value in counts.items():
                if key in SNAPSHOT:
                    row[key] = max(row.get(key, 0), value)
                else:
                    row[key] = round(row.get(key, 0) + value, 1)
            self._write(self._prune(days))

    def clear(self) -> None:
        """Удалить всю статистику. История откликов для Excel (data/responses.jsonl) остаётся, но заново из неё
        статистика не соберётся: пустой stats.json и есть пометка «очищено»."""
        with _lock:
            self._write({})

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def _write(self, days: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"v": 1, "days": dict(sorted(days.items()))}, ensure_ascii=False,
                                  separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.path)

    def _prune(self, days: dict) -> dict:
        if self.keep_days <= 0:
            return days
        border = (date.today() - timedelta(days=self.keep_days)).isoformat()
        return {day: row for day, row in days.items() if day > border}

    def _from_history(self) -> dict[str, dict[str, float]]:
        """Первое открытие: посчитать дни по уже накопленной истории откликов (только числа)."""
        source = self.root / "data" / "responses.jsonl"
        if not source.exists():
            return {}
        days: dict[str, Counter] = {}
        runs: dict[str, set] = {}
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            day = str(record.get("at", ""))[:10]
            if len(day) != 10:
                continue
            row = days.setdefault(day, Counter())
            runs.setdefault(day, set()).add(record.get("run"))
            status = record.get("status")
            if status == "отклик отправлен":
                row["applied"] += 1
                row["letters"] += bool(record.get("letter"))
            elif status == "пропущена":
                kind = skip_kind(str(record.get("reason", "")))
                if kind:
                    row["skipped"] += 1
                    row[kind] += 1
            elif status == "ошибка":
                row["failed"] += 1
        for day, ids in runs.items():
            days[day]["runs"] += len(ids - {None})
        return self._prune({day: dict(row) for day, row in days.items()})


# ---------- что записать после запуска ----------

def responses_counts(stats, seconds: float) -> dict[str, float]:
    """Итоги запуска откликов (runner.Stats) → счётчики дня."""
    counts: Counter = Counter(applied=stats.applied, letters=stats.with_letter, failed=stats.failed, runs=1,
                              minutes=round(seconds / 60, 1))
    for reason, number in stats.skipped.items():
        kind = skip_kind(reason)
        if kind:
            counts["skipped"] += number
            counts[kind] += number
    return dict(counts)


def chats_counts(stats, seconds: float) -> dict[str, float]:
    """Итоги чатов (chats.ChatStats) → счётчики дня."""
    return {"rejections": stats.opened, "ai_answers": stats.answered, "waiting": len(stats.other_unread),
            "runs": 1, "minutes": round(seconds / 60, 1)}


def record(root: Path, cfg, counts: dict[str, float]) -> None:
    """Записать итоги, если статистику ведём ([stats] disabled = false). Ошибка записи работе не мешает."""
    if cfg.stats.disabled:
        return
    try:
        StatsStore(root, cfg.stats.keep_days).add(counts)
    except OSError:
        pass


# ---------- для графиков ----------

def buckets(days: dict[str, dict], period_days: int | None, step: str, today: date | None = None):
    """Ряды для графика: [(подпись, {показатель: число})] за последние period_days дней (None — всё время),
    сгруппированные по дням, неделям (с понедельника) или месяцам. Пустые дни — нули, а не пропуски."""
    today = today or date.today()
    if period_days:
        start = today - timedelta(days=period_days - 1)
    else:
        first = min((d for d in days), default=today.isoformat())
        start = min(date.fromisoformat(first), today)
    months = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")

    def key_of(d: date):
        if step == "week":
            monday = d - timedelta(days=d.weekday())
            return monday, monday.strftime("%d.%m")
        if step == "month":
            return date(d.year, d.month, 1), months[d.month - 1] + (f" {d.year % 100:02d}" if d.month == 1 else "")
        return d, d.strftime("%d.%m")

    order: dict = {}
    d = start
    while d <= today:
        key, label = key_of(d)
        row = order.setdefault(key, (label, Counter()))[1]
        for metric, value in days.get(d.isoformat(), {}).items():
            if metric in SNAPSHOT:
                row[metric] = max(row[metric], value)
            else:
                row[metric] += value
        d += timedelta(days=1)
    return [(label, dict(row)) for label, row in order.values()]


def totals(days: dict[str, dict], period_days: int | None, today: date | None = None) -> Counter:
    total: Counter = Counter()
    for _, row in buckets(days, period_days, "day", today):
        for metric, value in row.items():
            total[metric] = max(total[metric], value) if metric in SNAPSHOT else total[metric] + value
    return total

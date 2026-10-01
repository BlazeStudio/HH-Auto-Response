"""Журнал откликов: все попытки → data/responses.jsonl, отчёт → responses.xlsx, статистика.

data/responses.jsonl — источник правды (по строке JSON на каждую обработанную вакансию).
Из него собирается Excel с листами «Сводка», «Откликнулся», «Вопросы», «Пропущенные»,
«Не откликнулся» и считается статистика: всего, за сегодня, за последний запуск, по дням.
"""

from __future__ import annotations

import csv
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .logger import log
from .responder import Result, Status
from .search import Vacancy

APPLIED, QUESTIONS, SKIPPED, FAILED = "applied", "questions", "skipped", "failed"
HH_DAILY_LIMIT = 200
_SAVE_EVERY = 15.0  # не чаще раза в N секунд пересобираем Excel во время работы


@dataclass
class Totals:
    applied: int = 0
    letters: int = 0
    questions: int = 0
    skipped: int = 0
    failed: int = 0

    def add(self, record: dict) -> None:
        category = record["category"]
        if category == APPLIED:
            self.applied += 1
            self.letters += bool(record.get("letter"))
        else:
            setattr(self, category, getattr(self, category) + 1)


@dataclass
class Summary:
    total: Totals = field(default_factory=Totals)
    today: Totals = field(default_factory=Totals)
    last_run: Totals = field(default_factory=Totals)
    last_run_at: str = ""
    by_day: dict[str, Totals] = field(default_factory=dict)  # «2026-10-01» → итоги, новые дни первыми


def category_of(result: Result) -> str:
    if result.status is Status.APPLIED:
        return APPLIED
    if result.status is Status.SKIPPED:
        return QUESTIONS if result.questions else SKIPPED
    return FAILED  # ошибка или лимит hh


def response_form_url(vacancy_id: str) -> str:
    return f"https://hh.ru/applicant/vacancy_response?vacancyId={vacancy_id}"


class ResultsBook:
    def __init__(self, root: Path):
        self.data_path = root / "data" / "responses.jsonl"
        self.xlsx_path = root / "logs" / "responses.xlsx"
        self.run_id = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._last_save = 0.0
        self._lock_warned = False
        self._migrate_csv(root / "logs" / "responses.csv")

    # ---------- запись ----------

    def add(self, vac: Vacancy, result: Result) -> None:
        record = {
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "run": self.run_id,
            "id": vac.id,
            "title": vac.title,
            "company": vac.company,
            "category": category_of(result),
            "status": result.status.value,
            "letter": result.letter,
            "reason": result.reason,
            "questions": result.questions or [],
            "url": vac.url,
        }
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        with self.data_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if time.monotonic() - self._last_save >= _SAVE_EVERY:
            self.save_xlsx()

    def records(self) -> list[dict]:
        if not self.data_path.exists():
            return []
        rows = []
        for line in self.data_path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # недописанная строка (например, при аварийном завершении)
        return rows

    # ---------- статистика ----------

    def summary(self) -> Summary:
        return summarize(self.records(), self.run_id)

    # ---------- Excel ----------

    def save_xlsx(self) -> bool:
        """Пересобирает responses.xlsx. False — файл открыт в Excel и не перезаписан."""
        self._last_save = time.monotonic()
        try:
            from .report import build_workbook
        except ImportError as e:  # нет openpyxl
            log.warning(f"  ! Excel-отчёт не собран ({e}); установите: pip install openpyxl")
            return False
        records = self.records()
        self.xlsx_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.xlsx_path.with_name("~responses.tmp.xlsx")
        build_workbook(records, summarize(records, self.run_id)).save(tmp)
        try:
            os.replace(tmp, self.xlsx_path)
        except PermissionError:
            tmp.unlink(missing_ok=True)
            if not self._lock_warned:
                log.warning(f"  ! {self.xlsx_path.name} открыт в Excel — обновлю таблицу, когда вы его закроете")
                self._lock_warned = True
            return False
        self._lock_warned = False
        return True

    # ---------- перенос старого CSV ----------

    def _migrate_csv(self, csv_path: Path) -> None:
        """Однократно переносит историю из прежнего responses.csv в responses.jsonl."""
        if self.data_path.exists() or not csv_path.exists():
            return
        categories = {"отклик отправлен": APPLIED, "пропущена": SKIPPED}
        rows = []
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f, delimiter=";"):
                reason = row.get("комментарий", "")
                category = categories.get(row.get("статус", ""), FAILED)
                if category == SKIPPED and "вопрос" in reason:
                    category = QUESTIONS
                rows.append({
                    "at": row.get("время", ""), "run": "перенесено из responses.csv", "id": row.get("id", ""),
                    "title": row.get("вакансия", ""), "company": row.get("компания", ""), "category": category,
                    "status": row.get("статус", ""), "letter": row.get("письмо") == "да", "reason": reason,
                    "questions": [], "url": row.get("ссылка", ""),
                })
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        csv_path.rename(csv_path.with_name("responses_old.csv"))
        log.info(f"  перенёс {len(rows)} записей из responses.csv в новый журнал (старый файл: responses_old.csv)")


def summarize(records: list[dict], current_run: str = "") -> Summary:
    summary = Summary()
    today = datetime.now().strftime("%Y-%m-%d")
    runs = [r["run"] for r in records if r.get("run") and not r["run"].startswith("перенесено")]
    last_run = current_run if current_run in runs else (max(runs) if runs else "")
    by_day: dict[str, Totals] = defaultdict(Totals)
    for record in records:
        summary.total.add(record)
        day = record.get("at", "")[:10]
        by_day[day].add(record)
        if day == today:
            summary.today.add(record)
        if last_run and record.get("run") == last_run:
            summary.last_run.add(record)
    summary.last_run_at = last_run
    summary.by_day = dict(sorted(by_day.items(), reverse=True))
    return summary


def load_summary(root: Path) -> Summary:
    """Статистика для окна приложения (заодно переносит старый responses.csv, если он есть)."""
    return ResultsBook(root).summary()


def log_totals(summary: Summary) -> None:
    log.info(
        f"  за сегодня откликов: {summary.today.applied} (лимит hh — около {HH_DAILY_LIMIT} в сутки), "
        f"всего за всё время: {summary.total.applied}"
    )


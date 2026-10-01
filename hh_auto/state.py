"""Память между запусками (data/state.json): какие вакансии уже обработаны."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .search import Vacancy


class State:
    """Какие вакансии уже обработаны — чтобы не трогать их повторно."""

    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.applied: dict[str, dict] = data.get("applied", {})
        self.skipped: dict[str, dict] = data.get("skipped", {})

    def seen(self, vacancy_id: str, retry_skipped: bool = False) -> str | None:
        """Причина, по которой вакансию не надо трогать, или None."""
        if vacancy_id in self.applied:
            return f"отклик уже отправлен {self.applied[vacancy_id]['at']}"
        if not retry_skipped and vacancy_id in self.skipped:
            return f"пропущена ранее: {self.skipped[vacancy_id]['reason']}"
        return None

    def mark_applied(self, vac: Vacancy, letter: bool) -> None:
        self.applied[vac.id] = {"title": vac.title, "company": vac.company, "letter": letter, "at": _now()}
        self.skipped.pop(vac.id, None)
        self._save()

    def mark_skipped(self, vac: Vacancy, reason: str) -> None:
        self.skipped[vac.id] = {"title": vac.title, "company": vac.company, "reason": reason, "at": _now()}
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"applied": self.applied, "skipped": self.skipped}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

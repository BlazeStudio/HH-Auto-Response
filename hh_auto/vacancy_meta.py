"""Данные вакансий, которые hh отдаёт вместе со страницей выдачи, и отбор по ним.

Кроме карточек, страница поиска hh содержит своё состояние (<template id="HH-Lux-InitialState">): по каждой
вакансии — проверен ли работодатель, предупреждения hh о нём, рейтинг по отзывам, сколько откликов, когда
вакансия создана, есть ли вопросы при отклике, ключевые навыки и «подходит по навыкам на N%». Всё это
читается одним запросом к уже открытой странице — без переходов и кликов. По этим данным:
  • отбор ([quality]): предупреждения hh, «вечные» и перегруженные вакансии, низкий рейтинг;
  • уровень вакансии по названию ([filters] levels) и вопросы при отклике ([questions] skip_all);
  • советы к резюме: какие навыки чаще всего просят в вашей выдаче (data/skills.json).
Если hh перестанет отдавать эти данные, отбор по ним просто не срабатывает — отклики идут как раньше.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page

from .config import Config

# Состояние страницы → по вакансии только нужное (страница — мегабайты, обратно уходит несколько килобайт)
_META_JS = """() => {
  const el = document.getElementById('HH-Lux-InitialState');
  if (!el) return null;
  let state;
  try { state = JSON.parse(el.content ? el.content.textContent : el.textContent); } catch (e) { return null; }
  const list = ((state || {}).vacancySearchResult || {}).vacancies;
  if (!Array.isArray(list)) return null;
  return list.map((v) => {
    const c = v.company || {};
    const neg = c.employersNegativeAttribute || v.employersNegativeAttribute || {};
    const labels = (v.labels || []).concat(v.extraLabels || []);
    const skillLabel = labels.find((l) => /skillsPercentage/i.test(l.id || ''));
    const match = skillLabel ? /(\\d+)\\s*%/.exec(skillLabel.text || '') : null;
    const reviews = c.employerReviews || {};
    return {
      id: String(v.vacancyId || ''),
      employer_id: c.id ? String(c.id) : '',
      trusted: c['@trusted'] !== false,
      approved: !c['@state'] || c['@state'] === 'APPROVED',
      additional_check: !!c.employerOnAdditionalCheck,
      flags: Object.keys(neg).filter((k) => neg[k] === true),
      rating: reviews.totalRating ? parseFloat(reviews.totalRating) : null,
      reviews: reviews.reviewsCount || 0,
      responses: typeof v.totalResponsesCount === 'number' ? v.totalResponsesCount : null,
      created: v.creationTime || '',
      has_test: !!v.userTestPresent,
      skills: ((v.snippet || {}).skill || '').split(',').map((s) => s.trim()).filter(Boolean),
      match: match ? parseInt(match[1], 10) : null,
      reply_fast: labels.some((l) => /reply-speed/i.test(l.id || '')),
      experience: v.workExperience || '',
    };
  }).filter((m) => m.id);
}"""


@dataclass
class VacancyMeta:
    id: str
    employer_id: str = ""
    trusted: bool = True
    approved: bool = True
    additional_check: bool = False
    flags: tuple[str, ...] = ()  # предупреждения hh о работодателе: hasFrequentComplaints, pdResale…
    rating: float | None = None  # рейтинг по отзывам (из 5)
    reviews: int = 0
    responses: int | None = None  # сколько всего откликов
    created: str = ""  # когда вакансию создали (ISO): переопубликованная «вечная» вакансия создана давно
    has_test: bool = False  # при отклике — вопросы работодателя
    skills: tuple[str, ...] = ()  # ключевые навыки из карточки
    match: int | None = None  # «Подходит по навыкам на N%» (hh показывает не у всех вакансий)
    reply_fast: bool = False  # «Отвечает в течение дня / трёх дней»
    experience: str = ""  # noExperience, between1And3, between3And6, moreThan6

    @classmethod
    def from_raw(cls, raw: dict) -> VacancyMeta:
        return cls(
            id=str(raw.get("id", "")), employer_id=str(raw.get("employer_id") or ""),
            trusted=bool(raw.get("trusted", True)), approved=bool(raw.get("approved", True)),
            additional_check=bool(raw.get("additional_check")), flags=tuple(raw.get("flags") or ()),
            rating=raw.get("rating"), reviews=int(raw.get("reviews") or 0), responses=raw.get("responses"),
            created=str(raw.get("created") or ""), has_test=bool(raw.get("has_test")),
            skills=tuple(raw.get("skills") or ()), match=raw.get("match"), reply_fast=bool(raw.get("reply_fast")),
            experience=str(raw.get("experience") or ""))

    def age_days(self, now: datetime | None = None) -> int | None:
        try:
            created = datetime.fromisoformat(self.created[:19])
        except ValueError:
            return None
        return ((now or datetime.now()) - created).days


def read_meta(page: Page) -> dict[str, VacancyMeta]:
    """{id вакансии: данные} с открытой страницы выдачи. Нет данных (другая вёрстка, ошибка) — пусто."""
    try:
        raw = page.evaluate(_META_JS)
    except Exception:  # noqa: BLE001 — данные необязательные: без них отбор просто не срабатывает
        return {}
    return {m.id: m for m in (VacancyMeta.from_raw(r) for r in raw or []) if m.id}


# ─────────────────────────── отбор ───────────────────────────

# Уровень в названии вакансии: «Senior Python», «Ведущий инженер», «Стажёр-аналитик», «Team Lead»
_LEVEL_WORDS = {
    "intern": r"стаж[её]р\w*|intern(?:ship)?|trainee|практикант\w*",
    "junior": r"junior|джун\w*|младш\w+|начинающ\w+",
    "middle": r"middle|мидл\w*",
    "senior": r"senior|сеньор\w*|синьор\w*|старш\w+|ведущ\w+",
    "lead": r"lead|тимлид\w*|техлид\w*|лид|head|principal|chief",
}
_LEVEL_RE = {level: re.compile(rf"(?<![\w-])(?:{words})(?![\w])", re.I) for level, words in _LEVEL_WORDS.items()}

# «Пишите в Telegram», «резюме в WhatsApp», ссылки t.me/… и wa.me/… — частый признак обмана и «серых» вакансий.
# Просто слово Telegram не считается: «разработчик Telegram-ботов» — нормальная вакансия; @ник тоже нет —
# в вакансиях на Java это аннотации (@Override)
_MESSENGER = re.compile(
    r"(?:пиш\w*|напиш\w*|свяж\w*|связ[ьи]|контакт\w*|отклик\w*|резюме|отправ\w*|присыл\w*|звон\w*|сообщ\w*|обращ\w*)"
    r"[^.!?\n]{0,30}?(?:telegram|телеграм\w*|whats\s?app|ватсап\w*|вотсап\w*|viber|вайбер\w*)"
    r"|t\.me/|wa\.me/", re.I)


def title_levels(title: str) -> set[str]:
    """Уровни, явно указанные в названии вакансии (их может быть несколько: «Middle/Senior»)."""
    return {level for level, pattern in _LEVEL_RE.items() if pattern.search(title)}


def skip_reason(title: str, snippet: str, meta: VacancyMeta | None, cfg: Config,
                now: datetime | None = None) -> tuple[str, str] | None:
    """Пропустить ли вакансию по уровню, данным hh и вопросам при отклике. (итог, подробно) или None.
    «Итог» — одинаковый у всех вакансий одной причины: по нему строится сводка в конце запуска."""
    if cfg.filters.levels:
        found = title_levels(title)
        if found and not found & set(cfg.filters.levels):
            return ("уровень вакансии не из выбранных ([filters] levels)",
                    f"в названии уровень {', '.join(sorted(found))}, а вы выбрали {', '.join(cfg.filters.levels)}")
    q = cfg.quality
    if q.skip_messengers and _MESSENGER.search(f"{title} {snippet}"):
        return "отбор: просят писать в мессенджер", "в описании просят писать в мессенджер (частый признак обмана)"
    if meta is None:
        return None
    if cfg.questions.skip_all and meta.has_test:
        return ("вопросы работодателя при отклике ([questions] skip_all)",
                "при отклике вопросы работодателя ([questions] skip_all)")
    if q.skip_suspicious:
        if "misleadingVacancyAlert" in meta.flags:
            return "отбор: hh предупреждает о вакансии", "hh предупреждает: вакансия может вводить в заблуждение"
        if "pdResale" in meta.flags:
            return ("отбор: hh предупреждает о вакансии",
                    "hh предупреждает: работодатель может передавать персональные данные третьим лицам")
        if meta.additional_check or not meta.approved or not meta.trusted:
            return ("отбор: работодатель не подтверждён или на проверке",
                    "работодатель не подтверждён hh или на дополнительной проверке")
    if q.skip_complaints:
        if "hasFrequentComplaints" in meta.flags:
            return "отбор: на работодателя часто жалуются", "hh: на работодателя часто жалуются соискатели"
        if "hasBreaches" in meta.flags:
            return "отбор: на работодателя часто жалуются", "hh: у работодателя были нарушения правил"
    if q.max_age_days:
        age = meta.age_days(now)
        if age is not None and age > q.max_age_days:
            return ("отбор: «вечная» вакансия ([quality] max_age_days)",
                    f"вакансию создали {age} дн. назад и переопубликуют — «вечная» (больше {q.max_age_days} дн.)")
    if q.max_responses and meta.responses is not None and meta.responses > q.max_responses:
        return ("отбор: слишком много откликов ([quality] max_responses)",
                f"на вакансию уже {meta.responses} откликов (больше {q.max_responses})")
    if q.min_rating and meta.rating is not None and meta.reviews >= 5 and meta.rating < q.min_rating:
        return ("отбор: низкий рейтинг работодателя ([quality] min_rating)",
                f"рейтинг работодателя {meta.rating:g} по {meta.reviews} отзывам (меньше {q.min_rating:g})")
    return None


# ─────────────────────────── советы к резюме ───────────────────────────

SKILLS_FILE = "skills.json"
SEEN_LIMIT = 5000  # сколько последних вакансий помнить, чтобы не считать одну дважды
_skills_lock = threading.Lock()


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def has_skill(skill: str, resume: str) -> bool:
    """Навык есть в тексте резюме (resume — уже _norm). «C++» и «C#» не путаются с «C»."""
    needle = _norm(skill)
    return bool(needle) and re.search(rf"(?<![\w+#]){re.escape(needle)}(?![\w+#])", resume) is not None


class SkillStats:
    """Какие ключевые навыки встречаются в вакансиях вашей выдачи. Только навыки и числа: без вакансий
    и компаний (id вакансий — только чтобы одну не посчитать дважды)."""

    def __init__(self, root: Path):
        self.path = root / "data" / SKILLS_FILE

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("seen", [])
        data.setdefault("vacancies", 0)
        data.setdefault("skills", {})
        data.setdefault("match_sum", 0)
        data.setdefault("match_n", 0)
        return data

    def record(self, metas: list[VacancyMeta]) -> int:
        """Посчитать вакансии, которых ещё не было. Возвращает, сколько новых."""
        with _skills_lock:
            data = self.load()
            seen = set(data["seen"])
            fresh = [m for m in metas if m.id and m.id not in seen]
            if not fresh:
                return 0
            for meta in fresh:
                data["seen"].append(meta.id)
                data["vacancies"] += 1
                for skill in dict.fromkeys(meta.skills):  # один навык — один раз на вакансию
                    key = _norm(skill)
                    if key:
                        name, count = data["skills"].get(key, [skill, 0])
                        data["skills"][key] = [name, count + 1]
                if meta.match is not None:
                    data["match_sum"] += meta.match
                    data["match_n"] += 1
            data["seen"] = data["seen"][-SEEN_LIMIT:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temp.replace(self.path)
            return len(fresh)

    def tips(self, resume_text: str, top: int = 15) -> tuple[int, int | None, list[tuple[str, float, bool]]]:
        """(сколько вакансий учтено, средний % совпадения по навыкам или None,
        [(навык, доля вакансий, есть ли в резюме)] — самые частые)."""
        data = self.load()
        total = data["vacancies"]
        resume = _norm(resume_text)
        ranked = sorted(data["skills"].values(), key=lambda pair: -pair[1])[:top]
        rows = [(name, count / total if total else 0.0, has_skill(name, resume)) for name, count in ranked]
        match = round(data["match_sum"] / data["match_n"]) if data["match_n"] else None
        return total, match, rows

    def clear(self) -> None:
        with _skills_lock:
            try:
                self.path.unlink()
            except OSError:
                pass

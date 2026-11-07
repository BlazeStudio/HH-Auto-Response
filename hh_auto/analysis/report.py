"""Сравнение резюме с рынком: что просят, что у вас есть, что изучить, хватает ли опыта, какая зарплата.

Без нейросети — по числам, каждое из которых можно проверить:
1. Навыки вакансии — её «ключевые навыки» и навыки из словаря, упомянутые в названии и требованиях
   (ключевые навыки указаны не у всех вакансий). Навыки резюме — «Ключевые навыки» и языки, отдельно —
   упомянутые только в тексте опыта, и всё, что из них следует (DRF → Django → Python, PostgreSQL → SQL).
2. Совпадение с вакансией — доля её профессиональных навыков («гибкие» не в счёт), которые есть в резюме.
   «Подходит» — от FIT (60%): столько нужно, чтобы откликаться не наугад.
3. «Ваш рынок» — вакансии, где у вас уже есть хотя бы NEAR (30%) навыков. Остальное в выдаче — другая
   профессия (Java при Python), и по ней советовать нечего: спрос и зарплаты считаются по вашему рынку.
4. Что изучить — по очереди: навык, который сильнее всего приближает к вакансиям вашего рынка (прибавка
   к совпадению с каждой, с весом «насколько вы к ней уже близки»), затем следующий с учётом выбранного.
   Рядом — сколько вакансий с ним станут подходящими. Одна вакансия в нескольких городах считается один раз.
5. Оценка 0–100 — с весами: навыки 45 (сколько востребованных навыков есть, по спросу), опыт 20 (на какую
   долю вакансий хватает опыта), название 15 (те ли слова, по которым ищут), оформление 20.
"""

from __future__ import annotations

import re
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from .market import MarketVacancy
from .resume import ResumeProfile
from .skills import LANG, LEGACY, SOFT, Vocabulary, implied

FIT = 0.6
NEAR = 0.3
MIN_SKILLS = 2  # меньше профессиональных навыков у вакансии — совпадение по ней не считаем
MIN_NEAR = 15  # «ваш рынок» меньше — считаем по всем вакансиям
TOP = 25
EXPERIENCE = ("noExperience", "between1And3", "between3And6", "moreThan6")
EXPERIENCE_LABELS = {"noExperience": "без опыта", "between1And3": "1–3 года", "between3And6": "3–6 лет",
                     "moreThan6": "больше 6 лет"}
FORMAT_LABELS = {"REMOTE": "удалённо", "HYBRID": "гибрид", "ON_SITE": "в офисе", "FIELD_WORK": "разъездная"}
WEIGHTS = {"skills": 45, "experience": 20, "title": 15, "structure": 20}
PART_LABELS = {"skills": "Навыки", "experience": "Опыт", "title": "Название", "structure": "Оформление"}

# слова названий, которые не о профессии
_LEVEL_WORDS = {"junior", "middle", "senior", "lead", "младший", "старший", "ведущий", "главный", "стажер", "стажёр",
                "intern", "trainee", "head", "principal", "chief", "team", "тимлид", "teamlead", "мидл", "джуниор",
                "сеньор", "удаленно", "удалённо", "remote", "гибрид", "junior+", "middle+", "senior+"}
_TITLE_STOP = {"и", "в", "на", "по", "с", "для", "or", "and", "of", "the", "in", "to", "a", "для", "со", "из"}
_TITLE_SYNONYMS = {"developer": "разработчик", "программист": "разработчик", "dev": "разработчик",
                   "engineer": "инженер", "analyst": "аналитик", "tester": "тестировщик", "qa": "тестировщик",
                   "designer": "дизайнер", "manager": "менеджер", "administrator": "администратор",
                   "admin": "администратор", "architect": "архитектор", "scientist": "scientist",
                   "бэкенд": "backend", "back-end": "backend", "бекенд": "backend", "фронтенд": "frontend",
                   "front-end": "frontend", "фуллстек": "fullstack", "full-stack": "fullstack", "данных": "data",
                   "данные": "data", "ии": "ai", "питон": "python"}
_TITLE_WORD = re.compile(r"[a-zа-я0-9+#]+(?:-[a-zа-я0-9+#]+)*")
# общие слова должностей: «Java-разработчик» и «Python-разработчик» — разные профессии
_GENERIC = {"разработчик", "инженер", "аналитик", "специалист", "менеджер", "руководитель", "тестировщик",
            "архитектор", "администратор", "консультант", "эксперт", "сотрудник", "оператор", "ассистент",
            "помощник", "директор", "начальник", "стажер", "middle", "senior", "junior"}


@dataclass
class SkillRow:
    name: str
    kind: str
    count: int  # в скольких вакансиях вашего рынка
    share: float  # доля вашего рынка
    share_all: float  # доля всех вакансий с навыками
    status: str  # key — в «Ключевых навыках», text — только в тексте опыта, missing — нет
    lift: float | None = None  # насколько больше платят там, где его просят: 0.12 — +12%
    gain: int = 0  # сколько вакансий станут подходящими, если добавить только его
    legacy: bool = False


@dataclass
class PathStep:
    name: str
    gain: int  # сколько вакансий стали подходящими
    fit_after: int  # подходящих всего после шага
    share: float  # как часто его просят на вашем рынке


@dataclass
class Advice:
    level: str  # high, medium, low
    text: str


@dataclass
class Check:
    ok: bool | None  # True — хорошо, False — стоит поправить, None — для сведения
    title: str
    detail: str
    weight: float = 1.0
    partial: bool = False  # наполовину: «Обо мне» есть, но коротко


@dataclass
class Match:
    id: str
    title: str
    area: str
    salary: int | None
    coverage: float
    missing: list[str]
    applied: bool
    have: int = 0  # навыков вакансии есть в резюме
    size: int = 0  # всего её профессиональных навыков

    @property
    def url(self) -> str:
        return f"https://hh.ru/vacancy/{self.id}"


@dataclass
class Report:
    profile: ResumeProfile
    total: int = 0  # вакансий в выборке
    with_skills: int = 0  # из них с навыками — по ним считается совпадение
    near: int = 0  # ваш рынок
    fit: int = 0  # подходящих
    score: int | None = None
    parts: dict[str, tuple[int, str]] = field(default_factory=dict)  # часть оценки → (баллы, пояснение)
    market: list[SkillRow] = field(default_factory=list)
    soft: list[SkillRow] = field(default_factory=list)
    yours: list[SkillRow] = field(default_factory=list)
    path: list[PathStep] = field(default_factory=list)
    salary: dict | None = None
    experience: list[tuple[str, float]] = field(default_factory=list)
    your_level: str | None = None
    formats: list[tuple[str, float]] = field(default_factory=list)
    english_share: float = 0.0
    titles: list[tuple[str, float]] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    advice: list[Advice] = field(default_factory=list)
    hh_match: int | None = None
    key_count: int = 0  # навыков в «Ключевых навыках» (после объединения написаний)

    @property
    def verdict(self) -> str:
        if self.score is None:
            return ""
        return ("отличное соответствие рынку" if self.score >= 80 else "хорошее соответствие" if self.score >= 65
                else "среднее соответствие" if self.score >= 50 else "слабое соответствие")


def level_of(months: int | None) -> str | None:
    if months is None:
        return None
    years = months / 12
    return EXPERIENCE[0 if years < 1 else 1 if years < 3 else 2 if years < 6 else 3]


def _title_words(title: str) -> set[str]:
    words = set()
    for word in _TITLE_WORD.findall(title.lower().replace("ё", "е")):
        parts = [word] + (word.split("-") if "-" in word else [])
        for part in parts:
            part = _TITLE_SYNONYMS.get(part, part)
            if len(part) > 1 and part not in _LEVEL_WORDS and part not in _TITLE_STOP and not part.isdigit():
                words.add(part)
    return words


def plural(n: int, one: str, few: str, many: str) -> str:
    """«1 вакансия», «3 вакансии», «11 вакансий»."""
    word = one if n % 10 == 1 and n % 100 != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many
    return f"{n} {word}"


def signed(value: float) -> str:
    """Прибавка в процентах: «+12%», «−5%», «0%» (не «-0%»)."""
    points = round(value * 100)
    return "0%" if points == 0 else f"{points:+d}%".replace("-", "−")


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value / 1000:.0f} тыс. ₽" if value >= 1000 else f"{value:.0f} ₽"


def build(profile: ResumeProfile, vacancies: list[MarketVacancy], applied: set[str] | None = None,
          today: date | None = None) -> Report:
    applied = applied or set()
    # одна вакансия в десяти городах — одна строка спроса, а не десять
    unique: dict[tuple, MarketVacancy] = {}
    for v in vacancies:
        unique.setdefault((v.title.lower().strip(), tuple(sorted(v.skills))), v)
    vacancies = list(unique.values())
    report = Report(profile=profile, total=len(vacancies))
    names = Counter(s for v in vacancies for s in dict.fromkeys(v.skills))
    vocab = Vocabulary(name for name, _ in names.most_common())

    # ── навыки резюме ──
    key_raw = {vocab.key(s) for s in profile.key_skills} - {None}
    report.key_count = len(key_raw)
    for line in profile.languages:  # языки у hh — свой раздел, но для вакансий это тоже навык
        name = vocab.key(line)
        if name and vocab.kind(name) == LANG and name in vocab.entries:
            key_raw.add(name)
    key = implied(key_raw)
    have = implied(key | vocab.find(profile.skill_text or profile.text))

    def status(name: str) -> str:
        return "key" if name in key else "text" if name in have else "missing"

    # ── навыки вакансий и совпадение ──
    skills_of: dict[str, set[str]] = {}
    hard_of: dict[str, set[str]] = {}
    coverage: dict[str, float] = {}
    for n, v in enumerate(vacancies):
        if n % 64 == 63:
            # считается в фоне, а окно в это время рисуется: без уступок каждое его действие ждало бы очереди
            # к интерпретатору, и переход на вкладку тянулся по 2 секунды
            time.sleep(0)
        found = {vocab.key(s) for s in v.skills} - {None}
        found |= vocab.find(f"{v.title}\n{v.text}", extra=False)
        skills_of[v.id] = found
        hard = {s for s in found if vocab.kind(s) != SOFT}
        hard_of[v.id] = hard
        if len(hard) >= MIN_SKILLS:
            coverage[v.id] = len(hard & have) / len(hard)
    report.with_skills = len(coverage)
    # ваш рынок: навыков хватает хотя бы на NEAR, и профессия та же — в названии вакансии есть слово из вашей
    # должности («Python», «Backend», «Data»). Одного общего «разработчик» или «аналитик» мало: «Риск-аналитик»
    # с Python и SQL — другая работа. Таких вакансий мало — смотрим только на навыки, ещё меньше — на все
    mine_words = _title_words(profile.title)
    by_skills = [v for v in vacancies if coverage.get(v.id, 0) >= NEAR]

    def same_job(v: MarketVacancy) -> bool:
        common = _title_words(v.title) & mine_words
        return bool(common - _GENERIC) or bool(common) and coverage[v.id] >= 0.5

    near = [v for v in by_skills if same_job(v)] if mine_words else by_skills
    if len(near) < MIN_NEAR:
        near = by_skills if len(by_skills) >= MIN_NEAR else [v for v in vacancies if v.id in coverage]
    near_ids = {v.id for v in near}
    report.near = len(near)
    report.fit = sum(1 for vid in near_ids if coverage[vid] >= FIT)
    matches = [v.match for v in vacancies if v.match is not None]
    report.hh_match = round(sum(matches) / len(matches)) if matches else None
    if not near:
        _structure(report, profile, today)
        report.advice = _advice(report, profile)
        return report

    count_near = Counter(s for v in near for s in skills_of[v.id])
    count_all = Counter(s for vid in coverage for s in skills_of[vid])
    with_skills = max(len(coverage), 1)

    # зарплаты: насколько больше платят там, где навык просят
    paid = [v for v in near if v.salary]
    base_pay = statistics.median(v.salary for v in paid) if len(paid) >= 20 else None
    pay_by_skill: dict[str, list[int]] = defaultdict(list)
    for v in paid:
        for s in skills_of[v.id]:
            pay_by_skill[s].append(v.salary)

    def lift(name: str) -> float | None:
        values = pay_by_skill.get(name, [])
        if base_pay is None or len(values) < 8:
            return None
        return max(-0.5, min(1.0, statistics.median(values) / base_pay - 1))

    # что изучить: сколько вакансий вашего рынка станут подходящими (у каждой — сколько навыков есть и каких нет)
    have_n = {vid: len(hard_of[vid] & have) for vid in near_ids}
    size = {vid: len(hard_of[vid]) for vid in near_ids}
    missing_in: dict[str, list[str]] = defaultdict(list)
    for vid in near_ids:
        for s in hard_of[vid] - have:
            missing_in[s].append(vid)

    def gain(name: str, current: dict[str, int]) -> int:
        return sum(1 for vid in missing_in.get(name, ())
                   if current[vid] / size[vid] < FIT <= (current[vid] + 1) / size[vid])

    def value(name: str, current: dict[str, int]) -> float:
        # насколько навык приближает к вакансиям: прибавка к совпадению (1/размер) с весом «насколько вы
        # к ней уже близки». Одна мелкая вакансия «Python + 1С» не перевешивает десяток Data-вакансий с ClickHouse.
        # Где вы уже подходите, прибавка весит вдвое меньше: навык, который открывает новые вакансии, полезнее
        return sum(current[vid] / size[vid] ** 2 * (0.5 if current[vid] / size[vid] >= FIT else 1)
                   for vid in missing_in.get(name, ()))

    def row(name: str) -> SkillRow:
        return SkillRow(name=name, kind=vocab.kind(name), count=count_near[name], share=count_near[name] / len(near),
                        share_all=count_all[name] / with_skills, status=status(name), lift=lift(name),
                        gain=gain(name, have_n) if status(name) == "missing" else 0, legacy=name in LEGACY)

    ranked = [s for s, _ in count_near.most_common()]
    report.market = [row(s) for s in ranked if vocab.kind(s) != SOFT][:TOP]
    report.soft = [row(s) for s in ranked if vocab.kind(s) == SOFT][:8]
    listed = sorted(key_raw, key=lambda s: (-count_all[s], s.lower()))
    report.yours = [row(s) for s in listed]

    # путь: по одному навыку, каждый раз — самый полезный с учётом уже выбранных
    current = dict(have_n)
    candidates = [s for s in ranked if vocab.kind(s) != SOFT and s not in have
                  and count_near[s] >= max(2, len(near) * 0.01)]
    fit_now = report.fit
    for _ in range(5):
        if not candidates:
            break
        best = max(candidates, key=lambda s: (value(s, current), gain(s, current), lift(s) or 0))
        got = gain(best, current)
        if got == 0 and count_near[best] < len(near) * 0.05:
            break
        candidates.remove(best)
        for vid in missing_in.get(best, ()):
            current[vid] += 1
        fit_now += got
        report.path.append(PathStep(best, got, fit_now, count_near[best] / len(near)))

    # ── опыт ──
    report.your_level = level_of(profile.experience_months)
    levels = Counter(v.experience for v in near if v.experience in EXPERIENCE)
    total_levels = sum(levels.values())
    if total_levels:
        report.experience = [(level, levels[level] / total_levels) for level in EXPERIENCE]
        if report.your_level:
            mine = EXPERIENCE.index(report.your_level)
            share = sum(s for level, s in report.experience if EXPERIENCE.index(level) <= mine)
            if mine + 1 < len(EXPERIENCE):  # на ступень выше — тоже можно, если остальное сильное
                share += 0.4 * report.experience[mine + 1][1]
            report.parts["experience"] = (round(min(share, 1) * 100), f"хватает для {_pct(min(share, 1))} вакансий")

    # ── зарплата ──
    level_pay = [v.salary for v in paid if report.your_level and v.experience == report.your_level]
    values = level_pay if len(level_pay) >= 8 else [v.salary for v in paid]
    if len(values) >= 5:
        q1, _, q3 = statistics.quantiles(values, n=4)
        report.salary = {"n": len(values), "q1": q1, "median": statistics.median(values), "q3": q3,
                         "level": EXPERIENCE_LABELS.get(report.your_level or "", "") if values is level_pay else "",
                         "yours": profile.salary, "share": len(paid) / len(near)}

    # ── формат, английский, названия ──
    formats = Counter(f for v in near for f in set(v.formats))
    report.formats = [(f, formats[f] / len(near)) for f, _ in formats.most_common()]
    report.english_share = sum(1 for v in near if v.english or "Английский язык" in skills_of[v.id]) / len(near)
    # название: по навыкам вам подходят эти вакансии — а находят ли вас по словам должности?
    suitable = [v for v in vacancies if coverage.get(v.id, 0) >= FIT] or near
    words = Counter(w for v in suitable for w in _title_words(v.title))
    report.titles = [(w, n / len(suitable)) for w, n in words.most_common(12)]
    if profile.title:
        hits = sum(1 for v in suitable if _title_words(v.title) & mine_words) / len(suitable)
        # подходят по навыкам и смежные профессии (Data Engineer при «Python-разработчике»): 60% — уже отлично
        score = min(hits / 0.6, 1) * 100 - (15 if len(profile.title) > 60 else 0)
        report.parts["title"] = (max(0, round(score)), f"слова должности есть в {_pct(hits)} названий вакансий, "
                                                        "которые подходят вам по навыкам")
    else:
        report.parts["title"] = (0, "должность в резюме не найдена")

    # ── навыки: сколько востребованных есть, с весом по спросу ──
    weight = sum(r.count for r in report.market)
    if weight:
        got = sum(r.count * (1 if r.status == "key" else 0.75 if r.status == "text" else 0) for r in report.market)
        present = sum(1 for r in report.market if r.status != "missing")
        report.parts["skills"] = (round(got / weight * 100),
                                  f"{present} из {len(report.market)} самых востребованных навыков")

    _structure(report, profile, today)
    _matches(report, near, skills_of, hard_of, coverage, have, count_near, applied)
    total_weight = sum(WEIGHTS[k] for k in report.parts)
    if total_weight:
        report.score = round(sum(WEIGHTS[k] * points for k, (points, _) in report.parts.items()) / total_weight)
    report.advice = _advice(report, profile)
    return report


def _matches(report: Report, near, skills_of, hard_of, coverage, have, count_near, applied) -> None:
    """Подбор: вакансии, где у вас больше всего навыков и хватает опыта, — в вашем городе, там, куда готовы
    переехать, или удалённо (если город в резюме указан). Уже откликались — в конце."""
    mine = EXPERIENCE.index(report.your_level) if report.your_level else None
    places = {c.lower() for c in [report.profile.city] + report.profile.relocate if c}
    reachable = [v for v in near if not places or v.area.lower() in places or "REMOTE" in v.formats]
    picked = []
    for v in reachable if len(reachable) >= 3 else near:
        c = coverage.get(v.id)
        if c is None or c < 0.5:
            continue
        if mine is not None and v.experience in EXPERIENCE and EXPERIENCE.index(v.experience) > mine + 1:
            continue
        missing = sorted(hard_of[v.id] - have, key=lambda s: -count_near[s])
        size = len(hard_of[v.id])
        # 2 навыка из 2 — слабее, чем 7 из 8: сглаживаем, чтобы наверху были вакансии, о которых известно больше
        smooth = (size - len(missing) + 1) / (size + 2)
        picked.append((v.id in applied, -smooth, -(v.salary or 0),
                       Match(v.id, v.title, v.area, v.salary, c, missing[:4], v.id in applied,
                             size - len(missing), size)))
    picked.sort(key=lambda item: item[:3])
    report.matches = [item[3] for item in picked[:12]]


def _structure(report: Report, profile: ResumeProfile, today: date | None) -> None:
    """Оформление: то, что видно работодателю и по чему hh подбирает вакансии."""
    today = today or date.today()
    checks = report.checks
    n = report.key_count
    checks.append(Check(n >= 15, "Ключевые навыки",
                        f"{n} — hh подбирает по ним вакансии и считает «подходит по навыкам»" if n >= 15 else
                        f"{n} — мало: hh подбирает по ним вакансии, добавьте до 15–30 того, что умеете" if n else
                        "раздел не найден — без него hh не видит ваших навыков", weight=3, partial=8 <= n < 15))
    about = len(profile.about)
    checks.append(Check(about >= 300, "«Обо мне»",
                        f"{about} симв. — коротко о главном есть" if about >= 300 else
                        f"всего {about} симв. — 3–5 предложений: кто вы, в чём сильны, чем полезны" if about else
                        "нет — работодатель часто читает только его", weight=2, partial=0 < about < 300))
    a = profile.achievements
    checks.append(Check(a >= 4, "Результаты в цифрах",
                        f"{plural(a, 'строка', 'строки', 'строк')} с цифрами — хорошо: «ускорил в 3 раза», "
                        "«−40% ошибок»" if a >= 4 else
                        f"{a} — добавьте цифры: на сколько ускорили, сколько клиентов, какой объём" if a else
                        "нет — «сделал X, и стало на N% лучше» убеждает сильнее перечня обязанностей",
                        weight=2, partial=1 <= a < 4))
    if profile.jobs:
        chars = profile.last_job_chars
        checks.append(Check(chars >= 600, "Последнее место работы",
                            f"описание {chars} симв." if chars >= 600 else
                            f"описание всего {chars} симв. — распишите задачи, стек и результаты",
                            weight=2, partial=300 <= chars < 600))
    if profile.updated:
        days = (today - profile.updated).days
        checks.append(Check(days <= 14, "Свежесть резюме",
                            f"обновлено {days} дн. назад" if days > 0 else "обновлено сегодня" if days == 0 else
                            "обновлено недавно", weight=1))
    checks.append(Check(bool(profile.title) and len(profile.title) <= 60, "Название (должность)",
                        f"«{profile.title}»" + (f" — {len(profile.title)} симв., длинновато: работодатели ищут по "
                                                "названию, короче — точнее" if len(profile.title) > 60 else "")
                        if profile.title else "не найдено — по названию резюме ищут чаще всего", weight=1))
    if report.english_share >= 0.1:
        checks.append(Check(bool(profile.english), "Английский",
                            f"уровень {profile.english.upper()}" if profile.english else
                            f"не указан, а его просят в {_pct(report.english_share)} вакансий", weight=1))
    checks.append(Check(None, "Желаемая зарплата",
                        f"{money(profile.salary)} на руки" if profile.salary else "не указана", weight=0))
    checks.append(Check(None, "Ссылки (портфолио, GitHub)",
                        ", ".join(profile.links[:3]) if profile.links else "нет", weight=0))
    weighted = [c for c in checks if c.ok is not None and c.weight]
    if weighted:
        points = sum(c.weight * (1 if c.ok else 0.5 if c.partial else 0) for c in weighted)
        report.parts["structure"] = (round(points / sum(c.weight for c in weighted) * 100),
                                     f"в порядке {sum(1 for c in weighted if c.ok)} из {len(weighted)} пунктов")


def _advice(report: Report, profile: ResumeProfile) -> list[Advice]:
    """Что сделать — по убыванию пользы."""
    advice: list[Advice] = []
    near = max(report.near, 1)
    quick = [r for r in report.market + report.yours if r.status == "text" and r.share >= 0.03]
    quick = list({r.name: r for r in sorted(quick, key=lambda r: -r.share)}.values())[:6]
    if quick:
        advice.append(Advice("high", "Добавьте в «Ключевые навыки» то, что уже есть в вашем опыте: "
                             + ", ".join(f"{r.name} ({_pct(r.share)})" for r in quick)
                             + ". hh сравнивает с вакансиями именно этот список."))
    for step in report.path[:3]:
        if step.gain:
            level = "high" if step.gain >= max(3, near * 0.05) else "medium"
            more = plural(step.gain, "вакансия станет", "вакансии станут", "вакансий станут")
            advice.append(Advice(level, f"Изучите {step.name}: ещё {more} подходящими "
                                        f"(его просят в {_pct(step.share)} вакансий вашего рынка)."))
        elif step.share >= 0.1:
            advice.append(Advice("medium", f"Чаще всего из того, чего нет в резюме, просят {step.name} "
                                           f"({_pct(step.share)} вакансий)."))
    if "experience" in report.parts and report.parts["experience"][0] < 50 and report.experience:
        top = max(report.experience, key=lambda item: item[1])
        mine = EXPERIENCE_LABELS.get(report.your_level or "", "")
        advice.append(Advice("medium", f"Опыта хватает меньше чем для половины вакансий: чаще просят "
                                       f"«{EXPERIENCE_LABELS[top[0]]}» ({_pct(top[1])}), у вас — {mine}. Подробно "
                                       "распишите задачи, сложные проекты и пет-проекты: это засчитывают как опыт."))
    if profile.title:
        mine_words = _title_words(profile.title)
        popular = [(w, s) for w, s in report.titles if s >= 0.25 and w not in mine_words and w not in _GENERIC]
        if popular:
            advice.append(Advice("medium", "В названиях вакансий часто есть "
                                 + ", ".join(f"«{w}» ({_pct(s)})" for w, s in popular[:3])
                                 + " — а в названии резюме нет. Работодатели ищут резюме по этим словам."))
        if len(profile.title) > 60:
            advice.append(Advice("low", f"Название резюме длинное ({len(profile.title)} симв.). Оставьте должность "
                                        "и 1–2 главных направления, остальное — в «Обо мне»."))
    for check in report.checks:
        if check.ok is False and check.title in ("Ключевые навыки", "«Обо мне»", "Результаты в цифрах",
                                                 "Последнее место работы"):
            advice.append(Advice("medium" if check.weight >= 2 else "low", f"{check.title}: {check.detail}."))
        elif check.ok is False and check.title == "Свежесть резюме":
            advice.append(Advice("low", f"Резюме {check.detail}: hh показывает выше свежие. Обновите его или включите "
                                        "«Поднятие резюме» в настройках."))
        elif check.ok is False and check.title == "Английский":
            advice.append(Advice("medium", f"Укажите уровень английского: его просят в {_pct(report.english_share)} "
                                           "вакансий, а без уровня hh считает, что его нет."))
    salary = report.salary
    if salary:
        where = f" для опыта {salary['level']}" if salary["level"] else ""
        span = f"{money(salary['q1'])} – {money(salary['q3'])}, середина {money(salary['median'])}"
        if profile.salary and profile.salary > salary["q3"] * 1.1:
            advice.append(Advice("medium", f"Желаемая зарплата ({money(profile.salary)}) выше, чем в 3/4 вакансий"
                                           f"{where} ({span}): откликов и приглашений будет меньше."))
        elif profile.salary and profile.salary < salary["q1"] * 0.9:
            advice.append(Advice("low", f"Желаемая зарплата ({money(profile.salary)}) ниже рынка{where} ({span}) — "
                                        "можно просить больше."))
        elif not profile.salary:
            advice.append(Advice("low", f"Ориентир для зарплаты{where}: {span} на руки "
                                        f"(по {salary['n']} вакансиям с зарплатой)."))
    rare = [r for r in report.yours if r.share_all < 0.005 and r.kind != SOFT] if report.with_skills >= 150 else []
    if rare:
        advice.append(Advice("low", "Почти не просят: " + ", ".join(r.name for r in rare[:6])
                             + ". Если место в «Ключевых навыках» кончается — поставьте вместо них востребованные."))
    old = [r.name for r in report.yours if r.legacy and r.share_all < 0.02]
    if old:
        advice.append(Advice("low", "Устаревает и редко нужно: " + ", ".join(old) + " — упоминайте в опыте, "
                                    "но не на первом месте."))
    soft = [r for r in report.soft if r.status != "key" and r.share >= 0.03][:3]
    if soft:
        advice.append(Advice("low", "Часто просят и «гибкие» навыки: " + ", ".join(f"{r.name} ({_pct(r.share)})"
                                                                                   for r in soft)
                             + ". Если это про вас — добавьте в навыки: hh учитывает их в совпадении."))
    return advice


def to_text(report: Report) -> str:
    """Отчёт текстом — для консоли (scripts/analyze_resume.py)."""
    p = report.profile
    out = []
    years = f"{p.experience_months // 12} г. {p.experience_months % 12} мес." if p.experience_months else "не найден"
    out.append(f"Резюме: «{p.title or 'должность не найдена'}», опыт {years}, ключевых навыков {report.key_count}")
    out.append(f"Вакансий: {report.total}, с навыками {report.with_skills}, ваш рынок {report.near}, "
               f"подходят (≥{FIT:.0%} навыков) {report.fit}")
    if report.score is not None:
        out.append(f"\nОЦЕНКА: {report.score}/100 — {report.verdict}")
        for key, (points, note) in report.parts.items():
            out.append(f"  {PART_LABELS[key]:<11} {points:>3}  {note}")
    if report.advice:
        out.append("\nЧТО СДЕЛАТЬ")
        marks = {"high": "!!", "medium": "! ", "low": "· "}
        for item in report.advice:
            out.append(f"  {marks[item.level]} {item.text}")
    if report.market:
        out.append("\nНАВЫКИ РЫНКА (доля вашего рынка · зарплата · в резюме)")
        labels = {"key": "в навыках", "text": "только в опыте", "missing": "нет"}
        for r in report.market:
            pay = signed(r.lift) if r.lift is not None else ""
            extra = f"  +{r.gain} подходящих" if r.gain else ""
            out.append(f"  {r.name:<32} {_pct(r.share):>4}  {pay:>5}  {labels[r.status]}{extra}")
    if report.path:
        out.append("\nЧТО ИЗУЧИТЬ ПО ОЧЕРЕДИ")
        for i, step in enumerate(report.path, 1):
            out.append(f"  {i}. {step.name}: +{step.gain} подходящих (станет {step.fit_after}), "
                       f"просят в {_pct(step.share)}")
    if report.yours:
        out.append("\nВАШИ НАВЫКИ: АКТУАЛЬНОСТЬ")
        for r in report.yours:
            out.append(f"  {r.name:<32} {_pct(r.share_all):>4}  {relevance(r)}")
    if report.salary:
        s = report.salary
        out.append(f"\nЗАРПЛАТА{' (опыт ' + s['level'] + ')' if s['level'] else ''}: {money(s['q1'])} – "
                   f"{money(s['q3'])}, середина {money(s['median'])} — по {s['n']} вакансиям")
    if report.experience:
        out.append("ОПЫТ: " + ", ".join(f"{EXPERIENCE_LABELS[k]} {_pct(v)}" for k, v in report.experience))
    if report.formats:
        out.append("ФОРМАТ: " + ", ".join(f"{FORMAT_LABELS.get(k, k)} {_pct(v)}" for k, v in report.formats))
    if report.titles:
        out.append("СЛОВА В НАЗВАНИЯХ: " + ", ".join(f"{w} {_pct(s)}" for w, s in report.titles[:8]))
    if report.checks:
        out.append("\nОФОРМЛЕНИЕ")
        for c in report.checks:
            mark = "✓" if c.ok else "·" if c.ok is None else "~" if c.partial else "✗"
            out.append(f"  {mark} {c.title}: {c.detail}")
    if report.matches:
        out.append("\nПОДХОДЯЩИЕ ВАКАНСИИ")
        for m in report.matches:
            out.append(f"  {m.coverage:.0%}  {m.title} — {money(m.salary)}"
                       + (f"; нет: {', '.join(m.missing)}" if m.missing else "")
                       + ("  [уже откликались]" if m.applied else "") + f"  {m.url}")
    return "\n".join(out)


def relevance(row: SkillRow) -> str:
    """Насколько навык из резюме нужен рынку."""
    if row.share_all >= 0.1:
        return "часто просят"
    if row.share_all >= 0.03:
        return "актуально"
    if row.share_all >= 0.005:
        return "редко"
    return "почти не просят"



AI_PROMPT = (
    "Ты — карьерный консультант, который хорошо знает hh.ru. Тебе дают резюме соискателя и сводку по рынку "
    "вакансий, которую программа посчитала по реальной выдаче hh. Дай 5–8 конкретных советов, как улучшить ЭТО "
    "резюме под ЭТОТ рынок: что поменять в названии, в «Обо мне» и в описании опыта (с примерами формулировок "
    "из самого резюме), что добавить в ключевые навыки, что поставить выше, что убрать. Опирайся на цифры "
    "сводки. Не выдумывай опыт: навык, которого в резюме нет, можно только советовать изучить, а не «добавить». "
    "Пиши по-русски, коротко, нумерованным списком, без вступления и заключения.")


def ai_review(ai, report: Report, resume_text: str) -> str:
    """Советы нейросети по резюме и посчитанной сводке рынка (ai — AiClient). Ошибка связи — AiError."""
    lines = [f"Оценка программы: {report.score}/100 ({report.verdict})" if report.score is not None else ""]
    lines += [f"{PART_LABELS[k]}: {points}/100 — {note}" for k, (points, note) in report.parts.items()]
    labels = {"key": "есть в ключевых навыках", "text": "есть только в тексте опыта", "missing": "нет в резюме"}
    lines.append("Самые востребованные навыки (доля вакансий вашего рынка):")
    lines += [f"- {r.name}: {_pct(r.share)}, {labels[r.status]}" for r in report.market[:18]]
    if report.path:
        lines.append("Что изучить (по пользе): " + ", ".join(s.name for s in report.path))
    rare = [r.name for r in report.yours if relevance(r) == "почти не просят"]
    if rare:
        lines.append("Навыки резюме, которые почти не просят: " + ", ".join(rare))
    if report.salary:
        s = report.salary
        lines.append(f"Зарплаты на руки: {money(s['q1'])} – {money(s['q3'])}, середина {money(s['median'])}")
    if report.experience:
        lines.append("Требуемый опыт: " + ", ".join(f"{EXPERIENCE_LABELS[k]} {_pct(v)}" for k, v in report.experience))
    if report.titles:
        lines.append("Частые слова в названиях подходящих вакансий: "
                     + ", ".join(f"{w} {_pct(s)}" for w, s in report.titles[:8]))
    lines += [f"Оформление — {c.title}: {c.detail}" for c in report.checks if c.ok is False or c.partial]
    user = (f"РЕЗЮМЕ:\n{resume_text[:7000]}\n\nСВОДКА ПО РЫНКУ ({report.near} вакансий вашего рынка, всего "
            f"{report.total}):\n" + "\n".join(line for line in lines if line))
    if "qwen3" in getattr(ai, "model", "").lower():  # «рассуждающая» модель — без долгих размышлений
        user += "\n\n/no_think"
    return ai.complete([{"role": "system", "content": AI_PROMPT}, {"role": "user", "content": user}], max_tokens=1600)

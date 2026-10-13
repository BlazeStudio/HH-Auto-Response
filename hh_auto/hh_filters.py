"""Фильтры поиска самого hh.ru: что задаётся в config.toml ([hh_filters]) и как это превращается в ссылку.

Названия параметров и значения сверены с фильтрами на hh.ru/search/vacancy. Пустое значение в настройках —
фильтр не трогаем: на hh это «выбрано всё» (или как уже задано в самой ссылке поиска).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .hh_dicts import INDUSTRY_NAMES, PROFESSIONAL_ROLES

ORDER_BY = {
    "": "Как на hh (по соответствию)",
    "relevance": "По соответствию",
    "publication_time": "По дате",
    "salary_desc": "По убыванию зарплаты",
    "salary_asc": "По возрастанию зарплаты",
    "distance": "По удалённости от дома",
}
SEARCH_PERIOD = {0: "За всё время", 7: "За неделю", 3: "За три дня", 1: "За сутки"}
WORK_SCHEDULE = {
    "FIVE_ON_TWO_OFF": "5/2", "SIX_ON_ONE_OFF": "6/1", "FOUR_ON_THREE_OFF": "4/3", "FOUR_ON_TWO_OFF": "4/2",
    "FOUR_ON_FOUR_OFF": "4/4", "THREE_ON_THREE_OFF": "3/3", "THREE_ON_TWO_OFF": "3/2", "TWO_ON_TWO_OFF": "2/2",
    "TWO_ON_ONE_OFF": "2/1", "ONE_ON_THREE_OFF": "1/3", "ONE_ON_TWO_OFF": "1/2", "WEEKEND": "По выходным",
    "FLEXIBLE": "Свободный", "OTHER": "Другое",
}
WORKING_HOURS = {
    "HOURS_2": "2 часа", "HOURS_3": "3 часа", "HOURS_4": "4 часа", "HOURS_5": "5 часов", "HOURS_6": "6 часов",
    "HOURS_7": "7 часов", "HOURS_8": "8 часов", "HOURS_9": "9 часов", "HOURS_10": "10 часов", "HOURS_11": "11 часов",
    "HOURS_12": "12 часов", "HOURS_24": "24 часа", "FLEXIBLE": "По договорённости", "OTHER": "Другое",
}
EXPERIENCE = {
    "noExperience": "Нет опыта", "between1And3": "От 1 года до 3 лет", "between3And6": "От 3 до 6 лет",
    "moreThan6": "Более 6 лет",
}
WORK_FORMAT = {"REMOTE": "Удалённо", "HYBRID": "Гибрид", "ON_SITE": "На месте работодателя", "FIELD_WORK": "Разъездной"}
SALARY_MODE = {"": "Не важно", "MONTH": "За месяц", "SHIFT": "За смену", "HOUR": "За час",
               "FLY_IN_FLY_OUT": "За вахту", "SERVICE": "За услугу"}
SALARY_FREQUENCY = {"DAILY": "Ежедневно", "WEEKLY": "Раз в неделю", "TWICE_PER_MONTH": "Два раза в месяц",
                    "MONTHLY": "Раз в месяц", "PER_PROJECT": "За проект"}
EMPLOYMENT_FORM = {"FULL": "Полная занятость", "PART": "Частичная занятость", "PROJECT": "Подработка",
                   "FLY_IN_FLY_OUT": "Вахта"}
EDUCATION = {"not_required_or_not_specified": "Не требуется или не указано", "special_secondary": "Среднее профессиональное",
             "higher": "Высшее"}
LABELS = {
    "not_from_agency": "Без вакансий от кадровых агентств", "with_address": "С адресом",
    "accept_labor_contract": "Трудовой договор", "accredited_it": "От аккредитованных ИТ-компаний",
    "with_salary": "Указан доход", "internship": "Стажировка", "low_performance": "Меньше 10 откликов",
    "night_shifts": "Вечерние или ночные смены", "accept_kids": "Доступные с 14 лет",
}
INCLUSIVENESS = {"CHRONIC_DISEASES": "Хронические заболевания", "MOBILITY": "Нарушения мобильности",
                 "HEARING": "Нарушения слуха", "VISION": "Нарушения зрения", "MENTAL": "Ментальные особенности",
                 "UNSPECIFIED": "Категория не указана"}
SEARCH_FIELDS = {"name": "В названии вакансии", "company_name": "В названии компании",
                 "description": "В описании вакансии"}


@dataclass
class HhFilters:
    """Имена полей совпадают с параметрами ссылки hh. Пусто / 0 / false — фильтр не задан."""

    order_by: str = ""  # relevance | publication_time | salary_desc | salary_asc | distance
    search_period: int = 0  # 0 — за всё время, 7 — неделя, 3 — три дня, 1 — сутки
    work_schedule_by_days: list[str] = field(default_factory=list)  # график работы
    working_hours: list[str] = field(default_factory=list)  # рабочие часы в день
    experience: list[str] = field(default_factory=list)  # опыт работы
    work_format: list[str] = field(default_factory=list)  # формат работы
    salary: int = 0  # уровень дохода от, ₽
    salary_mode: str = ""  # за что указан доход: MONTH | SHIFT | HOUR | FLY_IN_FLY_OUT | SERVICE
    salary_frequency: list[str] = field(default_factory=list)  # частота выплат
    employment_form: list[str] = field(default_factory=list)  # тип занятости
    accept_temporary: bool = False  # оформление по ГПХ или по совместительству
    professional_role: list[str] = field(default_factory=list)  # специализации (id из справочника hh)
    employer_id: list[str] = field(default_factory=list)  # компании (id из ссылки hh.ru/employer/<id>)
    industry: list[str] = field(default_factory=list)  # отрасль компании (id, подотрасль — «7.540»)
    excluded_text: str = ""  # слова-исключения
    education: list[str] = field(default_factory=list)  # образование
    label: list[str] = field(default_factory=list)  # другие параметры
    inclusiveness_types: list[str] = field(default_factory=list)  # особенности здоровья
    search_field: list[str] = field(default_factory=list)  # где искать слова запроса


# Списочные фильтры: поле → справочник «значение → подпись» (None — любые id, проверяем только формат)
LIST_OPTIONS: dict[str, dict | None] = {
    "work_schedule_by_days": WORK_SCHEDULE, "working_hours": WORKING_HOURS, "experience": EXPERIENCE,
    "work_format": WORK_FORMAT, "salary_frequency": SALARY_FREQUENCY, "employment_form": EMPLOYMENT_FORM,
    "professional_role": None, "employer_id": None, "industry": None, "education": EDUCATION, "label": LABELS,
    "inclusiveness_types": INCLUSIVENESS, "search_field": SEARCH_FIELDS,
}
ID_FORMATS = {"professional_role": r"\d+", "employer_id": r"\d+", "industry": r"\d+(?:\.\d+)?"}
MANAGED = [f.name for f in fields(HhFilters)]  # параметры ссылки, которыми управляют настройки
TITLES = {
    "order_by": "сортировка", "search_period": "период", "work_schedule_by_days": "график",
    "working_hours": "часы в день", "experience": "опыт", "work_format": "формат", "salary": "доход",
    "salary_mode": "доход указан",
    "salary_frequency": "выплаты", "employment_form": "занятость", "accept_temporary": "ГПХ/совместительство",
    "professional_role": "специализации", "employer_id": "компании", "industry": "отрасли",
    "excluded_text": "слова-исключения", "education": "образование", "label": "другие параметры",
    "inclusiveness_types": "особенности здоровья", "search_field": "искать",
}


def normalize(f: HhFilters) -> HhFilters:
    """Значения из TOML к виду ссылки: числа → строки, ссылки на компании → их id, без пустых и повторов."""
    for name in LIST_OPTIONS:
        values = []
        for raw in getattr(f, name) or []:
            value = str(raw).strip()
            if name == "employer_id":
                found = re.search(r"employer/(\d+)|^(\d+)$", value)
                value = (found.group(1) or found.group(2)) if found else value
            if value and value not in values:
                values.append(value)
        setattr(f, name, values)
    f.order_by, f.salary_mode, f.excluded_text = f.order_by.strip(), f.salary_mode.strip(), f.excluded_text.strip()
    return f


def validate(f: HhFilters) -> list[str]:
    """Ошибки в фильтрах hh (пустой список — всё в порядке)."""
    errors = []
    if f.order_by not in ORDER_BY:
        errors.append(f"order_by: «{f.order_by}», допустимо: {', '.join(k for k in ORDER_BY if k)}")
    if f.search_period not in SEARCH_PERIOD:
        errors.append(f"search_period: {f.search_period}, допустимо: 0 (всё время), 7, 3, 1")
    if f.salary < 0:
        errors.append("salary: доход не может быть отрицательным")
    if f.salary_mode not in SALARY_MODE:
        errors.append(f"salary_mode: «{f.salary_mode}», допустимо: {', '.join(k for k in SALARY_MODE if k)}")
    for name, options in LIST_OPTIONS.items():
        values = getattr(f, name)
        if options is not None:
            wrong = [v for v in values if v not in options]
            if wrong:
                errors.append(f"{name}: неизвестные значения {wrong}, допустимо: {', '.join(options)}")
        else:
            wrong = [v for v in values if not re.fullmatch(ID_FORMATS[name], v)]
            if wrong:
                errors.append(f"{name}: должны быть номера (id), а не {wrong}")
    return errors


def to_params(f: HhFilters) -> list[tuple[str, str]]:
    """Параметры ссылки для заданных фильтров (незаданные не попадают)."""
    params: list[tuple[str, str]] = []
    for name in MANAGED:
        value = getattr(f, name)
        if name == "search_period":
            if value:
                params.append((name, str(value)))
        elif name == "salary":
            if value:
                params.append((name, str(value)))
        elif name == "accept_temporary":
            if value:
                params.append((name, "true"))
        elif isinstance(value, list):
            params += [(name, v) for v in value]
        elif value:
            params.append((name, value))
    return params


def apply(url: str, f: HhFilters) -> str:
    """Подставляет заданные фильтры в ссылку поиска: заменяют то, что было в ссылке; незаданные не трогаем."""
    u = urlparse(url)
    params = to_params(f)
    replaced = {name for name, _ in params}
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k not in replaced]
    query += params
    counts: dict[str, int] = {}
    for k, _ in params:
        counts[k] = counts.get(k, 0) + 1
    if any(c > 1 for c in counts.values()) and not any(k == "ored_clusters" for k, _ in query):
        query.append(("ored_clusters", "true"))  # несколько значений одного фильтра — через «ИЛИ», как на hh
    return urlunparse(u._replace(query=urlencode(query)))


def from_url(url: str) -> HhFilters:
    """Фильтры из ссылки поиска hh (например, скопированной после настройки фильтров на сайте)."""
    f = HhFilters()
    for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True):
        if key not in MANAGED or not value:
            continue
        current = getattr(f, key)
        if isinstance(current, list):
            current.append(value)
        elif isinstance(current, bool):
            setattr(f, key, value.lower() == "true")
        elif isinstance(current, int):
            setattr(f, key, int(value) if value.isdigit() else 0)
        else:
            setattr(f, key, value)
    return normalize(f)


def strip(url: str) -> str:
    """Ссылка без параметров, которыми управляют настройки (и без ored_clusters)."""
    u = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k not in MANAGED and k != "ored_clusters"]
    return urlunparse(u._replace(query=urlencode(query)))


def count(f: HhFilters) -> int:
    """Сколько фильтров задано — для подписи в окне."""
    return len({name for name, _ in to_params(f)})


def _names(name: str, values: list[str]) -> str:
    options = LIST_OPTIONS.get(name) or {"professional_role": PROFESSIONAL_ROLES, "industry": INDUSTRY_NAMES}.get(name, {})
    if len(values) > 4:
        return f"{len(values)} шт."
    return ", ".join(options.get(v, v) for v in values)


def describe(url: str) -> str:
    """Человекочитаемые фильтры hh в ссылке — для журнала."""
    f = from_url(url)
    parts = []
    for name in MANAGED:
        value = getattr(f, name)
        if not value:
            continue
        title = TITLES[name]
        if name == "order_by":
            parts.append(f"{title}: {ORDER_BY.get(value, value).lower()}")
        elif name == "search_period":
            parts.append(f"{title}: {SEARCH_PERIOD.get(value, value)}".lower())
        elif name == "salary":
            mode = SALARY_MODE.get(f.salary_mode, "").lower() if f.salary_mode else ""
            parts.append(f"{title}: от {value:,} ₽".replace(",", " ") + (f" {mode}" if mode else ""))
        elif name == "salary_mode":
            if not f.salary:  # с суммой режим уже показан в «доход»
                parts.append(f"{title}: {SALARY_MODE.get(value, value).lower()}")
        elif name == "accept_temporary":
            parts.append(title)
        elif name == "excluded_text":
            parts.append(f"{title}: «{value}»")
        else:
            parts.append(f"{title}: {_names(name, value)}")
    return "; ".join(parts) or "не заданы (всё, что найдёт hh)"

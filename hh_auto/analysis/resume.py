"""Резюме для анализа: текст из файла (PDF, Word, TXT) или с hh — и всё, что из него понятно без нейросети.

Формат любой: выгрузка hh («Скачать» → PDF), текст страницы резюме на hh, свой файл Word. Разделы
(«Опыт работы», «Навыки», «Обо мне»…) узнаются по заголовкам, а чего нет — то просто не оценивается.
Телефон и почта из текста убираются сразу: для анализа они не нужны.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree

from ..hh.resume import clean_resume_text

FILE = "analysis_resume.json"
FILE_TYPES = (("Резюме", "*.pdf *.docx *.txt *.md"), ("PDF", "*.pdf"), ("Word", "*.docx"), ("Текст", "*.txt *.md"))
MAX_CHARS = 30_000


class ResumeError(Exception):
    pass


# ─────────────────────────── чтение файла ───────────────────────────

def read_file(path: Path) -> str:
    """Текст резюме из файла. Неизвестный формат, пустой или испорченный файл — ResumeError с подсказкой."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            text = _pdf_text(path)
        elif suffix == ".docx":
            text = _docx_text(path)
        elif suffix in (".txt", ".md", ""):
            text = _plain_text(path)
        elif suffix in (".doc", ".rtf", ".odt"):
            raise ResumeError(f"формат {suffix} не поддерживается — сохраните резюме как PDF или DOCX "
                              "(на hh: «Скачать» → PDF)")
        else:
            raise ResumeError(f"не знаю, как читать {suffix}: подойдут PDF, DOCX и TXT")
    except OSError as e:
        raise ResumeError(f"файл не открылся: {e}") from e
    text = clean_resume_text(text)
    if len(text) < 200:
        raise ResumeError("в файле почти нет текста. Если это скан или картинка — сохраните резюме с hh "
                          "(«Скачать» → PDF) или из Word")
    return text[:MAX_CHARS]


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ResumeError("для PDF нужен пакет pypdf: pip install pypdf (или загрузите резюме в DOCX/TXT)") from e
    try:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages[:20]]
    except Exception as e:  # noqa: BLE001 — pypdf бросает свои ошибки на испорченных и зашифрованных файлах
        raise ResumeError(f"PDF не читается ({e})") from e
    text = "\n".join(pages)
    # pypdf склеивает соседние надписи: «9 месяцевВеб-студия», «ВысшееРоссийский университет»
    return re.sub(r"([а-яё])([A-ZА-ЯЁ])", r"\1 \2", text)


def _docx_text(path: Path) -> str:
    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        raise ResumeError("файл Word не читается — пересохраните его как DOCX или PDF") from e
    lines = []
    for paragraph in root.iter(f"{word}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{word}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{word}tab":
                parts.append("  ")
            elif node.tag == f"{word}br":
                parts.append("\n")
        lines.append("".join(parts))
    return "\n".join(lines)


def _plain_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ─────────────────────────── хранение ───────────────────────────

def save(root: Path, text: str, source: str) -> None:
    path = root / "data" / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"source": source, "loaded": datetime.now().strftime("%Y-%m-%d %H:%M"), "text": text}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load(root: Path) -> dict | None:
    """{"source", "loaded", "text"} — последнее загруженное для анализа резюме, или None."""
    try:
        data = json.loads((root / "data" / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("text") else None


def forget(root: Path) -> None:
    try:
        (root / "data" / FILE).unlink()
    except OSError:
        pass


# ─────────────────────────── разбор ───────────────────────────

# Заголовки разделов — строка начинается с них (у hh: «Опыт работы — 3 года», «Навыки  Python  pandas»)
_SECTIONS = (
    ("position", r"желаемая должность(?: и зарплата)?|desired position"),
    ("experience", r"опыт работы|work experience|experience"),
    ("education", r"образование|education|повышение квалификации(?:, курсы)?|курсы|courses"),
    ("skills", r"(?:ключевые |профессиональные |технические )?навыки|key skills|skills|hard skills"),
    ("languages", r"знание языков|languages"),
    ("about", r"обо мне|о себе|about me|about|summary"),
    ("extra", r"дополнительная информация|additional information|портфолио|рекомендации|сертификаты|"
              r"тесты, экзамены|подтверждение навыков|контакты|специализации"),
)
_HEADER = re.compile(r"^(" + "|".join(f"(?P<{name}>{words})" for name, words in _SECTIONS)
                     + r")\s*(?::|—|-|–|\s{2,}|$)", re.I)
_INLINE_ABOUT = re.compile(r"^(?:обо мне|о себе)\s+(?=\S)", re.I)  # «Обо мне Python-разработчик, …» (выгрузка hh)
_LEVEL_LINE = re.compile(r"^(?:продвинутый|средний|базовый|экспертный|начальный) уровень$", re.I)
_LANGUAGE_LINE = re.compile(r"^(?:русский|английский|немецкий|французский|испанский|китайский|итальянский|"
                            r"японский|корейский|арабский|турецкий|украинский|белорусский|казахский|english|"
                            r"german|french|spanish|chinese)\w*\s*(?:язык)?\s*[—–:-]", re.I)
_ROLE = re.compile(r"разработчик|developer|программист|engineer|инженер|аналитик|analyst|scientist|devops|qa\b|"
                   r"тестировщик|tester|дизайнер|designer|менеджер|manager|руководитель|head of|lead\b|архитектор|"
                   r"architect|администратор|administrator|специалист|бухгалтер|экономист|юрист|маркетолог|"
                   r"рекрутер|hr\b|консультант|оператор|продавец|водитель|врач|учитель|преподаватель|"
                   r"директор|product|project|owner|sre\b|стажер|стажёр|intern", re.I)
_MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7, "август": 8,
           "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5,
           "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MONTH = r"(январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|" \
         r"декабр\w*|jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)"
_NOW = r"(?:по\s+)?(?:настоящее\s+время|наст\.?\s*время|н\.\s?в\.?|сейчас|текущ\w+\s+время|present|now|current)"
_RANGE = re.compile(rf"{_MONTH}\s+(\d{{4}})\s*[—–-]\s*(?:{_MONTH}\s+(\d{{4}})|({_NOW}))", re.I)
_NUM_RANGE = re.compile(rf"\b(\d{{1,2}})[./](\d{{4}})\s*[—–-]\s*(?:(\d{{1,2}})[./](\d{{4}})|({_NOW}))", re.I)
_TOTAL = re.compile(r"опыт работы\s*[—:–-]?\s*(?:(\d+)\s*(?:год|года|лет)\w*)?\s*(?:и\s*)?(?:(\d+)\s*месяц\w*)?", re.I)
_DURATION = re.compile(r"\d+\s*(?:год|года|лет)\b|\d+\s*месяц", re.I)
_METRIC = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|процент\w*|раз(?:а|ы)?\b|x\b|×|млн|млрд|трлн|тыс|k\b|час\w*|минут\w*|"
                     r"секунд\w*|дн(?:я|ей)\b|недел\w*|₽|руб)|в\s+\d+(?:[.,]\d+)?\s*раз|[−-]\s?\d+\s*%", re.I)
_SALARY = re.compile(r"(?:от\s+)?(\d[\d\s]{2,10}\d)\s*(₽|руб\.?|rub|rur)\s*(на руки|до вычета налогов)?", re.I)
_UPDATED = re.compile(r"резюме обновлено\s+(\d{1,2})\s+([а-я]+)\s+(\d{4})", re.I)
_ENGLISH = re.compile(r"(?:английск\w*|english)(?:\s+язык)?\s*[—–:(\-]*\s*(?:язык\s*[—–:-]?\s*)?"
                      r"(a1|a2|b1|b2|c1|c2|native|родной|носитель|fluent|свободн\w*|продвинут\w*|"
                      r"upper[- ]?intermediate|pre[- ]?intermediate|intermediate|elementary|beginner|advanced|"
                      r"proficiency|средн\w*|базов\w*|технич\w*|разговорн\w*|читаю\w*)", re.I)
_LINK = re.compile(r"(?:https?://|www\.)\S+|(?:github|gitlab)\.com/\S+", re.I)

# уровень английского → CEFR (так его указывает hh в вакансиях)
ENGLISH_LEVELS = ("a1", "a2", "b1", "b2", "c1", "c2")
_ENGLISH_WORDS = {"native": "c2", "родной": "c2", "носитель": "c2", "fluent": "c1", "свободн": "c1",
                  "продвинут": "c1", "advanced": "c1", "proficiency": "c2", "upper": "b2", "intermediate": "b1",
                  "pre": "a2", "elementary": "a2", "beginner": "a1", "средн": "b1", "технич": "b1", "разговорн": "b1",
                  "базов": "a2", "читаю": "a2"}


@dataclass
class ResumeProfile:
    text: str  # весь текст без контактов
    title: str = ""  # желаемая должность
    salary: int | None = None  # желаемая зарплата, ₽ в месяц на руки
    key_skills: list[str] = field(default_factory=list)  # «Ключевые навыки» — как написаны
    languages: list[str] = field(default_factory=list)  # «Английский — C1 — Продвинутый»
    english: str = ""  # a1…c2 или пусто — не указан
    experience_months: int | None = None
    jobs: int = 0  # сколько мест работы с датами
    last_job_chars: int = 0  # длина описания последнего места работы
    about: str = ""
    updated: date | None = None
    formats: set[str] = field(default_factory=set)  # REMOTE, HYBRID, ON_SITE, FIELD_WORK
    city: str = ""
    relocate: list[str] = field(default_factory=list)  # «Готов к переезду: Санкт-Петербург»
    achievements: int = 0  # строк опыта с цифрами результата: «в 3 раза», «−73%», «за 20 минут»
    links: list[str] = field(default_factory=list)
    skill_text: str = ""  # где искать навыки, упомянутые в тексте: без списка специализаций и шапки hh

    @property
    def experience_years(self) -> float | None:
        return None if self.experience_months is None else self.experience_months / 12


def parse(text: str) -> ResumeProfile:
    # hh пишет «О себе» с неразрывным пробелом; двойные пробелы не трогаем — ими в PDF разделены навыки
    lines = [line.replace("\N{NO-BREAK SPACE}", " ").strip() for line in text.splitlines()]
    profile = ResumeProfile(text=text)
    sections: dict[str, list[str]] = {}
    current = "head"
    for line in lines:
        if not line:
            continue
        if _UPDATED.search(line):  # колонтитул выгрузки hh на каждой странице
            if profile.updated is None:
                profile.updated = _updated(line)
            continue
        about = _INLINE_ABOUT.match(line)
        header = _HEADER.match(line)
        if about:
            current = "about"
            line = line[about.end():]
        elif header:
            current = next(name for name, _ in _SECTIONS if header.group(name))
            rest = line[header.end():].strip()
            if current == "experience":
                sections.setdefault(current, []).append(line)  # «Опыт работы — 3 года 2 месяца»
                continue
            line = rest
            if current == "skills" and _HEADER.match(line or ""):  # «Навыки» → «Знание языков Русский — Родной»
                continue
            if not line:
                continue
        sections.setdefault(current, []).append(line)

    profile.title = _title(sections, lines)
    profile.salary = _salary(sections.get("position", []) + sections.get("head", [])[:12])
    for line in sections.get("skills", []) + sections.get("languages", []):
        line = re.sub(r"^(?:навыки|знание языков)\s+", "", line, flags=re.I)
        if _LEVEL_LINE.match(line):
            continue
        if _LANGUAGE_LINE.match(line):
            profile.languages.append(line)
            continue
        for item in re.split(r"\s{2,}|\t|\s*[,;•·|]\s*", line):
            item = item.strip(" -—–")
            if 1 <= len(item) <= 60 and item.lower() not in ("навыки", "ключевые навыки"):
                profile.key_skills.append(item)
    profile.key_skills = list(dict.fromkeys(profile.key_skills))
    english = _ENGLISH.search(" ".join(profile.languages) or text)
    if english:
        profile.english = _english_level(english.group(1))
    elif re.search(r"английск|english", " ".join(profile.languages), re.I):
        profile.english = "b1"

    experience = sections.get("experience", [])
    exp_text = "\n".join(experience)
    total = _TOTAL.search(exp_text) or _TOTAL.search(text)
    if total and (total.group(1) or total.group(2)):
        profile.experience_months = int(total.group(1) or 0) * 12 + int(total.group(2) or 0)
    periods, starts = _periods(exp_text or text)
    if profile.experience_months is None and periods:
        profile.experience_months = _months(periods)
    profile.jobs = len(periods)
    if starts:  # описание последнего места — от первой даты до второй (hh перечисляет с последнего)
        end = starts[1] if len(starts) > 1 else len(exp_text)
        profile.last_job_chars = len(exp_text[starts[0]:end])
    profile.achievements = sum(1 for line in experience if _METRIC.search(line) and not _RANGE.search(line)
                               and not (_DURATION.search(line) and len(line) < 40))
    profile.about = "\n".join(sections.get("about", []))
    profile.formats = _formats(text)
    city = re.search(r"(?:проживает|город|location)\s*:\s*([^\n,]+)", text, re.I)
    profile.city = city.group(1).strip() if city else ""
    move = re.search(r"готов\w* к переезду\s*:\s*([^\n]+)", text, re.I)
    if move:
        profile.relocate = [c.strip() for c in move.group(1).split(",")
                            if c.strip()[:1].isupper() and not re.search(r"готов|командиров", c, re.I)]
    profile.links = [link.rstrip(".,;)") for link in _LINK.findall(text) if "t.me/" not in link.lower()]
    # где искать навыки в тексте: всё, кроме шапки и «Специализаций» hh («Специалист технической поддержки»
    # там — желаемая сфера, а не навык)
    profile.skill_text = "\n".join(line for name, part in sections.items() if name not in ("head", "extra")
                                   for line in part)
    return profile


def _title(sections: dict[str, list[str]], lines: list[str]) -> str:
    for line in sections.get("position", []):
        if not _SALARY.search(line) and not line.lower().startswith(("специализац", "уровень дохода")):
            return line[:120]
    for line in lines[:15]:  # текст страницы hh начинается с должности; свой файл — где-то в шапке
        if line and len(line) <= 100 and _ROLE.search(line) and not _HEADER.match(line) and ":" not in line[:25]:
            return line
    return ""


def _salary(lines: list[str]) -> int | None:
    for line in lines:
        m = _SALARY.search(line)
        if m:
            value = int(re.sub(r"\D", "", m.group(1)))
            if m.group(3) and m.group(3).lower().startswith("до"):
                value = round(value * 0.87)
            return value if 10_000 <= value <= 5_000_000 else None
    return None


def _english_level(word: str) -> str:
    word = word.lower().replace(" ", "-")
    if word in ENGLISH_LEVELS:
        return word
    if word.startswith("upper"):
        return "b2"
    if word.startswith("pre"):
        return "a2"
    return next((level for prefix, level in _ENGLISH_WORDS.items() if word.startswith(prefix)), "b1")


def _month(word: str) -> int:
    word = word.lower()
    return next((n for prefix, n in _MONTHS.items() if word.startswith(prefix)), 1)


def _periods(text: str) -> tuple[list[tuple[int, int]], list[int]]:
    """Периоды работы в месяцах от нашей эры [(начало, конец)] и где в тексте каждый начинается."""
    now = date.today()
    today = now.year * 12 + now.month
    periods, starts = [], []
    for m in _RANGE.finditer(text):
        start = int(m.group(2)) * 12 + _month(m.group(1))
        end = today if m.group(5) else int(m.group(4)) * 12 + _month(m.group(3))
        if start <= end <= today + 1:
            periods.append((start, end))
            starts.append(m.start())
    if not periods:
        for m in _NUM_RANGE.finditer(text):
            start = int(m.group(2)) * 12 + int(m.group(1))
            end = today if m.group(5) else int(m.group(4)) * 12 + int(m.group(3))
            if start <= end <= today + 1:
                periods.append((start, end))
                starts.append(m.start())
    return periods, starts


def _months(periods: list[tuple[int, int]]) -> int:
    """Сколько всего месяцев работы: пересекающиеся периоды (совмещение) не считаются дважды."""
    total, last_end = 0, None
    for start, end in sorted(periods):
        if last_end is not None and start <= last_end:
            if end > last_end:
                total += end - last_end
                last_end = end
            continue
        total += end - start + 1
        last_end = end
    return total


def _updated(line: str) -> date | None:
    m = _UPDATED.search(line)
    try:
        return date(int(m.group(3)), _month(m.group(2)), int(m.group(1)))
    except (ValueError, AttributeError):
        return None


def _formats(text: str) -> set[str]:
    m = re.search(r"(?:формат работы|график работы|work format)\s*:\s*([^\n]+)", text, re.I)
    if not m:
        return set()
    value = m.group(1).lower()
    found = set()
    if "удал" in value or "remote" in value:
        found.add("REMOTE")
    if "гибрид" in value or "hybrid" in value:
        found.add("HYBRID")
    if "месте работодателя" in value or "офис" in value or "полный день" in value or "office" in value:
        found.add("ON_SITE")
    if "разъезд" in value:
        found.add("FIELD_WORK")
    return found

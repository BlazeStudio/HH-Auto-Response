"""Несколько резюме ([resumes]): под каждую вакансию — своё, по словам в её названии.

На hh можно держать несколько резюме: «ИИ-инженер», «Python-разработчик», «Системный аналитик». Резюме разработчика
на вакансию аналитика не подходит, поэтому резюме выбирается по названию вакансии. В настройках — по строке
на резюме: «резюме: слова». Строка сработала, если в названии вакансии есть хотя бы одно из её слов:
  • сработала одна строка — её резюме;
  • несколько или ни одной — решает нейросеть ([resumes] ai_pick); без неё — больше совпавших слов (при равенстве —
    что выше в правилах), а если не совпало ни одно слово — резюме по умолчанию ([resumes] default).
Выбранное резюме программа отмечает в окне отклика hh («Резюме для отклика», responder.py), письмо нейросети
и ответы на анкету пишутся по его тексту. Что выбрано и почему — в журнале и в таблице откликов.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.logger import log

LIST_FILE = "resumes.json"  # data/resumes.json — ваши резюме с hh (ID и название): для настроек и запасной список
TEXTS_DIR = "resumes"  # data/resumes/<ID>.txt — текст каждого резюме для нейросети
SHORT_WORD = 3  # слова до стольких букв ищутся только целиком: «ai» не найдётся в «Trainee» и «Airflow»

_SPLIT = re.compile(r"^(.+?):(?!//)(.*)$")  # первое двоеточие, которое не часть ссылки (https://)
_LINK_ID = re.compile(r"/resume/([0-9a-z]+)", re.I)
_BARE_ID = re.compile(r"\s*([0-9a-f]{20,})\s*", re.I)
_QUOTES = (('"', '"'), ("«", "»"), ("'", "'"))


@dataclass
class Rule:
    resume: str  # как записано в настройках: название резюме, его часть или ссылка на него
    words: list[str]
    line: int  # номер строки в настройках — для сообщений


@dataclass
class MyResume:
    id: str
    title: str
    words: list[str] = field(default_factory=list)  # слова из правил — на какие вакансии это резюме
    text: str = ""  # текст для нейросети (письма, анкеты); пусто — не загружали


@dataclass
class Choice:
    resume: MyResume
    why: str  # «в названии «ai», «llm»», «по умолчанию: …», «нейросеть: …» — для журнала и таблицы


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", _norm(text)))


def parse_rules(text: str) -> list[Rule]:
    """Правила из настроек: по строке на резюме, «резюме: слово, слово». Пустые строки и «# …» пропускаются.
    Ошибка в строке — ValueError с понятным текстом (validate_config покажет его в настройках)."""
    rules = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SPLIT.match(line)
        if not match or not match.group(1).strip():
            raise ValueError(f"строка {number} «{line[:60]}» — нужно «название резюме: слова через запятую»")
        words = [word.strip() for word in re.split(r"[,;]", match.group(2)) if word.strip()]
        if not words:
            raise ValueError(f"строка {number} «{line[:60]}» — после двоеточия нет ни одного слова")
        rules.append(Rule(match.group(1).strip(), words, number))
    return rules


def _pattern(word: str) -> re.Pattern | None:
    """Слово правила — как искать в названии: с начала слова («аналит» найдёт «Бизнес-аналитик»), короткое
    или в кавычках («ai», "java") — только целиком (иначе «java» нашлась бы в «JavaScript»)."""
    word = word.strip()
    whole = False
    for left, right in _QUOTES:
        if len(word) > 2 and word.startswith(left) and word.endswith(right):
            word, whole = word[1:-1].strip(), True
            break
    word = _norm(word)
    if not word:
        return None
    whole = whole or len(word) <= SHORT_WORD
    start = r"(?<!\w)" if re.match(r"\w", word) else ""  # «.net» — и в «ASP.NET»
    end = r"(?!\w)" if whole and re.search(r"\w$", word) else ""
    return re.compile(start + re.escape(word) + end)


def word_hits(title: str, words: list[str]) -> list[str]:
    """Слова правила, которые есть в названии вакансии (как записаны в настройках)."""
    text = _norm(title)
    return [word for word in words if (pattern := _pattern(word)) is not None and pattern.search(text)]


def find_resume(spec: str, resumes: list[MyResume]) -> list[MyResume]:
    """Ваши резюме, о которых строка настроек: ссылка или ID — ровно это резюме; иначе название целиком, его часть
    или все его слова (дефисы, скобки и точки не важны). Больше одного — запись неоднозначна."""
    link = _LINK_ID.search(spec) or _BARE_ID.fullmatch(spec)
    if link:
        return [r for r in resumes if r.id.lower() == link.group(1).lower()]
    want, words = _norm(spec), _tokens(spec)
    tests = (lambda title: _norm(title) == want, lambda title: want in _norm(title),
             lambda title: bool(words) and words <= _tokens(title))
    for test in tests:
        found = [r for r in resumes if test(r.title)]
        if found:
            return found
    return []


def mentioned(text: str, resumes: list[MyResume]) -> set[str]:
    """ID резюме, о которых уже есть строка в правилах (и недописанная, без слов) — для «Подставить мои резюме»."""
    found: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SPLIT.match(line)
        spec = (match.group(1) if match else line).strip()
        hits = find_resume(spec, resumes) if spec else []
        if len(hits) == 1:
            found.add(hits[0].id)
    return found


def _quote(words: list[str]) -> str:
    return ", ".join(f"«{word.strip(chr(34))}»" for word in words)


def short(title: str, limit: int = 60) -> str:
    return title if len(title) <= limit else title[:limit - 1].rstrip() + "…"


class ResumePicker:
    """Какое резюме — под вакансию: по словам в названии, при сомнении — нейросеть, иначе — по умолчанию."""

    def __init__(self, targets: list[MyResume], default: MyResume, mine: list[MyResume] | None = None,
                 ai=None, ai_pick: bool = True):
        self.targets = targets  # резюме из правил, по порядку строк; у каждого — свои слова
        self.default = default
        # все ваши резюме на hh; из правил и по умолчанию — те же объекты (с текстом для нейросети)
        known = {r.id: r for r in targets + [default]}
        self.mine = [known.get(r.id, r) for r in mine] if mine else list(known.values())
        self.ai = ai if ai_pick else None  # задаётся после загрузки: нейросеть создаётся позже резюме
        self.ai_pick = ai_pick
        self._asked: dict[str, Choice] = {}  # ответы нейросети за запуск: та же вакансия — тот же ответ

    def candidates(self) -> list[MyResume]:
        """Из чего выбирать, если по словам не понять: резюме из правил и резюме по умолчанию."""
        return list({r.id: r for r in self.targets + [self.default]}.values())

    def by_title(self, title: str) -> MyResume | None:
        """Ваше резюме по названию из окна отклика (hh подставил своё) — чтобы письмо было по нему."""
        found = [r for r in self.mine if _same(r.title, title)]
        return found[0] if len(found) == 1 else None

    def by_id(self, resume_id: str) -> MyResume | None:
        return next((r for r in self.mine if r.id == resume_id), None)

    def pick(self, title: str, company: str = "", snippet: str = "", use_ai: bool = True) -> Choice:
        matched = [(r, hits) for r in self.targets if (hits := word_hits(title, r.words))]
        if len(matched) == 1:
            resume, hits = matched[0]
            return Choice(resume, f"в названии {_quote(hits)}")
        if use_ai and self.ai is not None:
            choice = self._ask(title, company, snippet, [r for r, _ in matched] or self.candidates(), len(matched))
            if choice is not None:
                return choice
        if matched:
            resume, hits = max(matched, key=lambda pair: len(pair[1]))  # при равенстве — первое, что выше в правилах
            rest = "; ".join(f"«{short(r.title, 40)}»: {_quote(h)}" for r, h in matched if r is not resume)
            tie = all(len(h) == len(hits) for _r, h in matched)
            return Choice(resume, f"в названии {_quote(hits)} — " + ("подошло и другое, это выше в правилах"
                                                                       if tie else "совпало больше слов")
                          + f" ({rest})")
        return Choice(self.default, "по умолчанию: в названии нет слов из правил")

    def _ask(self, title: str, company: str, snippet: str, options: list[MyResume], matched: int) -> Choice | None:
        from ..ai.client import AiError

        if len(options) == 1:
            return Choice(options[0], "по умолчанию: в названии нет слов из правил")
        key = _norm(f"{title}\n{snippet[:300]}")
        if key in self._asked:
            return self._asked[key]
        try:
            index, reason = self.ai.pick_resume(title, company, snippet,
                                                [(r.title, r.words, r.text) for r in options])
        except AiError as e:
            log.warning(f"  ! нейросеть не выбрала резюме ({e}) — " + ("по числу совпавших слов" if matched
                                                                       else "беру резюме по умолчанию"))
            return None
        what = f"по словам подошли {matched} резюме" if matched else "в названии нет слов из правил"
        choice = Choice(options[index], f"{what}, нейросеть выбрала: {reason or 'без пояснения'}")
        self._asked[key] = choice
        return choice

    def for_chat(self, title: str, company: str, applied: dict) -> tuple[MyResume, str]:
        """Резюме для ИИ-ответов в чате вакансии: с которым откликались (data/state.json), а если отклик был
        до подбора резюме или не этой программой — по словам в названии, без нейросети."""
        from ..core.state import company_key

        want = (_norm(title), company_key(company))
        for entry in reversed(list(applied.values())):
            if entry.get("resume") and (_norm(entry.get("title", "")), company_key(entry.get("company", ""))) == want:
                resume = self.by_id(entry["resume"])
                if resume is not None:
                    return resume, "с ним откликались на эту вакансию"
        choice = self.pick(title, company, use_ai=False)
        return choice.resume, choice.why

    def text_for(self, resume: MyResume | None) -> str:
        """Текст резюме для нейросети. Своего текста нет (hh не открыл резюме и кеша нет) — текст резюме
        по умолчанию: это тот же человек и тот же опыт, а письмо без резюме вышло бы пустым."""
        if resume is not None and resume.text:
            return resume.text
        if resume is not None and resume is not self.default and self.default.text:
            warned = self.__dict__.setdefault("_no_text", set())
            if resume.id not in warned:
                warned.add(resume.id)
                log.warning(f"  ! текста резюме «{short(resume.title)}» нет — нейросеть пишет по резюме по умолчанию "
                            f"«{short(self.default.title)}»")
        return self.default.text


def _same(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a == b or a in b or b in a)


# ─────────────────────────── загрузка с hh ───────────────────────────


def save_list(root: Path, resumes: list[MyResume]) -> None:
    path = root / "data" / LIST_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([{"id": r.id, "title": r.title} for r in resumes], ensure_ascii=False, indent=1),
                        encoding="utf-8")
    except OSError as e:  # список — для удобства в настройках, работе не мешает
        log.debug(f"  список резюме не записался: {e}")


def saved_list(root: Path) -> list[MyResume]:
    """Ваши резюме с прошлого раза (data/resumes.json) — для настроек и если «Мои резюме» не открылись."""
    try:
        data = json.loads((root / "data" / LIST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [MyResume(str(item["id"]), str(item.get("title", ""))) for item in data
            if isinstance(item, dict) and item.get("id")] if isinstance(data, list) else []


def list_mine(page, root: Path) -> list[MyResume]:
    """Резюме со страницы «Мои резюме»; не открылась — список с прошлого раза."""
    from .raising import list_resumes

    try:
        found = [MyResume(r.id, " ".join((r.title or "").split())) for r in list_resumes(page)]
    except Exception as e:  # noqa: BLE001 — есть запасной список
        log.warning(f"  ! «Мои резюме» не открылись: {(str(e).splitlines() or [''])[0][:120]}")
        found = []
    old = {r.id: r.title for r in saved_list(root)}
    for resume in found:
        if not resume.title:  # на «Мои резюме» названия не видно — с прошлого раза или со страницы резюме
            resume.title = old.get(resume.id) or _title_from_page(page, resume.id)
    if found:
        save_list(root, found)
        return found
    old = saved_list(root)
    if old:
        log.warning(f"  ! на странице «Мои резюме» резюме не нашлись — беру список с прошлого раза ({len(old)})")
    return old


def _title_from_page(page, resume_id: str) -> str:
    """Название резюме (должность) со страницы резюме — если на «Мои резюме» его не прочитать."""
    from ..web import selectors as S
    from ..web.browser import open_url

    try:
        open_url(page, f"https://hh.ru/resume/{resume_id}", attempts=1, timeout=15)
        for selector in S.RESUME_PAGE_TITLE:
            title = page.locator(selector)
            if title.count():
                return " ".join(title.first.inner_text().split())
    except Exception as e:  # noqa: BLE001 — без названия резюме узнаётся по ссылке в правилах
        log.debug(f"  название резюме {resume_id[:8]}… не прочиталось: {(str(e).splitlines() or [''])[0][:120]}")
    return ""


def resolve(cfg, mine: list[MyResume]) -> tuple[list[MyResume], MyResume | None, str, list[str]]:
    """Правила → ваши резюме. Возвращает (резюме из правил со словами, резюме по умолчанию, откуда оно,
    предупреждения). Строку, которая не узнала резюме или узнала несколько, пропускаем — с предупреждением."""
    warnings: list[str] = []
    targets: dict[str, MyResume] = {}
    names = "; ".join(f"«{short(r.title, 50)}»" for r in mine)
    for rule in parse_rules(cfg.resumes.rules):
        found = find_resume(rule.resume, mine)
        if not found:
            warnings.append(f"строка {rule.line} «{short(rule.resume)}»: такого резюме нет среди ваших ({names}) — "
                            "строку пропускаю. Впишите часть названия резюме или ссылку на него")
            continue
        if len(found) > 1:
            warnings.append(f"строка {rule.line} «{short(rule.resume)}» подходит к нескольким резюме ("
                            + "; ".join(f"«{short(r.title, 50)}»" for r in found)
                            + ") — строку пропускаю. Уточните название или вставьте ссылку на резюме")
            continue
        resume = targets.setdefault(found[0].id, MyResume(found[0].id, found[0].title))
        resume.words += [word for word in rule.words if word not in resume.words]
    default, source = None, ""
    spec = cfg.resumes.default.strip()
    if spec:
        found = find_resume(spec, mine)
        if len(found) == 1:
            default, source = targets.get(found[0].id) or MyResume(found[0].id, found[0].title), "из настроек"
        else:
            warnings.append(f"резюме по умолчанию «{short(spec)}»: " + ("такого нет среди ваших" if not found
                                                                         else "подходит к нескольким")
                            + " — беру другое")
    if default is None and cfg.resume_id:
        known = next((r for r in mine if r.id == cfg.resume_id), None)
        if known is not None:
            default = targets.get(known.id) or MyResume(known.id, known.title)
            source = "из ссылки на резюме (resume_url или resume= в ссылке поиска)"
    if default is None and targets:
        default, source = next(iter(targets.values())), "первое в правилах"
    return list(targets.values()), default, source, warnings


def load(page, cfg, root: Path, need_text: bool,
         header: str = "Шаг 2/3: резюме — подбор под вакансию ([resumes])") -> ResumePicker | None:
    """Шаг 2 запуска при [resumes] enabled: ваши резюме с hh, кто на какие вакансии, резюме по умолчанию и, если
    письма или анкеты пишет нейросеть, тексты резюме. None — подбирать не из чего: дальше как с одним резюме."""
    from .resume import fetch_resume_text

    log.info(header)
    mine = list_mine(page, root)
    if not mine:
        log.warning("  ! не нашёл ни одного вашего резюме — подбор выключен, отклики с резюме, которое подставит hh")
        return None
    log.info(f"  ваши резюме на hh ({len(mine)}):")
    for resume in mine:
        log.info(f"    • «{resume.title}» — hh.ru/resume/{resume.id}")
    targets, default, source, warnings = resolve(cfg, mine)
    for warning in warnings:
        log.warning(f"  ! [resumes] {warning}")
    if not targets or default is None:
        log.warning("  ! ни одна строка правил не узнала ваше резюме — подбор выключен, работаю с одним резюме. "
                    "Проверьте «Настройки» → «Поиск» → «Несколько резюме»")
        return None
    log.info("  какое резюме на какие вакансии (слова в названии вакансии):")
    for resume in targets:
        log.info(f"    «{short(resume.title)}» ← {', '.join(resume.words)}")
    log.info(f"  по умолчанию (в названии нет слов из правил): «{short(default.title)}» — {source}")
    idle = [r for r in mine if r.id not in {t.id for t in targets} | {default.id}]
    if idle:
        log.info("  не участвуют в подборе: " + "; ".join(f"«{short(r.title, 50)}»" for r in idle))
    picker = ResumePicker(targets, default, mine, ai_pick=cfg.resumes.ai_pick)
    if need_text:
        texts = root / "data" / TEXTS_DIR
        for resume in picker.candidates():
            resume.text = fetch_resume_text(page, cfg, texts / f"{resume.id}.txt", resume_id=resume.id,
                                            title=short(resume.title))
        if default.text:  # data/resume.txt — резюме по умолчанию: «Проверка ИИ», советы к резюме, анализ
            try:
                (root / "data" / "resume.txt").write_text(default.text, encoding="utf-8")
            except OSError:
                pass
    return picker

"""Шаг 2: проверяем, что резюме из ссылки существует и принадлежит вашему аккаунту."""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Page

from ..web import selectors as S
from ..web.auth import RESUMES_URL, on_resumes_page
from ..web.browser import open_url
from ..core.config import Config
from ..core.logger import log


def verify_resume(page: Page, cfg: Config) -> str | None:
    """Возвращает название резюме (должность) — по нему сверяем резюме в окне отклика."""
    log.info("Шаг 2/3: проверка резюме")
    resume_id = cfg.resume_id
    if not resume_id:
        log.warning("  ! ID резюме не найден ни в resume_url, ни в параметре resume= ссылки поиска — проверка пропущена")
        return None
    source = "resume_url" if cfg.resume_url else "параметра resume= в ссылке поиска"
    log.info(f"  ID резюме (из {source}): {resume_id}")

    if not on_resumes_page(page):
        page.goto(RESUMES_URL, wait_until="domcontentloaded")
    links = page.locator(f'a[href*="/resume/{resume_id}"]')
    try:
        links.first.wait_for(state="attached", timeout=10_000)
        log.success("  ✓ резюме найдено в списке ваших резюме")
    except Exception:
        log.warning("  ! резюме с таким ID нет в списке ваших резюме (hh.ru/applicant/resumes) — проверьте ссылку")

    page.goto(f"https://hh.ru/resume/{resume_id}", wait_until="domcontentloaded")
    for selector in S.RESUME_PAGE_TITLE:
        title_el = page.locator(selector)
        if title_el.count():
            title = " ".join(title_el.first.inner_text().split())
            log.success(f"  ✓ резюме: «{title}»")
            return title

    log.warning(
        f"  ! не удалось прочитать название резюме (заголовок страницы: «{page.title()}») — "
        "сверка резюме в окне отклика отключена, название будет только выводиться в лог"
    )
    return None


_UI_LINES = {"редактировать", "добавить", "профиль", "/", "показать ещё", "скрыть", "изменить", "указать уровни",
             "перейти к тестам", "подробнее", "электронные сертификаты", "поднятие резюме", "портфолио",
             "рекомендации", "желаемую зарплату", "категория прав", "наличие автомобиля", "показывать рекомендации",
             "посмотреть курсы", "видимость резюме", "по-русски", "пройденные тесты и экзамены"}
# Реклама и подсказки hh на странице резюме — не часть резюме
_UI_NOISE = re.compile(r"промокод|подобрали для вас|автоподнят|минус \d+[–-]\d+%|видно (?:всем|работодател)|"
                       r"покажите в резюме|тем больше доверия|подтверждайте навыки|курсы по профессии|"
                       r"завершённость резюме|ещё вы можете добавить|сертификаты о ваших|получайте, чтобы|"
                       r"получить на госуслугах|подробные условия передачи|добавьте работы в портфолио", re.I)
_PHONE = re.compile(r"\+?\d[\d\s()\-]{8,}\d")
_EMAIL = re.compile(r"\S+@\S+\.\S+")
PROFILE_URL = "https://hh.ru/applicant/profile/me"


def clean_resume_text(raw: str) -> str:
    """Текст резюме без контактов и кнопок интерфейса: нейросети телефон и почта не нужны."""
    lines: list[str] = []
    for line in raw.splitlines():
        line = line.replace("\N{NO-BREAK SPACE}", " ").strip()  # hh пишет «Перейти к тестам» неразрывным пробелом
        if not line or line.lower() in _UI_LINES or _UI_NOISE.search(line):
            continue
        line = _PHONE.sub(lambda m: "[телефон скрыт]" if 10 <= sum(c.isdigit() for c in m.group()) <= 15
                          else m.group(), line)  # «2018 - 2022» — это годы, а не телефон
        line = _EMAIL.sub("[почта скрыта]", line)
        if not lines or lines[-1] != line:  # hh дублирует заголовки
            lines.append(line)
    return "\n".join(lines)


# Подпись и значение: «Проживает: Москва» или подпись отдельной строкой, а значение — следующей. Без двоеточия
# и значения подпись не считается: «Город Москва и область» — это текст вакансии или рекламы, а не факт о вас
_PERSONAL = re.compile(r"^(проживает|город(?:\s+проживания)?|место\s+(?:проживания|жительства)|гражданство|"
                       r"разрешение\s+на\s+работу|готовность\s+к\s+переезду|переезд)\s*(?::\s*(.*))?$", re.I)
_CITY_LABEL = re.compile(r"^(?:проживает|город|место)", re.I)
_CITY_LINE = re.compile(r"^(?:проживает|город(?:\s+проживания)?|место\s+(?:проживания|жительства))\s*:", re.I | re.M)
_NOT_A_VALUE = ("указать", "добавить", "изменить", "редактировать", "не указан")
_LIVES_IN = re.compile(r"жив[уёе]|прожива|город|гражданств", re.I)


def has_city(text: str) -> bool:
    """В тексте резюме есть строка с городом — нейросети есть что ответить на «В каком городе вы живёте?»."""
    return bool(_CITY_LINE.search(text))


def personal_lines(raw: str) -> list[str]:
    """Город, гражданство и переезд из текста страницы — строками «Проживает: Москва», каждая подпись один раз."""
    rows = [" ".join(line.replace("\N{NO-BREAK SPACE}", " ").split()) for line in raw.splitlines()]
    rows = [row for row in rows if row]
    found: dict[str, str] = {}
    for i, row in enumerate(rows):
        match = _PERSONAL.match(row)
        if not match:
            continue
        value = (match.group(2) or "").strip()
        if not value and i + 1 < len(rows) and not _PERSONAL.match(rows[i + 1]):
            value = rows[i + 1]
        if not value or len(value) > 80 or value.endswith("?") or value.lower().startswith(_NOT_A_VALUE):
            continue
        label = match.group(1)
        label = "Проживает" if _CITY_LABEL.match(label) else " ".join(label.split()).capitalize()
        found.setdefault(label, value)
    return [f"{label}: {value}" for label, value in found.items()]


def _with_personal(page: Page, text: str) -> str:
    """hh держит город, гражданство и переезд рядом с резюме, а не в нём самом: без них нейросеть отвечала на
    «В каком городе вы живёте?» общей фразой. Ищем их на странице резюме вокруг блока с текстом, затем в профиле."""
    if has_city(text):
        return text
    found = personal_lines(page.locator("body").inner_text())
    if not any(line.startswith("Проживает:") for line in found):
        try:
            open_url(page, PROFILE_URL, attempts=1, timeout=15)
            labels = {line.split(":")[0] for line in found}
            found += [line for line in personal_lines(page.locator("body").inner_text())
                      if line.split(":")[0] not in labels]
        except Exception as e:  # профиль не критичен: резюме уже прочитано, а город можно написать в контексте
            log.debug(f"  профиль hh не открылся: {(str(e).splitlines() or [''])[0]}")
    if found:
        log.info(f"  из анкеты hh для нейросети: {', '.join(line.split(':')[0] for line in found)}")
    return "\n".join([clean_resume_text("\n".join(found)), text]) if found else text


def _warn_no_city(cfg: Config, text: str) -> None:
    if not has_city(text) and not _LIVES_IN.search(cfg.ai.context):
        log.warning("  ! в резюме с hh не нашлось города — на вопрос о нём нейросеть ответит общей фразой. "
                    "Допишите его в «Настройки» → «Что ещё знать о вас»")


def _first_resume_id(page: Page) -> str | None:
    """ID неизвестен (поиск по запросу, ссылки на резюме нет) — первое резюме из «Мои резюме»."""
    from .raising import list_resumes

    try:
        resumes = list_resumes(page)
    except Exception as e:  # список не открылся — останется кеш
        log.debug(f"  список резюме не открылся: {(str(e).splitlines() or [''])[0]}")
        return None
    if not resumes:
        return None
    log.info(f"  ID резюме не задан — для нейросети беру первое из «Мои резюме»: «{resumes[0].title}». "
             "Другое резюме можно указать в настройках («Ссылка на резюме»)")
    return resumes[0].id


def fetch_resume_text(page: Page, cfg: Config, cache: Path, max_chars: int = 8000) -> str:
    """Текст резюме со страницы hh — контекст для ИИ-ответов. Если не открылось — берём прошлый из кеша."""
    resume_id = cfg.resume_id or _first_resume_id(page)
    if resume_id:
        try:
            url = f"https://hh.ru/resume/{resume_id}"
            # страницу резюме обычно только что открыла проверка резюме — второй раз её не загружаем;
            # переход — с повтором: прошлая страница hh, догружаясь, перебивала его (net::ERR_ABORTED)
            if page.url.split("?")[0] != url:
                open_url(page, url, attempts=2, timeout=20)
            area = page.locator('[data-qa="resume"], main, [data-qa="main-content"]').first
            area.wait_for(timeout=15_000)
            text = clean_resume_text(area.inner_text())
            if len(text) > 200:
                text = _with_personal(page, text)[:max_chars]
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(text, encoding="utf-8")
                log.success(f"  ✓ резюме для ИИ загружено с hh ({len(text)} симв.)")
                _warn_no_city(cfg, text)
                return text
        except Exception as e:  # страница резюме не критична — есть кеш
            log.warning(f"  ! не удалось прочитать резюме с hh: {(str(e).splitlines() or [''])[0]}")
    else:
        log.warning("  ! ID резюме неизвестен (resume_url или resume= в ссылке поиска) — беру резюме из кеша")
    if cache.exists():
        text = cache.read_text(encoding="utf-8")
        log.info(f"  резюме для ИИ — из прошлой загрузки ({len(text)} симв.)")
        _warn_no_city(cfg, text)
        return text
    log.warning("  ! резюме для ИИ нет — нейросеть будет отвечать только по контексту из настроек")
    return ""

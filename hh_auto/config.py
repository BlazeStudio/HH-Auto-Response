"""Загрузка и проверка config.toml."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


class ConfigError(Exception):
    pass


@dataclass
class Limits:
    max_responses: int = 100  # откликов за запуск (у hh лимит ~200 в сутки)
    max_pages: int = 40  # страниц выдачи
    delay_min: float = 6  # пауза между вакансиями, сек
    delay_max: float = 15
    page_delay_min: float = 3  # пауза перед следующей страницей выдачи
    page_delay_max: float = 7
    action_delay_min: float = 0.6  # пауза между кликами внутри одной вакансии
    action_delay_max: float = 1.6
    max_consecutive_errors: int = 5  # подряд идущих ошибок до аварийной остановки


SEARCH_MODES = ("resume", "query")
AREAS = {"": "Как в профиле hh", "1": "Москва", "2": "Санкт-Петербург", "113": "Вся Россия"}


@dataclass
class Search:
    # resume — вакансии по ссылке search_url (обычно «подходящие к резюме»);
    # query  — общий поиск hh по тексту query
    mode: str = "resume"
    query: str = ""  # как в строке поиска hh, например: python разработчик
    area: str = ""  # регион hh: 1 — Москва, 2 — Санкт-Петербург, 113 — вся Россия; пусто — как в профиле hh
    title_only: bool = False  # искать запрос только в названии вакансии


# Встроенные фильтры поиска hh: значение параметра ссылки → подпись
EXPERIENCE = {
    "noExperience": "Нет опыта",
    "between1And3": "От 1 года до 3 лет",
    "between3And6": "От 3 до 6 лет",
    "moreThan6": "Более 6 лет",
}
WORK_FORMAT = {
    "REMOTE": "Удалённо",
    "HYBRID": "Гибрид",
    "ON_SITE": "На месте работодателя",
    "FIELD_WORK": "Разъездной",
}


@dataclass
class Filters:
    # Фильтры самого hh (подставляются в ссылку поиска). Пусто — как в search_url.
    experience: list[str] = field(default_factory=list)  # noExperience, between1And3, between3And6, moreThan6
    work_format: list[str] = field(default_factory=list)  # REMOTE, HYBRID, ON_SITE, FIELD_WORK
    # слова в названии вакансии, при которых она пропускается без отклика (регистр не важен)
    exclude_title_words: list[str] = field(default_factory=lambda: ["преподаватель", "куратор"])
    # слова в названии компании, при которых вакансия пропускается (регистр не важен)
    exclude_company_words: list[str] = field(default_factory=list)
    # откликаться ТОЛЬКО на вакансии, где есть хотя бы одно из слов (пусто — на все)
    include_words: list[str] = field(default_factory=list)
    include_in_snippet: bool = False  # искать include_words и в описании из карточки, не только в названии


@dataclass
class Questions:
    # Работодатель задал ровно один вопрос и он о зарплате → отвечаем salary_answer,
    # прикладываем сгенерированное письмо и откликаемся. Остальные вопросы пока пропускаем.
    answer_salary: bool = True
    salary_answer: str = "Рассматриваю от 100"
    # с включённым ИИ ([ai] enabled) анкеты заполняет нейросеть: любые вопросы, текст и варианты
    max_ai_questions: int = 15  # анкеты длиннее — пропускаем (обычно это тесты)
    salary_keywords: list[str] = field(
        default_factory=lambda: [
            "зарплат", "заработн", "сумм", "доход", "оклад", "вознагражд", "компенсац", "з/п", "₽", "руб",
        ]
    )


@dataclass
class Chats:
    url: str = "https://hh.ru/chat"
    rejection_text: str = "Отказ"  # последнее сообщение чата, по которому узнаём отказ
    delay_min: float = 0.5  # пауза после открытия чата, чтобы hh отметил его прочитанным
    delay_max: float = 1.0


# Провайдеры ИИ: (адрес OpenAI-совместимого API, модель по умолчанию, где взять ключ)
AI_PROVIDERS = {
    "deepseek": ("https://api.deepseek.com", "deepseek-chat",
                 "platform.deepseek.com → API keys. Платно, но очень дёшево (копейки за анкету)"),
    "openrouter": ("https://openrouter.ai/api/v1", "deepseek/deepseek-chat-v3-0324:free",
                   "openrouter.ai → Keys. Бесплатные модели — с пометкой :free (актуальные — на openrouter.ai/models)"),
    "ollama": ("http://localhost:11434/v1", "qwen3:8b",
               "ollama.com — нейросеть на вашем компьютере, бесплатно, ключ не нужен"),
    "custom": ("", "", "любой OpenAI-совместимый сервис: укажите base_url и model"),
}


@dataclass
class Ai:
    # ИИ-ответы на анкеты работодателей («Робот-рекрутер») в чатах
    enabled: bool = False
    provider: str = "openrouter"  # deepseek | openrouter | ollama | custom
    api_key: str = ""  # или переменная окружения HH_AI_API_KEY
    model: str = ""  # пусто — модель провайдера по умолчанию
    base_url: str = ""  # пусто — адрес провайдера
    # Что ещё нейросети знать о вас, кроме резюме: зарплата, формат работы, город, когда готовы выйти…
    context: str = ""
    # true — отвечать только ботам hh (Робот-рекрутер, ИИ-помощник…), живым людям отвечаете вы.
    # false — отвечать всем; встречи, собеседования и звонки всё равно остаются вам
    only_robot: bool = False
    robot_markers: list[str] = field(default_factory=lambda: ["Робот-рекрутер", "ИИ-помощник", "ассистент рекрутера",
                                                             "на базе AI", "на базе ИИ"])
    max_answer_chars: int = 300  # длина одного ответа
    reply_wait: float = 25  # сколько ждать следующего вопроса робота после ответа, сек
    max_answers_per_chat: int = 20
    timeout: float = 60  # ожидание ответа нейросети, сек


LETTER_MODES = ("generate", "ai", "template", "none")


@dataclass
class Letter:
    # generate — кнопка hh «Сгенерировать» (нужна подписка), ai — пишет нейросеть из [ai] (подписка не нужна),
    # template — готовый текст template_text, none — откликаться без письма
    mode: str = "generate"
    template_text: str = ""  # для mode = template; можно вставить {vacancy} и {company}
    generate_timeout: float = 60  # сколько ждать генерацию письма, сек
    min_length: int = 30  # письмо короче считаем несгенерированным
    fallback_text: str = ""  # запасной текст, если генерация не удалась ("" = пропустить вакансию)
    ai_max_chars: int = 900  # длина письма нейросети (mode = ai или запасной вариант для generate)


@dataclass
class Relocation:
    confirm_other_region: bool = True  # соглашаться на «вакансия в другом регионе/стране»
    decline_if_not_remote: bool = True  # другой регион без «Можно удалённо» = придётся переезжать → отказ
    # слова в карточке вакансии, означающие переезд → отказ
    decline_keywords: list[str] = field(
        default_factory=lambda: ["переезд", "переех", "релокац", "перемещ", "relocat"]
    )


@dataclass
class Browser:
    channel: str = "chrome"  # chrome | msedge | "" (встроенный Chromium Playwright)
    headless: bool = False
    profile_dir: str = "browser-profile"
    slow_mo: int = 0
    login_timeout: int = 600  # сколько ждать ручного входа, сек


@dataclass
class Geo:
    proxy: str = ""  # http://user:pass@host:port или socks5://host:port
    locale: str = "ru-RU"
    timezone: str = "Europe/Moscow"
    check_ip: bool = True  # показывать страну внешнего IP перед стартом
    require_ru_ip: bool = False  # останавливаться, если IP не российский

    def playwright_proxy(self) -> dict | None:
        if not self.proxy:
            return None
        u = urlparse(self.proxy)
        if u.scheme not in ("http", "https", "socks5") or not u.hostname:
            raise ConfigError(f"[geo] proxy: неверный формат «{self.proxy}», пример: http://user:pass@1.2.3.4:8080")
        server = f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")
        proxy = {"server": server}
        if u.username:
            proxy["username"] = unquote(u.username)
            proxy["password"] = unquote(u.password or "")
        return proxy

    def proxy_for_log(self) -> str:
        if not self.proxy:
            return "не используется (прямое подключение)"
        u = urlparse(self.proxy)
        return f"{u.scheme}://{'***@' if u.username else ''}{u.hostname}:{u.port}"


@dataclass
class Logs:
    # скриншот и HTML страницы при каждой ошибке (logs/snapshots) — по ним чинят селекторы, если hh поменял
    # вёрстку. Занимают место (~1–3 МБ на снимок); false — не сохранять
    snapshots: bool = True


@dataclass
class Notify:
    # уведомление по итогам запуска (откликов отправлено, пропущено, ошибок). Включается только с вашего
    # разрешения: приложение спросит после первого запуска, в консоли — вручную здесь
    desktop: bool = False  # системное уведомление Windows / macOS / Linux
    telegram: bool = False  # сообщение в Telegram от вашего бота (удобно при запуске на сервере)
    telegram_token: str = ""  # токен бота от @BotFather (или переменная окружения HH_TELEGRAM_TOKEN)
    telegram_chat_id: str = ""  # ваш chat id (узнать: написать боту и открыть api.telegram.org/bot<токен>/getUpdates)


@dataclass
class Config:
    search_url: str
    resume_url: str = ""
    skip_on_resume_mismatch: bool = True
    search: Search = field(default_factory=Search)
    limits: Limits = field(default_factory=Limits)
    filters: Filters = field(default_factory=Filters)
    letter: Letter = field(default_factory=Letter)
    questions: Questions = field(default_factory=Questions)
    relocation: Relocation = field(default_factory=Relocation)
    chats: Chats = field(default_factory=Chats)
    ai: Ai = field(default_factory=Ai)
    browser: Browser = field(default_factory=Browser)
    geo: Geo = field(default_factory=Geo)
    logs: Logs = field(default_factory=Logs)
    notify: Notify = field(default_factory=Notify)

    @property
    def resume_id(self) -> str | None:
        """ID резюме: из resume_url, иначе из параметра resume= ссылки поиска."""
        if self.resume_url:
            m = re.search(r"/resume/([0-9a-zA-Z]+)", self.resume_url)
            if m:
                return m.group(1)
        return parse_qs(urlparse(self.search_url).query).get("resume", [None])[0]


def save_config(cfg: Config, path: Path) -> None:
    """Сохраняет настройки в TOML (используется приложением; комментарии не сохраняются)."""
    lines = [
        "# Настройки HH-Auto-Response. Файл записан приложением; описание параметров — в config.example.toml",
        "",
    ]
    top = {"search_url": cfg.search_url, "resume_url": cfg.resume_url,
           "skip_on_resume_mismatch": cfg.skip_on_resume_mismatch}
    lines += [f"{key} = {_toml_value(value)}" for key, value in top.items()]
    for f in fields(cfg):
        section = getattr(cfg, f.name)
        if not hasattr(section, "__dataclass_fields__"):
            continue
        lines += ["", f"[{f.name}]"]
        lines += [f"{sf.name} = {_toml_value(getattr(section, sf.name))}" for sf in fields(section)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    # JSON-строка — валидная базовая строка TOML (те же экранирования \" \\ \n \uXXXX)
    return json.dumps(str(value), ensure_ascii=False)


def _section(cls, data: dict, name: str):
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"[{name}]: неизвестные параметры {', '.join(sorted(unknown))}")
    return cls(**data)


def validate_search_url(search_url: str) -> None:
    u = urlparse(search_url)
    if u.hostname not in ("hh.ru", "www.hh.ru") or not u.path.startswith("/search/vacancy"):
        raise ConfigError(
            "search_url должен быть ссылкой на поиск вакансий hh.ru вида https://hh.ru/search/vacancy?... "
            "(региональные сайты hh.kz, hh.uz и т.п. не поддерживаются)"
        )


def validate_search_source(cfg: Config) -> None:
    """Есть ли откуда брать вакансии: ссылка (mode = resume) или текст запроса (mode = query)."""
    if cfg.search.mode == "query":
        if not cfg.search.query.strip():
            raise ConfigError("[search] mode = query, но запрос (query) пустой")
    else:
        validate_search_url(cfg.search_url)


def load_config(path: Path, check_search_url: bool = True) -> Config:
    """check_search_url=False — для формы настроек в приложении: ссылку ещё могут не вписать."""
    if not path.exists():
        raise ConfigError(f"не найден {path}. Скопируйте config.example.toml в config.toml и укажите ссылку поиска.")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: ошибка синтаксиса TOML: {e}") from e

    cfg = Config(
        search_url=raw.pop("search_url", "").strip(),
        resume_url=raw.pop("resume_url", "").strip(),
        skip_on_resume_mismatch=raw.pop("skip_on_resume_mismatch", True),
        search=_section(Search, raw.pop("search", {}), "search"),
        limits=_section(Limits, raw.pop("limits", {}), "limits"),
        filters=_section(Filters, raw.pop("filters", {}), "filters"),
        letter=_section(Letter, raw.pop("letter", {}), "letter"),
        questions=_section(Questions, raw.pop("questions", {}), "questions"),
        relocation=_section(Relocation, raw.pop("relocation", {}), "relocation"),
        chats=_section(Chats, raw.pop("chats", {}), "chats"),
        ai=_section(Ai, raw.pop("ai", {}), "ai"),
        browser=_section(Browser, raw.pop("browser", {}), "browser"),
        geo=_section(Geo, raw.pop("geo", {}), "geo"),
        logs=_section(Logs, raw.pop("logs", {}), "logs"),
        notify=_section(Notify, raw.pop("notify", {}), "notify"),
    )
    if raw:
        raise ConfigError(f"неизвестные параметры в config: {', '.join(sorted(raw))}")
    if check_search_url:
        validate_search_source(cfg)
    validate_config(cfg)
    return cfg


def validate_config(cfg: Config) -> None:
    """Проверки значений, общие для config.toml и формы настроек в приложении."""
    if cfg.ai.provider not in AI_PROVIDERS:
        raise ConfigError(f"[ai] provider: «{cfg.ai.provider}», допустимо: {', '.join(AI_PROVIDERS)}")
    if cfg.search.mode not in SEARCH_MODES:
        raise ConfigError(f"[search] mode: «{cfg.search.mode}», допустимо: {', '.join(SEARCH_MODES)}")
    if cfg.search.area and not cfg.search.area.isdigit():
        raise ConfigError("[search] area — номер региона hh (1 — Москва, 2 — Санкт-Петербург, 113 — вся Россия)")
    if cfg.search_url:
        validate_search_url(cfg.search_url)
    for key, allowed in (("experience", EXPERIENCE), ("work_format", WORK_FORMAT)):
        wrong = [v for v in getattr(cfg.filters, key) if v not in allowed]
        if wrong:
            raise ConfigError(f"[filters] {key}: неизвестные значения {wrong}, допустимо: {', '.join(allowed)}")
    if cfg.letter.mode not in LETTER_MODES:
        raise ConfigError(f"[letter] mode: «{cfg.letter.mode}», допустимо: {', '.join(LETTER_MODES)}")
    if cfg.letter.mode == "template" and not cfg.letter.template_text.strip():
        raise ConfigError("[letter] mode = template, но template_text пустой")
    if cfg.limits.delay_min > cfg.limits.delay_max:
        raise ConfigError("[limits] delay_min больше delay_max")
    cfg.geo.playwright_proxy()  # проверка формата прокси заранее

"""Загрузка и проверка config.toml."""

from __future__ import annotations

import re
import shutil
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import hh_filters
from .config_file import toml_value, write_values
from .hh_filters import HhFilters


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
    # не больше стольких откликов в одну компанию за per_company_days дней (0 — без ограничения): крупная
    # компания с десятком похожих вакансий не получит десяток одинаковых откликов
    per_company: int = 0
    per_company_days: int = 30


SEARCH_MODES = ("resume", "query")
AREAS = {"": "Как в профиле hh", "1": "Москва", "2": "Санкт-Петербург", "113": "Вся Россия"}


@dataclass
class Search:
    # resume — вакансии по ссылке search_url (обычно «подходящие к резюме»);
    # query  — общий поиск hh по тексту query
    mode: str = "resume"
    # как в строке поиска hh, например: python разработчик. Несколько запросов — по одному на строку:
    # за запуск пройдём их по очереди (общий лимит откликов), одинаковые вакансии не повторяются
    query: str = ""
    area: str = ""  # регион hh: 1 — Москва, 2 — Санкт-Петербург, 113 — вся Россия; пусто — как в профиле hh
    title_only: bool = False  # искать запрос только в названии вакансии

    @property
    def queries(self) -> list[str]:
        """Запросы по одному на строку, без пустых и повторов."""
        seen, result = set(), []
        for line in self.query.splitlines():
            line = line.strip()
            if line and line.lower() not in seen:
                seen.add(line.lower())
                result.append(line)
        return result


@dataclass
class Filters:
    # Фильтры программы (проверяются по карточке вакансии, без клика). Фильтры самого hh — в [hh_filters].
    # слова в названии вакансии, при которых она пропускается без отклика (регистр не важен)
    exclude_title_words: list[str] = field(default_factory=lambda: ["преподаватель", "куратор"])
    # слова в названии компании, при которых вакансия пропускается (регистр не важен)
    exclude_company_words: list[str] = field(default_factory=list)
    # откликаться ТОЛЬКО в компании, в названии которых есть хотя бы одно из слов (пусто — проверку пропускаем)
    include_company_words: list[str] = field(default_factory=list)
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


@dataclass
class AiFilter:
    # Фильтр вакансий нейросетью: перед откликом спрашиваем ИИ, подходит ли вакансия.
    # ИИ читает заголовок и сниппет карточки и по описанию criteria отвечает «интересна» / «не интересна».
    enabled: bool = False
    # Что вы ищете и чего не хотите — свободным текстом. Именно это описание уходит нейросети в каждой вакансии.
    criteria: str = ""
    max_snippet_chars: int = 800  # сколько символов сниппета карточки отправлять нейросети
    # true — при сбое ИИ (нет связи, непонятный ответ) вакансию пропустить; false — обработать как обычно
    skip_on_error: bool = False


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
    keep_days: int = 14  # снимки старше стольких дней удаляются при запуске; 0 — хранить всё


WEEKDAYS = {"1": "Пн", "2": "Вт", "3": "Ср", "4": "Чт", "5": "Пт", "6": "Сб", "7": "Вс"}


@dataclass
class Schedule:
    # Отклики по расписанию, пока открыто приложение (для консольных скриптов — Планировщик задач Windows
    # или cron, см. README). Время — по часам компьютера.
    enabled: bool = False
    times: list[str] = field(default_factory=lambda: ["10:00"])  # во сколько запускать: "10:00", "15:30"
    days: list[str] = field(default_factory=lambda: ["1", "2", "3", "4", "5"])  # 1 — понедельник … 7 — воскресенье
    chats_after: bool = False  # после откликов прочитать отказы в чатах (и ответить ботам, если включён ИИ)

    def slots(self) -> list[tuple[int, int]]:
        """Время запусков (часы, минуты) по порядку. Неверную запись не пропускает validate_config."""
        return sorted({_parse_time(t) for t in self.times})


def _parse_time(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", text)
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ConfigError(f"[schedule] times: «{text}» — нужно время вида 09:30")
    return int(m[1]), int(m[2])


@dataclass
class StatsSettings:
    # [stats] — вкладка «Статистика»: только счётчики по дням (сколько отправлено, пропущено, отказов…),
    # без названий вакансий и компаний; файл data/stats.json
    disabled: bool = False  # true — не вести статистику: ничего не записывается
    keep_days: int = 365  # дни старше удаляются сами; 0 — хранить всё


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
    ai_filter: AiFilter = field(default_factory=AiFilter)
    browser: Browser = field(default_factory=Browser)
    geo: Geo = field(default_factory=Geo)
    hh_filters: HhFilters = field(default_factory=HhFilters)
    logs: Logs = field(default_factory=Logs)
    notify: Notify = field(default_factory=Notify)
    schedule: Schedule = field(default_factory=Schedule)
    stats: StatsSettings = field(default_factory=StatsSettings)

    @property
    def resume_id(self) -> str | None:
        """ID резюме: из resume_url, иначе из параметра resume= ссылки поиска."""
        if self.resume_url:
            m = re.search(r"/resume/([0-9a-zA-Z]+)", self.resume_url)
            if m:
                return m.group(1)
        return parse_qs(urlparse(self.search_url).query).get("resume", [None])[0]


TOP_KEYS = ("search_url", "resume_url", "skip_on_resume_mismatch")


def config_values(cfg: Config) -> dict[str, dict]:
    """Настройки как {раздел: {параметр: значение}}, "" — параметры до первого раздела."""
    values: dict[str, dict] = {"": {key: getattr(cfg, key) for key in TOP_KEYS}}
    for f in fields(cfg):
        section = getattr(cfg, f.name)
        if hasattr(section, "__dataclass_fields__"):
            values[f.name] = {sf.name: getattr(section, sf.name) for sf in fields(section)}
    return values


def _full_text(values: dict[str, dict]) -> str:
    """Файл целиком, без комментариев — запасной путь, если править старый файл по строкам не вышло."""
    lines = ["# Настройки HH-Auto-Response; описание параметров — в config.example.toml", ""]
    lines += [f"{key} = {toml_value(value)}" for key, value in values.get("", {}).items()]
    for section, keys in values.items():
        if section:
            lines += ["", f"[{section}]"] + [f"{key} = {toml_value(value)}" for key, value in keys.items()]
    return "\n".join(lines) + "\n"


def save_config(cfg: Config, path: Path) -> None:
    """Сохраняет настройки, не трогая остального в файле: меняются только строки с другим значением,
    комментарии и порядок остаются, недостающие параметры дописываются в свой раздел."""
    from .paths import bundled

    write_values(path, config_values(cfg), example=bundled("config.example.toml"), fallback=_full_text)


def upgrade_config(path: Path) -> list[str]:
    """После обновления программы: дописать в config.toml параметры, которых в нём ещё нет (со значениями
    по умолчанию и пояснениями), ничего не меняя в остальном. Старый файл — в config.toml.bak.
    Возвращает добавленные «раздел.параметр»; файл с ошибкой синтаксиса не трогает."""
    if not path.exists():
        return []
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
        return []
    defaults = config_values(Config(search_url=""))
    old_filters = raw.get("filters", {})
    missing: dict[str, dict] = {}
    remove = []
    for section, keys in defaults.items():
        table = raw if section == "" else raw.get(section, {})
        if not isinstance(table, dict):
            continue
        for key, value in keys.items():
            if key in table:
                continue
            if section == "hh_filters" and key in ("experience", "work_format") and key in old_filters:
                value = old_filters[key]  # до 1.0.4 эти фильтры лежали в [filters] — переносим значение
                remove.append(("filters", key))
            missing.setdefault(section, {})[key] = value
    if not missing:
        return []
    from .paths import bundled

    shutil.copyfile(path, path.with_name(path.name + ".bak"))
    return write_values(path, missing, example=bundled("config.example.toml"), remove=remove)


def _section(cls, data: dict, name: str, ignored: list[str] | None = None):
    """Раздел настроек. Незнакомые параметры (от другой версии программы, опечатки) не мешают запуску —
    они остаются в файле, а в ignored попадает их список для предупреждения."""
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown and ignored is not None:
        ignored += [f"[{name}] {key}" for key in unknown]
    return cls(**{key: value for key, value in data.items() if key in known})


def _legacy_filters(raw: dict) -> dict:
    """До 1.0.4 опыт и формат работы лежали в [filters] — переносим их в [hh_filters], старый конфиг грузится как был."""
    old = raw.get("filters", {})
    new = raw.setdefault("hh_filters", {})
    for key in ("experience", "work_format"):
        if key in old:
            value = old.pop(key)
            new.setdefault(key, value)
    return raw.pop("filters", {})


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
        if not cfg.search.queries:
            raise ConfigError("[search] mode = query, но запрос (query) пустой")
    else:
        validate_search_url(cfg.search_url)


def load_config(path: Path, check_search_url: bool = True) -> Config:
    """check_search_url=False — для формы настроек в приложении: ссылку ещё могут не вписать."""
    if not path.exists():
        raise ConfigError(f"не найден {path}. Скопируйте туда config.example.toml и укажите ссылку поиска "
                          "(или настройте всё в приложении: python scripts/gui.py).")
    try:  # новая версия программы — новые параметры дописываются в файл, остальное не трогается
        added = upgrade_config(path)
    except OSError:  # файл только для чтения (например, в Docker) — работаем со значениями по умолчанию
        added = []
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: ошибка синтаксиса TOML: {e}") from e

    ignored: list[str] = []
    section = lambda cls, name, data=None: _section(cls, raw.pop(name, {}) if data is None else data, name, ignored)
    cfg = Config(
        search_url=raw.pop("search_url", "").strip(),
        resume_url=raw.pop("resume_url", "").strip(),
        skip_on_resume_mismatch=raw.pop("skip_on_resume_mismatch", True),
        search=section(Search, "search"),
        limits=section(Limits, "limits"),
        filters=section(Filters, "filters", _legacy_filters(raw)),
        letter=section(Letter, "letter"),
        questions=section(Questions, "questions"),
        relocation=section(Relocation, "relocation"),
        chats=section(Chats, "chats"),
        ai=section(Ai, "ai"),
        ai_filter=section(AiFilter, "ai_filter"),
        browser=section(Browser, "browser"),
        geo=section(Geo, "geo"),
        hh_filters=hh_filters.normalize(section(HhFilters, "hh_filters")),
        logs=section(Logs, "logs"),
        notify=section(Notify, "notify"),
        schedule=section(Schedule, "schedule"),
        stats=section(StatsSettings, "stats"),
    )
    ignored += sorted(raw)  # незнакомые разделы и параметры верхнего уровня
    cfg.ignored = ignored  # для предупреждения в логе: файл не ломается, но опечатку стоит увидеть
    cfg.added = added
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
    errors = hh_filters.validate(hh_filters.normalize(cfg.hh_filters))
    if errors:
        raise ConfigError("[hh_filters] " + errors[0])
    if cfg.letter.mode not in LETTER_MODES:
        raise ConfigError(f"[letter] mode: «{cfg.letter.mode}», допустимо: {', '.join(LETTER_MODES)}")
    if cfg.letter.mode == "template" and not cfg.letter.template_text.strip():
        raise ConfigError("[letter] mode = template, но template_text пустой")
    if cfg.limits.delay_min > cfg.limits.delay_max:
        raise ConfigError("[limits] delay_min больше delay_max")
    if cfg.ai_filter.enabled and not cfg.ai_filter.criteria.strip():
        raise ConfigError("[ai_filter] включён (enabled = true), но не заполнено поле criteria — "
                          "опишите, что вы ищете и чего не хотите")
    if cfg.ai_filter.max_snippet_chars < 0:
        raise ConfigError("[ai_filter] max_snippet_chars не может быть отрицательным")
    if cfg.logs.keep_days < 0:
        raise ConfigError("[logs] keep_days не может быть меньше 0 (0 — хранить снимки всегда)")
    if cfg.stats.keep_days < 0:
        raise ConfigError("[stats] keep_days не может быть меньше 0 (0 — хранить статистику всегда)")
    if cfg.limits.per_company < 0:
        raise ConfigError("[limits] per_company не может быть меньше 0 (0 — без ограничения)")
    if cfg.limits.per_company and cfg.limits.per_company_days < 1:
        raise ConfigError("[limits] per_company_days — хотя бы 1 день")
    for t in cfg.schedule.times:
        _parse_time(t)
    bad_days = [d for d in cfg.schedule.days if d not in WEEKDAYS]
    if bad_days:
        raise ConfigError(f"[schedule] days: «{bad_days[0]}» — дни недели числами: 1 — понедельник … 7 — воскресенье")
    if cfg.schedule.enabled and not (cfg.schedule.times and cfg.schedule.days):
        raise ConfigError("[schedule] включено, но не выбрано время или дни недели")
    cfg.geo.playwright_proxy()  # проверка формата прокси заранее

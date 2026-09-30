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


@dataclass
class Filters:
    # слова в названии вакансии, при которых она пропускается без отклика (регистр не важен)
    exclude_title_words: list[str] = field(default_factory=lambda: ["преподаватель", "куратор"])


@dataclass
class Questions:
    # Работодатель задал ровно один вопрос и он о зарплате → отвечаем salary_answer,
    # прикладываем сгенерированное письмо и откликаемся. Остальные вопросы пока пропускаем.
    answer_salary: bool = True
    salary_answer: str = "Рассматриваю от 100"
    salary_keywords: list[str] = field(
        default_factory=lambda: [
            "зарплат", "заработн", "сумм", "доход", "оклад", "вознагражд", "компенсац", "з/п", "₽", "руб",
        ]
    )


@dataclass
class Chats:
    url: str = "https://hh.ru/chat"
    rejection_text: str = "Отказ"  # последнее сообщение чата, по которому узнаём отказ
    delay_min: float = 1.5  # пауза после открытия чата, чтобы hh отметил его прочитанным
    delay_max: float = 3.5


@dataclass
class Letter:
    generate_timeout: float = 60  # сколько ждать генерацию письма, сек
    min_length: int = 30  # письмо короче считаем несгенерированным
    fallback_text: str = ""  # запасной текст, если генерация не удалась ("" = пропустить вакансию)


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
class Config:
    search_url: str
    resume_url: str = ""
    skip_on_resume_mismatch: bool = True
    limits: Limits = field(default_factory=Limits)
    filters: Filters = field(default_factory=Filters)
    letter: Letter = field(default_factory=Letter)
    questions: Questions = field(default_factory=Questions)
    relocation: Relocation = field(default_factory=Relocation)
    chats: Chats = field(default_factory=Chats)
    browser: Browser = field(default_factory=Browser)
    geo: Geo = field(default_factory=Geo)

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
        "# Настройки hh-auto. Файл записан приложением; описание параметров — в config.example.toml",
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


def load_config(path: Path, check_search_url: bool = True) -> Config:
    """check_search_url=False — для формы настроек в приложении: ссылку ещё могут не вписать."""
    if not path.exists():
        raise ConfigError(f"не найден {path}. Скопируйте config.example.toml в config.toml и укажите ссылку поиска.")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: ошибка синтаксиса TOML: {e}") from e

    search_url = raw.pop("search_url", "").strip()
    if check_search_url:
        validate_search_url(search_url)

    cfg = Config(
        search_url=search_url,
        resume_url=raw.pop("resume_url", "").strip(),
        skip_on_resume_mismatch=raw.pop("skip_on_resume_mismatch", True),
        limits=_section(Limits, raw.pop("limits", {}), "limits"),
        filters=_section(Filters, raw.pop("filters", {}), "filters"),
        letter=_section(Letter, raw.pop("letter", {}), "letter"),
        questions=_section(Questions, raw.pop("questions", {}), "questions"),
        relocation=_section(Relocation, raw.pop("relocation", {}), "relocation"),
        chats=_section(Chats, raw.pop("chats", {}), "chats"),
        browser=_section(Browser, raw.pop("browser", {}), "browser"),
        geo=_section(Geo, raw.pop("geo", {}), "geo"),
    )
    if raw:
        raise ConfigError(f"неизвестные параметры в config: {', '.join(sorted(raw))}")
    if cfg.limits.delay_min > cfg.limits.delay_max:
        raise ConfigError("[limits] delay_min больше delay_max")
    cfg.geo.playwright_proxy()  # проверка формата прокси заранее
    return cfg

"""Навыки: один навык — одно имя, как бы его ни писали в вакансиях и в резюме.

«PostgreSQL», «Postgres» и «postgresql 15» — один навык, «Английский язык» и «Английский — C1» — тоже.
Работодатели пишут ключевые навыки как придётся, резюме — свободный текст, и без этого «есть в резюме»
и «просят в вакансиях» не сравнить (раньше «Английский язык» считался отсутствующим у резюме с C1).

Словарь ниже — частые навыки (в основном IT, но и общие: языки, Excel, 1С, «гибкие» навыки). Навыки
не из словаря сравниваются по написанию, так что анализ работает для любой профессии: у бухгалтера
«1С: Бухгалтерия» и «Налоговая отчётность» найдутся так же, как у разработчика Python и Docker.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

HARD, SOFT, LANG = "hard", "soft", "lang"

# Название | вид | как ещё пишут — через «;». re: — регулярное выражение по тексту в нижнем регистре (ё → е),
# cs: — с учётом регистра (Go, а не «go»). Обычные варианты ищутся целым словом: «Java» не находится
# в «JavaScript», «Git» — в «GitLab», «SQL» — в «PostgreSQL».
_TABLE = r"""
Python | hard | python3; python 3; питон
Java | hard | java se; java ee; core java
JavaScript | hard | js; java script; ecmascript; es6; vanilla js
TypeScript | hard |
Go | hard | golang; go lang; cs:(?<![\w+#.\-])Go(?![\w+#\-])
C++ | hard | cpp; с++
C# | hard | c sharp; с#
.NET | hard | dotnet; .net core; .net framework
ASP.NET | hard | asp.net core
PHP | hard
Kotlin | hard
Swift | hard
Rust | hard
Scala | hard
Ruby | hard
Ruby on Rails | hard | rails; ror
Perl | hard
Lua | hard
Dart | hard
Objective-C | hard | objective c; obj-c
1С | hard | 1c; 1с:предприятие; 1с: предприятие; 1c:предприятие; 1с программирование; 1с: предприятие 8
Delphi | hard | object pascal
Pascal | hard
VBA | hard | visual basic for applications
Visual Basic | hard | vb6; vb.net
MATLAB | hard
R | hard | rstudio; r studio; re:язык\w* r(?![\w+#])
Bash | hard | shell; shell scripting; shell-скрипты; bash-скрипты
PowerShell | hard | power shell
SQL | hard | ansi sql
PL/SQL | hard | plsql; pl sql; oracle pl/sql
T-SQL | hard | tsql; transact-sql; transact sql
HTML | hard | html5; html 5
CSS | hard | css3; scss; sass
Django | hard | django framework; джанго
Django Rest Framework | hard | drf; django rest; django-rest-framework
FastAPI | hard | fast api
Flask | hard
aiohttp | hard
SQLAlchemy | hard | sql alchemy
Pydantic | hard
Celery | hard
Asyncio | hard | re:async\s*/\s*await|асинхронн\w* программировани\w*
pandas | hard
NumPy | hard
Polars | hard
SciPy | hard
Matplotlib | hard
Jupyter | hard | jupyter notebook; jupyterlab; jupyter lab
Scikit-learn | hard | sklearn; scikit learn
PyTorch | hard | torch
TensorFlow | hard
Keras | hard
XGBoost | hard
LightGBM | hard
CatBoost | hard
Machine Learning | hard | ml; машинное обучение; re:машинн\w* обучени\w*
Deep Learning | hard | глубокое обучение; re:глубок\w* обучени\w*
NLP | hard | natural language processing; re:обработк\w* естественн\w* язык\w*
Computer Vision | hard | компьютерное зрение; re:компьютерн\w* зрени\w*
OpenCV | hard
LLM | hard | llms; re:больш\w* языков\w* модел\w*|large language models?
RAG | hard | retrieval augmented generation; retrieval-augmented generation
LangChain | hard
MLflow | hard | ml flow
ChatGPT | hard | chat gpt
Spark | hard | apache spark
PySpark | hard
Hadoop | hard | apache hadoop; hdfs
Hive | hard | apache hive
Apache Airflow | hard | airflow
Apache Kafka | hard | kafka
RabbitMQ | hard | rabbit mq
Redis | hard
PostgreSQL | hard | postgres; postgre sql; pgsql; psql; постгрес
MySQL | hard | my sql
MS SQL | hard | mssql; ms sql server; sql server; microsoft sql server; mssql server
Oracle | hard | oracle db; oracle database
ClickHouse | hard | click house
Greenplum | hard
Vertica | hard
SQLite | hard
DuckDB | hard
MongoDB | hard | mongo; mongo db
Cassandra | hard | apache cassandra
Elasticsearch | hard | elastic search; elastic
NoSQL | hard | no sql; no-sql
Базы данных | hard | субд; работа с базами данных; основы баз данных; re:баз\w* данн\w*
ETL | hard | elt
DWH | hard | data warehouse; хранилище данных; re:хранилищ\w* данн\w*
Big Data | hard | bigdata; большие данные; re:больш\w* данн\w*
Power BI | hard | powerbi; power-bi; microsoft power bi
Tableau | hard
DataLens | hard | yandex datalens; яндекс datalens
Superset | hard | apache superset
Power Query | hard
DAX | hard
MS Excel | hard | excel; microsoft excel; эксель
Google Sheets | hard | google таблицы; гугл таблицы
Анализ данных | hard | data analysis; data analytics; data & analytics; re:анализ\w*(?:\s+[\w-]+){0,3}?\s+данн\w*|аналитик\w* данн\w*
Математическая статистика | hard | re:математическ\w* статистик\w*
Статистический анализ | hard | re:статистическ\w* анализ\w*
A/B тесты | hard | a/b testing; re:a/?b[- ]?тест\w*
Визуализация данных | hard | data visualization; re:визуализаци\w* данн\w*
Linux | hard | линукс; gnu/linux
Unix | hard
Ubuntu | hard
Debian | hard
CentOS | hard
Windows Server | hard
Docker | hard | докер
Docker Compose | hard | docker-compose; docker compose
Kubernetes | hard | k8s; kubectl; кубернетес
Helm | hard
OpenShift | hard
Ansible | hard
Terraform | hard
Jenkins | hard
GitLab CI | hard | gitlab-ci; gitlab ci/cd
GitHub Actions | hard
TeamCity | hard
CI/CD | hard | ci cd; ci-cd; ci / cd; cicd
Git | hard
GitLab | hard
GitHub | hard
Nginx | hard
Prometheus | hard
Grafana | hard
Zabbix | hard
ELK | hard | elk stack; elastic stack
OpenTelemetry | hard | open telemetry
DevOps | hard
AWS | hard | amazon web services
Azure | hard | microsoft azure
GCP | hard | google cloud; google cloud platform
Yandex Cloud | hard | yandex.cloud; яндекс облако; яндекс.облако
VMware | hard | vsphere
Proxmox | hard
TCP/IP | hard | tcp ip; tcp-ip
DNS | hard
Active Directory | hard
Микросервисная архитектура | hard | microservices; микросервисы; re:микросервис\w*
REST API | hard | rest; restful; rest-api; restful api
API | hard | апи; web api; веб-api
gRPC | hard
GraphQL | hard
SOAP | hard
WebSocket | hard | websockets
JSON | hard
XML | hard
Swagger | hard | openapi; open api
Postman | hard
HTTP | hard | https
ООП | hard | oop; re:объектно[- ]ориентированн\w*
Алгоритмы и структуры данных | hard | re:алгоритм\w* и структур\w* данн\w*|algorithms and data structures
Паттерны проектирования | hard | design patterns; re:паттерн\w* проектировани\w*|шаблон\w* проектировани\w*
SOLID | hard
Pytest | hard | py.test
Unit Testing | hard | unit test; unit tests; re:(?:unit|юнит)[- ]?тест\w*|модульн\w* тест\w*
Автоматизированное тестирование | hard | test automation; написание автотестов; re:автотест\w*|автоматизированн\w* тестировани\w*
Интеграционное тестирование | hard | integration testing; re:интеграционн\w* тест\w*
Нагрузочное тестирование | hard | load testing; re:нагрузочн\w* тест\w*
Selenium | hard
Playwright | hard
QA | hard | quality assurance
React | hard | react.js; reactjs; react js
Vue.js | hard | vue; vuejs; vue js
Angular | hard
AngularJS | hard | angular.js
Node.js | hard | nodejs; node js
jQuery | hard
Redux | hard
Next.js | hard | nextjs
Webpack | hard
Android | hard
iOS | hard
Flutter | hard
React Native | hard
Unity | hard
Agile | hard | эджайл
Scrum | hard
Kanban | hard | канбан
Atlassian Jira | hard | jira; джира
Atlassian Confluence | hard | confluence
BPMN | hard
UML | hard
Системный анализ | hard | re:системн\w* анализ\w*
Бизнес-анализ | hard | business analysis; бизнес анализ; re:бизнес[- ]анализ\w*
Управление проектами | hard | project management; проектный менеджмент; re:управлени\w* проект\w*
Техническая поддержка | hard | техническая поддержка пользователей; helpdesk; help desk; re:техническ\w* поддержк\w*
Figma | hard | фигма
SAP | hard
Битрикс24 | hard | bitrix24; битрикс 24
SEO | hard | seo оптимизация; seo-оптимизация
Информационная безопасность | hard | infosec; re:информационн\w* безопасност\w*
Английский язык | lang | re:английск\w*|english
Немецкий язык | lang | re:немецк\w*|german|deutsch
Французский язык | lang | re:французск\w*|french
Китайский язык | lang | re:китайск\w*|chinese|mandarin
Испанский язык | lang | re:испанск\w*|spanish
Аналитическое мышление | soft | analytical thinking; analytical skills; re:аналитическ\w* (?:мышлени\w*|склад\w* ума)
Работа в команде | soft | teamwork; team work; team player; re:работ\w* в команде|командн\w* работ\w*|командн\w* игрок\w*
Коммуникабельность | soft | communication skills; re:коммуникабельн\w*|коммуникативн\w* навык\w*
Деловая коммуникация | soft | re:делов\w* (?:коммуникаци\w*|общени\w*|переписк\w*)
Грамотная речь | soft | грамотность; re:грамотн\w* (?:реч\w*|устн\w*|письменн\w*)
Обучаемость | soft | re:обучаем\w*|быстр\w* (?:обучаюсь|учусь|осваиваю|разбираюсь)
Ответственность | soft | re:ответственн\w*\b(?!\s+за\b)
Стрессоустойчивость | soft | re:стрессоустойчив\w*
Ориентация на результат | soft | result-oriented; re:ориентаци\w* на результат|нацелен\w* на результат
Тайм-менеджмент | soft | time management; re:тайм[- ]?менеджмент\w*|управлени\w* времен\w*
Многозадачность | soft | multitasking; re:многозадачн\w*
Лидерство | soft | leadership; re:лидерск\w*|лидерств\w*
Управление командой | soft | team management; teamleading; руководство коллективом; re:управлени\w* команд\w*|руководств\w* (?:команд\w*|коллектив\w*)|тимлид\w*|team ?lead\w*
Наставничество | soft | mentoring; менторство; re:наставничеств\w*|менторинг\w*
Самостоятельность | soft | re:самостоятельн\w*
Креативность | soft | creativity; re:креативн\w*
Ведение переговоров | soft | negotiation; переговоры; re:(?:ведени\w*|проведени\w*) переговор\w*
Проведение презентаций | soft | подготовка презентаций; навыки презентации; presentation skills; re:(?:проведени\w*|подготовк\w*) презентаци\w*
Работа с большим объемом информации | soft | re:больш\w* объем\w* (?:информаци\w*|данн\w*)
"""

# Знаешь левое — знаешь и правое: в резюме с «Django Rest Framework» не обязательно писать «Django»,
# а «PostgreSQL» — это и «SQL». Только для резюме: в вакансии «PostgreSQL» не значит, что просят ещё и «SQL»
_IMPLIES = {
    "Django Rest Framework": ("Django", "REST API"),
    "FastAPI": ("REST API",),
    "REST API": ("API",),
    "gRPC": ("API",),
    "GraphQL": ("API",),
    "SOAP": ("API",),
    "PySpark": ("Spark",),
    "Docker Compose": ("Docker",),
    "GitLab CI": ("CI/CD", "GitLab"),
    "GitHub Actions": ("CI/CD", "GitHub"),
    "Jenkins": ("CI/CD",),
    "TeamCity": ("CI/CD",),
    "GitLab": ("Git",),
    "GitHub": ("Git",),
    "Helm": ("Kubernetes",),
    "OpenShift": ("Kubernetes",),
    "Next.js": ("React",),
    "React Native": ("React",),
    "Redux": ("React",),
    "Scrum": ("Agile",),
    "Kanban": ("Agile",),
    "Ubuntu": ("Linux",),
    "Debian": ("Linux",),
    "CentOS": ("Linux",),
    "ELK": ("Elasticsearch",),
    "DAX": ("Power BI",),
    "RAG": ("LLM",),
    "LangChain": ("LLM",),
    "ASP.NET": (".NET",),
    "Ruby on Rails": ("Ruby",),
    "Selenium": ("Автоматизированное тестирование",),
    "Playwright": ("Автоматизированное тестирование",),
    "Pytest": ("Unit Testing",),
    "Apache Airflow": ("ETL",),
    "SQL": ("Базы данных",),
}
for _name in ("Django", "Flask", "FastAPI", "aiohttp", "SQLAlchemy", "Pydantic", "Celery", "Asyncio", "pandas", "NumPy",
              "Polars", "SciPy", "Matplotlib", "Pytest", "PySpark", "Scikit-learn", "PyTorch", "Jupyter"):
    _IMPLIES[_name] = _IMPLIES.get(_name, ()) + ("Python",)
for _name in ("PostgreSQL", "MySQL", "MS SQL", "Oracle", "ClickHouse", "Greenplum", "Vertica", "SQLite", "DuckDB",
              "PL/SQL", "T-SQL"):
    _IMPLIES[_name] = _IMPLIES.get(_name, ()) + ("SQL",)
for _name in ("MongoDB", "Cassandra", "Redis"):
    _IMPLIES[_name] = _IMPLIES.get(_name, ()) + ("NoSQL",)
for _name in ("React", "Vue.js", "Angular", "AngularJS", "Node.js", "TypeScript", "jQuery", "Next.js", "Redux"):
    _IMPLIES[_name] = _IMPLIES.get(_name, ()) + ("JavaScript",)
for _name in ("Scikit-learn", "PyTorch", "TensorFlow", "Keras", "XGBoost", "LightGBM", "CatBoost", "MLflow",
              "Deep Learning", "Computer Vision", "NLP"):
    _IMPLIES[_name] = _IMPLIES.get(_name, ()) + ("Machine Learning",)
for _name in ("Spark", "Hadoop", "Hive"):
    _IMPLIES[_name] = _IMPLIES.get(_name, ()) + ("Big Data",)

# Технологии, которые уходят: если их ещё и почти не просят — в резюме их стоит опустить ниже
LEGACY = {"jQuery", "Delphi", "Pascal", "Visual Basic", "AngularJS", "SVN", "Mercurial", "Flash", "CoffeeScript",
          "Backbone.js", "Silverlight", "ActionScript"}

# Навык не из словаря похож на «гибкий» (soft skill) или на знание языка
_SOFT_HINT = re.compile(
    r"мышлени|коммуникаб|коммуникатив|ответствен|лидерств|командн|в команде|обучаем|стрессоустойч|переговор|"
    r"грамотн|пунктуал|организованн|организаторск|самостоятельн|инициатив|креатив|многозадачн|тайм-менедж|"
    r"time management|soft skill|клиентоориент|на результат|делов\w* (?:общени|переписк|коммуникац)|презентац|"
    r"навык\w* общени|межличност|эмпат|исполнительн|внимательн|аккуратн|усидчив|целеустрем|адаптив|гибкост|"
    r"управленческ\w* навык|ориентаци\w* на клиент")
_LANG_HINT = re.compile(r"(?:английск|немецк|французск|китайск|испанск|итальянск|японск|корейск|арабск|турецк)\w* язык")

_STOP = {"с", "и", "в", "во", "на", "по", "для", "к", "о", "об", "от", "из", "за", "а", "of", "and", "the", "in", "to",
         "for", "with", "или", "or"}
_WORD = re.compile(r"[a-zа-я0-9][a-zа-я0-9+#]*", re.I)


def norm(text: str) -> str:
    """Для сравнения: нижний регистр, «ё» → «е», любые тире → «-», без лишних пробелов."""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[‐‑‒–—―−]", "-", text)
    return " ".join(text.split())


def clean_name(raw: str) -> str | None:
    """Название навыка из карточки: без кавычек и пробелов по краям. Обрезанное hh («MS…», «SQL...») — None."""
    name = " ".join(str(raw).split()).strip(" \"'«»,;·•")
    if not name or name.endswith(("...", "…")) or len(name) > 80:
        return None
    return name


def _trie(words: Iterable[str]) -> str:
    """Регулярное выражение «любое из слов» в виде дерева префиксов: re проверяет тысячи вариантов
    за один проход, а не перебирает их по очереди в каждой позиции текста."""
    tree: dict = {}
    for word in words:
        node = tree
        for char in word:
            node = node.setdefault(char, {})
        node[""] = {}

    def walk(node: dict) -> str:
        branches = [re.escape(char) + walk(child) for char, child in sorted(node.items()) if char]
        if not branches:
            return ""
        body = branches[0] if len(branches) == 1 else "(?:" + "|".join(branches) + ")"
        if "" in node:  # здесь кончается одно из слов: продолжение необязательно (сначала — самое длинное)
            return f"(?:{body})?" if len(branches) > 1 or len(body) > 1 else f"{body}?"
        return body

    return walk(tree)


def _bounded(pattern: str) -> re.Pattern:
    # «C++» и «C#» — целые слова; «Java» в «JavaScript» и «Git» в «GitLab» — нет
    return re.compile(rf"(?<![\w+#])(?:{pattern})(?![\w+#])")


def _stem(word: str) -> str:
    return word[:max(4, min(len(word) - 2, 7))]


class _Entry:
    __slots__ = ("name", "kind", "plain", "patterns", "cased")

    def __init__(self, name: str, kind: str):
        self.name, self.kind = name, kind
        self.plain: set[str] = {norm(name)}
        self.patterns: list[re.Pattern] = []
        self.cased: list[re.Pattern] = []


@lru_cache(maxsize=1)
def _dictionary() -> tuple[dict[str, _Entry], dict[str, str]]:
    """(название → запись, вариант написания → название)."""
    entries: dict[str, _Entry] = {}
    aliases: dict[str, str] = {}
    for line in _TABLE.strip().splitlines():
        name, kind, *rest = [part.strip() for part in line.split("|", 2)]  # в регулярных выражениях тоже «|»
        entry = _Entry(name, kind)
        for alias in (a.strip() for a in (rest[0] if rest else "").split(";")):
            if alias.startswith("re:"):  # с начала слова: «работ\w* в команде» — не «разработку в команде»
                entry.patterns.append(re.compile(rf"(?<!\w)(?:{alias[3:]})"))
            elif alias.startswith("cs:"):
                entry.cased.append(re.compile(alias[3:]))
            elif alias:
                entry.plain.add(norm(alias))
        entries[name] = entry
        for alias in entry.plain:
            aliases.setdefault(alias, name)
    return entries, aliases


@lru_cache(maxsize=1)
def _dictionary_regex() -> re.Pattern:
    _, aliases = _dictionary()
    return _bounded(_trie(aliases))


def implied(names: Iterable[str]) -> set[str]:
    """Навыки вместе со следующими из них (DRF → Django → Python)."""
    result, todo = set(), list(names)
    while todo:
        name = todo.pop()
        if name not in result:
            result.add(name)
            todo.extend(_IMPLIES.get(name, ()))
    return result


class Vocabulary:
    """Все навыки, которые знает анализ: словарь плюс названия из вакансий (extra — в порядке частоты:
    первое написание становится названием навыка)."""

    def __init__(self, extra: Iterable[str] = ()):
        self.entries, self.aliases = _dictionary()
        self._keys: dict[str, str | None] = {}
        self.extra: dict[str, str] = {}  # нормализованное написание → название навыка не из словаря
        for raw in extra:
            name = clean_name(raw)
            if name and self.key(name) == name and name not in self.entries:
                self.extra.setdefault(norm(name), name)
        literal = [key for key, name in self.extra.items() if not self._stemmed(key)]
        self._extra_regex = _bounded(_trie(literal)) if literal else None
        # из нескольких русских слов («Работа с большим объёмом информации») — по основам слов в одной строке
        self._stems = [(name, [_stem(w) for w in _WORD.findall(key) if w not in _STOP and len(w) > 2])
                       for key, name in self.extra.items() if self._stemmed(key)]

    @staticmethod
    def _stemmed(key: str) -> bool:
        words = [w for w in _WORD.findall(key) if w not in _STOP and len(w) > 2]
        return len(words) >= 2 and bool(re.search(r"[а-я]", key))

    def key(self, raw: str) -> str | None:
        """Название навыка для написания из карточки или резюме: «postgres» → «PostgreSQL». None — не навык."""
        if raw not in self._keys:
            self._keys[raw] = self._key(raw)
        return self._keys[raw]

    def _key(self, raw: str) -> str | None:
        name = clean_name(raw)
        if name is None:
            return None
        text = norm(name)
        if text in self.aliases:
            return self.aliases[text]
        bare = re.sub(r"\s*v?\d+(?:\.\d+)*\+?$", "", text)  # «Python 3», «Vue 3», «PostgreSQL 15», «Angular 2+»
        if bare != text and bare in self.aliases:
            return self.aliases[bare]
        for entry in self.entries.values():  # «Английский — C1», «English (Upper-Intermediate)»
            if entry.kind in (LANG, SOFT) and any(p.search(text) for p in entry.patterns):
                return entry.name
        return self.extra.get(text, name)

    def kind(self, name: str) -> str:
        entry = self.entries.get(name)
        if entry is not None:
            return entry.kind
        text = norm(name)
        if _LANG_HINT.search(text):
            return LANG
        return SOFT if _SOFT_HINT.search(text) else HARD

    def find(self, text: str, extra: bool = True) -> set[str]:
        """Навыки, упомянутые в тексте. extra=False — только из словаря (для коротких карточек вакансий,
        где общие слова вроде «обучение» — не навык)."""
        low = "\n".join(norm(line) for line in text.splitlines())  # строки — для поиска по основам слов
        found = {self.aliases[m.group()] for m in _dictionary_regex().finditer(low)}
        for entry in self.entries.values():
            if entry.name not in found and (any(p.search(low) for p in entry.patterns)
                                            or any(p.search(text) for p in entry.cased)):
                found.add(entry.name)
        if not extra:
            return found
        if self._extra_regex is not None:
            found.update(self.extra[m.group()] for m in self._extra_regex.finditer(low))
        if self._stems:
            for line in low.splitlines() or [low]:
                words: dict[str, list[str]] = {}
                for word in _WORD.findall(line):
                    words.setdefault(word[:4], []).append(word)
                if not words:
                    continue
                for name, stems in self._stems:
                    if name not in found and all(any(w.startswith(s) for w in words.get(s[:4], ())) for s in stems):
                        found.add(name)
        return found

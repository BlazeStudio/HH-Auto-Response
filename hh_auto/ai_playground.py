"""Песочница ИИ: вы пишете от имени работодателя, нейросеть отвечает — в hh ничего не отправляется.

Переписка ведётся так же, как в настоящем чате (несколько вопросов подряд, ответы ИИ добавляются
в переписку), решения проходят те же страховки, что и в боевом режиме. Можно посмотреть ровно то,
что получает нейросеть: инструкцию, резюме, ваш контекст и переписку.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .ai import AiClient, Decision, FormFill, FormQuestion, ours_is_last, review
from .config import Config
from .resume import clean_resume_text

ROBOT = "Робот-рекрутер"
ACTIONS = {
    "question": "ответил бы",
    "info": "прочитал бы, не отвечая (уведомление)",
    "human": "не ответил бы — оставил бы вам",
    "wait": "не ответил бы — ждал бы работодателя",
}
# Готовые сообщения работодателя для быстрой проверки: (отправитель, текст)
SAMPLES = [
    (ROBOT, "Здравствуйте! Спасибо за интерес к вакансии. Чтобы работодатель узнал о вас больше, "
            "пожалуйста, ответьте на несколько вопросов. Это займет всего пару минут. Начнем?"),
    (ROBOT, "Какой у вас коммерческий опыт разработки на Python (сколько лет)?"),
    (ROBOT, "Какие у вас зарплатные ожидания (на руки)?"),
    (ROBOT, "Готовы ли вы работать в офисе 5 дней в неделю?"),
    (ROBOT, "Когда вы готовы приступить к работе?"),
    (ROBOT, "Есть ли у вас опыт работы с Kubernetes? Опишите кратко."),
    (ROBOT, "Укажите, пожалуйста, ваши паспортные данные для оформления пропуска."),
    (ROBOT, "Пришлите ссылку на ваш GitHub."),
    (ROBOT, "Спасибо за ответы! Анкета завершена, работодатель свяжется с вами."),
    ("Анна, HR", "Добрый день! Ваше резюме нас заинтересовало. Когда вам удобно созвониться?"),
    ("HR Team", "Здравствуйте! Благодарим за отклик. Мы рассмотрим ваше резюме и позже сообщим о решении."),
]


@dataclass
class Turn:
    sender: str
    message: str
    decision: Decision
    action: str  # что сделал бы боевой режим
    note: str = ""  # пояснение страховки
    seconds: float = 0.0
    raw: str = ""  # сырой ответ модели


@dataclass
class Playground:
    cfg: Config
    root: Path
    vacancy: str = "Python-разработчик — Тестовая компания"
    lines: list[str] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.client = AiClient(self.cfg.ai)  # для Ollama сам запускает её, если нужно
        self.resume_path = self.root / "data" / "resume.txt"
        self.resume = clean_resume_text(self.resume_path.read_text(encoding="utf-8")) if self.resume_path.exists() else ""
        self.reset()

    def reset(self) -> None:
        self.lines = [self.vacancy]
        self.sent = []

    @property
    def transcript(self) -> str:
        return "\n".join(self.lines)

    def describe(self) -> list[str]:
        if self.resume:
            when = datetime.fromtimestamp(self.resume_path.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
            resume = f"{len(self.resume)} символов, загружено с hh {when}"
        else:
            resume = "не загружено — нажмите «Загрузить резюме с hh» (или python ai_test.py --fetch-resume)"
        context = self.cfg.ai.context.strip()
        return [
            f"Нейросеть: {self.client.describe()}",
            f"Резюме: {resume}",
            f"Ваш контекст: {context if context else 'не заполнен («Настройки» → «Что ещё знать о вас»)'}",
            f"Отвечать только роботу: {'да' if self.cfg.ai.only_robot else 'нет'}, "
            f"длина ответа до {self.cfg.ai.max_answer_chars} символов",
        ]

    def preview(self) -> str:
        """Ровно то, что уйдёт нейросети при следующем вопросе."""
        messages = self.client.build_messages(self.resume, self.cfg.ai.context, self.transcript, self.sent)
        titles = {"system": "ИНСТРУКЦИЯ ДЛЯ НЕЙРОСЕТИ (system)", "user": "ЗАПРОС: РЕЗЮМЕ + КОНТЕКСТ + ПЕРЕПИСКА (user)"}
        blocks = [f"══════ {titles.get(m['role'], m['role'])} ══════\n{m['content']}" for m in messages]
        return "\n\n".join(blocks)

    def ask(self, sender: str, message: str) -> Turn:
        """Работодатель пишет сообщение — нейросеть решает, что сделать. Ничего никуда не отправляется."""
        self.lines += [sender, message.strip(), datetime.now().strftime("%H:%M")]
        is_robot = any(m.lower() in sender.lower() for m in self.cfg.ai.robot_markers)
        if self.sent and ours_is_last(self.transcript, self.sent[-1]):
            return Turn(sender, message, Decision("wait"), ACTIONS["wait"], "последнее сообщение — ваше")
        started = time.monotonic()
        decision = self.client.decide(self.resume, self.cfg.ai.context, self.transcript, self.sent)
        seconds = time.monotonic() - started
        note = review(decision, self.transcript, self.sent)
        if decision.kind == "question" and self.cfg.ai.only_robot and not is_robot:
            decision.kind = "human"
            note = "пишет не робот, а человек (в настройках «отвечать только роботу»)"
        if decision.kind == "question":
            self.lines += ["Вы", decision.reply]
            self.sent.append(decision.reply)
        return Turn(sender, message, decision, ACTIONS[decision.kind], note, seconds,
                    getattr(self.client, "last_raw", ""))


# Пример анкеты при отклике: текст, один вариант, несколько вариантов, вилка зарплат
SAMPLE_FORM = [
    FormQuestion(0, "Почему вас заинтересовала наша вакансия?", "text"),
    FormQuestion(1, "Сколько лет коммерческого опыта разработки на Python?", "single",
                 ["Нет опыта", "До 1 года", "1–3 года", "3–5 лет", "Более 5 лет"]),
    FormQuestion(2, "С какими технологиями вы работали в коммерческих проектах?", "multi",
                 ["Django", "FastAPI", "Kafka", "Go", "Kubernetes", "1С"]),
    FormQuestion(3, "Готовы ли вы работать в офисе?", "single", ["Да, полный день в офисе", "Гибрид", "Только удалённо"]),
    FormQuestion(4, "Ваши зарплатные ожидания (на руки)", "single",
                 ["до 150 000 ₽", "150 000–250 000 ₽", "250 000–350 000 ₽", "более 350 000 ₽", "Готов обсудить"]),
    FormQuestion(5, "Когда сможете приступить к работе?", "text"),
]


def fill_sample_form(pg: Playground) -> tuple[FormFill, float]:
    """Песочница анкет: как ИИ заполнил бы пример анкеты при отклике. Ничего не отправляется."""
    started = time.monotonic()
    fill = pg.client.fill_form(pg.resume, pg.cfg.ai.context, pg.vacancy, SAMPLE_FORM)
    return fill, time.monotonic() - started


SAMPLE_VACANCY = ("Backend-разработчик (Python)", "Тестовая компания", """Чем предстоит заниматься:
разработка и поддержка микросервисов на Python (FastAPI), интеграции с внешними API, оптимизация запросов к PostgreSQL,
участие в код-ревью и проектировании.
Требования: опыт коммерческой разработки на Python от 2 лет, PostgreSQL, Docker, понимание REST и очередей сообщений
(Kafka или RabbitMQ). Будет плюсом: Kubernetes, CI/CD, опыт в финтехе.
Условия: удалённо или гибрид, официальное оформление.""")


def write_sample_letter(pg: Playground) -> tuple[str, float]:
    """Песочница писем: письмо нейросети к примеру вакансии. Ничего не отправляется."""
    started = time.monotonic()
    title, company, description = SAMPLE_VACANCY
    text = pg.client.write_letter(pg.resume, pg.cfg.ai.context, title, company, description, pg.cfg.letter.ai_max_chars)
    return text, time.monotonic() - started

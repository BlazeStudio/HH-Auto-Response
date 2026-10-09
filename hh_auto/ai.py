"""ИИ-ответы на анкеты работодателей в чатах hh.

Работает с любым OpenAI-совместимым API: DeepSeek, OpenRouter (есть бесплатные модели),
Ollama (нейросеть на своём компьютере) и т.п. Нейросеть получает резюме, ваш контекст
и переписку и возвращает JSON: что это за сообщение и (если это вопрос анкеты) короткий ответ.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .config import AI_PROVIDERS, Ai

KINDS = ("question", "info", "human", "wait")

SYSTEM_PROMPT = """Ты помогаешь соискателю на hh.ru отвечать на анкету работодателя в чате.
Тебе дают резюме соискателя, его дополнительный контекст и переписку с работодателем.

Переписка взята со страницы как текст: в ней могут встречаться время, имена отправителей и подписи
кнопок интерфейса — не считай их сообщениями.

Прочитай ВСЕ сообщения работодателя после последнего ответа соискателя — их может быть несколько
(приветствие и вопрос, несколько вопросов подряд). Определи, что с ними сделать, и верни строго JSON без пояснений:
{"kind": "...", "question": "...", "reply": "..."}

kind:
- "question" — работодатель задал вопрос: бот («Робот-рекрутер», «ИИ-помощник», ассистент на базе AI) или
  живой рекрутер — неважно. Сюда же «Начнём?», «Готовы обсудить детали?», «Актуален ли поиск?» и подтверждения
  («Используем эти ответы?», «Всё верно?») — ответь коротко по сути («Да, готов»). Если вопросов несколько —
  ответь на все в одном сообщении, по порядку.
- "info" — ответ не нужен: благодарность за отклик, уведомление, «мы рассмотрим», отказ, завершение анкеты.
  Если в последнем сообщении есть вопрос к соискателю или просьба что-то указать, рассказать, прислать —
  это НИКОГДА не "info".
- "human" — ТОЛЬКО если предлагают встречу, собеседование, звонок или видеозвонок (договориться о времени —
  дело самого человека), просят документы, контакты, персональные данные или деньги. Всё остальное —
  "question". Отсутствие нужного опыта — тоже "question": отвечаешь честно (см. правила ниже).
- "wait" — последнее сообщение в переписке от соискателя: он уже ответил и ждёт работодателя.

question — ДОСЛОВНО вопрос из НОВЫХ сообщений, на который отвечаешь (для kind = "question"), иначе "".
  Вопросы из старой части переписки не бери: на них уже ответили.
reply — ответ именно на этот вопрос, по существу (для kind = "question"), иначе "". На открытый вопрос
  («какой», «в каком контексте», «расскажите») отвечай фактами, а не «Да, готов».

Правила ответа:
- от первого лица, по-русски (или на языке вопроса), коротко: 1–2 предложения на вопрос, не длиннее {max_chars} символов;
- только факты из резюме и контекста; не выдумывай опыт, цифры, компании, навыки и даты;
- если точного ответа в резюме и контексте нет (например, о зарплате или графике), это всё равно "question":
  ответь честно и нейтрально (например: «Готов обсудить на собеседовании»);
- на вопрос о количестве лет опыта отвечай числом из резюме; срок по отдельной технологии называй, только
  если он прямо следует из резюме (технология указана в конкретном месте работы с датами). Навыкам из
  списка «Навыки» и тому, что соискатель «изучает в свободное время», коммерческий стаж не приписывай —
  так и скажи (например: «Коммерческого опыта с NLP нет, изучаю LLM и RAG в свободное время»);
- не соглашайся на условия, которых нет в резюме или контексте: формат работы бери из строки резюме
  «Формат работы» (если там нет офиса, на вопрос про офис ответь, что предпочитаешь указанный формат);
- сроки выхода на работу, зарплату, готовность к переезду бери ТОЛЬКО из контекста соискателя;
  если там этого нет — «Готов обсудить на собеседовании». Никогда не придумывай сроки, суммы и ссылки;
- если прямого опыта с тем, о чём спрашивают, в резюме нет: найди смежный опыт и подай его выгодно
  («работал со смежным X, с Y знаком на базовом уровне — быстро освою»); если и смежного нет — честно
  «Особого опыта с Y не было, готов быстро освоить». Опыт, которого нет в резюме, не приписывай;
- пиши своими словами, живо и по делу; не копируй строки резюме дословно и не перечисляй весь стек подряд;
- ты говоришь как сам соискатель: никогда не пиши «в резюме не указано» / «в резюме нет» — пиши «опыта с X не было»;
- никаких приветствий, подписей, ссылок, телефонов, почты и паспортных данных."""


class AiError(Exception):
    pass


FORM_PROMPT = """Ты отвечаешь за соискателя на ОДИН вопрос анкеты работодателя при отклике на вакансию hh.ru.
Тебе дают ключевые факты и полное резюме соискателя, его дополнительный контекст, вакансию и вопрос.

Верни строго JSON без пояснений:
- текстовый вопрос: {"can_answer": true, "text": "ответ"}
- вопрос с вариантами: {"can_answer": true, "choices": ["точный текст варианта", ...]}
- нельзя честно ответить: {"can_answer": false, "reason": "коротко почему"}

Правила:
- текст — от первого лица, грамотно по-русски, коротко: 1–2 предложения, до {max_chars} символов;
- отвечай ТОЛЬКО на заданный вопрос: не начинай с «Меня заинтересовала вакансия…» и не пересказывай резюме,
  если об этом не спрашивали. На вопрос о доходе и грейде — только доход и грейд;
- доход — это ОЖИДАНИЯ соискателя («Ожидаю от 150 000 ₽»), а не «текущий доход». Если в блоке
  «РАСЧЁТ ПРОГРАММЫ» есть ожидаемый доход или грейд — используй их как есть, свои цифры не придумывай;
- грейд — Junior / Middle / Senior, а не цифры;
- варианты копируй ДОСЛОВНО из списка; для «выбери один» — ровно один вариант;
- только факты из резюме и контекста; не выдумывай опыт, сроки, суммы, компании и ссылки;
- стаж: общий — из строки «Опыт работы» в ключевых фактах; по отдельной технологии — только если он прямо
  следует из резюме. Навыкам «из списка» и тому, что «изучаю в свободное время», коммерческий стаж не приписывай;
- список технологий/навыков: выбирай ТОЛЬКО то, что прямо написано в резюме, остальное не выбирай;
- зарплата, сроки выхода, переезд, график — ТОЛЬКО из контекста. Для вилок выбирай вариант, в который попадает
  сумма из контекста («от 150 000» → вилка, начинающаяся со 150 000). Если в контексте этого нет — текстом
  «Готов обсудить на собеседовании», а в вариантах — «Готов обсудить» или ближайший честный вариант;
- формат работы — из строки резюме «Формат работы»; не соглашайся на то, чего там нет;
- нет прямого опыта с тем, о чём спрашивают: в тексте — подай смежный опыт выгодно («работал со смежным X,
  с Y знаком на базовом уровне — быстро освою») или честно «особого опыта с Y не было, готов освоить»;
  в вариантах — ближайший честный вариант («базовый», «до 1 года», «нет опыта»). Опыт, которого нет
  в резюме, не приписывай. Отсутствие опыта — НЕ причина для can_answer = false;
- пиши своими словами, живо и по делу; не копируй строки резюме дословно и не перечисляй весь стек подряд;
- ты говоришь как сам соискатель: никогда не пиши «в резюме не указано» / «в резюме нет» — пиши «опыта с X не было»;
- can_answer = false — ТОЛЬКО если просят: тестовое задание, код, решение задачи, ссылку на портфолио/GitHub,
  документы, контакты, персональные данные или оплату."""


LETTER_PROMPT = """Ты пишешь сопроводительное письмо соискателя к отклику на вакансию hh.ru.
Тебе дают резюме соискателя, его дополнительный контекст и вакансию (название, компания, описание).

Как писать:
- от первого лица, по-русски, живо и по-деловому, без канцелярита и штампов («динамично развивающаяся компания»);
- вакансию называй в кавычках, компанию — по имени: «Меня заинтересовала вакансия «Python-разработчик» в Ромашке»
  (если компания не указана — просто «ваша компания»);
- начни с «Здравствуйте!», затем 3–5 предложений: чем заинтересовала именно эта вакансия, 2–3 факта из резюме,
  которые совпадают с требованиями вакансии (своими словами, с конкретикой: задачи, результаты, технологии),
  и короткое предложение обсудить детали;
- если требования вакансии шире опыта — сделай упор на смежный опыт и быстрое обучение, но опыт,
  которого нет в резюме, не приписывай;
- никаких контактов, ссылок, подписи с именем, заполнителей в квадратных скобках, темы письма и markdown;
- зарплату упоминай только если о ней просят в вакансии и она есть в контексте;
- до {max_chars} символов.

Верни только текст письма."""


@dataclass
class FormQuestion:
    index: int  # номер блока вопроса на странице (с 0)
    text: str
    kind: str  # text | single | multi | unknown
    options: list[str] = field(default_factory=list)


@dataclass
class FormAnswer:
    question: FormQuestion
    text: str = ""
    choices: list[int] = field(default_factory=list)  # номера вариантов с 0

    def describe(self) -> str:
        if self.question.kind == "text":
            return self.text
        return "; ".join(self.question.options[c] for c in self.choices)


@dataclass
class FormFill:
    can_answer: bool
    reason: str = ""
    answers: list[FormAnswer] = field(default_factory=list)


_ollama_proc: subprocess.Popen | None = None  # Ollama, которую запустили мы сами
_ollama_models: set[tuple[str, str]] = set()  # (адрес, модель), которые мы загружали в память


def shutdown_ollama() -> None:
    """При выходе: выгрузить наши модели из видеопамяти и остановить Ollama, если её запускали мы.

    Ollama, которую запустил сам пользователь, продолжает работать — из неё только выгружаются наши модели.
    """
    global _ollama_proc
    for root, model in list(_ollama_models):
        try:
            body = json.dumps({"model": model, "keep_alive": 0}).encode()
            request = urllib.request.Request(f"{root}/api/generate", data=body,
                                             headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=3):
                pass
        except (urllib.error.URLError, OSError, ValueError):
            pass
    _ollama_models.clear()
    proc, _ollama_proc = _ollama_proc, None
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":  # вместе с дочерними процессами модели (ollama runner держит видеопамять)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        proc.terminate()


atexit.register(shutdown_ollama)


def is_local_url(url: str) -> bool:
    """Адрес указывает на этот же компьютер (localhost, 127.x, ::1)."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host in ("localhost", "::1", "0.0.0.0") or host.startswith("127.")


def ollama_url(base_url: str) -> str:
    """Адрес Ollama к виду OpenAI API: «http://192.168.1.50:11434» → «http://192.168.1.50:11434/v1»."""
    url = base_url.strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    if url and not url.endswith("/v1"):
        url += "/v1"
    return url


def ensure_ollama(base_url: str, wait: float = 20) -> bool:
    """Ollama отвечает? На этом компьютере, если не запущена, — запускаем её в фоне (обычная установка
    с ollama.com). На другом компьютере ничего не запускаем, только проверяем связь. True — отвечает."""
    root = base_url.rsplit("/v1", 1)[0]
    local = is_local_url(base_url)

    def alive() -> bool:
        try:
            with urllib.request.urlopen(f"{root}/api/version", timeout=2 if local else 6):
                return True
        except (urllib.error.URLError, OSError):
            return False

    if alive():
        return True
    if not local or (urllib.parse.urlparse(base_url).port or 11434) != 11434:
        return False  # другой компьютер или нестандартный порт (обычно SSH-туннель) — свою Ollama не запускаем
    candidates = [shutil.which("ollama"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")]
    exe = next((c for c in candidates if c and os.path.exists(c)), None)
    if not exe:
        return False
    global _ollama_proc
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    _ollama_proc = subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=flags)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(1)
        if alive():
            return True
    return False


@dataclass
class Decision:
    kind: str
    question: str = ""
    reply: str = ""
    button: str = ""  # нажать кнопку быстрого ответа вместо текста


# ─────────── страховки поверх нейросети (общие для боевого режима и песочницы) ───────────

SENSITIVE = re.compile(
    r"паспорт|снилс|\bинн\b|номер карт|на карту|банковск|реквизит|оплатить|предоплат|"
    r"внести (?:плат|оплат|взнос)|взнос|"
    r"http|www\.|ссылк|телеграм|telegram|whatsapp|ватсап|номер телефона|ваш телефон|e-?mail|почт",
    re.I,
)


def norm(text: str) -> str:
    return " ".join(text.lower().split())


def ours_is_last(transcript: str, last_sent: str) -> bool:
    """Переписка заканчивается нашим ответом: после него только время и подписи («Вы», «прочитано»)."""
    text, probe = norm(transcript), norm(last_sent)[:60]
    at = text.rfind(probe)
    if at < 0:
        return False
    rest = re.sub(r"\d{1,2}:\d{2}", "", text[at + len(norm(last_sent)):])
    return len(rest.strip()) < 20


# Провайдеры, которые понимают response_format = json_object (у остальных просим JSON словами)
JSON_MODE_PROVIDERS = ("ollama", "deepseek")

# Сообщения о завершении анкеты: отвечать не нужно, переходим к следующему чату
DONE = re.compile(
    r"ответы отправлены работодателю|анкет\w* (?:завершен|заполнен|пройден)|опрос (?:завершен|пройден)|"
    r"спасибо за (?:ваши )?ответы|благодарим за (?:ваши )?ответы",
    re.I,
)


def is_done_message(transcript: str) -> bool:
    return bool(DONE.search(last_message(transcript)))


_SERVICE_LINE = re.compile(r"\d{1,2}:\d{2}|вы|прочитано|доставлено|отправлено|сегодня|вчера", re.I)


_ABOUT_RESUME = re.compile(r"в (?:моём |моем )?резюме|резюме не|не указывал|не указан\w* в|по резюме", re.I)


def new_messages(transcript: str, sent: list[str], limit: int = 6) -> list[str]:
    """Неотвеченные сообщения работодателя (без времени и служебных строк).

    После нашего ответа — всё, что пришло позже. Если мы в этом чате ещё не отвечали, отправителя по тексту
    не различить (переписка на hh — просто текст), поэтому берём только хвост из вопросов подряд в самом конце:
    вопрос, после которого уже есть чей-то ответ, сюда не попадёт."""
    lines = [line.strip() for line in transcript.splitlines()
             if line.strip() and not _SERVICE_LINE.fullmatch(line.strip())]
    if sent:
        mine = norm(sent[-1])
        for i in range(len(lines) - 1, -1, -1):
            if mine and (norm(lines[i]) == mine or mine in norm(lines[i])):
                return lines[i + 1:][-limit:]
    run: list[str] = []
    for line in reversed(lines):
        if not line.rstrip().endswith("?"):
            break
        run.insert(0, line)
    return run[-limit:] or lines[-1:]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zа-яё0-9]{3,}", text.lower())}


def _in_messages(question: str, messages: list[str]) -> bool:
    """Вопрос модели взят из этих сообщений (дословно или почти: модели слегка перефразируют)."""
    if not question:
        return False
    q = norm(question)
    joined = norm(" ".join(messages))
    if q and (q in joined or any(norm(m) in q for m in messages if len(norm(m)) > 10)):
        return True
    words = _words(question)
    return bool(words) and len(words & _words(" ".join(messages))) / len(words) >= 0.6


# Открытый вопрос: «какой», «в каком контексте», «расскажите» — на него «Да, готов» не ответ
_OPEN_QUESTION = re.compile(r"\b(?:как(?:ой|ая|ое|ие|ом|их|им)|в каком|расскаж|опиш|уточни|сколько|почему|зачем|"
                            r"что (?:вы|именно)|где|когда|с чем|чем)\b", re.I)
_GENERIC_REPLY = re.compile(r"^(?:да|нет|ок|хорошо|готов|да,? готов|конечно|согласен)[.!]?$", re.I)


def _generic_reply_to_open_question(question: str, reply: str) -> bool:
    return bool(_OPEN_QUESTION.search(question)) and bool(_GENERIC_REPLY.match(reply.strip()))


def last_message(transcript: str) -> str:
    """Последнее сообщение переписки: последняя содержательная строка (без времени и служебных подписей)."""
    for line in reversed(transcript.splitlines()):
        line = line.strip()
        if line and not _SERVICE_LINE.fullmatch(line):
            return line
    return ""


# Встречи и звонки — время согласует сам человек, что бы ни решила модель
MEETING = re.compile(r"собеседовани|интервью|созвон|звон(?:ок|ка|ке|ить)|позвон|видеозвон|видеосвяз|встреч|"
                     r"zoom|телемост|google meet|удобн\w* (?:время|дат|день)|слот", re.I)


def review(decision: Decision, transcript: str, sent: list[str]) -> str:
    """Проверяет решение модели страховками. Может поменять decision.kind. Возвращает пояснение или "".

    Модель может ошибиться, поэтому правила ниже работают всегда:
      • документы, деньги, контакты, ссылки — решает человек, что бы ни выбрала модель;
      • в последнем сообщении есть вопрос, а модель «не стала отвечать» — тоже решает человек
        (иначе приглашение на созвон можно тихо «прочитать» и пропустить);
      • повтор нашего прошлого ответа — значит, ждём работодателя.
    """
    ours_last = bool(sent) and ours_is_last(transcript, sent[-1])
    if ours_last:
        return ""
    tail = last_message(transcript)
    sensitive = SENSITIVE.search(decision.question) if decision.question else None
    sensitive = sensitive or SENSITIVE.search(tail)
    if sensitive and decision.kind != "human":
        decision.kind = "human"
        return f"в сообщении «{sensitive.group(0)}» — такие вопросы решаете вы"
    meeting = MEETING.search(tail) or (MEETING.search(decision.question) if decision.question else None)
    if meeting and decision.kind != "human":
        decision.kind = "human"
        return f"предлагают {meeting.group(0)} — время согласуете вы"
    if decision.kind in ("info", "wait") and tail.rstrip().endswith("?"):
        decision.kind = "human"
        return "в последнем сообщении есть вопрос, а ИИ не стал отвечать — оставляю вам"
    if decision.kind == "question" and sent and norm(decision.reply) == norm(sent[-1]):
        decision.kind = "wait"
        return "ИИ предлагает повторить прошлый ответ — значит, ждём работодателя"
    return ""


class AiClient:
    def __init__(self, cfg: Ai):
        self.cfg = cfg
        self.notes: list[str] = []  # что поправили страховки в последнем decide — для журнала
        preset_url, preset_model = AI_PROVIDERS.get(cfg.provider, ("", ""))[:2]
        self.base_url = (cfg.base_url or preset_url).rstrip("/")
        if cfg.provider == "ollama":  # Ollama на другом компьютере: адрес можно ввести и без /v1
            self.base_url = ollama_url(self.base_url)
        self.local = is_local_url(self.base_url)
        self.model = cfg.model or preset_model
        self.api_key = cfg.api_key or os.environ.get("HH_AI_API_KEY", "")
        if not self.base_url or not self.model:
            raise AiError("для ИИ не задан адрес API или модель ([ai] base_url / model)")
        if cfg.provider in ("deepseek", "openrouter") and not self.api_key:
            raise AiError(f"для {cfg.provider} нужен API-ключ ([ai] api_key)")
        if cfg.provider == "ollama" and not ensure_ollama(self.base_url):
            if self.local and (urllib.parse.urlparse(self.base_url).port or 11434) != 11434:
                raise AiError(f"на {self.base_url.rsplit('/v1', 1)[0]} никто не отвечает — открыт ли SSH-туннель "
                              "к компьютеру с Ollama? См. docs/ollama.md")
            if not self.local:
                raise AiError(f"Ollama на {self.base_url.rsplit('/v1', 1)[0]} не отвечает. Проверьте, что она запущена "
                              "и слушает сеть (OLLAMA_HOST=0.0.0.0) или что открыт SSH-туннель — см. docs/ollama.md")
            raise AiError("Ollama не запущена и не нашлась — установите её с ollama.com и скачайте модель: "
                          f"ollama pull {self.model}")
        if cfg.provider == "ollama" and self.local:
            # при выходе выгрузим модель из видеопамяти — только на своём компьютере, чужим сервером он управляет сам
            _ollama_models.add((self.base_url.rsplit("/v1", 1)[0], self.model))

    def describe(self) -> str:
        return f"{self.cfg.provider}: {self.model} ({self.base_url})"

    def complete(self, messages: list[dict], max_tokens: int = 800, json_mode: bool = False) -> str:
        payload = {"model": self.model, "messages": messages, "temperature": 0.3, "max_tokens": max_tokens,
                   "stream": False}
        if json_mode and self.cfg.provider in JSON_MODE_PROVIDERS:
            # Режим «строго JSON»: модель не может ответить пересказом переписки вместо решения
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(f"{self.base_url}/chat/completions", data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.cfg.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            if self.cfg.provider == "ollama" and e.code == 404:
                where = "" if self.local else f" на компьютере с Ollama ({urllib.parse.urlparse(self.base_url).hostname})"
                raise AiError(f"модель «{self.model}» не скачана — выполните{where}: ollama pull {self.model}") from e
            raise AiError(f"сервис ИИ ответил HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if isinstance(e, TimeoutError) or "timed out" in str(e):  # связь есть, модель не успела ответить
                raise AiError(f"нейросеть не успела ответить за {self.cfg.timeout:g} с — увеличьте «[ai] timeout» "
                              "в настройках (на процессоре без видеокарты — 180 и больше)") from e
            if self.cfg.provider == "ollama" and self.local:
                raise AiError("Ollama не отвечает — запустите приложение Ollama (ollama.com) и повторите") from e
            if self.cfg.provider == "ollama":
                raise AiError(f"Ollama на {self.base_url} не отвечает: {e}. Проверьте компьютер с Ollama, сеть "
                              "или SSH-туннель — см. docs/ollama.md") from e
            raise AiError(f"нет связи с сервисом ИИ ({self.base_url}): {e}") from e
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise AiError(f"непонятный ответ сервиса ИИ: {str(data)[:300]}") from e
        # «Рассуждающие» модели (qwen3, deepseek-r1) пишут ход мыслей в <think>…</think> — он нам не нужен
        return re.sub(r"<think>.*?(</think>|$)", "", content, flags=re.S).strip()

    def ping(self) -> str:
        """Проверка подключения: короткий запрос, возвращает ответ модели."""
        prompt = "Ответь одним словом: работает" + (" /no_think" if "qwen3" in self.model.lower() else "")
        return self.complete([{"role": "user", "content": prompt}], max_tokens=20).strip()

    def build_messages(self, resume: str, context: str, transcript: str, sent: list[str] | None = None,
                       focus: str = "", buttons: list[str] | None = None) -> list[dict]:
        """Ровно то, что уходит нейросети: инструкция + резюме + контекст + переписка + новые сообщения.
        focus — один конкретный вопрос (когда работодатель задал несколько подряд)."""
        system = SYSTEM_PROMPT.replace("{max_chars}", str(self.cfg.max_answer_chars))
        fresh = new_messages(transcript, sent or [])
        parts = [
            f"РЕЗЮМЕ СОИСКАТЕЛЯ:\n{resume or '(не загружено)'}",
            f"ДОПОЛНИТЕЛЬНЫЙ КОНТЕКСТ ОТ СОИСКАТЕЛЯ:\n{context or '(нет)'}",
            f"ПЕРЕПИСКА (последние сообщения внизу):\n{transcript}",
            "НОВЫЕ СООБЩЕНИЯ РАБОТОДАТЕЛЯ (реши, что с ними делать):\n" + "\n".join(fresh or [last_message(transcript)]),
        ]
        if buttons:
            parts.append(
                "КНОПКИ ОТВЕТА В ЧАТЕ:\n" + "\n".join(f"- {b}" for b in buttons) + "\n"
                'Если одна кнопка прямо отвечает на новый вопрос (варианты «Да»/«Нет», «Интересно», выбор из списка, '
                'ответ на приглашение hh — тогда кнопка с интересом/согласием), добавь в JSON поле "button" с ТОЧНЫМ '
                'текстом кнопки, а kind = "question". Кнопки-подсказки с готовыми фразами соискателя '
                '(«Здравствуйте!», «Какая схема оплаты?», «У меня есть профильный опыт») не выбирай. '
                'Ни одна не подходит — поле "button" не добавляй.')
        if focus:
            parts.append(f"СЕЙЧАС ОТВЕТЬ ТОЛЬКО НА ЭТОТ ВОПРОС:\n{focus}")
        # Отправленные ответы уже есть в переписке. Отдельным списком их не даём: модели (проверено на qwen3)
        # принимают такой список за «анкета пройдена» и перестают отвечать. От повторов защищает chats.py.
        user = "\n\n".join(parts)
        if "qwen3" in self.model.lower():
            user += "\n\n/no_think"  # у qwen3 рассуждения отключаются этой командой — ответ в разы быстрее
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def decide(self, resume: str, context: str, transcript: str, sent: list[str],
               buttons: list[str] | None = None) -> Decision:
        if is_done_message(transcript):  # завершение анкеты узнаём сами — без нейросети
            self.last_raw = ""
            return Decision("info")
        buttons = buttons or []
        self.notes = []
        messages = self.build_messages(resume, context, transcript, sent, buttons=buttons)
        self.last_raw = self.complete(messages, json_mode=True)
        try:
            decision = self._parse(self.last_raw)
        except AiError:
            # Модель ответила текстом вместо JSON — переспрашиваем один раз
            retry = messages + [
                {"role": "assistant", "content": self.last_raw[:2000]},
                {"role": "user", "content": 'Ответь ТОЛЬКО JSON вида {"kind": "...", "question": "...", "reply": "..."} '
                                            "без пояснений и форматирования."},
            ]
            self.last_raw = self.complete(retry, json_mode=True)
            decision = self._parse(self.last_raw)
        decision = self._fix_reply(decision, messages, resume, context)
        fresh = new_messages(transcript, sent)
        questions = [m for m in fresh if m.rstrip().endswith("?")]
        if decision.button:  # нажать можно только кнопку, которая правда есть в чате
            match = next((b for b in buttons if norm(b) == norm(decision.button)), None)
            decision.button = match or ""
            if match:
                return decision
            if decision.kind == "question" and not decision.reply:
                decision.kind = "human"
                return decision
        stale = decision.kind == "question" and not _in_messages(decision.question, fresh)
        if stale:
            self.notes.append(f"ИИ взял вопрос из старой части переписки («{decision.question[:60]}»)")
            if not questions:  # новых вопросов нет — отвечать не на что (например, «Ответьте на приглашение»)
                decision.kind, decision.reply = "info", ""
                return decision
        if decision.kind == "question" and (len(questions) > 1 or stale):
            # Несколько вопросов подряд или модель взяла старый вопрос — спрашиваем по каждому новому отдельно
            decision.reply = ""
            replies = []
            for question in questions:
                focused = self.build_messages(resume, context, transcript, sent, focus=question, buttons=buttons)
                try:
                    self.last_raw = self.complete(focused, json_mode=True)
                    part = self._fix_reply(self._parse(self.last_raw), focused, resume, context)
                except AiError:
                    continue
                if part.kind == "question" and part.reply and norm(part.reply) not in map(norm, replies):
                    replies.append(part.reply)
            if replies:
                decision.question, decision.reply = " / ".join(questions), " ".join(replies)
            else:
                decision.kind = "human"
                return decision
        if decision.kind == "question" and decision.reply and not decision.button:
            decision = self._check_on_topic(decision, resume, context, transcript, sent, buttons)
        return decision

    def _check_on_topic(self, decision: Decision, resume: str, context: str, transcript: str, sent: list[str],
                        buttons: list[str]) -> Decision:
        """Отвечает ли ответ по существу на вопрос. Нет — переспрашиваем с фокусом на вопрос, снова мимо — человеку."""
        for attempt in range(2):
            ok = not _generic_reply_to_open_question(decision.question, decision.reply) and self._on_topic(
                decision.question, decision.reply)
            if ok:
                return decision
            self.notes.append(f"ответ не по сути вопроса: «{decision.reply[:60]}»")
            if attempt:
                break
            focused = self.build_messages(resume, context, transcript, sent, focus=decision.question, buttons=buttons)
            focused[-1]["content"] += ("\n\nПрошлый ответ не отвечал на вопрос по существу. Ответь именно на него, "
                                       "конкретно, по резюме.")
            try:
                self.last_raw = self.complete(focused, json_mode=True)
                retry = self._fix_reply(self._parse(self.last_raw), focused, resume, context)
            except AiError:
                break
            if retry.kind != "question" or not retry.reply:
                break
            decision.reply = retry.reply
        decision.kind = "human"
        return decision

    def _on_topic(self, question: str, reply: str) -> bool:
        """Короткая проверка второй нейросетью-«редактором». При сбое считаем, что ответ годится."""
        prompt = (f"Вопрос работодателя: «{question}»\nОтвет соискателя: «{reply}»\n\n"
                  "Отвечает ли ответ по существу именно на этот вопрос (а не на другой и не общей фразой)? "
                  'Верни строго JSON {"ok": true} или {"ok": false}.')
        if "qwen3" in self.model.lower():
            prompt += " /no_think"
        try:
            raw = self.complete([{"role": "user", "content": prompt}], max_tokens=30, json_mode=True)
            found = re.search(r'"ok"\s*:\s*(true|false)', raw, re.I)
            return not found or found.group(1).lower() == "true"
        except AiError:
            return True

    def _fix_reply(self, decision: Decision, messages: list[dict], resume: str, context: str) -> Decision:
        """Страховка ответа в чате: без «в резюме не указано» и без опыта с технологиями, которых нет в резюме.
        Сначала просим модель переписать, не вышло — правим сами."""
        if decision.kind != "question" or not decision.reply:
            return decision
        # Срок выхода и зарплата — только из вашего контекста, как в анкетах при отклике
        if _START_Q.search(decision.question) and not _TIMING_CTX.search(context):
            decision.reply = "Готов обсудить на собеседовании."
            return decision
        if _SALARY_Q.search(decision.question):
            phrase = salary_phrase(context)
            if not phrase:
                decision.reply = "Готов обсудить на собеседовании."
            elif re.sub(r"\D", "", phrase) not in re.sub(r"\D", "", decision.reply):
                decision.reply = f"Ожидаю {phrase}."
            return decision
        techs = unbacked_tech(FormQuestion(0, decision.question, "text"), resume, context)
        known = f"{resume}\n{context}\n{decision.question}"
        invented = letter_invented_tech(decision.reply, known)  # «работал с AWS», а в резюме AWS нет
        problem = ""
        if _ABOUT_RESUME.search(decision.reply):
            problem = "не упоминай резюме — говори от первого лица («опыта с X не было, но …»)"
        elif claims_experience(decision.reply, techs):
            problem = (f"в резюме НЕТ {', '.join(techs)} — не приписывай этот опыт: подай смежный "
                       "или честно скажи, что его не было")
        elif invented:
            problem = f"в резюме НЕТ {', '.join(invented)} — убери их, пиши только про опыт из резюме"
        if not problem:
            return decision
        retry = messages + [{"role": "assistant", "content": self.last_raw[:2000]},
                            {"role": "user", "content": f"Перепиши reply: {problem}. Верни тот же JSON."}]
        try:
            self.last_raw = self.complete(retry, json_mode=True)
            fixed = self._parse(self.last_raw)
            if (fixed.kind == "question" and fixed.reply and not _ABOUT_RESUME.search(fixed.reply)
                    and not claims_experience(fixed.reply, techs) and not letter_invented_tech(fixed.reply, known)):
                return fixed
        except AiError:
            pass
        # модель упёрлась: выдуманный опыт заменяем честным ответом, фразы про резюме убираем
        if techs and claims_experience(decision.reply, techs):
            # убираем только предложения с выдуманным опытом — ответ про то, что есть в резюме, остаётся
            kept = [x for x in re.split(r"(?<=[.!?])\s+", decision.reply) if not claims_experience(x, techs)]
            decision.reply = " ".join(kept + [f"С {', '.join(techs)} напрямую не работал, но готов быстро освоить."])
        elif invented:
            decision.reply = drop_sentences_with(decision.reply, invented) or "Такого опыта не было, но готов освоить."
        else:
            kept = [x for x in re.split(r"(?<=[.!?])\s+", decision.reply) if not _ABOUT_RESUME.search(x)]
            decision.reply = " ".join(kept) or "Такого опыта не было, но готов быстро освоить."
        return decision

    def fill_form(self, resume: str, context: str, vacancy: str, questions: list[FormQuestion]) -> FormFill:
        """Анкета при отклике: каждый вопрос — отдельным запросом (так 8B-моделям заметно проще)."""
        answers: list[FormAnswer] = []
        for n, q in enumerate(questions, 1):
            # «Отметьте технологии»: короткие варианты отмечаем по резюме сами — точно и без выдумок
            picked = skills_from_resume(q, f"{resume}\n{context}")
            if picked is not None:
                answers.append(FormAnswer(q, choices=picked))
                continue
            # Зарплата и сроки — слишком важны, чтобы доверять модели: считаем сами
            fixed = context_topic_answer(q, context) or language_answer(q, resume, context)
            if fixed is not None:
                answers.append(fixed)
                continue
            item = self._answer_question(resume, context, vacancy, q)
            for _ in range(2):  # 8B-модели изредка отдают пустой ответ — переспрашиваем
                if not item.get("can_answer", True) or any(item.get(k) for k in ("text", "answer", "reply",
                                                                                 "choices", "choice")):
                    break
                item = self._answer_question(resume, context, vacancy, q)
            if not item.get("can_answer", True):
                reason = str(item.get("reason", "")).strip() or "нельзя честно ответить"
                # Отказываться можно только из-за теста/кода/ссылок/документов/оплаты — не из-за нехватки опыта
                if SENSITIVE.search(q.text) or _REFUSE_OK.search(q.text):
                    return FormFill(False, f"вопрос {n}: {reason}")
                fallback = self._honest_fallback(resume, context, vacancy, q)
                if fallback is None:
                    return FormFill(False, f"вопрос {n}: {reason}")
                answers.append(fallback)
                continue
            try:
                answers.append(self._to_answer(q, item, n))
            except AiError:
                neutral = _neutral_option(q)
                if neutral is None:
                    raise
                answers.append(FormAnswer(q, choices=[neutral]))
        answers = [self._no_invented_tech(resume, context, vacancy, a) for a in answers]
        for answer in answers:
            if answer.question.kind == "text":
                answer.text = fix_text_answer(answer.question, answer.text, resume, context)
        return FormFill(True, answers=answers)

    def _no_invented_tech(self, resume: str, context: str, vacancy: str, answer: FormAnswer) -> FormAnswer:
        """Вопрос про технологию, которой нет в резюме, а модель «вспомнила» опыт с ней — не пропускаем выдумку."""
        q = answer.question
        techs = unbacked_tech(q, resume, context)
        if not techs or not _EXPERIENCE_Q.search(q.text):
            return answer
        names = ", ".join(techs)
        if q.kind != "text":  # в вариантах — скромный вариант вместо «1–3 года» с технологией, которой нет в резюме
            modest = _modest_option(q)
            if modest is not None and answer.choices and not any(_MODEST.search(q.options[c]) for c in answer.choices):
                return FormAnswer(q, choices=[modest])
            return answer
        if not claims_experience(answer.text, techs):
            return answer
        forced = FormQuestion(q.index, f"{q.text}\n(В резюме НЕТ {names}. Не утверждай, что работал с этим: подай "
                                       f"смежный опыт из резюме или честно скажи, что опыта с {names} не было.)", "text")
        try:
            item = self._answer_question(resume, context, vacancy, forced)
            text = _clean_reply(str(item.get("text") or item.get("answer") or ""), self.cfg.max_answer_chars)
        except AiError:
            text = ""
        if not text or claims_experience(text, techs):
            text = f"С {names} напрямую не работал, но готов быстро освоить."
        return FormAnswer(q, text=text)

    def write_letter(self, resume: str, context: str, title: str, company: str, description: str,
                     max_chars: int = 900) -> str:
        """Сопроводительное письмо под вакансию — для тех, у кого нет подписки hh (кнопки «Сгенерировать»)."""
        user = "\n\n".join([
            f"РЕЗЮМЕ СОИСКАТЕЛЯ:\n{resume or '(не загружено)'}",
            f"ДОПОЛНИТЕЛЬНЫЙ КОНТЕКСТ ОТ СОИСКАТЕЛЯ:\n{context or '(нет)'}",
            f"ВАКАНСИЯ: «{title}», компания {company}",
            f"ОПИСАНИЕ ВАКАНСИИ:\n{description or '(не загрузилось — пиши по названию вакансии)'}",
        ])
        if "qwen3" in self.model.lower():
            user += "\n\n/no_think"
        messages = [{"role": "system", "content": LETTER_PROMPT.replace("{max_chars}", str(max_chars))},
                    {"role": "user", "content": user}]
        self.last_raw = self.complete(messages, max_tokens=900)
        text = clean_letter(self.last_raw, max_chars)
        known = f"{resume}\n{context}\n{title}\n{company}"
        invented = letter_invented_tech(text, known)
        if invented:  # технологии из вакансии, которых нет в резюме, модель записала себе в опыт — переписываем
            names = ", ".join(invented)
            messages += [{"role": "assistant", "content": self.last_raw},
                         {"role": "user", "content": f"В резюме НЕТ {names}. Перепиши письмо: не упоминай их как свой "
                                                     "опыт — только то, что есть в резюме. Верни только текст письма."}]
            self.last_raw = self.complete(messages, max_tokens=900)
            text = clean_letter(self.last_raw, max_chars)
            invented = letter_invented_tech(text, known)
            if invented:  # снова — убираем предложения с выдуманным опытом
                text = drop_sentences_with(text, invented)
        return text

    def _honest_fallback(self, resume: str, context: str, vacancy: str, q: FormQuestion) -> FormAnswer | None:
        """Модель отказалась из-за «нет опыта в резюме» — так нельзя. Даём честный ответ вместо пропуска вакансии."""
        forced = FormQuestion(q.index, q.text + "\n(Отказаться нельзя: если прямого опыта нет — подай смежный опыт "
                                                  "или честно ответь, что особого опыта не было и готов освоить; "
                                                  "из вариантов выбери ближайший честный.)", q.kind, q.options)
        item = self._answer_question(resume, context, vacancy, forced)
        if item.get("can_answer", True):
            try:
                return self._to_answer(q, item, q.index + 1)
            except AiError:
                pass
        if q.kind == "text":
            return FormAnswer(q, text="Опыта с этим не было, но готов быстро освоить.")
        k = _modest_option(q)
        if k is None:
            k = _neutral_option(q)
        return FormAnswer(q, choices=[k]) if k is not None else None

    def _answer_question(self, resume: str, context: str, vacancy: str, q: FormQuestion) -> dict:
        kind = {"text": "текстовый ответ", "single": "выбери ОДИН вариант",
                "multi": "выбери ОДИН ИЛИ НЕСКОЛЬКО вариантов"}.get(q.kind, q.kind)
        question = f"[{kind}] {q.text}"
        if q.options:
            question += "\nВарианты:\n" + "\n".join(f"- {o}" for o in q.options)
        user = "\n\n".join([
            f"КЛЮЧЕВЫЕ ФАКТЫ ИЗ РЕЗЮМЕ:\n{key_facts(resume) or '(нет)'}",
            f"РЕЗЮМЕ СОИСКАТЕЛЯ:\n{resume or '(не загружено)'}",
            f"ДОПОЛНИТЕЛЬНЫЙ КОНТЕКСТ ОТ СОИСКАТЕЛЯ:\n{context or '(нет)'}",
            f"ВАКАНСИЯ: {vacancy}",
        ])
        hints = program_hints(q, resume, context)
        if hints:
            user += "\n\nРАСЧЁТ ПРОГРАММЫ (используй как есть):\n" + "\n".join(hints)
        user += f"\n\nВОПРОС:\n{question}"
        if "qwen3" in self.model.lower():
            user += "\n\n/no_think"
        messages = [{"role": "system", "content": FORM_PROMPT.replace("{max_chars}", str(self.cfg.max_answer_chars))},
                    {"role": "user", "content": user}]
        for attempt in range(2):
            self.last_raw = self.complete(messages, json_mode=True)
            match = re.search(r"\{.*\}", self.last_raw, re.S)
            try:
                if match:
                    return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
            messages = messages + [{"role": "assistant", "content": self.last_raw[:2000]},
                                   {"role": "user", "content": "Ответь ТОЛЬКО JSON по схеме из инструкции."}]
        raise AiError(f"ИИ вернул не JSON: {self.last_raw[:200]}")

    def _to_answer(self, q: FormQuestion, item: dict, n: int) -> FormAnswer:
        if q.kind == "text":
            raw = item.get("text") or item.get("answer") or item.get("reply") or ""  # модели путают ключи
            text = _clean_reply(str(raw), self.cfg.max_answer_chars)
            if not text:
                raise AiError(f"ИИ дал пустой ответ на вопрос {n}")
            return FormAnswer(q, text=text)
        picked = item.get("choices") or item.get("choice") or item.get("answer") or []
        if isinstance(picked, str):
            picked = [picked]
        choices = []
        for value in picked:
            k = _match_option(str(value), q.options)
            if k is None:
                raise AiError(f"ИИ выбрал вариант, которого нет в вопросе {n}: «{value}»")
            if k not in choices:
                choices.append(k)
        if not choices:
            raise AiError(f"ИИ не выбрал ни одного варианта в вопросе {n}")
        return FormAnswer(q, choices=sorted(choices[:1] if q.kind == "single" else choices))

    def _parse(self, raw: str) -> Decision:
        match = re.search(r"\{.*\}", raw, re.S)  # модели иногда оборачивают JSON в ```json … ```
        if not match:
            raise AiError(f"ИИ вернул не JSON: {raw[:200]}")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise AiError(f"ИИ вернул испорченный JSON: {raw[:200]}") from e
        kind = str(data.get("kind", "")).strip().lower()
        if kind == "button" or (not kind and data.get("button")):  # модели путают: «ответ кнопкой» — это вопрос
            kind = "question"
        if kind not in KINDS:
            raise AiError(f"ИИ вернул неизвестный тип «{kind}»")
        reply = _clean_reply(str(data.get("reply", "")), self.cfg.max_answer_chars)
        button = str(data.get("button") or "").strip()
        if kind == "question" and not reply and not button:
            kind = "human"  # вопрос есть, а ответа нет — пусть отвечает человек
        return Decision(kind=kind, question=str(data.get("question", "")).strip(), reply=reply, button=button)


_KEY_FACT = re.compile(r"^(?:Опыт работы|Формат работы|Тип занятости|Уровень дохода|Командировки|Переезд|"
                       r"Гражданство|Проживает|Желательное время в пути)", re.I)


def key_facts(resume: str) -> str:
    """Строки резюме, которые чаще всего нужны для анкет: общий стаж, формат, занятость…"""
    return "\n".join(line for line in resume.splitlines() if _KEY_FACT.match(line.strip()))


_SALARY_Q = re.compile(r"зарплат|заработн|доход|оклад|вознагражд|ожидани[яй] по (?:оплате|зп)|сколько.*получать", re.I)
_TIMING_Q = re.compile(r"приступить|выйти на работу|выход на работу|когда (?:готов|сможете|можете)|срок.*выход|"
                       r"переезд|релокац|график|командировк", re.I)
# Вопрос о сроке выхода (без переезда и графика — там бывают факты из резюме)
_START_Q = re.compile(r"приступить|выйти на работу|выход на работу|срок\w* выхода|когда (?:готов|сможете|можете)", re.I)
# Есть ли в контексте соискателя данные на эту тему
_SALARY_CTX = re.compile(r"зарплат|заработн|доход|оклад|на руки|\d\s*(?:к\b|тыс|₽|руб)", re.I)
_TIMING_CTX = re.compile(r"выйти|выход|приступ|недел|месяц|сразу|немедленно|переезд|релокац|график|командиров", re.I)


def _amount(text: str) -> int | None:
    """Первая сумма в тексте: «от 150» и «150к» → 150 000, «150 000 ₽» → 150 000."""
    match = re.search(r"(?:от|не менее|минимум)?\s*(\d[\d\s]{0,9}\d|\d)\s*(к|тыс)?", text, re.I)
    if not match:
        return None
    value = int(re.sub(r"\s", "", match.group(1)))
    if match.group(2) or value < 1000:
        value *= 1000
    return value


def _range(option: str) -> tuple[float, float] | None:
    """Вилка из текста варианта: «до 150 000» → (0, 150000), «150 000–250 000» → (150000, 250000)."""
    numbers = [_amount(part) for part in re.findall(r"\d[\d\s]*\d|\d+", option)]
    numbers = [n for n in numbers if n]
    if not numbers:
        return None
    low = option.lower()
    if re.search(r"\bдо\b|менее|меньше", low):
        return 0, numbers[0]
    if re.search(r"более|больше|свыше|\bот\b", low) and len(numbers) == 1:
        return numbers[0], float("inf")
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    return numbers[0], numbers[0]


_GRADE_Q = re.compile(r"грейд|junior|middle|senior|джун|мидл|сеньор|синьор|уровень (?:специалиста|квалификации)", re.I)
_INTEREST_Q = re.compile(r"заинтересова|почему|привлека|мотивац|интересн|о себе|расскажите", re.I)
_OFFTOPIC_OPENER = re.compile(r"^\s*(?:Меня (?:заинтересовала|привлекла)|Мне интересна|Я заинтересован)[^.!?]*[.!?]\s*")


_TECH_WORD = re.compile(r"\b[A-Za-z][A-Za-z0-9+#.\-]*[A-Za-z0-9+#]\b")
_NO_EXPERIENCE = re.compile(r"не работал|не использовал|не было|нет (?:прямого |коммерческого )?опыта|напрямую не|"
                            r"не применял|не доводилось|знаком на базовом|готов (?:быстро )?освоить|изуча", re.I)
_EXPERIENCE_Q = re.compile(r"опыт|работал|использовал|владеете|знаете|уровень|сколько лет", re.I)


def unbacked_tech(q: FormQuestion, resume: str, context: str) -> list[str]:
    """Технологии из вопроса, которых нет ни в резюме, ни в контексте (Airflow, Kafka…)."""
    known = f"{resume}\n{context}".lower()
    words = dict.fromkeys(w for w in _TECH_WORD.findall(q.text) if len(w) > 1)
    return [w for w in words if not re.search(rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])", known)]


def claims_experience(text: str, techs: list[str]) -> bool:
    """Ответ утверждает опыт с технологией, которой нет в резюме, и без оговорки «не работал / готов освоить»."""
    lowered = text.lower()
    return any(t.lower() in lowered for t in techs) and not _NO_EXPERIENCE.search(text)


def salary_phrase(context: str) -> str | None:
    """Ожидаемый доход из контекста: «Уровень дохода от 150» → «от 150 000 ₽»."""
    found = _SALARY_CTX.search(context)
    if not found:
        return None
    marked = re.search(r"(\d[\d\s]{0,9}\d|\d)\s*(?:к\b|тыс|₽|руб)", context, re.I)  # «400к», «150 000 ₽»
    start = marked.start() if marked else found.start()
    amount = _amount(context[start:])
    if not amount:
        return None
    window = context[max(0, min(found.start(), start) - 12):start + 25].lower()
    prefix = "от " if re.search(r"\bот\b|не менее|минимум", window) else ""
    net = " на руки" if "на руки" in context.lower() else ""
    return f"{prefix}{amount:,}".replace(",", " ") + f" ₽{net}"


def grade_from_resume(resume: str) -> str | None:
    """Грейд по общему стажу из строки «Опыт работы: 3 года 2 месяца»."""
    line = re.search(r"^Опыт работы:?\s*(.+)$", resume, re.M | re.I)
    if not line:
        return None
    years = re.search(r"(\d+)\s*(?:год|лет)", line.group(1))
    months = re.search(r"(\d+)\s*месяц", line.group(1))
    total = (int(years.group(1)) if years else 0) + (int(months.group(1)) / 12 if months else 0)
    if not years and not months:
        return None
    if total < 1:
        return "Junior"
    if total < 3:
        return "Junior+"
    if total < 6:
        return "Middle"
    return "Senior"


def program_hints(q: FormQuestion, resume: str, context: str) -> list[str]:
    """Факты, которые программа считает сама и подсказывает модели (доход, грейд)."""
    hints = []
    if _SALARY_Q.search(q.text) and (phrase := salary_phrase(context)):
        hints.append(f"ожидаемый доход: {phrase}")
    if _GRADE_Q.search(q.text) and (grade := grade_from_resume(resume)):
        hints.append(f"грейд по стажу: {grade}")
    return hints


def fix_text_answer(q: FormQuestion, text: str, resume: str, context: str) -> str:
    """Страховка для текстовых ответов: без вступления «Меня заинтересовала…» не по теме,
    а в ответе о доходе — ровно сумма из контекста (иначе собираем ответ сами)."""
    if not _INTEREST_Q.search(q.text):
        text = _OFFTOPIC_OPENER.sub("", text).strip() or text
        text = text[:1].upper() + text[1:]
    text = re.sub(r"[Мм]ой текущий (?:уровень )?доход", "Мои ожидания по доходу —", text)
    grade = grade_from_resume(resume) if _GRADE_Q.search(q.text) else None
    if grade and not _SALARY_Q.search(q.text) and (grade not in text or text.startswith("Ожидаю")):
        return f"Оцениваю свой уровень как {grade}."
    phrase = salary_phrase(context) if _SALARY_Q.search(q.text) else None
    if phrase:
        digits = re.sub(r"\D", "", phrase)
        if digits not in re.sub(r"\D", "", text) and digits[:-3] not in re.findall(r"\d+", text):
            parts = [f"Ожидаю доход {phrase}."]
            grade = grade_from_resume(resume) if _GRADE_Q.search(q.text) else None
            if grade:
                parts.append(f"Свой уровень оцениваю как {grade}.")
            return " ".join(parts)
    return text


def context_topic_answer(q: FormQuestion, context: str) -> FormAnswer | None:
    """Зарплата и сроки: без данных в контексте — «Готов обсудить»; вилку зарплат выбираем расчётом.

    None — вопрос не про эти темы или решить сами не можем (тогда отвечает нейросеть).
    """
    salary, timing = _SALARY_Q.search(q.text), _TIMING_Q.search(q.text)
    if not salary and not timing:
        return None
    relevant = _SALARY_CTX if salary else _TIMING_CTX
    has_info = bool(relevant.search(context))
    if not has_info:  # в контексте об этом ничего — честно «обсудим», а не выдумка модели
        if q.kind == "text":
            return FormAnswer(q, text="Готов обсудить на собеседовании.")
        neutral = _neutral_option(q)
        return FormAnswer(q, choices=[neutral]) if neutral is not None else None
    if salary and q.kind == "single":
        marked = re.search(r"(\d[\d\s]{0,9}\d|\d)\s*(?:к\b|тыс|₽|руб)", context, re.I)  # «400к», «150 000 ₽»
        start = marked.start() if marked else relevant.search(context).start()
        wanted = _amount(context[start:])
        if wanted:
            for k, option in enumerate(q.options):
                bounds = _range(option)
                if bounds and bounds[0] <= wanted < bounds[1] or (bounds and bounds[0] == bounds[1] == wanted):
                    return FormAnswer(q, choices=[k])
    return None  # текстовый ответ про зарплату/сроки по контексту — пусть сформулирует нейросеть


# Причины, по которым анкету правда нельзя заполнять за человека
_REFUSE_OK = re.compile(r"тестов\w* задани|тестовое|\bзадани[еяю]\b|реши(?:те)? задач|решени[еяю] задач|"
                        r"напиши(?:те)? (?:код|функци|запрос|скрипт|программ)|ссылк|github|gitlab|портфолио|"
                        r"документ|контакт|паспорт|оплат", re.I)
# Скромные варианты: «нет опыта», «базовый», «A1» — честный выбор, когда в резюме ничего нет
_MODEST = re.compile(r"^\s*нет\b|нет опыта|не было|не работал|не владею|базов|начальн|elementary|beginner|\bA1\b|A1–A2|A1-A2|"
                     r"до 1 года|менее (?:1|года)", re.I)
_LANG_Q = re.compile(r"английск|english|немецк|китайск|иностранн\w* язык|уровень языка", re.I)
_LANG_CTX = re.compile(r"английск|english|немецк|китайск|\b[ABC][12]\b|intermediate|upper|advanced|fluent", re.I)


def _modest_option(q: FormQuestion) -> int | None:
    return next((k for k, o in enumerate(q.options) if _MODEST.search(o)), None)


def language_answer(q: FormQuestion, resume: str, context: str) -> FormAnswer | None:
    """Уровень языка: если в резюме и контексте о нём ничего — самый скромный вариант, а не «угаданный» моделью."""
    if not _LANG_Q.search(q.text) or _LANG_CTX.search(f"{resume}\n{context}"):
        return None
    if q.kind == "text":
        return FormAnswer(q, text="Базовый уровень, читаю техническую документацию.")
    k = _modest_option(q)
    return FormAnswer(q, choices=[k if k is not None else 0]) if q.options else None


_NEUTRAL = re.compile(r"готов обсудить|обсудим|по договор[её]нности|обсуждается|на собеседовании", re.I)


def _neutral_option(q: FormQuestion) -> int | None:
    return next((k for k, o in enumerate(q.options) if _NEUTRAL.search(o)), None)


def skills_from_resume(q: FormQuestion, resume: str) -> list[int] | None:
    """Вопрос «отметьте технологии/навыки» с короткими вариантами: отмечаем те, что есть в резюме.

    None — вопрос не такой (решает нейросеть). Пустой результат тоже None: пусть решает модель.
    """
    if q.kind != "multi" or not q.options or any(len(o) > 30 or len(o.split()) > 3 for o in q.options):
        return None
    text = norm(resume)
    found = []
    for k, option in enumerate(q.options):
        name = norm(option)
        if name and re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text):
            found.append(k)
    return found or None


def _match_option(value: str, options: list[str]) -> int | None:
    """Номер варианта по тексту от модели: точное совпадение, затем вхождение, затем номер «3»."""
    v = norm(value).strip(" .«»\"")
    for k, option in enumerate(options):
        if norm(option) == v:
            return k
    if v.isdigit():  # модель вернула номер варианта, а не текст
        return int(v) - 1 if 1 <= int(v) <= len(options) else None
    for k, option in enumerate(options):
        if v and (v in norm(option) or norm(option) in v):
            return k
    return None


def _clean_reply(text: str, max_chars: int) -> str:
    text = " ".join(text.replace(" ", " ").split()).strip().strip('"«»').strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    return cut[: end + 1] if end > max_chars * 0.5 else cut.rstrip() + "…"


_LETTER_JUNK = re.compile(r"\[[^\]]{2,40}\]|^\s*(?:Тема|Subject)\s*:|^\s*С уважением|^\s*---", re.I | re.M)


def clean_letter(text: str, max_chars: int) -> str:
    """Письмо от нейросети: без markdown, подписи, заполнителей «[Ваше имя]» и с абзацами как в hh."""
    text = text.replace("**", "").replace("__", "").strip().strip('"«»').strip()
    lines = []
    for line in text.splitlines():
        if _LETTER_JUNK.search(line):
            if re.match(r"\s*С уважением", line, re.I):
                break  # дальше подпись — имя и контакты не нужны
            continue
        lines.append(line.strip().lstrip("#").strip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    text = re.sub(r"^(Здравствуйте[!,.]|Добрый день[!,.])\s*(?=\S)", r"\1\n\n", text)  # приветствие — отдельной строкой
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    return cut[: end + 1] if end > max_chars * 0.5 else cut.rstrip() + "…"


def letter_invented_tech(text: str, known: str) -> list[str]:
    """Технологии (латиницей) в письме, которых нет ни в резюме, ни в контексте, — если о них не сказано
    честно («не работал», «готов освоить»)."""
    known = known.lower()
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if _NO_EXPERIENCE.search(sentence):
            continue
        for word in _TECH_WORD.findall(sentence):
            if len(word) > 1 and word not in found and not re.search(
                    rf"(?<![a-z0-9]){re.escape(word.lower())}(?![a-z0-9])", known):
                found.append(word)
    return found


def drop_sentences_with(text: str, words: list[str]) -> str:
    """Убирает из письма предложения, где упомянуты эти слова (абзацы сохраняются)."""
    paragraphs = []
    for paragraph in text.split("\n"):
        kept = [s for s in re.split(r"(?<=[.!?])\s+", paragraph)
                if not any(re.search(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])", s) for w in words)]
        paragraphs.append(" ".join(kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(paragraphs)).strip()

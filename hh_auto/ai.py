"""ИИ-ответы на анкеты работодателей в чатах hh.

Работает с любым OpenAI-совместимым API: DeepSeek, OpenRouter (есть бесплатные модели),
Ollama (нейросеть на своём компьютере) и т.п. Нейросеть получает резюме, ваш контекст
и переписку и возвращает JSON: что это за сообщение и (если это вопрос анкеты) короткий ответ.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import AI_PROVIDERS, Ai

KINDS = ("question", "info", "human", "wait")

SYSTEM_PROMPT = """Ты помогаешь соискателю на hh.ru отвечать на анкету работодателя в чате.
Тебе дают резюме соискателя, его дополнительный контекст и переписку с работодателем.

Переписка взята со страницы как текст: в ней могут встречаться время, имена отправителей и подписи
кнопок интерфейса — не считай их сообщениями.

Определи, что нужно сделать с ПОСЛЕДНИМ сообщением работодателя, и верни строго JSON без пояснений:
{"kind": "...", "question": "...", "reply": "..."}

kind:
- "question" — работодатель (обычно «Робот-рекрутер») задал вопрос анкеты, на который можно ответить по резюме
  и контексту. Сюда же относится вопрос «Начнём?» — ответь согласием.
- "info" — ответ не нужен: благодарность за отклик, уведомление, «мы рассмотрим», отказ, завершение анкеты.
- "human" — нужен ответ самого человека: приглашение на собеседование, предложение созвониться, тестовое
  задание, просьба прислать документы, контакты или персональные данные, вопрос о деньгах/оплате,
  ссылка на внешний сайт, или вопрос, на который нельзя честно ответить по резюме и контексту.
- "wait" — последнее сообщение в переписке от соискателя: он уже ответил и ждёт работодателя.

question — текст вопроса, на который отвечаешь (для kind = "question"), иначе "".
reply — ответ для kind = "question", иначе "".

Правила ответа:
- от первого лица, по-русски (или на языке вопроса), коротко: 1–2 предложения, не длиннее {max_chars} символов;
- только факты из резюме и контекста; не выдумывай опыт, цифры, компании, навыки и даты;
- если точного ответа в резюме нет, ответь честно и нейтрально (например: «Готов обсудить на собеседовании»);
- на вопрос о сроках и количестве лет опыта отвечай числом из резюме;
- никаких приветствий, подписей, ссылок, телефонов, почты и паспортных данных."""


class AiError(Exception):
    pass


@dataclass
class Decision:
    kind: str
    question: str = ""
    reply: str = ""


class AiClient:
    def __init__(self, cfg: Ai):
        self.cfg = cfg
        preset_url, preset_model = AI_PROVIDERS.get(cfg.provider, ("", ""))[:2]
        self.base_url = (cfg.base_url or preset_url).rstrip("/")
        self.model = cfg.model or preset_model
        self.api_key = cfg.api_key or os.environ.get("HH_AI_API_KEY", "")
        if not self.base_url or not self.model:
            raise AiError("для ИИ не задан адрес API или модель ([ai] base_url / model)")
        if cfg.provider in ("deepseek", "openrouter") and not self.api_key:
            raise AiError(f"для {cfg.provider} нужен API-ключ ([ai] api_key)")

    def describe(self) -> str:
        return f"{self.cfg.provider}: {self.model} ({self.base_url})"

    def complete(self, messages: list[dict], max_tokens: int = 500) -> str:
        body = json.dumps({
            "model": self.model, "messages": messages, "temperature": 0.3, "max_tokens": max_tokens, "stream": False,
        }).encode("utf-8")
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
                raise AiError(f"модель «{self.model}» не скачана — выполните в консоли: ollama pull {self.model}") from e
            raise AiError(f"сервис ИИ ответил HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if self.cfg.provider == "ollama":
                raise AiError("Ollama не отвечает — запустите приложение Ollama (ollama.com) и повторите") from e
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

    def decide(self, resume: str, context: str, transcript: str, sent: list[str]) -> Decision:
        system = SYSTEM_PROMPT.replace("{max_chars}", str(self.cfg.max_answer_chars))
        parts = [
            f"РЕЗЮМЕ СОИСКАТЕЛЯ:\n{resume or '(не загружено)'}",
            f"ДОПОЛНИТЕЛЬНЫЙ КОНТЕКСТ ОТ СОИСКАТЕЛЯ:\n{context or '(нет)'}",
            f"ПЕРЕПИСКА (последние сообщения внизу):\n{transcript}",
        ]
        if sent:
            parts.append("ОТВЕТЫ, КОТОРЫЕ СОИСКАТЕЛЬ УЖЕ ОТПРАВИЛ В ЭТОМ ЧАТЕ:\n" + "\n".join(f"- {s}" for s in sent))
        user = "\n\n".join(parts)
        if "qwen3" in self.model.lower():
            user += "\n\n/no_think"  # у qwen3 рассуждения отключаются этой командой — ответ в разы быстрее
        raw = self.complete([{"role": "system", "content": system}, {"role": "user", "content": user}])
        return self._parse(raw)

    def _parse(self, raw: str) -> Decision:
        match = re.search(r"\{.*\}", raw, re.S)  # модели иногда оборачивают JSON в ```json … ```
        if not match:
            raise AiError(f"ИИ вернул не JSON: {raw[:200]}")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise AiError(f"ИИ вернул испорченный JSON: {raw[:200]}") from e
        kind = str(data.get("kind", "")).strip().lower()
        if kind not in KINDS:
            raise AiError(f"ИИ вернул неизвестный тип «{kind}»")
        reply = _clean_reply(str(data.get("reply", "")), self.cfg.max_answer_chars)
        if kind == "question" and not reply:
            kind = "human"  # вопрос есть, а ответа нет — пусть отвечает человек
        return Decision(kind=kind, question=str(data.get("question", "")).strip(), reply=reply)


def _clean_reply(text: str, max_chars: int) -> str:
    text = " ".join(text.replace(" ", " ").split()).strip().strip('"«»').strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    return cut[: end + 1] if end > max_chars * 0.5 else cut.rstrip() + "…"

"""Макет hh.ru и нейросети для проверок без сети: tests/e2e_mock_hh.py и tests/smoke_gui.py.

Браузер настоящий (тот же запуск, что в программе), но все запросы перехватываются: страницы отдаёт этот файл,
наружу ничего не уходит. Вёрстка — только то, на что программа опирается (data-qa из web/selectors.py), поведение —
как у hh: окно отклика, быстрый отклик, предупреждение о другом регионе, страница вопросов, чаты. Что «ушло
работодателю», записывается в MockHh.sent и MockHh.chat_sent.
"""

from __future__ import annotations

import html
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

RESUME_ID = "abcdef0123456789abcdef0123456789abcdef"
RESUME_TITLE = "Python-разработчик"
RESUME_TEXT = """Python-разработчик
Опыт работы: 3 года 2 месяца
Формат работы: Удалённо, Гибрид
Контакты
+7 999 123-45-67 — предпочитаемый способ связи
ivan@example.com
ООО «Ромашка»
Июнь 2023 — настоящее время
Разрабатывал сервисы на Python и FastAPI, PostgreSQL, Docker, Redis. Сократил время обработки отчёта с двух часов
до 15 минут. Настраивал CI/CD, код-ревью и интеграции с внешними API.
Навыки
Python
FastAPI
PostgreSQL
Docker
Redis
"""
PROFILE_TEXT = """Профиль
Иван Иванов
Город проживания
Москва
Гражданство
Россия
Телефон
+7 999 123-45-67
Почта
ivan@example.com
"""
CITY = "В каком городе вы проживаете на постоянной основе?"
DOCUMENTS = ("Готовы ли вы после телефонного интервью предоставить официальные документы, подтверждающие периоды "
             "работы из резюме (выписку из ЭТК Госуслуг, договоры ГПХ/ИП с актами выполненных работ)?")
YES_NO_DOCUMENTS = ["Да, готов предоставить документы", "Нет, не готов предоставлять документы"]


def vacancy(id_: int, title: str, flow: str, remote: bool = True, company: str = "", questions: list | None = None,
            snippet: str = "Разработка сервисов на Python, PostgreSQL, Docker.") -> dict:
    return {"id": str(id_), "title": title, "company": company or f"Компания {id_}", "flow": flow, "remote": remote,
            "questions": questions or [], "snippet": snippet}


def default_vacancies() -> list[list[dict]]:
    """Страницы выдачи: все варианты, как hh реагирует на «Откликнуться»."""
    return [[
        vacancy(101, "Python-разработчик", "modal"),
        vacancy(102, "Backend-разработчик", "quick"),
        vacancy(103, "Python developer", "optional"),
        vacancy(104, "Разработчик Python", "optional_window"),
        vacancy(105, "Python в другом регионе", "relocation"),
        vacancy(106, "Python с переездом", "relocation", remote=False),
        vacancy(107, "Python с анкетой", "questions", questions=[
            {"kind": "single", "text": DOCUMENTS, "options": YES_NO_DOCUMENTS},
            {"kind": "text", "text": CITY},
            {"kind": "multi", "text": "Отметьте технологии, с которыми работали",
             "options": ["Python", "PostgreSQL", "Kafka"]},
        ]),
        vacancy(108, "Python с вложением", "questions", questions=[
            {"kind": "text", "text": "Приложите, пожалуйста, скан диплома"}]),
        vacancy(109, "Уже откликались", "none"),
        vacancy(110, "Преподаватель Python", "modal"),
        vacancy(111, "Python в отдельном окне", "window"),
    ], [
        vacancy(201, "Python со второй страницы", "modal"),
    ]]


@dataclass
class MockChat:
    id: str
    title: str
    company: str
    messages: list[str]  # что уже написал работодатель
    script: list[str] = field(default_factory=list)  # что он напишет после каждого ответа
    buttons: list[str] = field(default_factory=list)
    unread: bool = True
    red: bool = False


def default_chats() -> list[MockChat]:
    return [
        MockChat("1", "Отказ", "Компания 1", ["Отказ. К сожалению, мы выбрали другого кандидата"], red=True),
        MockChat("2", "Анкета робота", "Компания 2", [CITY],
                 script=["Готовы ли вы предоставить официальные документы, подтверждающие периоды работы?",
                         "Спасибо за ответы! Анкета завершена, работодатель свяжется с вами."]),
        MockChat("3", "Приглашение", "Компания 3", ["Добрый день! Когда вам удобно созвониться?"]),
        MockChat("4", "Паспорт", "Компания 4", ["Укажите, пожалуйста, ваши паспортные данные для оформления пропуска."]),
        MockChat("5", "Документы кнопкой", "Компания 5", ["Готовы ли вы предоставить выписку из ЭТК?"],
                 buttons=["Нет, не готов", "Да, готов"],
                 script=["Спасибо за ответы! Анкета завершена, работодатель свяжется с вами."]),
    ]


_PAGE_STYLE = """<style>body{font:14px sans-serif;margin:0} .dlg{position:fixed;left:20px;top:20px;right:20px;background:#fff;
border:1px solid #888;padding:12px;z-index:5} .drop{position:fixed;right:20px;top:60px;background:#fff;border:1px solid #888;z-index:6} textarea{width:90%;height:60px;display:block} #app{display:flex}
#list{width:300px;height:400px;overflow:auto} #panel{flex:1;padding:8px} .msg{margin:4px 0} #messages{min-height:120px}
#answers{display:flex;flex-direction:column;align-items:flex-start}</style>"""

_SEARCH_JS = r"""
const post = (body) => fetch('/mock/sent', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                            body: JSON.stringify(body)});
const qa = (root, name) => root.querySelector(`[data-qa="${name}"]`);
function responded(card) {
  const button = qa(card, 'vacancy-serp__vacancy_response');
  button.textContent = 'Вы откликнулись';
  button.removeAttribute('data-flow');
}
function dialog(inner, role) {
  const box = document.createElement('div');
  box.setAttribute('role', role || 'dialog');
  box.className = 'dlg';
  box.innerHTML = inner;
  document.body.appendChild(box);
  return box;
}
const LETTER = '<textarea data-qa="vacancy-response-popup-form-letter-input"></textarea>';
// Капча с формой — та же вёрстка, что на hh.ru/account/captcha: картинка, «Другой текст», поле, «Отправить»,
// надпись «Неверный текст» (до ошибки скрыта). Неверный ответ — надпись и новая картинка, верный — done()
let captchaN = 0;
function captchaPicture() {
  captchaN++;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60"><rect width="200" height="60" fill="#eee"/>`
    + `<text x="16" y="40" font-size="26" font-family="monospace">${CAPTCHA_ANSWER} #${captchaN}</text></svg>`;
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}
function captchaForm(done) {
  const box = dialog(`<form><img data-qa="account-captcha-picture" alt="captcha" width="200" height="60" src="${captchaPicture()}">
    <button type="button" data-qa="captcha-renew-text" aria-label="Другой текст">↻</button>
    <input name="captchaText" type="text" data-qa="account-captcha-input">
    <div data-qa="account-captcha-error" style="visibility:hidden">Неверный текст. Пожалуйста, повторите попытку.</div>
    <button type="submit" data-qa="account-captcha-submit">Отправить</button></form>`);
  qa(box, 'captcha-renew-text').onclick = () => { qa(box, 'account-captcha-picture').src = captchaPicture(); };
  box.querySelector('form').onsubmit = async (event) => {
    event.preventDefault();
    const input = qa(box, 'account-captcha-input');
    await fetch('/mock/captcha', {method: 'POST', body: input.value});
    if (input.value === CAPTCHA_ANSWER) {
      box.remove();
      done();
      return;
    }
    qa(box, 'account-captcha-error').style.visibility = 'visible';
    qa(box, 'account-captcha-picture').src = captchaPicture();
    input.value = '';
  };
}
function modal(card, id, flow, letter) {
  const box = dialog(`<span data-qa="resume-title">${RESUME_TITLE}</span>${letter}
    <button type="button" data-qa="generate-cover-letter">Сгенерировать</button>
    <button type="button" data-qa="vacancy-response-submit-popup">Откликнуться</button>
    <button type="button" data-qa="response-popup-close">Закрыть</button>`);
  qa(box, 'response-popup-close').onclick = () => box.remove();
  qa(box, 'generate-cover-letter').onclick = () => {
    if (!NO_GENERATE) {
      const area = box.querySelector('textarea');
      if (area) setTimeout(() => { area.value = 'Письмо, которое написал сам hh кнопкой «Сгенерировать», длинное.'; }, 200);
    }
  };
  qa(box, 'vacancy-response-submit-popup').onclick = async () => {
    const area = box.querySelector('textarea');
    const text = area ? area.value : (box.dataset.letter || '');
    if ((flow === 'modal' || flow === 'captcha') && !text) return;  // письмо обязательное: hh не отправит
    if (flow === 'captcha' && CAPTCHA_ANSWER) {  // капча с формой, как на hh: ответ вводит программа
      captchaForm(async () => {
        await post({id, flow, letter: text});
        box.remove();
        responded(card);
      });
      return;
    }
    if (flow === 'captcha') {  // hh просит капчу; когда человек её решил, отклик уходит сам
      const captcha = dialog('<div data-qa="account-captcha-input">Введите символы с картинки</div>');
      setTimeout(async () => {
        await post({id, flow, letter: text});
        captcha.remove();
        box.remove();
        responded(card);
      }, CAPTCHA_MS);
      return;
    }
    await post({id, flow, letter: text});
    box.remove();
    responded(card);
  };
  return box;
}
// «Глаз» в карточке: меню «Скрыть эту вакансию» / «Скрыть вакансии компании» — отдельным слоем поверх страницы
document.addEventListener('click', (event) => {
  const eye = event.target.closest('[data-qa="vacancy__blacklist-show-add"]');
  if (!eye) return;
  const card = eye.closest('[data-qa="vacancy-serp__vacancy"]');
  const menu = document.createElement('div');
  menu.className = 'drop';
  menu.innerHTML = '<button type="button" data-qa="vacancy__blacklist-menu-add-vacancy">Скрыть эту вакансию</button>'
    + '<button type="button" data-qa="vacancy__blacklist-menu-add-employer">Скрыть вакансии компании</button>';
  document.body.appendChild(menu);
  qa(menu, 'vacancy__blacklist-menu-add-vacancy').onclick = async () => {
    await fetch('/mock/hidden', {method: 'POST', body: JSON.stringify({id: card.dataset.id})});
    menu.remove();
    card.innerHTML = 'Вакансия скрыта';
  };
  qa(menu, 'vacancy__blacklist-menu-add-employer').onclick = () => { menu.remove(); };
});
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-qa="vacancy-serp__vacancy_response"]');
  if (!button) return;
  event.preventDefault();
  const card = button.closest('[data-qa="vacancy-serp__vacancy"]');
  const id = card.dataset.id, flow = button.dataset.flow;
  if (flow === 'modal' || flow === 'captcha') modal(card, id, flow, LETTER);
  else if (flow === 'optional') {
    const box = modal(card, id, flow,
      '<button type="button" data-qa="vacancy-response-letter-toggle">Добавить сопроводительное</button><div class="slot"></div>');
    qa(box, 'vacancy-response-letter-toggle').onclick = (e) => {
      box.querySelector('.slot').innerHTML = LETTER;
      e.target.remove();
    };
  } else if (flow === 'optional_window') {
    const box = modal(card, id, flow,
      '<button type="button" data-qa="vacancy-response-letter-toggle">Добавить сопроводительное</button>');
    qa(box, 'vacancy-response-letter-toggle').onclick = () => {
      const second = dialog(`${LETTER}<button type="button" data-qa="vacancy-response-letter-submit">Сохранить</button>`);
      qa(second, 'vacancy-response-letter-submit').onclick = () => {
        box.dataset.letter = second.querySelector('textarea').value;
        second.remove();
      };
    };
  } else if (flow === 'quick') {
    post({id, flow, letter: ''});
    const informer = document.createElement('div');
    informer.setAttribute('data-qa', 'vacancy-response-letter-informer');
    informer.innerHTML = 'Ваш отклик отправлен работодателю <button type="button" data-qa="vacancy-response-letter-toggle">Приложить письмо</button>';
    card.appendChild(informer);
    responded(card);
    qa(informer, 'vacancy-response-letter-toggle').onclick = () => {
      const box = dialog(`${LETTER}<button type="button" data-qa="vacancy-response-letter-submit">Отправить</button>`);
      qa(box, 'vacancy-response-letter-submit').onclick = async () => {
        await post({id, flow: 'quick_letter', letter: box.querySelector('textarea').value});
        box.remove();
      };
    };
  } else if (flow === 'relocation') {
    const box = dialog(`Вакансия в другом регионе, вы не указали, что хотите переехать
      <button type="button" data-qa="relocation-warning-confirm">Все равно откликнуться</button>
      <button type="button" data-qa="relocation-warning-abort">Отменить</button>`, 'alertdialog');
    qa(box, 'relocation-warning-abort').onclick = () => box.remove();
    qa(box, 'relocation-warning-confirm').onclick = () => { box.remove(); modal(card, id, 'relocation', LETTER); };
  } else if (flow === 'questions') {
    location.href = `/applicant/vacancy_response?vacancyId=${id}`;
  } else if (flow === 'window') {
    window.open(`/applicant/vacancy_response?vacancyId=${id}&window=1`);
  } else if (flow === 'limit') {
    dialog('Вы можете оставить не более 200 откликов в сутки', 'dialog');
  }
});
"""

_CHAT_JS = r"""
let opened = null;
const render = () => {
  const only = document.querySelector('[data-qa="chatik-checkbox-only-unread"]').checked;
  const list = document.getElementById('list');
  list.innerHTML = '';
  for (const chat of CHATS) {
    if (only && !chat.unread && chat !== opened) continue;
    const cell = document.createElement('a');
    cell.setAttribute('data-qa', `chatik-open-chat-${chat.id}`);
    cell.href = `/chat/${chat.id}`;
    cell.style.display = 'block';
    const last = chat.log[chat.log.length - 1];
    cell.innerHTML = `<div data-qa="chat-cell-title">${chat.title}</div><div data-qa="chat-cell-subtitle">${chat.company}</div>
      <div class="${chat.red ? 'last-message-color_red--x' : 'last-message--x'}">${last}</div>
      ${chat.unread ? '<span data-qa="chatik-info-badges">1</span>' : ''}`;
    cell.onclick = (e) => { e.preventDefault(); openChat(chat); };
    list.appendChild(cell);
  }
};
const say = (chat, text) => { chat.log.push(text); if (chat === opened) showPanel(chat); render(); };
function send(chat, text, kind) {
  fetch('/mock/chat', {method: 'POST', headers: {'Content-Type': 'application/json'},
                       body: JSON.stringify({chat: chat.id, text, kind})});
  chat.buttons = [];
  say(chat, text);
  if (chat.script.length) {
    const next = chat.script.shift();
    setTimeout(() => say(chat, next), 300);
  }
}
function showPanel(chat) {
  const panel = document.getElementById('panel');
  const draft = panel.querySelector('textarea');
  const keep = draft && opened === chat ? draft.value : '';
  panel.innerHTML = `<h3>${chat.title}</h3><div id="messages">${chat.log.map((m) => `<div class="msg">${m}</div>`).join('')}</div>
    <div id="answers">${chat.buttons.map((b) => `<button type="button" class="answer">${b}</button>`).join('')}</div>
    ${chat.closed ? '' : '<div><textarea></textarea><button type="button" data-qa="chat-send">Отправить</button></div>'}`;
  for (const button of panel.querySelectorAll('.answer')) button.onclick = () => send(chat, button.textContent, 'button');
  const box = panel.querySelector('textarea');
  if (box) {
    box.value = keep;
    panel.querySelector('[data-qa="chat-send"]').onclick = () => {
      const text = box.value.trim();
      if (text) { box.value = ''; send(chat, text, 'text'); }
    };
  }
}
function openChat(chat) {
  opened = chat;
  chat.unread = false;
  history.pushState({}, '', `/chat/${chat.id}`);
  showPanel(chat);
  render();
}
const box = document.querySelector('[data-qa="chatik-checkbox-only-unread"]');
box.addEventListener('change', () => { box.className = box.checked ? 'on' : 'unchecked'; render(); });
render();
"""


class MockHh:
    def __init__(self, pages: list[list[dict]] | None = None, chats: list[MockChat] | None = None):
        self.pages = pages if pages is not None else default_vacancies()
        self.chats = chats if chats is not None else default_chats()
        self.sent: list[dict] = []  # что ушло работодателям с откликами
        self.chat_sent: list[dict] = []  # что ушло в чаты
        self.hidden: list[str] = []  # вакансии, скрытые «глазом» в карточке («Скрыть эту вакансию»)
        self.touches: list[str] = []  # поднятые резюме
        self.touch_status = 200
        self.role = "applicant"
        self.expire_after_sent = 0  # после стольких откликов сессия «истекает»
        self.no_generate = False  # кнопка «Сгенерировать» не даёт письма (нет подписки)
        self.hide_responded = False  # выдача прячет вакансии с откликом (как «подходящие к резюме» после капчи)
        self.captcha_ms = 3000  # сколько «человек» решает капчу (вакансии с flow="captcha")
        self.captcha_answer = ""  # задан — капча с формой: решается только этим ответом (его вводит программа)
        self.captcha_tries: list[str] = []  # что вводили в форму капчи
        self.visited: list[str] = []
        self.outside: list[str] = []  # запросы мимо hh: их быть не должно

    # ---------- подключение к браузеру ----------

    def install(self, context) -> None:
        context.add_cookies([{"name": "hhrole", "value": self.role, "domain": "hh.ru", "path": "/"},
                             {"name": "_xsrf", "value": "token", "domain": "hh.ru", "path": "/"}])
        context.route("**/*", self._route)

    def _route(self, route, request) -> None:
        url = urlparse(request.url)
        if url.hostname not in ("hh.ru", "www.hh.ru"):
            self.outside.append(request.url)
            route.abort()
            return
        if request.is_navigation_request():
            self.visited.append(url.path)
        status, body, kind = self._respond(request.method, url.path, parse_qs(url.query), request.post_data)
        route.fulfill(status=status, content_type=kind, body=body)

    # ---------- страницы ----------

    def _respond(self, method: str, path: str, query: dict, data: str | None) -> tuple[int, str, str]:
        text, page = "text/plain; charset=utf-8", "text/html; charset=utf-8"
        if path == "/mock/sent":
            record = json.loads(data or "{}")
            self.sent.append(record)
            return 200, "ok", text
        if path == "/mock/captcha":
            self.captcha_tries.append(data or "")
            return 200, "ok", text
        if path == "/mock/hidden":
            self.hidden.append(json.loads(data or "{}").get("id", ""))
            return 200, "ok", text
        if path == "/mock/chat":
            self.chat_sent.append(json.loads(data or "{}"))
            return 200, "ok", text
        if path == "/applicant/resumes/touch":
            self.touches.append(query.get("resume", [""])[0] or (data or ""))
            return self.touch_status, "", text
        if path in ("/applicant/resumes", "/applicant/profile/me"):
            return 200, self._profile(), page
        if path.startswith("/resume/"):
            return 200, self._resume(), page
        if path.startswith("/search/vacancy"):
            return 200, self._search(query), page
        if path == "/applicant/vacancy_response":
            return 200, self._questions(query), page
        if path.startswith("/vacancy/"):
            return 200, self._vacancy(path.rstrip("/").rsplit("/", 1)[-1]), page
        if path == "/chat" or path.startswith("/chat/"):
            return 200, self._chat(), page
        return 200, f"<!doctype html><title>hh</title>{_PAGE_STYLE}<body><main>Главная</main></body>", page

    def _profile(self) -> str:
        lines = "".join(f"<div>{html.escape(line)}</div>" for line in PROFILE_TEXT.splitlines())
        return (f"<!doctype html><meta charset=utf-8><title>Профиль</title>{_PAGE_STYLE}<body>{lines}"
                f'<section><a href="/resume/{RESUME_ID}">{RESUME_TITLE}</a> Обновлено сегодня в 09:00</section></body>')

    def _resume(self) -> str:
        lines = "".join(f"<div>{html.escape(line)}</div>" for line in RESUME_TEXT.splitlines())
        return (f"<!doctype html><meta charset=utf-8><title>Резюме</title>{_PAGE_STYLE}<body><header>Шапка hh</header>"
                f'<main><h1 data-qa="resume-block-title-position">{RESUME_TITLE}</h1>{lines}</main></body>')

    def _state(self, cards: list[dict], total: int) -> str:
        now = datetime.now().isoformat(timespec="seconds")
        return json.dumps({"vacancySearchResult": {"totalResults": total, "vacancies": [{
            "vacancyId": int(v["id"]), "name": v["title"], "company": {"id": int(v["id"]), "name": v["company"]},
            "creationTime": now, "totalResponsesCount": 3, "userTestPresent": v["flow"] == "questions",
            "workExperience": "between1And3", "area": {"name": "Москва"},
            "compensation": {"from": 150000, "to": 250000, "currencyCode": "RUR", "gross": False, "mode": "MONTH"},
            "workFormats": [{"workFormatsElement": ["REMOTE"]}],
            "snippet": {"skill": "Python, PostgreSQL, Docker, Kafka", "req": v["snippet"], "resp": ""},
        } for v in cards]}}, ensure_ascii=False)

    def _search(self, query: dict) -> str:
        number = int(query.get("page", ["0"])[0])
        cards = self.pages[number] if number < len(self.pages) else []
        total = sum(len(p) for p in self.pages)
        done = {r["id"] for r in self.sent}
        items = []
        for v in cards:
            if self.hide_responded and v["id"] in done:
                continue
            if v["id"] in self.hidden:  # как hh: скрытую вакансию выдача больше не показывает
                continue
            label = "Откликнуться" if v["flow"] != "none" and v["id"] not in done else "Вы откликнулись"
            remote = '<span data-qa="vacancy-label-work-schedule-remote">Можно удалённо</span>' if v["remote"] else ""
            items.append(
                f'<div data-qa="vacancy-serp__vacancy" data-id="{v["id"]}">'
                f'<a data-qa="serp-item__title" href="https://hh.ru/vacancy/{v["id"]}?from=mock">'
                f'<span data-qa="serp-item__title-text">{html.escape(v["title"])}</span></a> '
                f'<span data-qa="vacancy-serp__vacancy-employer-text">{html.escape(v["company"])}</span> {remote}'
                f'<div data-qa="vacancy-serp__vacancy_snippet_responsibility">{html.escape(v["snippet"])}</div>'
                f'<a href="#" data-qa="vacancy-serp__vacancy_response" data-flow="{v["flow"]}">{label}</a>'
                '<button type="button" aria-label="Скрыть" data-qa="vacancy__blacklist-show-add">◉</button></div>')
        pager = "".join(f'<a data-qa="pager-page" href="?page={i}">{i + 1}</a>' for i in range(len(self.pages)))
        script = (f"const RESUME_TITLE = {json.dumps(RESUME_TITLE, ensure_ascii=False)};\n"
                  f"const NO_GENERATE = {json.dumps(self.no_generate)};\n"
                  f"const CAPTCHA_MS = {self.captcha_ms};\n"
                  f"const CAPTCHA_ANSWER = {json.dumps(self.captcha_answer)};\n{_SEARCH_JS}")
        expired_tail = ""
        if self.expire_after_sent:
            # сессия истекает прямо после отправки: следующая проверка программы увидит гостя
            expired_tail = (f"const LIMIT = {self.expire_after_sent}; let sentHere = {len(self.sent)};"
                            "const realFetch = window.fetch; window.fetch = async (...a) => {"
                            "const r = await realFetch(...a); if (String(a[0]).includes('/mock/sent') && ++sentHere >= LIMIT)"
                            " document.cookie = 'hhrole=anonymous; path=/'; return r; };")
        return (f"<!doctype html><meta charset=utf-8><title>Выдача</title>{_PAGE_STYLE}<body>"
                f'<h1 data-qa="vacancies-search-header">Найдено {total} вакансий</h1>'
                f'<template id="HH-Lux-InitialState">{html.escape(self._state(cards, total), quote=False)}</template>'
                f'<div id="cards">{"".join(items)}</div><div>{pager}</div><script>{expired_tail}{script}</script></body>')

    def _vacancy(self, vacancy_id: str) -> str:
        """Страница вакансии: «Вы откликнулись», если отклик уже ушёл."""
        vac = next((v for page in self.pages for v in page if v["id"] == vacancy_id), None)
        title = html.escape(vac["title"]) if vac else "Вакансия"
        state = ('<div data-qa="vacancy-response-link-view-topic">Вы откликнулись</div>'
                 if vacancy_id in {r["id"] for r in self.sent} else
                 '<a href="#" data-qa="vacancy-response-link-top">Откликнуться</a>')
        return (f"<!doctype html><meta charset=utf-8><title>{title}</title>{_PAGE_STYLE}<body>"
                f'<h1 data-qa="vacancy-title">{title}</h1>{state}</body>')

    def _questions(self, query: dict) -> str:
        wanted = query.get("vacancyId", [""])[0]
        vac = next((v for page in self.pages for v in page if v["id"] == wanted), None)
        blocks = []
        for i, q in enumerate((vac or {}).get("questions", [])):
            inner = ""
            if q["kind"] == "text":
                inner = f'<textarea name="q{i}"></textarea>'
            else:
                kind = "radio" if q["kind"] == "single" else "checkbox"
                inner = "".join(f'<label><input type="{kind}" name="q{i}" value="{html.escape(o)}">{html.escape(o)}</label>'
                                for o in q["options"])
            blocks.append(f'<div data-qa="task-body"><div data-qa="task-question">{html.escape(q["text"])}</div>{inner}</div>')
        script = f"""
const id = {json.dumps(wanted)};
document.querySelector('[data-qa="vacancy-response-letter-toggle"]').onclick = (e) => {{
  document.getElementById('slot').innerHTML = '<textarea data-qa="vacancy-response-popup-form-letter-input"></textarea>';
  e.target.remove();
}};
document.querySelector('[data-qa="vacancy-response-submit-popup"]').onclick = async () => {{
  const answers = [];
  for (const block of document.querySelectorAll('[data-qa="task-body"]')) {{
    const area = block.querySelector('textarea');
    answers.push(area ? area.value : Array.from(block.querySelectorAll('input:checked')).map((i) => i.value));
  }}
  const letter = document.querySelector('[data-qa="vacancy-response-popup-form-letter-input"]');
  await fetch('/mock/sent', {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
                             body: JSON.stringify({{id, flow: 'questions', answers, letter: letter ? letter.value : ''}})}});
  document.body.innerHTML = 'Отклик отправлен';
}};"""
        return (f"<!doctype html><meta charset=utf-8><title>Вопросы</title>{_PAGE_STYLE}<body>"
                f'<div data-qa="employer-asking-for-test">Работодатель просит ответить на вопросы</div>{"".join(blocks)}'
                '<button type="button" data-qa="vacancy-response-letter-toggle">Добавить</button><div id="slot"></div>'
                f'<button type="button" data-qa="vacancy-response-submit-popup">Откликнуться</button><script>{script}</script></body>')

    def _chat(self) -> str:
        data = [{"id": c.id, "title": c.title, "company": c.company, "log": list(c.messages), "script": list(c.script),
                 "buttons": list(c.buttons), "unread": c.unread, "red": c.red} for c in self.chats]
        return (f"<!doctype html><meta charset=utf-8><title>Чаты</title>{_PAGE_STYLE}<body>"
                '<label><input type="checkbox" class="unchecked" data-qa="chatik-checkbox-only-unread">'
                '<span>Только непрочитанные</span></label><div id="app"><div id="list"></div><div id="panel"></div></div>'
                f'<script>const CHATS = {json.dumps(data, ensure_ascii=False)};{_CHAT_JS}</script></body>')


# ─────────────────────────── нейросеть ───────────────────────────


class FakeLlm:
    """OpenAI-совместимый сервер на этом компьютере: отвечает заготовками и запоминает, что у него спросили.
    Проверяем не нейросеть, а программу вокруг неё: что она отправила и что сделала с ответом."""

    LETTER = ("Здравствуйте!\n\nРазрабатывал сервисы на Python и FastAPI, сократил время обработки отчёта с двух часов "
              "до 15 минут, настраивал CI/CD. Интересны такие задачи, буду рад обсудить детали.")

    def __init__(self):
        self.requests: list[list[dict]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 — так зовёт http.server
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                messages = payload["messages"]
                owner.requests.append(messages)
                body = json.dumps({"choices": [{"message": {"content": owner.answer(messages)}}]},
                                  ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def asked(self, marker: str) -> list[str]:
        """Запросы к нейросети, в которых встречается marker (весь текст запроса)."""
        texts = ["\n".join(m["content"] for m in messages) for messages in self.requests]
        return [text for text in texts if marker in text]

    def answer(self, messages: list[dict]) -> str:
        system, user = messages[0]["content"], messages[-1]["content"]
        if "Отвечает ли ответ по существу" in user:
            return '{"ok": true}'
        if system.startswith("Ты отвечаешь за соискателя"):
            return self._form(user)
        if system.startswith("Ты помогаешь соискателю на hh.ru"):
            return self._chat(user)
        if system.startswith("Ты пишешь сопроводительное"):
            # как небольшая модель, которая не слушается: называет вакансию и компанию из описания
            found = re.search(r"(?m)^(.+?) ищет: (.+?)\. Нужны", user)
            return self.LETTER + (f" Мне интересна вакансия «{found.group(2)}» в компании {found.group(1)}." if found else "")
        if system.startswith("Ты помогаешь соискателю отбирать"):
            return json.dumps({"interesting": "кассир" not in user.lower(), "reason": "проверка"}, ensure_ascii=False)
        return "работает"

    @staticmethod
    def _form(user: str) -> str:
        question = user.rsplit("ВОПРОС:", 1)[-1]
        options = re.findall(r"^- (.+)$", question, re.M)
        if options:
            return json.dumps({"can_answer": True, "choices": options[:1]}, ensure_ascii=False)
        city = re.search(r"Проживает:\s*([^\n]+)", user)
        if "город" in question.lower():
            return json.dumps({"can_answer": True, "text": f"Живу в городе {city.group(1)}." if city
                               else "Живу в Москве."}, ensure_ascii=False)
        return json.dumps({"can_answer": True, "text": "Ответ по резюме."}, ensure_ascii=False)

    @staticmethod
    def _chat(user: str) -> str:
        fresh = user.rsplit("НОВЫЕ СООБЩЕНИЯ РАБОТОДАТЕЛЯ", 1)[-1].split("\n", 1)[-1]
        fresh = fresh.split("\n\n", 1)[0].strip()
        last = fresh.splitlines()[-1] if fresh else ""
        if not last.rstrip().endswith("?"):
            return json.dumps({"kind": "info", "question": "", "reply": ""}, ensure_ascii=False)
        city = re.search(r"Проживает:\s*([^\n]+)", user)
        reply = f"Живу в городе {city.group(1)}." if city else "Ответ по резюме."
        return json.dumps({"kind": "question", "question": last, "reply": reply}, ensure_ascii=False)


def use_mock(mock: MockHh) -> None:
    """Запуски программы — в макет: браузер настоящий, но все запросы отвечает MockHh."""
    import hh_auto.app as app
    import hh_auto.hh.responder as responder

    real = getattr(app, "_real_launch", app.launch)
    app._real_launch = real

    def launch(pw, cfg):
        context = real(pw, cfg)
        mock.install(context)
        return context

    app.launch = launch
    responder.fetch_vacancy_text = lambda _page, vac, limit=4000: f"{vac.company} ищет: {vac.title}. Нужны Python и PostgreSQL."


def write_config(path, llm_url: str, **overrides) -> None:
    """config.toml для проверки: без пауз, браузер без окна, нейросеть — FakeLlm.
    overrides: раздел__ключ=значение, например letter__mode="ai"."""
    values = {
        "": {"search_url": f"https://hh.ru/search/vacancy?resume={RESUME_ID}&from=resumelist"},
        "limits": {"delay_min": 0.0, "delay_max": 0.0, "page_delay_min": 0.0, "page_delay_max": 0.0,
                   "action_delay_min": 0.0, "action_delay_max": 0.0, "max_responses": 50},
        "browser": {"headless": True, "hide_window": False, "login_timeout": 5},
        "geo": {"check_ip": False},
        "ai": {"enabled": True, "provider": "custom", "base_url": llm_url, "model": "stub", "reply_wait": 2.0,
               "timeout": 20.0},
        "letter": {"mode": "ai"},
        "chats": {"delay_min": 0.0, "delay_max": 0.0},
    }
    for key, value in overrides.items():
        section, _, name = key.rpartition("__")
        values.setdefault(section, {})[name] = value

    def dump(value) -> str:
        return json.dumps(value, ensure_ascii=False)

    lines = [f"{key} = {dump(value)}" for key, value in values.pop("").items()]
    for section, items in values.items():
        lines.append(f"\n[{section}]")
        lines += [f"{key} = {dump(value)}" for key, value in items.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

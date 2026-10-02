"""Чаты с работодателями: отказы читаем, анкеты «Робота-рекрутера» заполняем через ИИ.

Включаем в списке фильтр «Только непрочитанные» и идём по непрочитанным чатам:
  • последнее сообщение «Отказ» → открываем, чтобы hh отметил его прочитанным;
  • если включён ИИ ([ai] enabled) — открываем чат и отдаём переписку нейросети:
      - вопрос анкеты робота → отправляем короткий ответ, ждём следующий вопрос и так по кругу;
      - уведомление без вопроса («мы рассмотрим ваше резюме») → просто прочитано;
      - живой человек, приглашение, документы и т.п. → оставляем вам (список в итогах);
  • без ИИ остальные непрочитанные не трогаем — только выводим списком.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page
from playwright.sync_api import Error as PlaywrightError

from . import control
from . import selectors as S
from .ai import AiClient, AiError
from .auth import hh_role, is_auth_url
from .browser import FatalError, wait_captcha
from .config import Config
from .logger import log
from .resume import fetch_resume_text

_COLLECT_JS = """
(s) => Array.from(document.querySelectorAll(s.cell)).map((cell) => {
  const text = (el) => (el?.textContent || '').replace(/\\s+/g, ' ').trim();
  return {
    id: cell.getAttribute('data-qa').replace('chatik-open-chat-', ''),
    title: text(cell.querySelector(s.title)),
    company: text(cell.querySelector(s.subtitle)),
    last: text(cell.querySelector(s.last)),
    unread: !!cell.querySelector(s.badge),
  };
})
"""

# Прокручивает ближайший прокручиваемый контейнер списка чатов (список виртуальный:
# в DOM только видимые строки). Возвращает false, когда дальше крутить некуда.
_SCROLL_JS = """
(cellSelector) => {
  let el = document.querySelector(cellSelector);
  while (el && el !== document.body) {
    const style = getComputedStyle(el);
    if (/(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight) break;
    el = el.parentElement;
  }
  const target = el && el !== document.body ? el : document.scrollingElement;
  const before = target.scrollTop;
  target.scrollTop += target.clientHeight * 0.8;
  return target.scrollTop !== before;
}
"""

# Текст открытого чата: поднимаемся от поля ввода до самого большого блока, в котором ещё
# нет списка чатов, — это панель переписки (шапка с вакансией + сообщения). Блок ввода
# (поле + кнопки «Отправить», вложения) вырезаем, чтобы нейросеть видела только сообщения.
_TRANSCRIPT_JS = """
([inputSelector, cellSelector]) => {
  const input = Array.from(document.querySelectorAll(inputSelector)).find((el) => el.offsetParent !== null);
  if (!input) return null;
  let composer = input;
  while (composer.parentElement && !composer.parentElement.querySelector('button')) composer = composer.parentElement;
  if (composer.parentElement) composer = composer.parentElement;
  let el = input, best = input;
  while (el.parentElement && el.parentElement !== document.body) {
    el = el.parentElement;
    if (el.querySelector(cellSelector)) break;
    best = el;
  }
  let text = best.innerText;
  const tail = composer !== best ? composer.innerText.trim() : '';
  if (tail) {
    const at = text.lastIndexOf(tail);
    if (at >= 0) text = text.slice(0, at) + text.slice(at + tail.length);
  }
  // и на всякий случай срезаем с конца строки, совпадающие с подписями кнопок и полей
  const ui = new Set(Array.from(best.querySelectorAll('button, [role="button"], label'))
    .map((el) => el.innerText.trim()).filter(Boolean));
  const lines = text.split(String.fromCharCode(10));
  while (lines.length && (!lines[lines.length - 1].trim() || ui.has(lines[lines.length - 1].trim()))) lines.pop();
  return lines.join(String.fromCharCode(10)).trim();
}
"""


@dataclass
class ChatStats:
    opened: int = 0  # прочитано отказов
    info_read: int = 0  # прочитано уведомлений без вопроса (с ИИ)
    answered: int = 0  # отправлено ответов ИИ
    questionnaires: int = 0  # чатов, где ИИ отвечал на анкету
    failed: int = 0
    rejections_read: int = 0  # отказы, прочитанные ещё до запуска
    other_unread: list[str] = field(default_factory=list)  # нужен ваш ответ


@dataclass
class _Chat:
    id: str
    title: str
    company: str
    last: str
    unread: bool


def read_rejections(
    page: Page, cfg: Config, dry_run: bool = False, stats: ChatStats | None = None, root: Path | None = None
) -> ChatStats:
    rules = cfg.chats
    stats = stats if stats is not None else ChatStats()
    ai = _make_ai(cfg)
    log.info("")
    title = "чаты: отказы" + (" + ИИ-ответы на анкеты" if ai else "")
    log.info(f"Шаг 3/3: {title}" + ("  [DRY-RUN: ничего не открываю и не отправляю]" if dry_run else ""))
    resume = ""
    if ai:
        log.info(f"  ИИ: {ai.describe()}, отвечать {'только роботу-рекрутеру' if cfg.ai.only_robot else 'всем'}")
        resume = fetch_resume_text(page, cfg, (root or Path(".")) / "data" / "resume.txt")
    answers_log = (root or Path(".")) / "data" / "chat_answers.jsonl"

    log.info(f"  открываю чаты: {rules.url}")
    page.goto(rules.url, wait_until="domcontentloaded")
    wait_captcha(page)
    if is_auth_url(page.url) or hh_role(page) == "anonymous":
        raise FatalError("hh перекинул на страницу входа — сессия истекла, войдите заново")
    try:  # при уже включённом фильтре список может быть пуст — тогда ждём сам переключатель
        page.locator(f"{S.CHAT_CELL}, {S.CHAT_ONLY_UNREAD}").first.wait_for(state="attached", timeout=20_000)
    except PlaywrightError:
        log.warning("  ! список чатов не появился за 20 с (чатов нет или hh поменял вёрстку)")
        return stats

    # С фильтром в списке только непрочитанные чаты. Если включить не вышло —
    # отличаем непрочитанные по счётчику сообщений у чата.
    only_unread = _enable_only_unread(page)
    if only_unread:
        try:
            page.locator(S.CHAT_CELL).first.wait_for(timeout=5_000)
        except PlaywrightError:
            log.success("  ✓ непрочитанных чатов нет")
            return stats

    rejection = rules.rejection_text.strip().lower()
    seen: set[str] = set()
    found = 0
    for _ in range(500):  # защита от бесконечного цикла
        chats = [_Chat(**raw) for raw in page.evaluate(_COLLECT_JS, _selectors())]
        if only_unread:  # в отфильтрованном списке непрочитанные все, даже без счётчика
            for chat in chats:
                chat.unread = True
        for chat in chats:
            if chat.id in seen:
                continue
            is_rejection = chat.last.lower().startswith(rejection)
            if is_rejection and not chat.unread:
                seen.add(chat.id)
                stats.rejections_read += 1
                log.debug(f"  • уже прочитан отказ: «{chat.title}» — {chat.company}")
            elif not is_rejection and (not chat.unread or not ai):
                seen.add(chat.id)
                if chat.unread:
                    stats.other_unread.append(f"«{chat.title}» — {chat.company}: {chat.last[:80]}")
                    log.info(f"  • непрочитанный чат НЕ с отказом (не трогаю): «{chat.title}» — {chat.company}: «{chat.last[:80]}»")

        target = next((c for c in chats if c.id not in seen and c.unread), None)
        if target is None:
            if not page.evaluate(_SCROLL_JS, S.CHAT_CELL):
                break
            control.sleep(0.5)  # даём виртуальному списку дорисовать строки
            continue

        seen.add(target.id)
        found += 1
        is_rejection = target.last.lower().startswith(rejection)
        log.info("")
        log.info(f"[{found}] {'✉' if is_rejection else '✎'} «{target.title}» — {target.company}: «{target.last[:100]}»")
        if is_rejection:
            if dry_run:
                log.info("  [dry-run] отказ — здесь чат был бы открыт и прочитан")
                stats.opened += 1
            elif _open_chat(page, target, cfg):
                stats.opened += 1
            else:
                stats.failed += 1
        else:
            _handle_with_ai(page, target, cfg, ai, resume, stats, dry_run, answers_log)
    return stats


def _make_ai(cfg: Config) -> AiClient | None:
    if not cfg.ai.enabled:
        return None
    try:
        return AiClient(cfg.ai)
    except AiError as e:
        log.warning(f"  ! ИИ выключен: {e}")
        return None


def _enable_only_unread(page: Page) -> bool:
    """Включает в списке чатов фильтр «Только непрочитанные». True — фильтр включён."""
    box = page.locator(S.CHAT_ONLY_UNREAD)
    if not box.count():
        log.warning("  ! переключателя «Только непрочитанные» нет — ищу непрочитанные по счётчикам")
        return False
    box = box.first
    if box.is_checked():
        log.info("  фильтр «Только непрочитанные» уже включён")
        return True

    log.info("  → включаю фильтр «Только непрочитанные»")
    # Сам чекбокс hh прячет под своей отрисовкой — жмём подпись, а если не сработало, сам чекбокс
    attempts = (
        lambda: page.get_by_text("Только непрочитанные", exact=True).first.click(),
        lambda: box.check(force=True),
    )
    for attempt in attempts:
        try:
            attempt()
        except PlaywrightError as e:
            log.debug(f"  не получилось: {(str(e).splitlines() or [''])[0]}")
        for _ in range(10):
            if box.is_checked():
                control.sleep(1.0)  # список перезагружается
                log.success("  ✓ фильтр включён — в списке только непрочитанные чаты")
                return True
            control.sleep(0.3)
    log.warning("  ! не удалось включить фильтр — ищу непрочитанные по счётчикам")
    return False


def _click_chat(page: Page, chat: _Chat, cfg: Config) -> None:
    cell = page.locator(f'[data-qa="chatik-open-chat-{chat.id}"]')
    cell.scroll_into_view_if_needed()
    control.sleep(random.uniform(cfg.limits.action_delay_min, cfg.limits.action_delay_max) / 2)
    log.info("  → открываю чат")
    cell.click()
    try:
        page.wait_for_url(re.compile(rf"/chat/{chat.id}"), timeout=10_000)
    except PlaywrightError:
        log.debug(f"  адрес не сменился на /chat/{chat.id}: {page.url}")


def _back_to_list(page: Page) -> None:
    """Узкая вёрстка: чат открывается вместо списка — возвращаемся назад."""
    if not page.locator(S.CHAT_CELL).count():
        log.debug("  список чатов скрыт — возвращаюсь назад")
        page.go_back(wait_until="domcontentloaded")
        page.locator(S.CHAT_CELL).first.wait_for(timeout=15_000)


def _open_chat(page: Page, chat: _Chat, cfg: Config) -> bool:
    cell = page.locator(f'[data-qa="chatik-open-chat-{chat.id}"]')
    try:
        _click_chat(page, chat, cfg)
        delay = random.uniform(cfg.chats.delay_min, cfg.chats.delay_max)
        log.info(f"  ⏸ {delay:.1f} с — жду, пока hh отметит чат прочитанным")
        control.sleep(delay)
        _back_to_list(page)
        if cell.count() == 0 or cell.locator(S.CHAT_UNREAD_BADGE).count() == 0:
            log.success("  ✓ прочитан")
        else:
            log.warning("  ! счётчик непрочитанных у чата не исчез")
        return True
    except PlaywrightError as e:
        log.error(f"  ✗ не удалось открыть чат: {(str(e).splitlines() or [''])[0]}")
        return False


# ─────────────────────────── ИИ-ответы ───────────────────────────


def _handle_with_ai(page, chat: _Chat, cfg: Config, ai: AiClient, resume: str, stats: ChatStats,
                    dry_run: bool, answers_log: Path) -> None:
    needs_you = f"«{chat.title}» — {chat.company}: {chat.last[:80]}"
    try:
        _click_chat(page, chat, cfg)
        transcript = _wait_transcript(page)
        if transcript is None:
            log.info("  в чате нет поля для ответа (чат закрыт работодателем) — просто прочитан")
            stats.info_read += 1
            _back_to_list(page)
            return
        is_robot = any(marker.lower() in transcript.lower() for marker in cfg.ai.robot_markers)
        log.info(f"  пишет: {'Робот-рекрутер' if is_robot else 'работодатель'}")

        sent: list[str] = []
        for _ in range(cfg.ai.max_answers_per_chat):
            control.check()
            log.info("  … спрашиваю ИИ, что ответить")
            decision = ai.decide(resume, cfg.ai.context, transcript[-6000:], sent)
            if decision.kind == "info":
                log.success("  ✓ ответ не нужен (уведомление) — прочитано" if not sent else "  ✓ анкета завершена")
                if not sent:
                    stats.info_read += 1
                break
            if decision.kind == "wait":
                log.info("  последнее сообщение — ваше, ждём работодателя")
                break
            if decision.kind == "human" or (cfg.ai.only_robot and not is_robot):
                why = "нужен ваш ответ" if decision.kind == "human" else "пишет не робот, а человек"
                log.warning(f"  ! {why} — оставляю вам" + (f": «{decision.question[:120]}»" if decision.question else ""))
                stats.other_unread.append(needs_you)
                break

            log.info(f"  вопрос: «{decision.question[:200]}»")
            log.success(f"  ↳ ответ ИИ: «{decision.reply}»")
            if dry_run:
                log.info("  [dry-run] ответ не отправлен")
                _log_answer(answers_log, chat, decision.question, decision.reply, sent=False)
                break
            if not _send(page, decision.reply):
                log.error("  ✗ не удалось отправить ответ — оставляю чат вам")
                stats.failed += 1
                stats.other_unread.append(needs_you)
                break
            sent.append(decision.reply)
            stats.answered += 1
            if len(sent) == 1:
                stats.questionnaires += 1
            _log_answer(answers_log, chat, decision.question, decision.reply, sent=True)
            transcript = _wait_next_message(page, cfg.ai.reply_wait)
            if transcript is None:
                log.info(f"  робот не прислал новый вопрос за {cfg.ai.reply_wait:.0f} с — перехожу дальше")
                break
        else:
            log.warning(f"  ! достигнут лимит ответов в одном чате ({cfg.ai.max_answers_per_chat})")
        _back_to_list(page)
    except AiError as e:
        log.error(f"  ✗ ошибка ИИ: {e} — оставляю чат вам")
        stats.failed += 1
        stats.other_unread.append(needs_you)
    except PlaywrightError as e:
        log.error(f"  ✗ ошибка в чате: {(str(e).splitlines() or [''])[0]}")
        stats.failed += 1


def _transcript(page: Page) -> str | None:
    try:
        return page.evaluate(_TRANSCRIPT_JS, [S.CHAT_INPUT, S.CHAT_CELL])
    except PlaywrightError:
        return None


def _wait_transcript(page: Page, timeout: float = 8) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = _transcript(page)
        if text and text.strip():
            control.sleep(0.8)  # даём подгрузиться последним сообщениям
            return _transcript(page) or text
        control.sleep(0.4)
    return None


def _wait_next_message(page: Page, timeout: float) -> str | None:
    """После нашего ответа ждём, пока в переписке появится что-то новое (следующий вопрос робота)."""
    baseline = _transcript(page) or ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        control.sleep(1.0)
        text = _transcript(page) or ""
        if text != baseline:
            control.sleep(1.5)  # робот иногда шлёт несколько сообщений подряд
            return _transcript(page) or text
    return None


def _send(page: Page, text: str) -> bool:
    box = page.locator(S.CHAT_INPUT).filter(visible=True).last
    box.click()
    box.fill(text)
    control.sleep(random.uniform(0.4, 0.9))
    send = page.locator(S.CHAT_SEND).filter(visible=True)
    if send.count():
        send.last.click()
    else:
        box.press("Enter")
    probe = text[:40]
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        control.sleep(0.4)
        value = box.input_value() if box.evaluate("el => 'value' in el") else box.inner_text()
        if not value.strip() and probe in (_transcript(page) or ""):
            log.success("  ✓ ответ отправлен")
            return True
    return False


def _log_answer(path: Path, chat: _Chat, question: str, answer: str, sent: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "chat_id": chat.id, "title": chat.title,
        "company": chat.company, "question": question, "answer": answer, "sent": sent,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_chat_summary(stats: ChatStats, elapsed: float) -> None:
    minutes, seconds = divmod(int(elapsed), 60)
    log.info("")
    log.info("══════════════ Итоги ══════════════")
    log.success(f"  прочитано отказов:              {stats.opened}")
    if stats.answered or stats.questionnaires or stats.info_read:
        log.success(f"  ИИ ответил на анкеты:           {stats.questionnaires} чатов, {stats.answered} ответов")
        log.info(f"  прочитано уведомлений:          {stats.info_read}")
    log.info(f"  отказы, прочитанные ранее:      {stats.rejections_read}")
    (log.error if stats.failed else log.info)(f"  ошибок:                         {stats.failed}")
    if stats.other_unread:
        log.warning(f"  ждут ВАШЕГО ответа ({len(stats.other_unread)}):")
        for line in stats.other_unread:
            log.warning(f"      {line}")
    log.info(f"  время работы:                   {minutes} мин {seconds} с")


def _selectors() -> dict[str, str]:
    return {
        "cell": S.CHAT_CELL,
        "title": S.CHAT_TITLE,
        "subtitle": S.CHAT_SUBTITLE,
        "last": S.CHAT_LAST_MESSAGE,
        "badge": S.CHAT_UNREAD_BADGE,
    }

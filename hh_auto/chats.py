"""Чаты с работодателями: открываем непрочитанные чаты, где последнее сообщение — «Отказ».

Открытый чат hh отмечает прочитанным, так что отказы перестают висеть в непрочитанных.
Чаты с приглашениями и живыми сообщениями не трогаем — только выводим их в лог.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field

from playwright.sync_api import Page
from playwright.sync_api import Error as PlaywrightError

from . import selectors as S
from .auth import hh_role, is_auth_url
from .browser import FatalError, wait_captcha
from .config import Config
from .logger import log

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


@dataclass
class ChatStats:
    opened: int = 0  # открыто чатов с отказом
    failed: int = 0
    rejections_read: int = 0  # отказы, прочитанные ещё до запуска
    other_unread: list[str] = field(default_factory=list)  # непрочитанные чаты не с отказом


@dataclass
class _Chat:
    id: str
    title: str
    company: str
    last: str
    unread: bool


def read_rejections(page: Page, cfg: Config, dry_run: bool = False) -> ChatStats:
    rules = cfg.chats
    log.info("")
    log.info("Шаг 3/3: чаты с отказами" + ("  [DRY-RUN: только список, без открытия]" if dry_run else ""))
    log.info(f"  открываю чаты: {rules.url}")
    page.goto(rules.url, wait_until="domcontentloaded")
    wait_captcha(page)
    if is_auth_url(page.url) or hh_role(page) == "anonymous":
        raise FatalError("hh перекинул на страницу входа — сессия истекла, войдите заново")
    try:
        page.locator(S.CHAT_CELL).first.wait_for(timeout=20_000)
    except PlaywrightError:
        log.warning("  ! список чатов не появился за 20 с (чатов нет или hh поменял вёрстку)")
        return ChatStats()

    only_unread = page.locator(S.CHAT_ONLY_UNREAD)
    if only_unread.count():
        state = "включён" if only_unread.first.is_checked() else "выключен"
        log.info(f"  фильтр «Только непрочитанные»: {state}")

    rejection = rules.rejection_text.strip().lower()
    stats = ChatStats()
    seen: set[str] = set()
    found = 0
    for _ in range(500):  # защита от бесконечного цикла
        chats = [_Chat(**raw) for raw in page.evaluate(_COLLECT_JS, _selectors())]
        for chat in chats:
            if chat.id in seen:
                continue
            is_rejection = chat.last.lower().startswith(rejection)
            if is_rejection and not chat.unread:
                seen.add(chat.id)
                stats.rejections_read += 1
                log.debug(f"  • уже прочитан отказ: «{chat.title}» — {chat.company}")
            elif not is_rejection:
                seen.add(chat.id)
                if chat.unread:
                    stats.other_unread.append(f"«{chat.title}» — {chat.company}: {chat.last[:80]}")
                    log.info(f"  • непрочитанный чат НЕ с отказом (не трогаю): «{chat.title}» — {chat.company}: «{chat.last[:80]}»")

        target = next(
            (c for c in chats if c.id not in seen and c.unread and c.last.lower().startswith(rejection)), None
        )
        if target is None:
            if not page.evaluate(_SCROLL_JS, S.CHAT_CELL):
                break
            time.sleep(0.8)  # даём виртуальному списку дорисовать строки
            continue

        seen.add(target.id)
        found += 1
        log.info(f"[{found}] ✉ «{target.title}» — {target.company}: «{target.last}»")
        if dry_run:
            log.info("  [dry-run] здесь чат был бы открыт")
            stats.opened += 1
            continue
        if _open_chat(page, target, cfg):
            stats.opened += 1
        else:
            stats.failed += 1
    return stats


def _open_chat(page: Page, chat: _Chat, cfg: Config) -> bool:
    cell = page.locator(f'[data-qa="chatik-open-chat-{chat.id}"]')
    try:
        cell.scroll_into_view_if_needed()
        time.sleep(random.uniform(cfg.limits.action_delay_min, cfg.limits.action_delay_max))
        log.info("  → открываю чат")
        cell.click()
        try:
            page.wait_for_url(re.compile(rf"/chat/{chat.id}"), timeout=10_000)
        except PlaywrightError:
            log.debug(f"  адрес не сменился на /chat/{chat.id}: {page.url}")
        delay = random.uniform(cfg.chats.delay_min, cfg.chats.delay_max)
        log.info(f"  ⏸ {delay:.1f} с — жду, пока hh отметит чат прочитанным")
        time.sleep(delay)

        # Узкая вёрстка: чат открывается вместо списка — возвращаемся назад
        if not page.locator(S.CHAT_CELL).count():
            log.debug("  список чатов скрыт — возвращаюсь назад")
            page.go_back(wait_until="domcontentloaded")
            page.locator(S.CHAT_CELL).first.wait_for(timeout=15_000)

        if cell.count() == 0 or cell.locator(S.CHAT_UNREAD_BADGE).count() == 0:
            log.success("  ✓ прочитан")
        else:
            log.warning("  ! счётчик непрочитанных у чата не исчез")
        return True
    except PlaywrightError as e:
        log.error(f"  ✗ не удалось открыть чат: {(str(e).splitlines() or [''])[0]}")
        return False


def log_chat_summary(stats: ChatStats, elapsed: float) -> None:
    minutes, seconds = divmod(int(elapsed), 60)
    log.info("")
    log.info("══════════════ Итоги ══════════════")
    log.success(f"  открыто чатов с отказом:        {stats.opened}")
    log.info(f"  отказы, прочитанные ранее:      {stats.rejections_read}")
    (log.error if stats.failed else log.info)(f"  ошибок:                         {stats.failed}")
    if stats.other_unread:
        log.warning(f"  непрочитанные чаты НЕ с отказом ({len(stats.other_unread)}) — посмотрите сами:")
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

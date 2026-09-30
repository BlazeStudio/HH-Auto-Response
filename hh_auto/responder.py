"""Шаг 3: отклик на одну вакансию со всеми вариантами поведения hh.

После нажатия «Откликнуться» в карточке hh реагирует одним из способов:
  • окно отклика с полем «Сопроводительное письмо»  → «Сгенерировать» → «Откликнуться»;
  • отклик уходит сразу и появляется «Ваш отклик отправлен работодателю»
    → «Приложить письмо» → окно «Сопроводительное письмо» → «Сгенерировать» → «Отправить»;
  • предупреждение «вакансия в другом регионе/стране» → соглашаемся, если не нужен
    переезд, иначе отказываемся;
  • отдельное окно/страница (тесты, вопросы работодателя) → вакансию пропускаем.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Locator, Page
from playwright.sync_api import Error as PlaywrightError

from . import selectors as S
from .auth import hh_role, is_auth_url
from .browser import FatalError
from .config import Config
from .logger import log
from .search import Vacancy

LIMIT_TEXT = re.compile(r"не более \d+ откликов|лимит откликов|исчерпали лимит", re.I)
SEND_BUTTON_TEXT = re.compile(r"^\s*(Отправить|Откликнуться)\s*$", re.I)
CLOSE_BUTTON_TEXT = re.compile(r"^\s*(Закрыть|Отменить|Отмена)\s*$", re.I)
# Ошибки, которые hh пишет прямо в окне отклика/письма (тексты из переводов hh)
SUBMIT_ERROR_TEXT = re.compile(
    r"Отклик уже просмотрен работодателем|требуется ответить на вопросы теста|Произошла ошибка"
    r"|Превышен лимит символов|Недопустимое сообщение|Не отправилось|сайт перегружен"
    r"|не более \d+ откликов",
    re.I,
)
VIEWED_TEXT = "просмотрен работодателем"
RESPONDED_TEXT = re.compile(r"Вы откликнулись|Резюме доставлено|Отклик отправлен", re.I)
LETTER_SETTLE_SECONDS = 2.5  # текст письма не меняется столько секунд → генерация закончилась


class Status(str, Enum):
    APPLIED = "отклик отправлен"
    SKIPPED = "пропущена"
    FAILED = "ошибка"
    LIMIT = "лимит откликов"


@dataclass
class Result:
    status: Status
    reason: str = ""
    letter: bool = False
    remember: bool = False  # запомнить пропуск и не трогать вакансию в следующих запусках


class _Outcome(Enum):
    MODAL = "окно отклика"
    QUICK = "отклик отправлен сразу"
    NEW_WINDOW = "отдельное окно"
    NAVIGATED = "переход на отдельную страницу"
    QUESTIONS = "вопросы работодателя"
    RELOCATION = "предупреждение о другом регионе"
    LIMIT = "лимит откликов"
    NOTHING = "ничего не произошло"


class Responder:
    def __init__(self, page: Page, cfg: Config, snapshots_dir: Path, resume_title: str | None):
        self.page = page
        self.cfg = cfg
        self.snapshots_dir = snapshots_dir
        self.resume_title = resume_title
        self._search_url = ""

    def apply(self, vac: Vacancy) -> Result:
        """Откликается на вакансию; непредвиденные исключения превращает в Result(FAILED)."""
        try:
            return self._apply(vac)
        except FatalError:
            raise
        except Exception as e:
            first_line = (str(e).splitlines() or [""])[0]
            log.error(f"  ✗ непредвиденная ошибка: {type(e).__name__}: {first_line}")
            log.debug("  трассировка:", exc_info=True)
            self._snapshot(vac.id, "error")
            self._close_dialogs()
            return Result(Status.FAILED, f"{type(e).__name__}: {first_line}")

    def on_search_page(self) -> bool:
        return urlparse(self.page.url).path.startswith("/search/vacancy")

    def back_to_results(self, url: str) -> None:
        """После вакансии убеждаемся, что мы всё ещё в выдаче и авторизованы."""
        if is_auth_url(self.page.url) or hh_role(self.page) == "anonymous":
            raise FatalError("сессия hh истекла (вы больше не авторизованы), перезапустите скрипт и войдите заново")
        if not self.on_search_page():
            log.info("  ↩ возвращаюсь на страницу выдачи")
            self.page.goto(url, wait_until="domcontentloaded")

    # --- сценарий отклика ---

    def _apply(self, vac: Vacancy) -> Result:
        self._close_dialogs()  # хвосты от предыдущей вакансии
        card = self._card(vac.id)
        if card.count() == 0:
            self._snapshot(vac.id, "no-card")
            return Result(Status.FAILED, "карточка вакансии не найдена на странице")
        button = card.locator(S.RESPONSE_BUTTON).first
        if button.count() == 0 or "Откликнуться" not in button.inner_text():
            return Result(Status.SKIPPED, "в карточке нет кнопки «Откликнуться» (уже откликались?)", remember=True)

        card.scroll_into_view_if_needed()
        self._pause()
        self._search_url = self.page.url
        informers_before = self.page.locator(S.LETTER_INFORMER).count()
        new_pages: list[Page] = []

        def on_new_page(new_page: Page) -> None:
            new_pages.append(new_page)

        self.page.context.on("page", on_new_page)
        try:
            log.info("  → нажимаю «Откликнуться» в карточке")
            button.click()
            outcome = self._wait_click_outcome(card, informers_before, new_pages)
            log.info(f"  реакция hh: {outcome.value}")
            if outcome is _Outcome.RELOCATION:
                declined = self._handle_relocation(vac, card)
                if declined:
                    return declined
                outcome = self._wait_click_outcome(card, informers_before, new_pages)
                log.info(f"  реакция hh после подтверждения: {outcome.value}")
        finally:
            self.page.context.remove_listener("page", on_new_page)

        if outcome is _Outcome.MODAL:
            return self._handle_modal(vac, card)
        if outcome is _Outcome.QUICK:
            return self._handle_quick(vac, card)
        if outcome is _Outcome.LIMIT:
            return Result(Status.LIMIT, "hh сообщил о лимите откликов")
        if outcome is _Outcome.NEW_WINDOW:
            for new_page in new_pages:
                try:
                    new_page.wait_for_load_state("domcontentloaded", timeout=5_000)
                except PlaywrightError:
                    pass
                log.info(f"  закрываю отдельное окно: {new_page.url}")
                new_page.close()
            return Result(Status.SKIPPED, "открылось отдельное окно (пока не обрабатываем)", remember=True)
        if outcome in (_Outcome.NAVIGATED, _Outcome.QUESTIONS):
            return self._skip_questions()
        if outcome is _Outcome.RELOCATION:
            self._snapshot(vac.id, "relocation-stuck")
            self._close_dialogs()
            return Result(Status.FAILED, "предупреждение о другом регионе не закрылось после подтверждения")

        self._snapshot(vac.id, "no-reaction")
        return Result(Status.FAILED, "после нажатия «Откликнуться» ничего не появилось")

    def _wait_click_outcome(
        self, card: Locator, informers_before: int, new_pages: list[Page], timeout: float = 15
    ) -> _Outcome:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if new_pages:
                return _Outcome.NEW_WINDOW
            if not self.on_search_page():
                return _Outcome.NAVIGATED
            try:
                # вопросы проверяем раньше окна: там такая же кнопка «Откликнуться»
                if self._questions_shown():
                    return _Outcome.QUESTIONS
                if self._visible(self.page.locator(S.MODAL_SUBMIT)):
                    return _Outcome.MODAL
                if self._visible(self.page.locator(S.RELOCATION_CONFIRM)):
                    return _Outcome.RELOCATION
                if (
                    card.locator(S.LETTER_INFORMER).count()
                    or self.page.locator(S.LETTER_INFORMER).count() > informers_before
                ):
                    return _Outcome.QUICK
                if self._limit_reached():
                    return _Outcome.LIMIT
            except PlaywrightError:
                pass  # страница перерисовывается — проверим на следующей итерации
            time.sleep(0.3)
        return _Outcome.NOTHING

    def _handle_relocation(self, vac: Vacancy, card: Locator) -> Result | None:
        """Предупреждение «вакансия в другом регионе/стране»: соглашаемся, если не нужен переезд.

        Возвращает Result, если отказались, или None, если подтвердили и отклик продолжается.
        """
        confirm = self.page.locator(S.RELOCATION_CONFIRM).filter(visible=True).first
        dialog = self.page.locator(S.DIALOG).filter(has=self.page.locator(S.RELOCATION_CONFIRM))
        warning = " ".join((dialog.last if dialog.count() else confirm).inner_text().split())
        log.info(f"  текст предупреждения: «{warning}»")

        # Стандартный текст hh всегда упоминает переезд («…вы не указали, что хотите переехать…»),
        # поэтому слова о переезде ищем только в самой вакансии
        rules = self.cfg.relocation
        card_text = " ".join(card.inner_text().split()).lower()
        keyword = next((k for k in rules.decline_keywords if k.lower() in card_text), None)
        log.info(f"  удалённая работа: {'да' if vac.remote else 'нет'}, переезд в вакансии: {keyword or 'нет'}")

        reason = None
        if not rules.confirm_other_region:
            reason = "вакансия в другом регионе/стране ([relocation] confirm_other_region = false)"
        elif keyword:
            reason = f"вакансия с переездом (в карточке есть «{keyword}»)"
        elif rules.decline_if_not_remote and not vac.remote:
            reason = "вакансия в другом регионе без удалёнки — нужен переезд"
        if reason:
            log.info(f"  → отказываюсь: {reason}")
            abort = self.page.locator(S.RELOCATION_ABORT).filter(visible=True)
            if abort.count():
                abort.first.click()
            else:
                self.page.keyboard.press("Escape")
            return Result(Status.SKIPPED, reason, remember=True)

        self._pause()
        log.info("  → соглашаюсь: вакансия в другом регионе, но переезд не нужен")
        confirm.click()
        try:
            confirm.wait_for(state="hidden", timeout=5_000)
        except PlaywrightError:
            pass
        return None

    def _skip_questions(self) -> Result:
        """Страница с вопросами/тестом работодателя: пока не заполняем — отматываем назад к выдаче."""
        log.info(f"  hh открыл страницу с вопросами работодателя: {self.page.url}")
        log.info("  ↩ отматываю назад к выдаче")
        if self.page.url != self._search_url:
            try:
                self.page.go_back(wait_until="domcontentloaded")
            except PlaywrightError:
                pass
        if self.page.url != self._search_url or self._questions_shown():
            self.page.goto(self._search_url, wait_until="domcontentloaded")
        self._close_dialogs()
        return Result(Status.SKIPPED, "вопросы/тест работодателя (пока не заполняем)", remember=True)

    def _questions_shown(self) -> bool:
        return self._visible(self.page.locator(S.QUESTIONS))

    def _handle_modal(self, vac: Vacancy, card: Locator) -> Result:
        time.sleep(0.5)  # страница вопросов может дорисоваться чуть позже кнопки
        if not self.on_search_page() or self._questions_shown():
            return self._skip_questions()
        dialog = self._response_dialog()
        if self._visible(dialog.locator(S.HIDDEN_RESUME_WARNING)):
            self._close_dialogs()
            return Result(Status.SKIPPED, "hh требует сделать резюме видимым всем работодателям", remember=True)
        if not self._resume_matches(dialog):
            self._close_dialogs()
            return Result(Status.SKIPPED, "в окне отклика выбрано другое резюме")

        letter = False
        textarea = dialog.locator(S.LETTER_INPUT)
        if textarea.count():
            letter = self._write_letter(dialog, textarea.first)
            if not letter:
                self._close_dialogs()
                return Result(Status.SKIPPED, "не удалось получить сопроводительное письмо")
        else:
            log.warning("  ! в окне нет поля для письма — откликаюсь без него")

        error = self._submit(dialog, dialog.locator(S.MODAL_SUBMIT).first, "«Откликнуться» в окне")
        if error:
            if LIMIT_TEXT.search(error) or self._limit_reached():
                self._close_dialogs()
                return Result(Status.LIMIT, "hh сообщил о лимите откликов")
            if "вопрос" in error or not self.on_search_page() or self._questions_shown():
                return self._skip_questions()
            self._snapshot(vac.id, "modal-submit")
            self._close_dialogs()
            return Result(Status.FAILED, error)

        log.success("  ✓ ОТКЛИК ОТПРАВЛЕН" + (" вместе с сопроводительным письмом" if letter else " (без письма)"))
        self._log_card_state(card)
        self._close_dialogs()
        return Result(Status.APPLIED, letter=letter)

    def _handle_quick(self, vac: Vacancy, card: Locator) -> Result:
        log.success("  ✓ ОТКЛИК ОТПРАВЛЕН сразу — прикладываю письмо")
        informer = card.locator(S.LETTER_INFORMER)
        informer = informer.first if informer.count() else self.page.locator(S.LETTER_INFORMER).last
        toggle = informer.locator(S.LETTER_TOGGLE)
        if toggle.count() == 0:
            log.warning("  ! кнопки «Приложить письмо» нет — отклик остаётся без письма")
            return Result(Status.APPLIED, "без письма: нет кнопки «Приложить письмо»")

        self._pause()
        log.info("  → нажимаю «Приложить письмо»")
        toggle.first.click()
        form = self._wait_letter_form(informer)
        if form is None:
            self._snapshot(vac.id, "letter-form")
            log.warning("  ! форма письма не появилась — отклик остаётся без письма")
            return Result(Status.APPLIED, "без письма: форма письма не появилась")

        scope, textarea, submit = form
        if not self._write_letter(scope, textarea):
            self._close_dialogs()
            return Result(Status.APPLIED, "без письма: не удалось получить текст письма")
        error = self._submit(scope, submit, "«Отправить» (письмо)")
        if error:
            if VIEWED_TEXT in error:
                # Бот работодателя успел открыть отклик — письмо уже не приложить, просто закрываем
                log.warning("  ! hh: «Отклик уже просмотрен работодателем» — письмо не приложить, закрываю окно")
            else:
                self._snapshot(vac.id, "letter-submit")
            self._close_dialogs()
            return Result(Status.APPLIED, f"без письма: {error}")

        log.success("  ✓ СОПРОВОДИТЕЛЬНОЕ ПИСЬМО ОТПРАВЛЕНО")
        self._log_card_state(card)
        self._close_dialogs()
        return Result(Status.APPLIED, letter=True)

    def _wait_letter_form(self, informer: Locator, timeout: float = 10) -> tuple[Locator | Page, Locator, Locator] | None:
        """Ищет форму письма после «Приложить письмо»: (где искать кнопки, поле, кнопка отправки)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                # окно «Сопроводительное письмо»: «Отправить» лежит в подвале окна, вне формы
                dialog = (
                    self.page.locator('[role="dialog"]')
                    .filter(has=self.page.locator(S.LETTER_INPUT))
                    .filter(visible=True)
                )
                if dialog.count():
                    dialog = dialog.last
                    submit = dialog.locator(f"{S.LETTER_SUBMIT}, {S.MODAL_SUBMIT}").or_(
                        dialog.get_by_role("button", name=SEND_BUTTON_TEXT)
                    )
                    log.info("  открылось окно «Сопроводительное письмо»")
                    return dialog, dialog.locator(S.LETTER_INPUT).first, submit.first
                # форма раскрылась прямо в плашке «Ваш отклик отправлен»
                textarea = informer.locator("textarea").filter(visible=True)
                if textarea.count():
                    submit = informer.locator(S.LETTER_SUBMIT).or_(
                        informer.get_by_role("button", name=SEND_BUTTON_TEXT)
                    )
                    log.info("  форма письма раскрылась в карточке")
                    return informer, textarea.first, submit.first
            except PlaywrightError:
                pass
            time.sleep(0.3)
        return None

    # --- сопроводительное письмо ---

    def _write_letter(self, scope: Locator | Page, textarea: Locator) -> bool:
        before = textarea.input_value().strip()
        if before:
            log.debug(f"  в поле письма уже есть текст ({len(before)} симв.)")
        generate = scope.locator(S.GENERATE_LETTER).or_(scope.get_by_role("button", name="Сгенерировать")).first
        if generate.count():
            self._pause()
            log.info("  → нажимаю «Сгенерировать» и жду текст письма…")
            generate.click()
            text = self._wait_generated(textarea, before)
            if text:
                log.success(f"  ✓ текст письма готов ({len(text)} симв.): «{_preview(text)}»")
                log.debug(f"  полный текст письма:\n{text}")
                return True
            log.warning(f"  ! письмо не появилось за {self.cfg.letter.generate_timeout:.0f} с")
        else:
            log.warning("  ! кнопки «Сгенерировать» нет (проверьте подписку)")

        fallback = self.cfg.letter.fallback_text.strip()
        if fallback:
            textarea.fill(fallback)
            log.info(f"  → вставил запасной текст письма из config ({len(fallback)} симв.)")
            return True
        log.warning("  ! запасного текста нет ([letter] fallback_text в config.toml)")
        return False

    def _wait_generated(self, textarea: Locator, before: str) -> str:
        """Ждёт, пока текст в поле появится и перестанет меняться."""
        cfg = self.cfg.letter
        deadline = time.monotonic() + cfg.generate_timeout
        last, changed_at = before, None
        next_progress = time.monotonic() + 5
        while time.monotonic() < deadline:
            time.sleep(0.4)
            value = textarea.input_value().strip()
            now = time.monotonic()
            if value != last:
                last, changed_at = value, now
                continue
            if changed_at is not None and len(value) >= cfg.min_length and now - changed_at >= LETTER_SETTLE_SECONDS:
                return value
            if now >= next_progress:
                log.info(f"  …генерация идёт (в поле {len(value)} симв.)")
                next_progress = now + 5
        current = textarea.input_value().strip()
        if len(current) >= cfg.min_length:
            log.warning("  ! новый текст не появился — использую тот, что уже есть в поле")
            return current
        return ""

    # --- вспомогательное ---

    def _submit(self, scope: Locator | Page, button: Locator, label: str) -> str | None:
        """Жмёт кнопку отправки и ждёт, пока форма закроется. Возвращает текст ошибки или None."""
        if not self._wait_enabled(button, 10):
            return f"кнопка {label} неактивна"
        self._pause()
        log.info(f"  → нажимаю {label}")
        button.click()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if not self.on_search_page() or self._questions_shown():
                return "hh открыл страницу с вопросами работодателя"
            if not self._visible(button):
                return None
            error = self._error_text(scope)
            if error:
                log.debug(f"  hh показал ошибку: «{error}»")
                return error
            time.sleep(0.3)
        return f"после нажатия {label} форма не закрылась"

    def _error_text(self, scope: Locator | Page) -> str | None:
        try:
            error = scope.get_by_text(SUBMIT_ERROR_TEXT).filter(visible=True)
            if error.count():
                return " ".join(error.first.inner_text().split())
        except PlaywrightError:
            pass
        return None

    def _wait_enabled(self, button: Locator, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if button.is_enabled(timeout=1_000):
                    return True
            except PlaywrightError:
                pass
            time.sleep(0.3)
        return False

    def _resume_matches(self, dialog: Locator | Page) -> bool:
        title_el = dialog.locator(S.MODAL_RESUME_TITLE)
        if not title_el.count():
            log.debug("  название резюме в окне отклика не найдено")
            return True
        title = " ".join(title_el.first.inner_text().split())
        log.info(f"  резюме в отклике: «{title}»")
        if not self.resume_title or _same_title(title, self.resume_title):
            return True
        log.warning(f"  ! в окне выбрано другое резюме (ожидалось «{self.resume_title}»)")
        return not self.cfg.skip_on_resume_mismatch

    def _response_dialog(self) -> Locator | Page:
        dialog = self.page.locator('[role="dialog"]').filter(has=self.page.locator(S.MODAL_SUBMIT))
        return dialog.last if dialog.count() else self.page

    def _card(self, vacancy_id: str) -> Locator:
        marker = self.page.locator(f'[id="{vacancy_id}"], a[href*="/vacancy/{vacancy_id}?"]')
        return self.page.locator(S.VACANCY_CARD).filter(has=marker).first

    def _log_card_state(self, card: Locator) -> None:
        try:
            status = card.get_by_text(RESPONDED_TEXT).first
            status.wait_for(timeout=3_000)
            log.info(f"  карточка вакансии теперь: «{' '.join(status.inner_text().split())}»")
        except PlaywrightError:
            log.debug("  карточка вакансии не показала статус отклика")

    def _close_dialogs(self) -> None:
        """Закрывает всплывающие окна: крестик, кнопка «Закрыть»/«Отменить», в крайнем случае Escape.

        Если окно так и не закрылось — перезагружает страницу, иначе оно перекроет следующую вакансию.
        """
        for _ in range(3):
            try:
                dialogs = self.page.locator(S.DIALOG).filter(visible=True)
                if not dialogs.count():
                    return
                dialog = dialogs.last
                close = (
                    dialog.locator(S.MODAL_CLOSE)
                    .or_(dialog.get_by_role("button", name=CLOSE_BUTTON_TEXT))
                    .filter(visible=True)
                )
                if close.count():
                    log.debug(f"  закрываю окно кнопкой «{close.first.inner_text().strip() or '×'}»")
                    close.first.click()
                else:
                    log.debug("  закрываю всплывающее окно (Escape)")
                    self.page.keyboard.press("Escape")
                time.sleep(0.7)
            except PlaywrightError:
                break
        if self._visible(self.page.locator(S.DIALOG)):
            log.warning("  ! всплывающее окно не закрывается — перезагружаю страницу")
            self.page.reload(wait_until="domcontentloaded")

    def _limit_reached(self) -> bool:
        return self._visible(self.page.get_by_text(LIMIT_TEXT))

    @staticmethod
    def _visible(locator: Locator) -> bool:
        try:
            return locator.filter(visible=True).count() > 0
        except PlaywrightError:
            return False

    def _pause(self) -> None:
        limits = self.cfg.limits
        time.sleep(random.uniform(limits.action_delay_min, limits.action_delay_max))

    def _snapshot(self, vacancy_id: str, tag: str) -> None:
        """Скриншот + HTML страницы — чтобы по ним поправить селекторы, если hh поменял вёрстку."""
        base = self.snapshots_dir / f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{vacancy_id}_{tag}"
        try:
            self.snapshots_dir.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=f"{base}.png")
            Path(f"{base}.html").write_text(self.page.content(), encoding="utf-8")
            log.info(f"  сохранил скриншот и HTML: {base}.png")
        except Exception as e:
            log.debug(f"  не удалось сохранить снимок страницы: {e}")


def _preview(text: str, limit: int = 90) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _same_title(a: str, b: str) -> bool:
    a, b = (" ".join(x.lower().split()) for x in (a, b))
    return a == b or a in b or b in a

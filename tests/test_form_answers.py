"""Анкеты: город, вопрос о документах, что передаём нейросети. Нейросеть здесь не нужна.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта

from hh_auto.ai.client import (AiClient, Decision, FormQuestion, documents_answer, documents_question,  # noqa: E402
                               key_facts, own_words_about, review)
from hh_auto.core.config import Ai  # noqa: E402
from hh_auto.hh import raising, resume  # noqa: E402
from hh_auto.hh.resume import has_city, personal_lines  # noqa: E402

DOCUMENTS = ("Готовы ли вы после телефонного интервью предоставить официальные документы, подтверждающие периоды "
             "работы из резюме (выписку из ЭТК Госуслуг, договоры ГПХ/ИП с актами выполненных работ)?")
YES_NO = ["Да, готов предоставить документы", "Нет, не готов предоставлять документы"]
CITY = "В каком городе вы проживаете на постоянной основе?"


class FakeAi(AiClient):
    """Клиент без сети: на каждый вопрос отвечает заготовкой."""

    def __init__(self, reply: dict | None = None, **settings):
        super().__init__(Ai(provider="custom", base_url="http://example.invalid/v1", model="test", **settings))
        self.reply = reply if reply is not None else {"can_answer": True, "text": "Ответ модели."}
        self.asked: list[str] = []

    def _answer_question(self, resume: str, context: str, vacancy: str, q: FormQuestion) -> dict:
        self.asked.append(q.text)
        return self.reply


def form(*questions: FormQuestion) -> list[FormQuestion]:
    return list(questions)


class DocumentsQuestion(unittest.TestCase):
    def test_readiness_is_recognized(self):
        for text in (DOCUMENTS, "Готовы предоставить выписку из ЭТК?", "Сможете предоставить справку 2-НДФЛ?",
                     "Согласны ли вы показать трудовую книжку после собеседования?"):
            self.assertTrue(documents_question(text), text)

    def test_asking_to_send_now_is_not_readiness(self):
        for text in ("Приложите скан трудовой книжки. Готовы?", "Пришлите документы, подтверждающие стаж",
                     "Готовы ли вы предоставить паспортные данные и документы?"):
            self.assertFalse(documents_question(text), text)

    def test_other_questions_are_not_about_documents(self):
        for text in ("Готовы ли вы оформиться по трудовой книжке?", "Готовы работать по договору ГПХ?",
                     "Готовы ли вы работать в офисе?", CITY):
            self.assertFalse(documents_question(text), text)

    def test_answer_picks_yes(self):
        answer = documents_answer(FormQuestion(0, DOCUMENTS, "single", list(reversed(YES_NO))))
        self.assertEqual(answer.describe(), YES_NO[0])
        self.assertIn("готов", documents_answer(FormQuestion(0, DOCUMENTS, "text")).text)
        self.assertIsNone(documents_answer(FormQuestion(0, DOCUMENTS, "single", ["Паспорт", "Диплом"])))


class DocumentsInForm(unittest.TestCase):
    QUESTION = FormQuestion(0, DOCUMENTS, "single", YES_NO)

    def fill(self, context: str = "", **settings):
        ai = FakeAi(**settings)
        return ai, ai.fill_form("резюме", context, "вакансия", form(self.QUESTION))

    def test_off_by_default_tells_what_to_turn_on(self):
        ai, fill = self.fill()
        self.assertFalse(fill.can_answer)
        self.assertIn("Готов предоставить документы", fill.reason)
        self.assertEqual(ai.asked, [])

    def test_setting_answers_yes_without_the_model(self):
        ai, fill = self.fill(documents_ready=True)
        self.assertTrue(fill.can_answer)
        self.assertEqual(fill.answers[0].describe(), YES_NO[0])
        self.assertEqual(ai.asked, [])

    def test_own_words_in_context_count(self):
        for context in ("Я готов предоставить все необходимые документы", "Документы показать готов."):
            self.assertTrue(self.fill(context)[1].can_answer, context)

    def test_refusal_in_context_does_not_count(self):
        for context in ("Я не готов предоставлять документы", "Документы показать не готов.", "Готов выйти сразу"):
            self.assertFalse(self.fill(context)[1].can_answer, context)

    def test_setting_reaches_the_model_as_a_fact(self):
        self.assertIn("документы", FakeAi(documents_ready=True)._full_context("Живу в Москве"))
        self.assertEqual(FakeAi()._full_context("Живу в Москве"), "Живу в Москве")


class DocumentsInChat(unittest.TestCase):
    TRANSCRIPT = f"Робот-рекрутер\n{DOCUMENTS}"

    def test_answered_when_allowed(self):
        decision = FakeAi(documents_ready=True).decide("резюме", "", self.TRANSCRIPT, [])
        self.assertEqual(decision.kind, "question")
        self.assertIn("готов", decision.reply)

    def test_left_to_you_without_permission(self):
        decision = FakeAi().decide("резюме", "", self.TRANSCRIPT, [])
        self.assertEqual((decision.kind, decision.reply), ("human", ""))
        self.assertEqual(FakeAi().decide("резюме", "Не готов предоставлять документы", self.TRANSCRIPT, []).kind, "human")

    def test_button_is_preferred(self):
        decision = FakeAi(documents_ready=True).decide("резюме", "", self.TRANSCRIPT, [], ["Нет, не готов", "Да, готов"])
        self.assertEqual((decision.button, decision.reply), ("Да, готов", ""))

    def test_interview_word_does_not_turn_it_into_a_meeting(self):
        decision = Decision("question", DOCUMENTS, "Да, готов предоставить документы по запросу.")
        self.assertEqual(review(decision, self.TRANSCRIPT, []), "")
        self.assertEqual(decision.kind, "question")

    def test_real_invitation_is_still_a_meeting(self):
        decision = Decision("question", "Когда удобно на интервью?", "Завтра")
        self.assertTrue(review(decision, "Робот\nКогда удобно на интервью?", []))
        self.assertEqual(decision.kind, "human")


class VagueAnswers(unittest.TestCase):
    QUESTION = FormQuestion(0, CITY, "text")

    def test_refusal_is_not_a_skip_and_says_what_to_add(self):
        ai = FakeAi({"can_answer": False, "reason": "персональные данные"})
        fill = ai.fill_form("резюме", "", "вакансия", form(self.QUESTION))
        self.assertTrue(fill.can_answer)
        self.assertEqual(fill.answers[0].text, "Готов рассказать подробнее на собеседовании.")
        self.assertTrue(any("Что ещё знать о вас" in note for note in ai.notes))

    def test_real_answer_has_no_note(self):
        ai = FakeAi({"can_answer": True, "text": "Живу в Москве."})
        fill = ai.fill_form("Проживает: Москва", "", "вакансия", form(self.QUESTION))
        self.assertEqual(fill.answers[0].text, "Живу в Москве.")
        self.assertEqual(ai.notes, [])


class CityQuestion(unittest.TestCase):
    RESUME = "Опыт работы: 3 года\nРоссийский университет транспорта, Москва"
    QUESTION = FormQuestion(0, CITY, "text")

    def fill(self, resume: str, context: str = ""):
        ai = FakeAi({"can_answer": True, "text": "Проживаю в Москве."})
        return ai, ai.fill_form(resume, context, "вакансия", form(self.QUESTION))

    def test_city_of_a_university_is_not_where_you_live(self):
        ai, fill = self.fill(self.RESUME)
        self.assertEqual(fill.answers[0].text, "Готов рассказать подробнее на собеседовании.")
        self.assertEqual(ai.asked, [])
        self.assertTrue(any("Что ещё знать о вас" in note for note in ai.notes))

    def test_model_answers_when_the_city_is_known(self):
        for text, context in ((f"Проживает: Москва\n{self.RESUME}", ""), (self.RESUME, "Живу в Москве"),
                                (self.RESUME, "Москва"), (self.RESUME, "Москва, гибрид"), (self.RESUME, "Нижний Новгород."),
                                (self.RESUME, "Ожидаю от 150 000. Санкт-Петербург."), (self.RESUME, "мой адрес в Туле")):
            ai, fill = self.fill(text, context)
            self.assertEqual(ai.asked, [CITY], (text, context))

    def test_other_words_in_context_do_not_make_the_city_known(self):
        for context in ("Ожидаю от 150 000 ₽ на руки", "Могу выйти через 2 недели. Предпочитаю удалёнку"):
            ai, fill = self.fill(self.RESUME, context)
            self.assertEqual(ai.asked, [], context)

    def test_chat_does_not_guess_either(self):
        reply = FakeAi()._fix_reply(Decision("question", CITY, "Проживаю в Москве."), [], self.RESUME, "").reply
        self.assertEqual(reply, "Готов рассказать подробнее на собеседовании.")

    def test_other_questions_are_not_about_the_city(self):
        ai, _ = self.fill(self.RESUME)
        fill = ai.fill_form(self.RESUME, "", "вакансия", form(FormQuestion(0, "Почему вас интересует вакансия?", "text")))
        self.assertEqual(fill.answers[0].text, "Проживаю в Москве.")


class Office(unittest.TestCase):
    RESUME = "Опыт работы: 3 года\nФормат работы: Удалённо, Гибрид\nPython"
    OFFICE = "Готовы ли вы работать в офисе 5 дней в неделю?"

    def test_text_answer_comes_from_the_resume(self):
        ai = FakeAi({"can_answer": True, "text": "Готов работать в офисе 5 дней в неделю."})
        fill = ai.fill_form(self.RESUME, "", "вакансия", form(FormQuestion(0, self.OFFICE, "text")))
        self.assertEqual(fill.answers[0].text, "Рассматриваю формат работы: удалённо, гибрид.")
        self.assertEqual(ai.asked, [])

    def test_options_pick_hybrid_then_remote_then_no(self):
        for options, wanted in ((["Да", "Гибрид", "Только удалённо"], "Гибрид"), (["Да", "Нет"], "Нет")):
            fill = FakeAi().fill_form(self.RESUME, "", "вакансия", form(FormQuestion(0, self.OFFICE, "single", options)))
            self.assertEqual(fill.answers[0].describe(), wanted, options)
        remote = "Формат работы: Удалённо"
        fill = FakeAi().fill_form(remote, "", "вакансия",
                                  form(FormQuestion(0, self.OFFICE, "single", ["Да", "Гибрид", "Только удалённо"])))
        self.assertEqual(fill.answers[0].describe(), "Только удалённо")

    def test_left_to_the_model_when_office_is_fine(self):
        for text, context in ((self.RESUME, "Готов ходить в офис каждый день"),
                              ("Формат работы: На месте работодателя, Гибрид", ""), ("Опыт работы: 3 года", "")):
            ai = FakeAi()
            ai.fill_form(text, context, "вакансия", form(FormQuestion(0, self.OFFICE, "text")))
            self.assertEqual(ai.asked, [self.OFFICE], (text, context))

    def test_chat_too(self):
        reply = FakeAi()._fix_reply(Decision("question", self.OFFICE, "Да, готов."), [], self.RESUME, "").reply
        self.assertEqual(reply, "Рассматриваю формат работы: удалённо, гибрид.")


class OwnWords(unittest.TestCase):
    """#19: «опыта с Django не было», когда вы сами написали, что работали с ним."""

    QUESTION = "Сколько лет опыта с Django?"

    def test_found_in_context(self):
        self.assertEqual(own_words_about(self.QUESTION, "Живу в Москве. С Django работал 2 года."), "С Django работал 2 года.")
        self.assertEqual(own_words_about(self.QUESTION, "С Django не работал"), "")
        self.assertEqual(own_words_about(self.QUESTION, ""), "")

    def test_contradiction_is_rewritten(self):
        ai = FakeAi()
        ai.last_raw = ""
        ai.complete = lambda *a, **k: '{"kind": "question", "question": "q", "reply": "С Django работал 2 года."}'
        decision = ai._fix_reply(Decision("question", self.QUESTION, "Опыта с Django не было, но работал с Django 2 года."),
                                 [], "Go", "С Django работал 2 года")
        self.assertEqual((decision.kind, decision.reply), ("question", "С Django работал 2 года."))

    def test_still_contradicting_goes_to_you(self):
        ai = FakeAi()
        ai.last_raw = ""
        ai.complete = lambda *a, **k: '{"kind": "question", "question": "q", "reply": "Опыта с Django не было."}'
        decision = ai._fix_reply(Decision("question", self.QUESTION, "Опыта с Django не было."), [], "Go",
                                 "С Django работал 2 года")
        self.assertEqual(decision.kind, "human")


class AttachNow(unittest.TestCase):
    def test_asking_to_attach_a_file_is_a_skip(self):
        for text in ("Приложите, пожалуйста, скан диплома", "Загрузите копию трудовой книжки",
                     "Пришлите документы, подтверждающие стаж"):
            fill = FakeAi(documents_ready=True).fill_form("резюме", "", "вакансия", form(FormQuestion(0, text, "text")))
            self.assertFalse(fill.can_answer, text)
            self.assertIn("приложить", fill.reason)


class PersonalFacts(unittest.TestCase):
    def test_label_and_value_on_one_line(self):
        text = "Шапка\nПроживает: Москва\nГражданство: Россия, есть разрешение на работу: Россия\nПодвал"
        self.assertEqual(personal_lines(text), ["Проживает: Москва", "Гражданство: Россия, есть разрешение на работу: Россия"])

    def test_label_and_value_on_neighbouring_lines(self):
        text = "Город проживания\nСанкт-Петербург\nГражданство\nРоссия\nГотовность к переезду\nНе готов к переезду"
        self.assertEqual(personal_lines(text), ["Проживает: Санкт-Петербург", "Гражданство: Россия",
                                                "Готовность к переезду: Не готов к переезду"])

    def test_page_text_without_a_label_is_not_taken(self):
        for text in ("Город Москва и область", "Переезд возможен за счёт компании", "Поиск по вакансиям в Москве",
                     "Какой ваш город?\nМосква"):
            self.assertEqual(personal_lines(text), [], text)

    def test_empty_fields_are_skipped(self):
        self.assertEqual(personal_lines("Город\nУказать\nГражданство\nГород\nМосква"), ["Проживает: Москва"])

    def test_first_value_wins(self):
        self.assertEqual(personal_lines("Город: Москва\nПроживает: Казань"), ["Проживает: Москва"])

    def test_has_city(self):
        self.assertTrue(has_city("Python-разработчик\nПроживает: Москва"))
        self.assertFalse(has_city("Российский университет транспорта, Москва\nДоговор: город не важен"))

    def test_facts_reach_the_model_first(self):
        self.assertEqual(key_facts("Опыт работы: 3 года\nПроживает: Москва\nГражданство: Россия\nPython"),
                         "Опыт работы: 3 года\nПроживает: Москва\nГражданство: Россия")


class FakeLocator:
    def __init__(self, text: str):
        self.text = text
        self.first = self

    def wait_for(self, timeout: int = 0) -> None:
        pass

    def inner_text(self) -> str:
        return self.text


class FakePage:
    """Страницы hh: адрес → (текст блока с резюме, текст всей страницы)."""

    def __init__(self, pages: dict[str, tuple[str, str]], url: str = "https://hh.ru/"):
        self.pages, self.url, self.opened = pages, url, []

    def locator(self, selector: str) -> FakeLocator:
        area, body = self.pages.get(self.url, ("", ""))
        return FakeLocator(body if selector == "body" else area)


class FetchResume(unittest.TestCase):
    RESUME_URL = "https://hh.ru/resume/abc"
    TEXT = "Python-разработчик\nОпыт работы: 3 года\n" + "Описание опыта работы. " * 20

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cache = Path(folder.name) / "data" / "resume.txt"

    def fetch(self, page: FakePage, resume_id: str | None = "abc", context: str = "") -> str:
        cfg = SimpleNamespace(resume_id=resume_id, ai=SimpleNamespace(context=context))

        def open_url(target, url, attempts=3, timeout=45):
            if url not in target.pages:
                raise RuntimeError("страница не открылась")
            target.url = url
            target.opened.append(url)

        with patch.object(resume, "open_url", open_url):
            return resume.fetch_resume_text(page, cfg, self.cache)

    def test_city_around_the_resume_block_needs_no_extra_page(self):
        page = FakePage({self.RESUME_URL: (self.TEXT, f"Шапка\nПроживает: Москва\nГражданство: Россия\n{self.TEXT}")})
        text = self.fetch(page)
        self.assertTrue(text.startswith("Проживает: Москва\nГражданство: Россия\nPython-разработчик"))
        self.assertEqual(page.opened, [self.RESUME_URL])
        self.assertEqual(self.cache.read_text(encoding="utf-8"), text)

    def test_city_from_the_profile_page(self):
        page = FakePage({self.RESUME_URL: (self.TEXT, self.TEXT),
                         resume.PROFILE_URL: ("", "Город проживания\nКазань\nГражданство\nРоссия\nТелефон\n+7 999 123-45-67")})
        text = self.fetch(page)
        self.assertTrue(text.startswith("Проживает: Казань\nГражданство: Россия\n"))
        self.assertNotIn("999", text)

    def test_resume_is_kept_when_the_profile_does_not_open(self):
        text = self.fetch(FakePage({self.RESUME_URL: (self.TEXT, self.TEXT)}))
        self.assertIn("Опыт работы: 3 года", text)
        self.assertFalse(has_city(text))

    def test_login_wall_falls_back_to_cache(self):
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text("Прошлая загрузка", encoding="utf-8")
        page = FakePage({self.RESUME_URL: ("Войдите или зарегистрируйтесь", "Войдите или зарегистрируйтесь")})
        self.assertEqual(self.fetch(page), "Прошлая загрузка")

    def test_first_resume_is_used_when_the_id_is_unknown(self):
        page = FakePage({self.RESUME_URL: (f"Проживает: Москва\n{self.TEXT}", "")})
        with patch.object(raising, "list_resumes", lambda _page: [raising.Resume(id="abc", title="Разработчик")]):
            self.assertIn("Опыт работы", self.fetch(page, resume_id=None))


if __name__ == "__main__":
    unittest.main()

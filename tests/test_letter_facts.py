"""Проверка фактов в письме нейросети: цифры и технологии. Нейросеть здесь не нужна.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта

from hh_auto.ai.client import (AiClient, AiError, drop_sentences_with, letter_invented_numbers,  # noqa: E402
                               letter_invented_tech)
from hh_auto.core.config import Ai  # noqa: E402

RESUME = """Желаемая должность: Go-разработчик
Опыт работы: 4 года 2 месяца
Сентябрь 2021 — настоящее время, ООО «ЛогистикаПлюс»
Сервис обновления каталога на Go: время обновления сократилось с двух часов до 15 минут.
Навыки: Go, PostgreSQL, Docker, gRPC, S3, 1С, K8s, OAuth2"""
TITLE = "Backend-разработчик (Python)"
COMPANY = "Тестовая компания"


def numbers(letter: str, known: str = RESUME, title: str = TITLE, company: str = COMPANY) -> list[str]:
    return letter_invented_numbers(letter, known, title, company)


def tech(letter: str, known: str = RESUME, title: str = TITLE) -> list[str]:
    return letter_invented_tech(letter, f"{known}\n{COMPANY}", title)


class DistortedNumbers(unittest.TestCase):
    def test_multipliers_and_percents_not_in_resume(self):
        for phrase in ("в два раза", "В 2 раза", "вдвое", "в 10 раз", "в десять раз", "в 8 раз", "в 2-3 раза",
                       "в два с половиной раза", "в полтора раза", "двукратно", "2-кратно", "x10", "10x", "на порядок",
                       "в разы", "в несколько раз", "многократно", "на 90%", "на 90 %", "90 процентов",
                       "на девяносто процентов", "на 87,5%", "наполовину", "на треть"):
            with self.subTest(phrase):
                self.assertTrue(numbers(f"Ускорил обновление {phrase}."), phrase)

    def test_two_hours_is_not_two_times(self):
        """«Двух часов» в резюме не разрешает «в два раза»: это разные утверждения об одном числе."""
        self.assertEqual(numbers("Сократил время обновления в два раза."), ["в два раза"])
        self.assertEqual(numbers("Сократил время на 90%."), ["на 90%"])
        self.assertEqual(numbers("Ускорил обновление в 10 раз."), ["в 10 раз"])

    def test_other_duration_with_same_number(self):
        for phrase in ("с двух часов до 2 минут", "с 15 часов до 15 минут", "за 20 минут", "с двух до 15 минут",
                       "5 лет опыта", "более 10 лет", "3 года на Go", "почти 5 лет", "полгода", "пятилетний опыт",
                       "2х минут"):
            with self.subTest(phrase):
                self.assertTrue(numbers(f"Вот результат: {phrase}."), phrase)

    def test_other_numbers_not_in_resume(self):
        for phrase in ("запустил 7 сервисов", "обрабатывает 10 000 запросов", "10к запросов", "сотни сервисов",
                       "тысячи пользователей", "1,5 млн записей", "команда из девяти человек"):
            with self.subTest(phrase):
                self.assertTrue(numbers(f"За это время {phrase}."), phrase)

    def test_rounding_and_recalculation(self):
        known = "Опыт работы: 2 года 5 месяцев. Ускорил расчёты в 3 раза."
        self.assertEqual(numbers("Опыт 2,5 года.", known), ["2,5 года"])
        self.assertEqual(numbers("Ускорил расчёты на 300%.", known), ["на 300%"])
        self.assertEqual(numbers("Опыт 2 года и 5 месяцев, ускорил расчёты в 3 раза.", known), [])

    def test_vacancy_requirements_are_not_facts(self):
        """Требование вакансии «от 2 лет» — не стаж соискателя: описание вакансии в известное не входит."""
        self.assertEqual(numbers("Коммерческий опыт от 3 лет."), ["3 лет"])


class HonestNumbers(unittest.TestCase):
    def test_numbers_from_resume(self):
        for phrase in ("с двух часов до 15 минут", "с 2 часов до 15 минут", "с 2 ч до 15 мин", "до 15-минут", "15 минут",
                       "двух часов", "два часа", "2х часов", "двухчасовой", "4 года", "четыре года", "более 4 лет",
                       "4+ года", "4-х лет", "четырёхлетний опыт", "2 месяца", "4 года 2 месяца", "с 2021 года",
                       "в 2021 г.", "2021", "с сентября 2021"):
            with self.subTest(phrase):
                self.assertEqual(numbers(f"Вот опыт: {phrase}."), [], phrase)

    def test_names_with_digits_are_not_numbers(self):
        letter = "Работал с 1С, S3, K8s и OAuth2. Знаю HTTP/2, 3D, 5G, B2B, Web-3 и 152-ФЗ, это 1-й и 2-й проекты."
        self.assertEqual(numbers(letter), [])

    def test_multiplier_and_percent_from_resume_pass_in_any_spelling(self):
        known = "Ускорил отчёты в 3 раза, снизил расходы на 30%, простои вдвое."
        for phrase in ("в 3 раза", "в три раза", "втрое", "на 30%", "на 30 %", "тридцать процентов", "в 2 раза"):
            with self.subTest(phrase):
                self.assertEqual(numbers(f"Итог: {phrase}.", known), [], phrase)

    def test_context_and_prompt_are_facts(self):
        known = f"{RESUME}\nОжидаю от 150 000 ₽ на руки\nПишите, что я ускорил импорт на 40%."
        for phrase in ("от 150 000 ₽", "от 150 тысяч", "150к", "на 40%"):
            with self.subTest(phrase):
                self.assertEqual(numbers(f"Важно: {phrase}.", known), [], phrase)

    def test_words_that_only_look_like_numbers(self):
        for letter in ("Один из сервисов вёл сам.", "Одна из задач — индексы.", "Один раз настроил сборку.",
                       "Первый релиз, второй релиз.", "Несколько лет работаю с Go.", "Значительно ускорил обновление.",
                       "Сотрудников обучал.", "1. Вёл сервис.\n2) Настроил сборку."):
            with self.subTest(letter):
                self.assertEqual(numbers(letter), [], letter)

    def test_frequency_is_not_a_multiplier(self):
        self.assertEqual(numbers("Релизил 2 раза в неделю."), [])

    def test_numbers_inside_vacancy_and_company_names(self):
        self.assertEqual(numbers("На вакансию «Инженер 7 категории» в компании Фабрика 42.",
                                 title="Инженер 7 категории", company="Фабрика 42"), [])
        self.assertEqual(numbers("На вакансию инженера 7 категории.", title="Инженер 7 категории"), [])
        self.assertEqual(numbers("Запустил 7 сервисов.", title="Инженер 7 категории"), ["7 сервисов"])

    def test_honest_clause_keeps_plain_number_but_not_result(self):
        self.assertEqual(numbers("Опыта от 3 лет с Python нет, но готов освоить."), [])
        self.assertEqual(numbers("С Kafka не работал, зато ускорил обработку в 10 раз."), ["в 10 раз"])
        self.assertEqual(numbers("С Kafka не работал, но запустил 7 сервисов."), ["7 сервисов"])


class VacancyTitleTech(unittest.TestCase):
    def test_experience_with_tech_from_title(self):
        for letter in ("Хорошо понимаю Python.", "Опыт разработки на Python.", "Работаю Python-разработчиком.",
                       "На позиции Python-разработчика работал три года.", "Занимал должность Python-разработчика.",
                       "Вакансия «Backend-разработчик (Python)» мне подходит, потому что знаю Python."):
            with self.subTest(letter):
                self.assertEqual(tech(letter), ["Python"], letter)

    def test_title_mentioned_as_title(self):
        for letter in ("Откликаюсь на вакансию «Backend-разработчик (Python)».",
                       "Вакансия «Backend-разработчика (Python)» мне интересна.",
                       "Вакансия \"Python-разработчик\" мне интересна.",
                       "Интересна вакансия Backend-разработчика (Python).",
                       "Откликаюсь на позицию Python-разработчика.",
                       "Ваша позиция разработчика на Python мне подходит."):
            with self.subTest(letter):
                self.assertEqual(tech(letter), [], letter)

    def test_single_quoted_word_is_not_the_title(self):
        self.assertEqual(tech("Много писал на «Python»."), ["Python"])
        self.assertEqual(tech("Откликаюсь на «Python».", title="Python"), [])

    def test_company_name_is_not_invented(self):
        self.assertEqual(tech("Хочу работать в Ozon Tech.", known=f"{RESUME}\nOzon Tech"), [])

    def test_role_words_from_title_are_not_tech(self):
        self.assertEqual(tech("Опыт в backend-разработке на Go."), [])
        self.assertEqual(tech("Опыт в frontend-разработке на Go."), ["frontend"])

    def test_hyphenated_word_is_checked_by_parts(self):
        self.assertEqual(tech("Я Go-Backend разработчик."), [])
        self.assertEqual(tech("Я Python-Backend разработчик."), ["Python-Backend"])

    def test_tech_from_resume_context_and_prompt(self):
        self.assertEqual(tech("Хорошо понимаю Go и PostgreSQL."), [])
        self.assertEqual(tech("Хорошо понимаю Python.", known=f"{RESUME}\nПишу на Python для себя"), [])

    def test_honest_wording(self):
        for letter in ("С Python не работал, но готов освоить.", "С Python не работала, но готова освоить.",
                       "С Python напрямую не работал.", "Опыта с Python нет, но быстро разберусь.",
                       "Опыта с Python не было.", "Нет коммерческого опыта с Python.", "Готов быстро освоить Python.",
                       "Готов изучить Python.", "Изучаю Python в свободное время.", "Быстро освою Python.",
                       "Мне интересно перейти на Python.", "Хочу перейти на Python.", "Могу быстро перейти на Python.",
                       "С Python знаком на базовом уровне."):
            with self.subTest(letter):
                self.assertEqual(tech(letter), [], letter)

    def test_honest_wording_covers_only_its_clause(self):
        self.assertEqual(tech("С Kafka не работал, но хорошо понимаю Python."), ["Python"])
        self.assertEqual(tech("Хорошо понимаю Python и готов освоить Kafka."), ["Python"])
        self.assertEqual(tech("Работал на Go, но хорошо знаю Python и могу быстро освоить его."), ["Python"])
        self.assertEqual(tech("Хорошо понимаю Python, не работал с Kafka."), ["Python"])
        self.assertEqual(tech("Готов освоить Kafka, так как знаю Python."), ["Python"])
        self.assertEqual(tech("Не работал с Kafka, а также с Redis."), [])
        self.assertEqual(tech("С Kafka, RabbitMQ и NATS не работал."), [])
        self.assertEqual(tech("С Kafka не работал и не использовал Redis."), [])
        self.assertEqual(tech("Подходит, потому что мне интересно перейти на Python."), [])

    def test_not_about_experience_is_not_an_excuse(self):
        self.assertEqual(tech("Писал на Python сервисы, где не было простоев."), ["Python"])

    def test_without_title_works_as_before(self):
        self.assertEqual(letter_invented_tech("Работал с AWS.", RESUME), ["AWS"])
        self.assertEqual(letter_invented_tech("Работал с Go.", RESUME), [])


class FakeClient(AiClient):
    """Вместо нейросети — заранее заготовленные ответы; запросы запоминаем."""

    def __init__(self, *replies):
        super().__init__(Ai(provider="custom", base_url="http://localhost:1/v1", model="fake"))
        self.replies = list(replies)
        self.requests = []

    def complete(self, messages, max_tokens=800, json_mode=False):
        self.requests.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def write(client: FakeClient, context: str = "", prompt: str = "") -> str:
    return client.write_letter(RESUME, context, TITLE, COMPANY, "Нужен Python от 2 лет, FastAPI.", 900, True, prompt)


class WriteLetter(unittest.TestCase):
    GOOD = "Здравствуйте!\n\nСократил обновление с двух часов до 15 минут. Вакансия «Backend-разработчик (Python)» интересна."

    def test_clean_letter_is_not_rewritten(self):
        client = FakeClient(self.GOOD)
        self.assertEqual(write(client), self.GOOD)
        self.assertEqual(len(client.requests), 1)

    def test_distorted_number_asks_to_rewrite(self):
        client = FakeClient("Здравствуйте!\n\nУскорил обновление в 10 раз.", self.GOOD)
        self.assertEqual(write(client), self.GOOD)
        self.assertEqual(len(client.requests), 2)
        self.assertIn("в 10 раз", client.requests[1][-1]["content"])

    def test_title_tech_asks_to_rewrite(self):
        client = FakeClient("Здравствуйте!\n\nХорошо понимаю Python.", self.GOOD)
        self.assertEqual(write(client), self.GOOD)
        self.assertIn("НЕТ Python", client.requests[1][-1]["content"])

    def test_cut_only_bad_sentences_when_rewrite_did_not_help(self):
        stubborn = ("Здравствуйте!\n\nРаботаю с Go 4 года. Ускорил обновление в 10 раз. Хорошо понимаю Python. "
                    "С Python не работал, но готов освоить. Вакансия «Backend-разработчик (Python)» интересна. "
                    "Снизил задержки на 90%.\n\nГотов обсудить детали.")
        client = FakeClient(stubborn, stubborn)
        self.assertEqual(write(client), "Здравствуйте!\n\nРаботаю с Go 4 года. С Python не работал, но готов освоить. "
                                        "Вакансия «Backend-разработчик (Python)» интересна.\n\nГотов обсудить детали.")

    def test_cut_draft_when_rewrite_failed(self):
        client = FakeClient("Здравствуйте!\n\nРаботаю с Go. Ускорил обновление вдвое.", AiError("нет связи"))
        self.assertEqual(write(client), "Здравствуйте!\n\nРаботаю с Go.")

    def test_rewrite_with_new_invention_is_cut(self):
        client = FakeClient("Здравствуйте!\n\nРаботаю с Go. Ускорил вдвое.", "Здравствуйте!\n\nРаботаю с Go. Ускорил на 90%.")
        self.assertEqual(write(client), "Здравствуйте!\n\nРаботаю с Go.")

    def test_context_and_prompt_are_facts(self):
        letter = "Здравствуйте!\n\nУскорил импорт на 40%, пишу на Python для себя."
        client = FakeClient(letter)
        self.assertEqual(write(client, prompt="Пиши: ускорил импорт на 40%, Python для себя"), letter)
        self.assertEqual(len(client.requests), 1)
        client = FakeClient(letter)
        self.assertEqual(write(client, context="Ускорил импорт на 40%. Python — для себя"), letter)
        self.assertEqual(len(client.requests), 1)

    def test_forbidden_mentions_still_work(self):
        stubborn = "Здравствуйте!\n\nРаботаю с Go. Вакансия мне интересна."
        client = FakeClient(stubborn, stubborn)
        self.assertEqual(write(client, prompt="Не упоминай вакансию"), "Здравствуйте!\n\nРаботаю с Go.")

    def test_prompt_warns_about_numbers_and_title(self):
        client = FakeClient(self.GOOD)
        write(client)
        system = client.requests[0][0]["content"]
        self.assertIn("в N раз", system)
        self.assertIn("название и требования вакансии — не твой опыт", system)


class ChatRepliesStillWork(unittest.TestCase):
    def test_drop_sentences_with(self):
        self.assertEqual(drop_sentences_with("Работал с AWS. Работал с Go.", ["AWS"]), "Работал с Go.")


if __name__ == "__main__":
    unittest.main()

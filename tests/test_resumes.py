"""Несколько резюме ([resumes]): правила, поиск слов в названии вакансии, какое резюме выбрать и ответ нейросети."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hh_auto.ai.client import AiError, parse_resume_pick  # noqa: E402
from hh_auto.core.config import Config, ConfigError, validate_config  # noqa: E402
from hh_auto.hh.resumes import (MyResume, ResumePicker, find_resume, mentioned, parse_rules, resolve,  # noqa: E402
                                word_hits)

AI = MyResume("07ebe173ff1169f66c0039ed1f614636577070", "AI/LLM-инженер · ML-инженер (LLM/RAG) · Python Backend (AI)")
PY = MyResume("93952ea0ff11590a2a0039ed1f6e67746d324d", "Python-разработчик (Backend, LLM / AI-агенты, Data)")
DEV = MyResume("3753c2bcff0ea2b0390039ed1f576348314c68", "Python Backend Developer · DevOps · FinTech/InsurTech")
SA = MyResume("4444c2bcff0ea2b0390039ed1f576348314c68", "Системный аналитик")
MINE = [AI, PY, DEV, SA]
RULES = """AI/LLM-инженер: ai, ml, llm, rag, ии, нейросет, machine learning
Python-разработчик: python, backend, бэкенд, django
# комментарий
Системный аналитик: аналитик, analyst, бизнес-анализ
"""


def cfg(rules: str = RULES, default: str = "", resume_id: str | None = None):
    return SimpleNamespace(resumes=SimpleNamespace(rules=rules, default=default, ai_pick=True), resume_id=resume_id)


def picker(**kwargs) -> ResumePicker:
    targets, default, _source, warnings = resolve(cfg(**kwargs), MINE)
    assert not warnings, warnings
    return ResumePicker(targets, default, MINE)


class FakeAi:
    def __init__(self, answer=None, error: bool = False):
        self.answer, self.error, self.asked = answer, error, []

    def pick_resume(self, title, company, snippet, options):
        self.asked.append((title, [name for name, _words, _text in options]))
        if self.error:
            raise AiError("нет связи")
        names = [name for name, _words, _text in options]
        return names.index(self.answer), "так ближе"


class RulesTest(unittest.TestCase):
    def test_parse(self):
        rules = parse_rules(RULES)
        self.assertEqual([r.resume for r in rules], ["AI/LLM-инженер", "Python-разработчик", "Системный аналитик"])
        self.assertEqual(rules[2].words, ["аналитик", "analyst", "бизнес-анализ"])
        self.assertEqual(rules[2].line, 4)

    def test_link_with_colon(self):
        rules = parse_rules(f"https://hh.ru/resume/{SA.id}: аналитик; analyst")
        self.assertEqual(rules[0].resume, f"https://hh.ru/resume/{SA.id}")
        self.assertEqual(rules[0].words, ["аналитик", "analyst"])

    def test_errors(self):
        with self.assertRaisesRegex(ValueError, "строка 2"):
            parse_rules("ИИ: ai\nбез двоеточия")
        with self.assertRaisesRegex(ValueError, "ни одного слова"):
            parse_rules("ИИ:  ")
        with self.assertRaisesRegex(ValueError, "нужно"):
            parse_rules(f"https://hh.ru/resume/{SA.id}")

    def test_validate_config(self):
        config = Config(search_url="")
        config.resumes.enabled = True
        with self.assertRaisesRegex(ConfigError, r"\[resumes\]"):
            validate_config(config)
        config.resumes.rules = "ИИ: ai"
        validate_config(config)
        config.resumes.rules = "ИИ ai"
        with self.assertRaisesRegex(ConfigError, "строка 1"):
            validate_config(config)


class WordsTest(unittest.TestCase):
    def test_word_start(self):
        self.assertEqual(word_hits("Бизнес-аналитик", ["аналит"]), ["аналит"])
        self.assertEqual(word_hits("Ведущий системный аналитик (BPMN)", ["аналитик", "bpmn"]), ["аналитик", "bpmn"])
        self.assertEqual(word_hits("Data Engineer", ["ngineer"]), [])  # не с середины слова

    def test_short_words_whole(self):
        for title in ("Trainee Python", "Data Engineer (Airflow)", "Retail-менеджер", "Email-маркетолог"):
            self.assertEqual(word_hits(title, ["ai"]), [], title)
        self.assertEqual(word_hits("Python AI Engineer", ["ai"]), ["ai"])
        self.assertEqual(word_hits("ИИ-инженер", ["ии"]), ["ии"])
        self.assertEqual(word_hits("ML-инженер (LLM/RAG)", ["ml", "llm", "rag"]), ["ml", "llm", "rag"])

    def test_quotes_whole(self):
        self.assertEqual(word_hits("JavaScript developer", ['"java"']), [])
        self.assertEqual(word_hits("Java developer", ['"java"']), ['"java"'])
        self.assertEqual(word_hits("JavaScript developer", ["java"]), ["java"])  # без кавычек — с начала слова

    def test_case_yo_and_symbols(self):
        self.assertEqual(word_hits("Разработчик ПО (ЁЛКА)", ["елка"]), ["елка"])
        self.assertEqual(word_hits("C# / ASP.NET разработчик", [".net", "c#"]), [".net", "c#"])
        self.assertEqual(word_hits("Specialist in Machine   Learning", ["machine learning"]), ["machine learning"])


class FindTest(unittest.TestCase):
    def test_part_link_and_words(self):
        self.assertEqual(find_resume("AI/LLM-инженер", MINE), [AI])
        self.assertEqual(find_resume(f"https://hh.ru/resume/{SA.id}?hhtmFrom=x", MINE), [SA])
        self.assertEqual(find_resume(SA.id, MINE), [SA])
        self.assertEqual(find_resume("python разработчик", MINE), [PY])  # дефис и скобки не важны
        self.assertEqual(find_resume("системный АНАЛИТИК", MINE), [SA])

    def test_ambiguous_and_missing(self):
        self.assertEqual(find_resume("Python Backend", MINE), [AI, DEV])
        self.assertEqual(find_resume("Тестировщик", MINE), [])

    def test_resolve_warnings_and_default(self):
        targets, default, source, warnings = resolve(cfg(rules="Python Backend: backend\nТестировщик: qa\nСистемный: analyst"),
                                                     MINE)
        self.assertEqual([(r.id, r.words) for r in targets], [(SA.id, ["analyst"])])
        self.assertEqual(len(warnings), 2)
        self.assertIn("нескольким", warnings[0])
        self.assertIn("нет среди ваших", warnings[1])
        self.assertEqual((default.id, source), (SA.id, "первое в правилах"))
        _t, default, source, _w = resolve(cfg(resume_id=DEV.id), MINE)
        self.assertEqual(default.id, DEV.id)
        self.assertIn("ссылки на резюме", source)
        _t, default, source, _w = resolve(cfg(default="аналитик", resume_id=DEV.id), MINE)
        self.assertEqual((default.id, source), (SA.id, "из настроек"))

    def test_same_resume_two_lines(self):
        targets, _d, _s, _w = resolve(cfg(rules="Системный аналитик: analyst\nаналитик: аналит"), MINE)
        self.assertEqual([(r.id, r.words) for r in targets], [(SA.id, ["analyst", "аналит"])])

    def test_mentioned(self):
        text = f"AI/LLM-инженер: ai\nhttps://hh.ru/resume/{SA.id}: \nPython Backend: x\nчто-то без двоеточия"
        self.assertEqual(mentioned(text, MINE), {AI.id, SA.id})  # «Python Backend» — неоднозначно


class PickTest(unittest.TestCase):
    def test_examples_from_request(self):
        p = picker()
        cases = {
            "Python AI Engineer": AI.id,  # ai и python поровну — выше в правилах
            "Python Backend Developer": PY.id,
            "System Analyst": SA.id,
            "Системный аналитик": SA.id,
            "Бизнес-аналитик": SA.id,
            "Senior ML Engineer": AI.id,
        }
        for title, expected in cases.items():
            self.assertEqual(p.pick(title).resume.id, expected, title)

    def test_more_words_win(self):
        choice = picker().pick("Backend-разработчик Python (LLM)")
        self.assertEqual(choice.resume.id, PY.id)
        self.assertIn("совпало больше слов", choice.why)

    def test_default_when_nothing(self):
        choice = picker(default="аналитик").pick("Менеджер проектов")
        self.assertEqual(choice.resume.id, SA.id)
        self.assertIn("по умолчанию", choice.why)

    def test_ai_decides_doubt_only(self):
        p = picker()
        p.ai = FakeAi(answer=PY.title)
        self.assertEqual(p.pick("System Analyst").resume.id, SA.id)  # одно резюме — нейросеть не спрашиваем
        self.assertEqual(p.ai.asked, [])
        choice = p.pick("Python AI Engineer", snippet="Нужен опыт FastAPI")
        self.assertEqual(choice.resume.id, PY.id)
        self.assertIn("нейросеть выбрала: так ближе", choice.why)
        self.assertEqual(p.ai.asked[0][1], [AI.title, PY.title])  # только подошедшие по словам
        p.pick("Python AI Engineer", snippet="Нужен опыт FastAPI")
        self.assertEqual(len(p.ai.asked), 1)  # та же вакансия — без второго запроса
        p.ai.answer = SA.title
        choice = p.pick("Product Owner")
        self.assertEqual(choice.resume.id, SA.id)
        self.assertEqual(len(p.ai.asked[-1][1]), 3)  # ни одного слова — из всех

    def test_ai_error_falls_back(self):
        p = picker(default="аналитик")
        p.ai = FakeAi(error=True)
        self.assertEqual(p.pick("Python AI Engineer").resume.id, AI.id)
        self.assertEqual(p.pick("Менеджер проектов").resume.id, SA.id)

    def test_for_chat_and_texts(self):
        p = picker()
        for resume in p.candidates():
            resume.text = f"текст {resume.title}"
        applied = {"1": {"title": "Аналитик данных", "company": "ООО «Ромашка»", "resume": AI.id}}
        resume, why = p.for_chat("Аналитик данных", "Ромашка", applied)
        self.assertEqual((resume.id, why), (AI.id, "с ним откликались на эту вакансию"))
        self.assertEqual(p.text_for(resume), f"текст {AI.title}")
        resume, why = p.for_chat("Аналитик данных", "Другая", applied)
        self.assertEqual(resume.id, SA.id)
        self.assertIn("в названии", why)
        self.assertEqual(p.text_for(MyResume("x" * 38, "Чужое")), p.default.text)  # без текста — по умолчанию

    def test_by_title(self):
        p = picker()
        self.assertEqual(p.by_title("Системный аналитик").id, SA.id)
        self.assertIsNone(p.by_title("Python"))  # подходит к нескольким


class AiAnswerTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_resume_pick('{"resume": 2, "reason": "аналитика"}', 3), (1, "аналитика"))
        self.assertEqual(parse_resume_pick('```json\n{"resume": "3", "reason": ""}\n```', 3), (2, ""))
        self.assertEqual(parse_resume_pick('{"resume": 1, "reason": "обрез', 2)[0], 0)
        with self.assertRaises(AiError):
            parse_resume_pick('{"resume": 4}', 3)
        with self.assertRaises(AiError):
            parse_resume_pick("не знаю", 3)


if __name__ == "__main__":
    unittest.main()

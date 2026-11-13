"""Весь путь программы на макете hh: настоящий браузер, но ни одного запроса наружу и ни одного настоящего отклика.

    python tests/e2e_mock_hh.py                 всё
    python tests/e2e_mock_hh.py chats letters   только эти части (их имена — в PARTS внизу)

Нужен браузер: Chrome, Edge или встроенный Chromium (python -m playwright install chromium). Отчёт — строками
ok/BAD, код возврата 1, если что-то не так. Что проверяется: tests/mock_hh.py отдаёт страницы и записывает всё, что
ушло бы работодателю, а здесь сверяем именно это — ответы анкет, письма, сообщения в чатах, файлы и журнал.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта
sys.path.insert(0, str(Path(__file__).resolve().parent))

for stream in (sys.stdout, sys.stderr):  # кириллица в консоли Windows
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from mock_hh import (DOCUMENTS, RESUME_ID, FakeLlm, MockChat, MockHh, YES_NO_DOCUMENTS, use_mock, vacancy,  # noqa: E402
                     write_config)

from hh_auto.app import EXIT_FATAL, EXIT_OK, fetch_resume, run_background, run_chats, run_market, run_responses  # noqa: E402
from hh_auto.core import control  # noqa: E402
from hh_auto.core.config import load_config  # noqa: E402
from hh_auto.core.logger import setup_logging  # noqa: E402
from hh_auto.hh.chats import ChatStats  # noqa: E402
from hh_auto.hh.runner import RunOptions, Stats  # noqa: E402

results: list[str] = []
LLM = FakeLlm()
BASE = Path(tempfile.mkdtemp(prefix="hh-e2e-"))
_folders = 0


def check(name: str, ok, detail="") -> None:
    line = ("ok " if ok else "BAD") + f" {name}" + (f" — {detail}" if detail and not ok else "")
    results.append(line)
    print(line, flush=True)


@contextmanager
def session(mock: MockHh, **overrides):
    """Отдельная папка данных и настройки на один сценарий — как у человека, который запускает программу впервые."""
    global _folders
    _folders += 1
    root = BASE / f"run{_folders}"
    root.mkdir()  # профиль браузера — тоже внутри: настоящий профиль пользователя не трогаем
    write_config(root / "config.toml", LLM.url, **overrides)
    os.environ["HH_AUTO_HOME"] = str(root)
    use_mock(mock)
    control.reset()
    LLM.requests.clear()
    yield root


def cfg_of(root: Path):
    return load_config(root / "config.toml")


def log_of(root: Path, name: str = "run") -> Path:
    return setup_logging(root / "logs", name=name)


def text_of(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def by_id(sent: list[dict], vacancy_id: str) -> list[dict]:
    return [r for r in sent if r.get("id") == vacancy_id]


def part_responses() -> None:
    mock = MockHh()
    with session(mock, ai__documents_ready=True, ai__context="Ожидаю от 150 000 ₽ на руки") as root:
        log_path = log_of(root)
        stats = Stats()
        code = run_responses(cfg_of(root), RunOptions(), root, stats, log_path)
        log = text_of(log_path)
        check("отклики: запуск закончился без ошибки", code == EXIT_OK, f"код {code}")
        check("отклики: наружу не ушло ни одного запроса", not mock.outside, mock.outside[:3])
        sent = mock.sent
        for vid, what in (("101", "окно с письмом"), ("103", "письмо за кнопкой «Добавить»"),
                          ("104", "письмо в отдельном окне"), ("105", "другой регион, удалёнка"),
                          ("201", "вторая страница выдачи")):
            records = by_id(sent, vid)
            check(f"отклики: {what}", len(records) == 1 and records[0]["letter"].startswith(FakeLlm.LETTER), records)
        quick = by_id(sent, "102")
        check("отклики: быстрый отклик и письмо следом",
              [r["flow"] for r in quick] == ["quick", "quick_letter"] and quick[1]["letter"].startswith(FakeLlm.LETTER), quick)
        check("отклики: переезд без удалёнки — отказ", not by_id(sent, "106"))
        check("отклики: просьба приложить скан — пропуск", not by_id(sent, "108"))
        check("отклики: без кнопки не откликаемся", not by_id(sent, "109"))
        check("отклики: слово-исключение в названии", not by_id(sent, "110"))
        check("отклики: отдельное окно — пропуск", not by_id(sent, "111"))
        form = by_id(sent, "107")
        check("анкета: отправлена", len(form) == 1, form)
        if form:
            answers = form[0]["answers"]
            check("анкета: документы — согласие по галочке (#18)", answers[0] == [YES_NO_DOCUMENTS[0]], answers[0])
            check("анкета: город из профиля hh", answers[1] == "Живу в городе Москва.", answers[1])
            check("анкета: технологии только из резюме", answers[2] == ["Python", "PostgreSQL"], answers[2])
            check("анкета: письмо приложено", form[0]["letter"].startswith(FakeLlm.LETTER))
        check("итоги: счётчики", stats.applied == 7 and stats.with_letter == 7 and stats.failed == 0,
              f"applied={stats.applied} letter={stats.with_letter} failed={stats.failed} {dict(stats.skipped)}")
        resume = (root / "data" / "resume.txt").read_text(encoding="utf-8")
        check("резюме: город и гражданство из профиля", resume.startswith("Проживает: Москва\nГражданство: Россия\n"),
              resume[:80])
        check("резюме: без телефона и почты", "123-45-67" not in resume and "ivan@" not in resume)
        check("резюме: нейросеть получила город", bool(LLM.asked("Проживает: Москва")))
        check("контекст: дошёл до нейросети", bool(LLM.asked("Ожидаю от 150 000")))
        check("журнал: причина пропуска вложения", "просят приложить документ" in log)
        book = root / "logs" / "responses.xlsx"
        check("отчёт: Excel создан", book.exists())
        if book.exists():
            from openpyxl import load_workbook

            sheets = load_workbook(book, read_only=True).sheetnames
            check("отчёт: листы на месте", {"Сводка", "Откликнулся", "Пропущенные"} <= set(sheets), sheets)
        check("память: state.json", (root / "data" / "state.json").exists())
        check("статистика: stats.json", (root / "data" / "stats.json").exists())
        check("анализ: вакансии выдачи записаны", (root / "data" / "market.json").exists())

        # второй запуск: на те же вакансии не откликаемся
        before = len(mock.sent)
        stats = Stats()
        code = run_responses(cfg_of(root), RunOptions(), root, stats, log_of(root))
        check("повтор: второй запуск без новых откликов", code == EXIT_OK and len(mock.sent) == before,
              f"код {code}, новых {len(mock.sent) - before}")


def part_documents() -> None:
    """Галочка выключена — анкету не отправляем и говорим, что включить; слова в контексте тоже считаются."""
    pages = [[vacancy(301, "Анкета с документами", "questions", questions=[
        {"kind": "single", "text": DOCUMENTS, "options": YES_NO_DOCUMENTS}])]]
    mock = MockHh(pages=pages)
    with session(mock) as root:
        log_path = log_of(root)
        stats = Stats()
        run_responses(cfg_of(root), RunOptions(), root, stats, log_path)
        check("документы: без разрешения анкета не уходит", not mock.sent, mock.sent)
        check("документы: журнал говорит, что включить", "Готов предоставить документы" in text_of(log_path))
    mock = MockHh(pages=[[vacancy(302, "Анкета с документами", "questions", questions=[
        {"kind": "single", "text": DOCUMENTS, "options": YES_NO_DOCUMENTS}])]])
    with session(mock, ai__context="Я готов предоставить все необходимые документы") as root:
        run_responses(cfg_of(root), RunOptions(), root, Stats(), log_of(root))
        check("документы: слова в контексте — разрешение",
              len(mock.sent) == 1 and mock.sent[0]["answers"][0] == [YES_NO_DOCUMENTS[0]], mock.sent)


def part_letters() -> None:
    def pages() -> list[list[dict]]:
        return [[vacancy(401, "Инженер данных", "modal", company="Заря")]]

    mock = MockHh(pages=pages())
    with session(mock, letter__mode="template", letter__template_text="Отклик на {vacancy} в {company}, письмо из настроек.",
                 ai__enabled=False) as root:
        run_responses(cfg_of(root), RunOptions(), root, Stats(), log_of(root))
        check("письмо: готовый текст с подстановкой",
              mock.sent and mock.sent[0]["letter"] == "Отклик на Инженер данных в Заря, письмо из настроек.", mock.sent)

    mock = MockHh(pages=pages())
    with session(mock, letter__mode="generate", ai__enabled=False, letter__generate_timeout=8.0) as root:
        run_responses(cfg_of(root), RunOptions(), root, Stats(), log_of(root))
        check("письмо: кнопка hh «Сгенерировать»", mock.sent and "Сгенерировать" in mock.sent[0]["letter"], mock.sent)

    mock = MockHh(pages=pages())
    mock.no_generate = True
    with session(mock, letter__mode="generate", ai__enabled=True, letter__generate_timeout=2.0) as root:
        run_responses(cfg_of(root), RunOptions(), root, Stats(), log_of(root))
        check("письмо: без подписки пишет нейросеть", mock.sent and mock.sent[0]["letter"].startswith(FakeLlm.LETTER), mock.sent)

    mock = MockHh(pages=pages())
    mock.no_generate = True
    with session(mock, letter__mode="generate", ai__enabled=False, letter__generate_timeout=2.0,
                 letter__fallback_text="Запасной текст письма, он достаточно длинный.") as root:
        run_responses(cfg_of(root), RunOptions(), root, Stats(), log_of(root))
        check("письмо: запасной текст", mock.sent and mock.sent[0]["letter"].startswith("Запасной"), mock.sent)

    mock = MockHh(pages=[[vacancy(402, "Без письма", "optional")]])
    with session(mock, letter__mode="none", ai__enabled=False) as root:
        run_responses(cfg_of(root), RunOptions(), root, Stats(), log_of(root))
        check("письмо: режим «без письма»", mock.sent and mock.sent[0]["letter"] == "", mock.sent)

    # #19: просьба «не упоминать компанию и вакансию» — в поле «Что ещё знать о вас» и в своём промпте. Макет
    # нейросети не слушается и называет их (как qwen3:8b в issue), а программа должна это убрать до отправки
    rule = ("ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА ДЛЯ СОПРОВОДИТЕЛЬНОГО ПИСЬМА: НИКОГДА НЕ ПИШИ НАЗВАНИЕ ВАКАНСИИ В ПИСЬМЕ, пиши как живой "
            "человек, не нужно упоминать компанию и вакансию в письме.")
    mock = MockHh(pages=pages())
    with session(mock) as root:
        run_responses(cfg_of(root), RunOptions(), root, Stats(), log_of(root))
        letter = mock.sent[0]["letter"] if mock.sent else ""
        check("#19 без просьбы: вакансия и компания названы", "Заря" in letter and "Инженер данных" in letter, letter)
    for label, overrides in (("контекст", {"ai__context": rule}), ("свой промпт", {"letter__ai_prompt": rule})):
        mock = MockHh(pages=pages())
        with session(mock, **overrides) as root:
            run_responses(cfg_of(root), RunOptions(), root, Stats(), log_of(root))
            asked = LLM.asked("Ты пишешь сопроводительное")
            letter = mock.sent[0]["letter"] if mock.sent else ""
            check(f"#19 {label}: просьба дошла до нейросети", asked and "НИКОГДА НЕ ПИШИ" in asked[0])
            check(f"#19 {label}: в отправленном письме нет вакансии и компании",
                  letter.startswith(FakeLlm.LETTER) and "Заря" not in letter and "Инженер" not in letter
                  and "ваканси" not in letter.lower(), letter)


def part_dry_run() -> None:
    mock = MockHh()
    with session(mock) as root:
        stats = Stats()
        code = run_responses(cfg_of(root), RunOptions(dry_run=True), root, stats, log_of(root))
        check("пробный прогон: ничего не отправлено", code == EXIT_OK and not mock.sent, mock.sent)
        check("пробный прогон: вакансии найдены", stats.dry_run >= 8, stats.dry_run)
        check("пробный прогон: память не записана", not (root / "data" / "state.json").exists()
              or '"101"' not in (root / "data" / "state.json").read_text(encoding="utf-8"))


def part_hide() -> None:
    """[filters] hide_unwanted: отсеянные фильтрами вакансии скрываются на hh («глаз» → «Скрыть эту вакансию»),
    остальные — как обычно. В пробном прогоне ничего не скрывается."""
    def pages() -> list[list[dict]]:
        return [[vacancy(601, "Python-разработчик", "modal"), vacancy(602, "Преподаватель Python", "modal"),
                 vacancy(603, "Junior Python-разработчик", "modal"), vacancy(604, "Бэкенд-разработчик", "modal",
                                                                             company="Сбер Технологии")]]

    settings = {"filters__hide_unwanted": True, "filters__levels": ["middle", "senior"],
                "filters__exclude_company_words": ["сбер"]}
    mock = MockHh(pages=pages())
    with session(mock, **settings) as root:
        stats = Stats()
        code = run_responses(cfg_of(root), RunOptions(dry_run=True), root, stats, log_of(root))
        check("скрытие: пробный прогон ничего не скрывает", code == EXIT_OK and not mock.hidden, mock.hidden)
    mock = MockHh(pages=pages())
    with session(mock, **settings) as root:
        log_path = log_of(root)
        stats = Stats()
        code = run_responses(cfg_of(root), RunOptions(), root, stats, log_path)
        check("скрытие: запуск без ошибок", code == EXIT_OK, f"код {code}")
        check("скрытие: слово в названии, уровень, компания", sorted(mock.hidden) == ["602", "603", "604"], mock.hidden)
        check("скрытие: подходящая — отклик, не скрыта", len(by_id(mock.sent, "601")) == 1 and "601" not in mock.hidden)
        check("скрытие: счётчик в итогах", stats.hidden == 3 and "скрыто на hh: 3" in text_of(log_path), stats.hidden)
    mock = MockHh(pages=pages())
    with session(mock, filters__levels=["middle", "senior"]) as root:
        run_responses(cfg_of(root), RunOptions(), root, Stats(), log_of(root))
        check("скрытие: выключено — только пропуск", not mock.hidden, mock.hidden)


def part_failures() -> None:
    # лимит hh: останавливаемся сразу и больше не кликаем
    mock = MockHh(pages=[[vacancy(501, "Первая", "modal"), vacancy(502, "Лимит", "limit"), vacancy(503, "Лишняя", "modal")]])
    with session(mock) as root:
        log_path = log_of(root)
        run_responses(cfg_of(root), RunOptions(), root, Stats(), log_path)
        check("лимит hh: дальше не идём", [r["id"] for r in mock.sent] == ["501"], mock.sent)
        check("лимит hh: в журнале понятно", "лимит откликов исчерпан" in text_of(log_path))

    # сессия истекла посреди работы
    mock = MockHh(pages=[[vacancy(511, "Первая", "modal"), vacancy(512, "Вторая", "modal")]])
    mock.expire_after_sent = 1
    with session(mock) as root:
        log_path = log_of(root)
        code = run_responses(cfg_of(root), RunOptions(), root, Stats(), log_path)
        check("сессия истекла: остановка с ошибкой", code == EXIT_FATAL, code)
        check("сессия истекла: в журнале понятно", "сессия hh истекла" in text_of(log_path))

    # нейросеть недоступна: письма из неё — фатально, понятное сообщение
    mock = MockHh(pages=[[vacancy(521, "Письмо", "modal")]])
    with session(mock, ai__base_url="http://127.0.0.1:9/v1", ai__timeout=2.0) as root:
        log_path = log_of(root)
        stats = Stats()
        run_responses(cfg_of(root), RunOptions(), root, stats, log_path)
        check("нейросеть молчит: письмо не отправлено", not mock.sent, mock.sent)
        check("нейросеть молчит: в журнале понятно", "нейросеть не написала письмо" in text_of(log_path))

    # лимит этого запуска
    mock = MockHh(pages=[[vacancy(531, "Первая", "modal"), vacancy(532, "Вторая", "modal")]])
    with session(mock) as root:
        run_responses(cfg_of(root), RunOptions(limit=1), root, Stats(), log_of(root))
        check("лимит запуска: один отклик", len(mock.sent) == 1, mock.sent)

    # не вошли: без окна программа не ждёт входа, а объясняет
    mock = MockHh()
    mock.role = "anonymous"
    with session(mock) as root:
        log_path = log_of(root)
        code = run_responses(cfg_of(root), RunOptions(), root, Stats(), log_path)
        check("нет входа: без окна — ошибка, а не ожидание", code == EXIT_FATAL and "вход не выполнен" in text_of(log_path), code)


def part_chats() -> None:
    mock = MockHh()
    with session(mock, ai__documents_ready=True) as root:
        log_path = log_of(root, "chats")
        stats = ChatStats()
        code = run_chats(cfg_of(root), False, root, stats, log_path)
        log = text_of(log_path)
        sent = {}
        for record in mock.chat_sent:
            sent.setdefault(record["chat"], []).append(record)
        check("чаты: запуск без ошибки", code == EXIT_OK and stats.failed == 0, f"код {code}, ошибок {stats.failed}")
        check("чаты: отказ прочитан", stats.opened == 1, stats.opened)
        check("чаты: в отказ ничего не пишем", "1" not in sent)
        robot = [r["text"] for r in sent.get("2", [])]
        check("чаты: анкета робота — город из профиля", robot[:1] == ["Живу в городе Москва."], robot)
        check("чаты: анкета робота — документы по галочке", robot[1:2] == ["Да, готов предоставить документы по запросу."], robot)
        check("чаты: «созвониться» остаётся вам", "3" not in sent, sent.get("3"))
        check("чаты: паспорт остаётся вам", "4" not in sent, sent.get("4"))
        check("чаты: кнопка «Да, готов» нажата", [r["kind"] for r in sent.get("5", [])] == ["button"]
              and sent["5"][0]["text"] == "Да, готов", sent.get("5"))
        waiting = "\n".join(stats.other_unread)
        check("чаты: ждут вашего ответа", "Приглашение" in waiting and "Паспорт" in waiting, waiting)
        check("чаты: ответы записаны", (root / "data" / "chat_answers.jsonl").exists())
        check("чаты: наружу не ушло ни одного запроса", not mock.outside, mock.outside[:3])
        check("чаты: журнал", "ИИ ответил на анкеты" in log)

    mock = MockHh()
    with session(mock) as root:
        stats = ChatStats()
        run_chats(cfg_of(root), True, root, stats, log_of(root, "chats"))
        check("чаты «только показать»: ничего не отправлено", not mock.chat_sent, mock.chat_sent)

    mock = MockHh()
    with session(mock, chats__mode="rejections") as root:
        stats = ChatStats()
        run_chats(cfg_of(root), False, root, stats, log_of(root, "chats"))
        check("чаты «только отказы»: другие не тронуты", not mock.chat_sent and stats.opened == 1, mock.chat_sent)
        check("чаты «только отказы»: нейросеть не нужна", not LLM.requests)

    mock = MockHh(chats=[MockChat("7", "Документы", "Компания", ["Готовы ли вы предоставить выписку из ЭТК?"])])
    with session(mock) as root:
        stats = ChatStats()
        run_chats(cfg_of(root), False, root, stats, log_of(root, "chats"))
        check("чаты: документы без разрешения остаются вам", not mock.chat_sent and "Документы" in "\n".join(stats.other_unread),
              mock.chat_sent)


def part_resume() -> None:
    mock = MockHh()
    with session(mock) as root:
        code = fetch_resume(cfg_of(root), root, log_of(root, "resume"))
        text = (root / "data" / "resume.txt").read_text(encoding="utf-8")
        check("«Загрузить резюме»: без ошибки", code == EXIT_OK, code)
        check("«Загрузить резюме»: текст и город", "Проживает: Москва" in text and "FastAPI" in text, text[:100])

    # поиск по запросу без ссылки на резюме: резюме берётся из «Мои резюме»
    mock = MockHh()
    with session(mock, search__mode="query", search__query="python") as root:
        path = root / "config.toml"
        path.write_text(path.read_text(encoding="utf-8").replace(f"?resume={RESUME_ID}&from=resumelist", ""), encoding="utf-8")
        code = fetch_resume(cfg_of(root), root, log_of(root, "resume"))
        text = (root / "data" / "resume.txt").read_text(encoding="utf-8") if (root / "data" / "resume.txt").exists() else ""
        check("без ссылки на резюме: берётся первое из списка", code == EXIT_OK and "FastAPI" in text, text[:100])


def part_background() -> None:
    mock = MockHh()
    with session(mock, raising__enabled=True) as root:
        result: dict = {}
        code = run_background(cfg_of(root), root, log_of(root, "raise"), result, tasks=["raise"], force_raise=True)
        outcome = result.get("raise")
        check("подъём резюме: запрос ушёл", code == EXIT_OK and mock.touches, f"код {code}, {mock.touches}")
        check("подъём резюме: итог", outcome is not None and outcome.raised, outcome)
        check("подъём резюме: срок записан", (root / "data" / "raising.json").exists())

    mock = MockHh()
    mock.touch_status = 409
    with session(mock, raising__enabled=True) as root:
        result = {}
        run_background(cfg_of(root), root, log_of(root, "raise"), result, tasks=["raise"], force_raise=True)
        outcome = result.get("raise")
        check("подъём резюме: «рано» — не ошибка", outcome is not None and outcome.early and not outcome.failed, outcome)

    mock = MockHh(chats=[MockChat("9", "Приглашение", "Компания", ["Приглашение на собеседование"])])
    mock.chats[0].unread = True
    with session(mock, watch__invitations=True) as root:
        result = {}
        code = run_background(cfg_of(root), root, log_of(root, "watch"), result, tasks=["invitations"])
        check("фоновая проверка: приглашение без открытия чата", code == EXIT_OK and "invitations" in result and not mock.chat_sent,
              result)


def part_market() -> None:
    mock = MockHh()
    with session(mock) as root:
        result: dict = {}
        code = run_market(cfg_of(root), root, log_of(root, "market"), "python", result)
        check("проверка рынка: без откликов", code == EXIT_OK and not mock.sent, f"код {code}")
        check("проверка рынка: вакансии записаны", result.get("seen", 0) >= 10, result)
        from hh_auto.analysis import market, report, resume

        profile = resume.parse("Python-разработчик\nОпыт работы: 3 года\nНавыки\nPython\nPostgreSQL\n")
        built = report.build(profile, market.MarketStore(root).vacancies())
        check("анализ резюме: отчёт построен", built.score >= 0 and report.to_text(built), built.score)


def part_captcha() -> None:
    """Капча на «Откликнуться»: человек решил её, hh сам дослал отклик и спрятал вакансию из выдачи. Отклик
    засчитан, а дальше отклики идут сериями с перерывами. Последней частью: после капчи паузы до 20 минут длиннее."""
    import json

    import hh_auto.app as app
    from dataclasses import replace
    from hh_auto.web import browser

    mock = MockHh(pages=[[vacancy(301, "Python с капчей", "captcha"), vacancy(302, "Python после капчи", "modal"),
                          vacancy(303, "Ещё Python", "modal"), vacancy(304, "И ещё Python", "modal")]])
    mock.hide_responded = True
    breather, browser.BREATHER = browser.BREATHER, (0, 0)
    with session(mock, limits__captcha_rest=0.01) as root:
        mocked = app.launch

        def launch(pw, cfg):  # браузер без окна, но капчу «решает человек» (окно для неё не нужно)
            context = mocked(pw, cfg)
            browser._cfg = replace(cfg, browser=replace(cfg.browser, headless=False))
            return context

        app.launch = launch
        try:
            stats = Stats()
            log_path = log_of(root)
            code = run_responses(cfg_of(root), RunOptions(), root, stats, log_path)
        finally:
            app.launch, browser.BREATHER = mocked, breather
        log = text_of(log_path)
        check("капча: запуск без ошибки", code == EXIT_OK, f"код {code}")
        check("капча: отклик ушёл один раз", len(by_id(mock.sent, "301")) == 1, mock.sent)
        check("капча: отклик после капчи засчитан", "hh принял его после капчи" in log and stats.applied == 4,
              f"отправлено {stats.applied}")
        state = json.loads((root / "data" / "state.json").read_text(encoding="utf-8"))
        check("капча: отклик в памяти", "301" in state.get("applied", {}), list(state.get("applied", {})))
        pacing = json.loads((root / "data" / "pacing.json").read_text(encoding="utf-8"))
        check("капча: серия запомнена", pacing.get("series") == 2, pacing)
        check("капча: перерывы после капчи и после серии",
              "перерыв" in log and "после капчи" in log and "после серии из 2 откл." in log)


class FakeBot:
    """Telegram для проверки: запоминает, что прислал бот, и «отвечает» по сценарию — по ответу на каждую
    картинку. Сообщение из чужого чата программа принимать не должна."""

    def __init__(self, chat: str, replies: list[str]):
        import threading

        self.chat, self.replies = chat, list(replies)
        self.photos: list[tuple[bytes, str]] = []
        self.texts: list[str] = []
        self.answered = 0
        self.update = 100
        self.lock = threading.Lock()

    def __call__(self, token, method, params=None, files=None, timeout=20):
        params = params or {}
        with self.lock:
            if method == "sendPhoto":
                self.photos.append((files["photo"][1], params.get("caption", "")))
                return {}
            if method == "sendMessage":
                self.texts.append(params.get("text", ""))
                return {}
            if method == "getUpdates" and str(params.get("offset")) == "-1":  # «пропустить старое»
                return [self._message(self.chat, "старое сообщение")]
            if method == "getUpdates" and self.answered < len(self.photos) and self.answered < len(self.replies):
                reply = self.replies[self.answered]
                self.answered += 1
                return [self._message("999", reply), self._message(self.chat, reply)]
        time.sleep(0.2)  # «long polling»: новых сообщений нет
        return []

    def _message(self, chat: str, text: str) -> dict:
        self.update += 1
        return {"update_id": self.update, "message": {"chat": {"id": int(chat)}, "text": text}}


def part_captcha_telegram() -> None:
    """Капча в Telegram без окна браузера (headless): бот прислал картинку, первый ответ неверный — пришла новая
    картинка, второй верный — программа ввела его, hh принял отклик. Ответ из чужого чата не принят.
    Ответ как у hh: два русских слова, заглавная, с телефона — в кавычках, с двойным пробелом и точкой;
    вводится нажатиями клавиш (keydown на каждую букву, и русскую тоже), а не вставкой."""
    import hh_auto.core.notify as notify
    import hh_auto.web.captcha as phone
    from hh_auto.web import browser

    mock = MockHh(pages=[[vacancy(311, "Python с капчей из Telegram", "captcha"), vacancy(312, "Ещё Python", "modal")]])
    mock.captcha_answer = "Вынесет путь 7k"
    bot = FakeBot("4242", ["неверно", "«Вынесет  путь 7k»."])
    real_call, real_telegram = phone.telegram_call, notify.telegram
    phone.telegram_call, notify.telegram = bot, lambda *_args: True  # и итоги запуска — не в настоящий Telegram
    breather, browser.BREATHER = browser.BREATHER, (0, 0)
    with session(mock, limits__captcha_rest=0, notify__telegram=True, notify__telegram_token="123:fake",
                 notify__telegram_chat_id="4242") as root:
        try:
            stats = Stats()
            log_path = log_of(root)
            code = run_responses(cfg_of(root), RunOptions(), root, stats, log_path)
            deadline = time.monotonic() + 10  # «✓ Капча пройдена» уходит из потока бота
            while time.monotonic() < deadline and not any("пройдена" in t for t in bot.texts):
                time.sleep(0.2)
        finally:
            phone.telegram_call, notify.telegram, browser.BREATHER = real_call, real_telegram, breather
            browser._captchas.clear()  # «после капчи паузы длиннее» — не для следующих частей
            if phone._relay is not None:
                phone._relay.retire()
                phone._relay = None
        log = text_of(log_path)
        check("капча в Telegram: запуск без ошибки", code == EXIT_OK, f"код {code}")
        check("капча в Telegram: картинка пришла, после неверного ответа — ещё раз", len(bot.photos) == 2,
              [caption for _png, caption in bot.photos])
        check("капча в Telegram: картинка — PNG", all(png.startswith(b"\x89PNG") for png, _caption in bot.photos))
        check("капча в Telegram: в подписи — что делать", "Ответьте символами" in bot.photos[0][1] if bot.photos
              else False)
        check("капча в Telegram: ответы введены в форму, чужой чат и старое — нет",
              mock.captcha_tries == ["неверно", "Вынесет путь 7k"], mock.captcha_tries)
        check("капча в Telegram: введено нажатиями клавиш, по букве", mock.captcha_keys == mock.captcha_tries,
              mock.captcha_keys)
        check("капча в Telegram: «не подошло» в логе", "ответ из Telegram не подошёл" in log)
        check("капча в Telegram: бот сообщил, что капча пройдена", any("Капча пройдена" in t for t in bot.texts),
              bot.texts)
        check("капча в Telegram: отклик ушёл один раз", len(by_id(mock.sent, "311")) == 1, mock.sent)
        check("капча в Telegram: оба отклика засчитаны", stats.applied == 2, f"отправлено {stats.applied}")


PARTS = {"responses": part_responses, "documents": part_documents, "letters": part_letters, "dry_run": part_dry_run,
         "hide": part_hide, "failures": part_failures, "chats": part_chats, "resume": part_resume, "background": part_background,
         "market": part_market, "captcha_telegram": part_captcha_telegram, "captcha": part_captcha}


def main() -> int:
    wanted = sys.argv[1:] or list(PARTS)
    started = time.monotonic()
    for name in wanted:
        print(f"--- {name}", flush=True)
        try:
            PARTS[name]()
        except Exception as e:  # чтобы одна упавшая часть не прятала остальные
            import traceback

            check(f"{name}: часть упала", False, f"{type(e).__name__}: {e}")
            traceback.print_exc()
    bad = [line for line in results if line.startswith("BAD")]
    print(f"\nИтог: {len(results) - len(bad)} ok, {len(bad)} BAD за {time.monotonic() - started:.0f} с")
    LLM.close()
    os._exit(1 if bad else 0)  # браузеры уже закрыты: не ждём потоки логов


if __name__ == "__main__":
    main()

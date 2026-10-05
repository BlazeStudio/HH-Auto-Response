"""Песочница ИИ: проверить ответы нейросети на анкеты, ничего не отправляя в hh.

    python ai_test.py                  диалог: вы пишете за работодателя, ИИ отвечает
    python ai_test.py --demo           прогнать готовую анкету из примеров
    python ai_test.py --show-prompt    показать, что получает нейросеть, и выйти
    python ai_test.py --form           как ИИ заполнит пример анкеты при отклике (текст + варианты)
    python ai_test.py --letter         какое сопроводительное письмо ИИ напишет к примеру вакансии
    python ai_test.py --fetch-resume   сначала загрузить свежее резюме с hh (откроется браузер)

В диалоге:  текст — сообщение от «Робота-рекрутера»;  hr: текст — от живого HR;
            /prompt — что получает нейросеть;  /raw — показывать сырой ответ модели;
            /new — новый диалог;  /q — выход.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hh_auto.ai import AiError
from hh_auto.ai_playground import (ROBOT, SAMPLE_FORM, SAMPLE_VACANCY, SAMPLES, Playground, Turn, fill_sample_form,
                                   write_sample_letter)
from hh_auto.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parent


def show(turn: Turn, raw: bool) -> None:
    d = turn.decision
    print(f"  ИИ: {d.kind} — {turn.action}  ({turn.seconds:.1f} с)")
    if d.question:
        print(f"      вопрос: «{d.question}»")
    if d.reply and d.kind == "question":
        print(f"      ответ:  «{d.reply}»")
    elif d.reply:
        print(f"      (черновик ответа, который НЕ был бы отправлен: «{d.reply}»)")
    if turn.note:
        print(f"      страховка: {turn.note}")
    if raw and turn.raw:
        print(f"      сырой ответ модели: {turn.raw}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Песочница ИИ-ответов на анкеты (ничего не отправляет)")
    p.add_argument("--config", type=Path, default=ROOT / "config.toml")
    p.add_argument("--demo", action="store_true", help="прогнать готовые примеры сообщений")
    p.add_argument("--show-prompt", action="store_true", help="показать, что получает нейросеть, и выйти")
    p.add_argument("--fetch-resume", action="store_true", help="загрузить свежее резюме с hh")
    p.add_argument("--form", action="store_true", help="заполнить пример анкеты при отклике")
    p.add_argument("--letter", action="store_true", help="написать письмо к примеру вакансии")
    p.add_argument("--raw", action="store_true", help="показывать сырой ответ модели")
    args = p.parse_args()

    try:
        cfg = load_config(args.config, check_search_url=False)
    except ConfigError as e:
        print(f"Ошибка конфигурации: {e}")
        return 2
    if args.fetch_resume:
        from hh_auto.app import fetch_resume
        from hh_auto.logger import setup_logging

        log_path = setup_logging(ROOT / "logs", name="resume")
        if fetch_resume(cfg, ROOT, log_path) != 0:
            return 1

    try:
        pg = Playground(cfg, ROOT)
    except AiError as e:
        print(f"Нейросеть недоступна: {e}")
        return 1
    print("═" * 70)
    print("Песочница ИИ — в hh ничего не отправляется")
    for line in pg.describe():
        print("  " + line)
    print("═" * 70)
    if args.show_prompt:
        print(pg.preview())
        return 0

    if args.letter:
        title, company, description = SAMPLE_VACANCY
        print(f"Пример вакансии: «{title}» — {company}\n{description}\n")
        try:
            text, seconds = write_sample_letter(pg)
        except AiError as e:
            print(f"  ошибка ИИ: {e}")
            return 1
        print(f"Письмо ИИ ({seconds:.1f} с, {len(text)} симв.):\n{text}")
        return 0

    if args.form:
        print("Пример анкеты при отклике (ничего не отправляется):")
        for n, q in enumerate(SAMPLE_FORM, 1):
            print(f"  {n}. {q.text}" + (f"  [{' | '.join(q.options)}]" if q.options else ""))
        try:
            fill, seconds = fill_sample_form(pg)
        except AiError as e:
            print(f"  ошибка ИИ: {e}")
            return 1
        print(f"\nЗаполнено за {seconds:.1f} с" + ("" if fill.can_answer else f" — ИИ отказался: {fill.reason}"))
        for answer in fill.answers:
            print(f"  {answer.question.text}\n    → {answer.describe()}")
        return 0

    if args.demo:
        for sender, text in SAMPLES:
            print(f"\n{sender}: {text}")
            try:
                show(pg.ask(sender, text), args.raw)
            except AiError as e:
                print(f"  ошибка ИИ: {e}")
        return 0

    print(__doc__.split("В диалоге:")[1].strip())
    raw = args.raw
    while True:
        try:
            line = input("\nработодатель> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/q", "/quit", "/exit"):
            return 0
        if line == "/prompt":
            print(pg.preview())
            continue
        if line == "/raw":
            raw = not raw
            print(f"  сырой ответ модели: {'показываю' if raw else 'скрываю'}")
            continue
        if line == "/new":
            pg.reset()
            print("  новый диалог")
            continue
        sender, text = (("HR", line[3:]) if line.lower().startswith("hr:") else (ROBOT, line))
        try:
            show(pg.ask(sender, text.strip()), raw)
        except AiError as e:
            print(f"  ошибка ИИ: {e}")


if __name__ == "__main__":
    sys.exit(main())

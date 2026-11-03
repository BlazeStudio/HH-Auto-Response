"""Анализ резюме в консоли — то же, что вкладка «Анализ резюме» в окне программы.

    python scripts/analyze_resume.py резюме.pdf                     сравнить с уже собранными вакансиями
    python scripts/analyze_resume.py резюме.pdf --scan "python"     сначала пролистать выдачу по запросу
    python scripts/analyze_resume.py --scan ""                      …или по поиску из config.toml
    python scripts/analyze_resume.py                                резюме с hh, сохранённое при откликах

Резюме — PDF (на hh: «Скачать» → PDF), DOCX или TXT. Вакансии копятся при обычных откликах и при --scan
(программа листает выдачу, ни на что не откликаясь) в data/market.json. Отчёт печатается в консоль.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # пакет hh_auto лежит в папке проекта

from hh_auto.analysis import market, report, resume  # noqa: E402
from hh_auto.app import EXIT_OK, run_market  # noqa: E402
from hh_auto.core.config import ConfigError, load_config  # noqa: E402
from hh_auto.core.logger import setup_logging  # noqa: E402
from hh_auto.core.paths import app_root  # noqa: E402
from hh_auto.reports.results import ResultsBook  # noqa: E402

ROOT = app_root()  # hh-data/ в папке проекта или HH_AUTO_HOME


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Сравнить резюме с вакансиями hh: навыки, опыт, зарплата, советы")
    p.add_argument("file", nargs="?", type=Path, help="резюме: PDF, DOCX или TXT (по умолчанию — сохранённое с hh)")
    p.add_argument("--scan", metavar="ЗАПРОС",
                   help="сначала пролистать выдачу по запросу (\"\" — по поиску из config.toml)")
    p.add_argument("--pages", type=int, default=5, help="сколько страниц выдачи пролистать (по 100 вакансий)")
    p.add_argument("--query", metavar="ВЫДАЧА", default="", help="сравнивать только с вакансиями этой выдачи")
    p.add_argument("--config", type=Path, default=ROOT / "config.toml", help="путь к config.toml")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):  # кириллица в консоли Windows
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.file:
        try:
            text = resume.read_file(args.file)
        except resume.ResumeError as e:
            print(f"Не получилось прочитать резюме: {e}", file=sys.stderr)
            return 2
    else:
        saved = resume.load(ROOT)
        cache = ROOT / "data" / "resume.txt"
        text = saved["text"] if saved else cache.read_text(encoding="utf-8") if cache.exists() else ""
        if not text:
            print("Резюме не указано: python scripts/analyze_resume.py резюме.pdf", file=sys.stderr)
            return 2
    if args.scan is not None:
        try:
            cfg = load_config(args.config, check_search_url=not args.scan)
        except ConfigError as e:
            print(f"Ошибка конфигурации: {e}", file=sys.stderr)
            return 2
        log_path = setup_logging(ROOT / "logs", False, name="market")
        if run_market(cfg, ROOT, log_path, args.scan, max(1, min(args.pages, 20))) != EXIT_OK:
            return 1
    vacancies = market.MarketStore(ROOT).vacancies(label=args.query or None)
    applied = {r.get("id") for r in ResultsBook(ROOT).records() if r.get("category") == "applied"}
    print(report.to_text(report.build(resume.parse(text), vacancies, applied)))
    if not vacancies:
        print("\nВакансий для сравнения нет — добавьте --scan \"запрос\" или запустите отклики.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

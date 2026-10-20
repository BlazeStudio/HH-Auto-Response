"""config.toml как текст: правка значений на месте и дописывание новых параметров.

Раньше приложение переписывало config.toml целиком: пропадали комментарии и порядок строк. Теперь меняются
только строки, у которых изменилось значение. Параметры, которых в файле ещё нет (появились в новой версии),
дописываются в конец своего раздела вместе с пояснением из config.example.toml. Комментарии, порядок,
незнакомые программе параметры остаются как были.
"""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

_HEADER = re.compile(r"^[ \t]*\[[ \t]*([A-Za-z0-9_-]+)[ \t]*\][ \t]*(#.*)?$")
_KEY = re.compile(r"^[ \t]*([A-Za-z0-9_-]+)[ \t]*=[ \t]*")
_CODE_IN_COMMENT = re.compile(r'^\s*#\s*([A-Za-z0-9_-]+\s*=|""")')


@dataclass
class _Entry:
    section: str  # "" — параметры до первого раздела
    key: str
    line_start: int  # начало строки с ключом
    value_start: int
    value_end: int
    line_end: int  # после перевода строки, которым кончается значение (с комментарием в конце строки)


def toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    # JSON-строка — валидная базовая строка TOML (те же экранирования \" \\ \n \uXXXX)
    return json.dumps(str(value), ensure_ascii=False)


def _scan_value(text: str, i: int) -> int:
    """Конец значения TOML, начинающегося с позиции i (строки, многострочные строки, массивы, таблицы)."""
    n = len(text)
    if text.startswith('"""', i) or text.startswith("'''", i):
        quote, j = text[i:i + 3], i + 3
        while j < n:
            if quote == '"""' and text[j] == "\\":
                j += 2
                continue
            if text.startswith(quote, j):
                k = j + 3
                while k < n and text[k] == quote[0] and k - j < 5:  # до двух кавычек сразу перед закрывающими
                    k += 1
                return k
            j += 1
        return n
    if text.startswith('"', i):
        j = i + 1
        while j < n and text[j] != "\n":
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == '"':
                return j + 1
            j += 1
        return j
    if text.startswith("'", i):
        j = text.find("'", i + 1)
        line = text.find("\n", i)
        return (j + 1) if j != -1 and (line == -1 or j < line) else (line if line != -1 else n)
    if text.startswith("[", i) or text.startswith("{", i):
        opening, closing = ("[", "]") if text[i] == "[" else ("{", "}")
        depth, j = 0, i
        while j < n:
            c = text[j]
            if c in "\"'":
                j = _scan_value(text, j)
                continue
            if c == "#":
                line = text.find("\n", j)
                j = n if line == -1 else line
                continue
            if c == opening:
                depth += 1
            elif c == closing:
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return n
    m = re.compile(r"[^\s#,\]}]*").match(text, i)
    return m.end()


def _parse(text: str) -> tuple[list[_Entry], dict[str, tuple[int, int]]]:
    """Ключи с их местами в тексте и разделы: имя → (начало строки заголовка, конец строки заголовка)."""
    entries: list[_Entry] = []
    sections: dict[str, tuple[int, int]] = {}
    section, pos, n = "", 0, len(text)
    while pos < n:
        end = text.find("\n", pos)
        end = n if end == -1 else end
        line = text[pos:end].rstrip("\r")
        header = _HEADER.match(line)
        if header:
            section = header[1]
            sections.setdefault(section, (pos, min(end + 1, n)))
            pos = end + 1
            continue
        key = _KEY.match(line)
        if key:
            start = pos + key.end()
            stop = _scan_value(text, start)
            line_end = text.find("\n", stop)
            line_end = n if line_end == -1 else line_end + 1
            entries.append(_Entry(section, key[1], pos, start, stop, line_end))
            pos = line_end
            continue
        pos = end + 1
    return entries, sections


def _same(raw_value: str, value) -> bool:
    try:
        return tomllib.loads("v = " + raw_value)["v"] == value
    except (tomllib.TOMLDecodeError, ValueError):
        return False


def example_notes(example: str) -> dict[tuple[str, str], tuple[list[str], str]]:
    """Пояснения к параметрам из config.example.toml: (раздел, ключ) → (строки комментария над ключом,
    комментарий в конце строки с отступом)."""
    notes = {}
    entries, _ = _parse(example)
    for entry in entries:
        above: list[str] = []
        for line in reversed(example[:entry.line_start].split("\n")[:-1]):  # целые строки над ключом
            line = line.rstrip("\r")
            if not line.lstrip().startswith("#"):
                break
            above.insert(0, line)
        if any(_CODE_IN_COMMENT.match(line) for line in above):
            above = []  # там закомментированный пример другого параметра — не его пояснение
        tail = example[entry.value_end:entry.line_end].rstrip("\r\n")
        notes[(entry.section, entry.key)] = (above, tail if tail.strip().startswith("#") else "")
    return notes


def update_text(text: str, values: dict[str, dict], notes=None, remove=()) -> tuple[str, list[str]]:
    """Подставить значения в текст config.toml. values — {раздел: {ключ: значение}}, "" — верх файла.
    Меняются только строки с другим значением, недостающие ключи дописываются в свой раздел (с пояснением
    из notes). remove — [(раздел, ключ)] убрать. Возвращает (новый текст, добавленные «раздел.ключ»)."""
    notes = notes or {}
    newline = "\r\n" if "\r\n" in text else "\n"
    entries, sections = _parse(text)
    have = {(e.section, e.key): e for e in entries}
    edits: list[tuple[int, int, str]] = []  # (начало, конец, на что заменить)
    added: list[str] = []

    for section_key in remove:
        entry = have.get(section_key)
        if entry is not None:
            edits.append((entry.line_start, entry.line_end, ""))

    def new_lines(section: str, keys: dict) -> list[str]:
        lines = []
        for key, value in keys.items():
            above, tail = notes.get((section, key), ([], ""))
            lines += above + [f"{key} = {toml_value(value)}{tail}"]
            added.append(f"{section}.{key}" if section else key)
        return lines

    appended_sections: list[str] = []
    for section, keys in values.items():
        missing = {}
        for key, value in keys.items():
            entry = have.get((section, key))
            if entry is None:
                missing[key] = value
            elif not _same(text[entry.value_start:entry.value_end], value):
                edits.append((entry.value_start, entry.value_end, toml_value(value)))
        if not missing:
            continue
        lines = new_lines(section, missing)
        in_section = [e for e in entries if e.section == section]
        if in_section:  # после последнего параметра раздела
            at = in_section[-1].line_end
            prefix = "" if at == 0 or text[at - 1] == "\n" else newline
            edits.append((at, at, prefix + newline.join(lines) + newline))
        elif section in sections:  # раздел есть, параметров нет — сразу после заголовка
            at = sections[section][1]
            prefix = "" if text[at - 1:at] == "\n" else newline
            edits.append((at, at, prefix + newline.join(lines) + newline))
        elif section == "":  # верх файла: перед первым разделом
            at = min((start for start, _ in sections.values()), default=len(text))
            edits.append((at, at, newline.join(lines) + newline + (newline if sections else "")))
        else:
            appended_sections.append(newline.join([f"[{section}]"] + lines))

    for start, end, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        text = text[:start] + replacement + text[end:]
    if appended_sections:
        text = text.rstrip("\r\n") + newline + newline + (newline + newline).join(appended_sections) + newline
    return text, added


def _check(text: str, values: dict[str, dict]) -> bool:
    """Новый текст читается, и в нём ровно те значения, что записывали."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False
    for section, keys in values.items():
        table = data if section == "" else data.get(section, {})
        if any(table.get(key) != value for key, value in keys.items()):
            return False
    return True


def write_values(path: Path, values: dict[str, dict], example: Path | None = None, remove=(),
                 fallback=None) -> list[str]:
    """Записать значения в config.toml, сохранив всё остальное. Если файла нет — он начинается с
    config.example.toml (с пояснениями). fallback(values) → полный текст файла — на случай, если файл
    устроен так, что править его по строкам не вышло (тогда старый остаётся в config.toml.bak)."""
    def read(file: Path) -> str:  # как есть: read_text заменил бы окончания строк Windows на \n
        with open(file, encoding="utf-8", newline="") as f:
            return f.read()

    example_text = read(example) if example is not None and example.exists() else ""
    original = read(path) if path.exists() else example_text
    text, added = update_text(original, values, example_notes(example_text) if example_text else {}, remove)
    if not _check(text, values):
        if fallback is None:
            raise ValueError("не удалось записать настройки по строкам")
        if path.exists():
            shutil.copyfile(path, path.with_name(path.name + ".bak"))
        text = fallback(values)
    if path.exists() and text == original:
        return added
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    tmp.replace(path)
    return added

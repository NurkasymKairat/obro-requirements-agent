"""Незаполненные места шаблона: прочерки, пустые поля и строки, заглушки, пустые разделы."""
from __future__ import annotations

import re

from agent.schemas import Finding

from .base import STD_SET, Doc, finding, plain

_EMPTY = re.compile(r"^(?:[-–—−_.…?]+|n/?a|tbd|todo|нет данных|не задано|не указано)?$", re.I)
# «Prod: —» внутри ячейки или строки
_INLINE_EMPTY = re.compile(r"[A-Za-zА-Яа-яЁё][\w /-]{0,40}:\s*[—–−]+(?=\s*(?:$|[|.,;]))")
_PLACEHOLDER = re.compile(
    r"\b(?:tbd|todo|to be (?:defined|determined|done)|"
    r"заполня(?:е|ю)тся|будет (?:заполнен\w*|определен\w*|уточнен\w*|описан\w*|добавлен\w*)|"
    r"не заполнен\w*|не определен\w*|уточняется|подлежит уточнению|в разработке)\b|<\s*\.{3}\s*>|\?{3,}",
    re.I,
)


def check(doc: Doc) -> list[Finding]:
    out: list[Finding] = []
    table_lines: set[int] = set()

    for table in doc.tables():
        table_lines.add(table.header.no)
        table_lines.update(r.no for r in table.rows)
        width = len(table.header.cells())
        dashed, blank_rows = [], []
        for row in table.rows:
            cells = row.cells()
            if cells and all(not plain(c[0]) for c in cells):
                blank_rows.append(row)
            elif width == 2 and len(cells) == 2 and plain(cells[0][0]):
                value = plain(cells[1][0])
                if _EMPTY.match(value) or _INLINE_EMPTY.search(value):
                    dashed.append(row)
        if dashed:
            names = ", ".join(f"«{plain(r.cells()[0][0])}»" for r in dashed)
            out.append(finding(
                "template", criterion="полнота (complete)", kind="incomplete", severity="medium",
                standard=STD_SET,
                description=f"В таблице раздела «{doc.section_of(table.header)}» не заполнены значения полей: {names}. "
                            "Вместо значения стоит прочерк или пусто.",
                quotes=[doc.quote_line(r) for r in dashed],
                fix="Заполнить значения полей либо явно написать «не применимо» с причиной.",
            ))
        if blank_rows and len(blank_rows) == len(table.rows):
            out.append(finding(
                "template", criterion="полнота (complete)", kind="incomplete", severity="medium",
                standard=STD_SET,
                description=f"Таблица раздела «{doc.section_of(table.header)}» содержит только заголовок: "
                            "ни одна строка не заполнена.",
                quotes=[doc.quote_line(table.header)],
                fix="Заполнить таблицу или удалить раздел, если он не применим.",
            ))

    for line in doc.lines:
        if line.in_code or line.no in table_lines or not line.text.strip():
            continue
        text = line.text.lower().replace("ё", "е")
        m = _PLACEHOLDER.search(text)
        if m:
            out.append(finding(
                "template", criterion="полнота (complete)", kind="incomplete", severity="high",
                standard=STD_SET,
                description=f"В разделе «{doc.section_of(line)}» вместо содержания стоит заглушка или отметка "
                            f"о незаполненности: «{_short(line.text)}».",
                quotes=[doc.quote_line(line)],
                fix="Заменить заглушку содержанием раздела до начала разработки и тестирования.",
            ))
        elif _INLINE_EMPTY.search(line.text):
            out.append(finding(
                "template", criterion="полнота (complete)", kind="incomplete", severity="medium",
                standard=STD_SET,
                description=f"В разделе «{doc.section_of(line)}» значение заменено прочерком.",
                quotes=[doc.quote_line(line)],
                fix="Указать значение либо явно написать «не применимо» с причиной.",
            ))

    out.extend(_empty_sections(doc))
    return out


def _short(text: str, limit: int = 160) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _empty_sections(doc: Doc) -> list[Finding]:
    out = []
    heads = [(ln, *ln.heading()) for ln in doc.lines if ln.heading()]
    for i, (line, level, title) in enumerate(heads):
        nxt = heads[i + 1] if i + 1 < len(heads) else None
        if nxt is None or nxt[1] > level:
            continue  # последний заголовок или есть подразделы
        body = doc.lines[line.no: nxt[0].no - 1]
        if any(b.text.strip() and not re.fullmatch(r"\s*[-*_]{3,}\s*", b.text) for b in body):
            continue
        out.append(finding(
            "template", criterion="полнота (complete)", kind="incomplete", severity="medium",
            standard=STD_SET,
            description=f"Раздел «{title}» пуст: под заголовком нет содержания.",
            quotes=[doc.quote_line(line)],
            fix="Заполнить раздел или удалить его, если он не применим.",
        ))
    return out

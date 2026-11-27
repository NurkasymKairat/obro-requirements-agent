"""Типы полей: одно и то же поле описано в разных таблицах с разными типами."""
from __future__ import annotations

import re

from agent.schemas import Finding

from .base import STD_SET, Doc, Line, finding, plain

_NAME_COL = re.compile(r"^(parameter|param|параметр|поле|field|name|имя|наименование|атрибут|attribute)$")
_TYPE_COL = re.compile(r"^(format|формат|type|тип|тип данных|data type)$")
_CANON = {
    "int": "integer", "integer": "integer", "long": "integer", "number": "number", "numeric": "number",
    "decimal": "number", "float": "number", "double": "number", "целое": "integer", "число": "number",
    "str": "string", "string": "string", "text": "string", "varchar": "string", "строка": "string",
    "bool": "boolean", "boolean": "boolean", "логический": "boolean",
    "date": "date", "datetime": "datetime", "timestamp": "datetime", "дата": "date",
    "array": "array", "list": "array", "массив": "array", "object": "object", "объект": "object",
}


def _canon(raw: str) -> str:
    word = re.split(r"[\s(\[<,]", plain(raw).lower(), maxsplit=1)[0]
    return _CANON.get(word, word)


def check(doc: Doc) -> list[Finding]:
    seen: dict[str, list[tuple[str, Line]]] = {}
    for table in doc.tables():
        header = table.header_cells()
        name_i = next((i for i, h in enumerate(header) if _NAME_COL.match(h)), None)
        type_i = next((i for i, h in enumerate(header) if _TYPE_COL.match(h)), None)
        if name_i is None or type_i is None:
            continue
        for row in table.rows:
            cells = row.cells()
            if len(cells) <= max(name_i, type_i):
                continue
            name, typ = plain(cells[name_i][0]), _canon(cells[type_i][0])
            if name and typ:
                seen.setdefault(name, []).append((typ, row))

    conflicts = {n: rows for n, rows in seen.items() if len({t for t, _ in rows}) > 1}
    if not conflicts:
        return []
    parts, quotes = [], []
    for name, rows in conflicts.items():
        variants = "; ".join(f"{t} в разделе «{doc.section_of(r)}»" for t, r in rows)
        parts.append(f"«{name}» — {variants}")
        quotes.extend(doc.quote_line(r) for _, r in rows)
    return [finding(
        "fields", criterion="согласованность (consistent)", kind="contradiction", severity="medium",
        standard=STD_SET,
        description="Одно и то же поле описано с разными типами: " + ". ".join(parts) + ".",
        quotes=quotes,
        fix="Привести тип поля к одному во всех таблицах и примерах либо явно описать преобразование "
            "и его причину.",
    )]

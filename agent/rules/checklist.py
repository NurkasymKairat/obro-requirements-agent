"""Незаполненные чек-листы: варианты ответа не выбраны, флажки не отмечены."""
from __future__ import annotations

import re

from agent.schemas import Finding

from .base import STD_SET, Doc, finding, plain

_OPTION = r"(?:да|нет|не требуется|не применимо|yes|no|n/?a)"
_UNCHOSEN = re.compile(rf"^{_OPTION}(?:\s*/\s*{_OPTION})+$", re.I)
_BOX = re.compile(r"^\s*[-*+]\s+\[( |x|X|х|Х)\]\s+\S")
MAX_QUOTES = 3


def check(doc: Doc) -> list[Finding]:
    out: list[Finding] = []

    for table in doc.tables():
        open_rows = [r for r in table.rows if any(_UNCHOSEN.match(plain(c[0])) for c in r.cells())]
        if not open_rows:
            continue
        out.append(finding(
            "checklist", criterion="полнота (complete)", kind="incomplete", severity="medium",
            standard=STD_SET,
            description=f"Чек-лист раздела «{doc.section_of(table.header)}» не заполнен: "
                        f"в {len(open_rows)} из {len(table.rows)} строк оставлены все варианты ответа, "
                        "отметка не выбрана. Ревью требований по чек-листу не проведено или не зафиксировано.",
            quotes=[doc.quote_line(r) for r in open_rows[:MAX_QUOTES]],
            fix="Пройти чек-лист и оставить в каждой строке один выбранный ответ.",
        ))

    boxes = [(ln, _BOX.match(ln.text)) for ln in doc.lines if not ln.in_code and _BOX.match(ln.text)]
    unchecked = [ln for ln, m in boxes if m.group(1) == " "]
    if boxes and len(unchecked) == len(boxes):
        out.append(finding(
            "checklist", criterion="полнота (complete)", kind="incomplete", severity="medium",
            standard=STD_SET,
            description=f"Ни один из {len(boxes)} пунктов чек-листа не отмечен.",
            quotes=[doc.quote_line(ln) for ln in unchecked[:MAX_QUOTES]],
            fix="Пройти чек-лист и отметить выполненные пункты.",
        ))
    return out

"""Общие средства детерминированных правил: строки с позициями, таблицы Markdown, сборка замечаний."""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

from agent.schemas import Document, Finding, Quote

STD_REQ = "ISO/IEC/IEEE 29148 п. 5.2.5"
STD_SET = "ISO/IEC/IEEE 29148 п. 5.2.6; IEEE 830 п. 4.3"

_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Line:
    no: int          # номер строки с 1
    start: int       # смещение начала строки в Document.text
    text: str        # строка без перевода строки
    in_code: bool    # внутри блока кода (включая строки-ограничители)

    @property
    def end(self) -> int:
        return self.start + len(self.text)

    @property
    def is_table_row(self) -> bool:
        return not self.in_code and self.text.lstrip().startswith("|")

    @property
    def is_separator(self) -> bool:
        return self.is_table_row and bool(_SEPARATOR.match(self.text))

    def heading(self) -> tuple[int, str] | None:
        if self.in_code:
            return None
        m = _HEADING.match(self.text)
        return (len(m.group(1)), m.group(2)) if m else None

    def content_span(self) -> tuple[int, int]:
        """Позиции содержимого строки без внешних пробелов, маркеров и крайних «|»."""
        m = re.match(r"^[\s|]*(?:[*+-]\s+|#{1,6}\s+)?", self.text)
        left = m.end() if m else 0
        right = len(self.text.rstrip().rstrip("|").rstrip())
        if right <= left:
            left, right = 0, len(self.text)
        return self.start + left, self.start + right

    def cells(self) -> list[tuple[str, int, int]]:
        """Ячейки строки таблицы: (текст без пробелов, начало, конец) в координатах документа."""
        out = []
        body = self.text
        pos = body.find("|") + 1
        while True:
            nxt = body.find("|", pos)
            if nxt == -1:
                break
            raw = body[pos:nxt]
            stripped = raw.strip()
            lead = len(raw) - len(raw.lstrip())
            begin = self.start + pos + (lead if stripped else 0)
            out.append((stripped, begin, begin + len(stripped)))
            pos = nxt + 1
        return out


@dataclass
class Table:
    header: Line
    rows: list[Line]

    def header_cells(self) -> list[str]:
        return [plain(c[0]).lower() for c in self.header.cells()]


class Doc:
    """Документ, разложенный на строки, с поиском блока по позиции."""

    def __init__(self, document: Document):
        self.document = document
        self.text = document.text
        self.lines: list[Line] = []
        pos, in_code = 0, False
        for no, raw in enumerate(self.text.split("\n"), start=1):
            fence = bool(_FENCE.match(raw))
            self.lines.append(Line(no, pos, raw.rstrip("\r"), in_code or fence))
            if fence:
                in_code = not in_code
            pos += len(raw) + 1
        self._blocks = sorted(document.blocks, key=lambda b: b.start)
        self._starts = [b.start for b in self._blocks]
        self._line_starts = [ln.start for ln in self.lines]

    def tables(self) -> list[Table]:
        out, i = [], 0
        while i < len(self.lines):
            ln = self.lines[i]
            if ln.is_table_row and i + 1 < len(self.lines) and self.lines[i + 1].is_separator:
                rows, j = [], i + 2
                while j < len(self.lines) and self.lines[j].is_table_row:
                    rows.append(self.lines[j])
                    j += 1
                out.append(Table(ln, rows))
                i = j
            else:
                i += 1
        return out

    def section_of(self, line: Line) -> str:
        for prev in reversed(self.lines[: line.no]):
            h = prev.heading()
            if h:
                return h[1]
        return ""

    def line_no(self, offset: int) -> int:
        return bisect.bisect_right(self._line_starts, offset)

    def quote(self, start: int, end: int) -> Quote:
        idx = bisect.bisect_right(self._starts, start) - 1
        block = self._blocks[idx] if idx >= 0 and start < self._blocks[idx].end else None
        return Quote(
            text=self.text[start:end],
            block_id=block.block_id if block else "",
            start=start,
            end=end,
            location=block.location if block else f"строка {self.line_no(start)}",
        )

    def quote_line(self, line: Line) -> Quote:
        return self.quote(*line.content_span())


def plain(cell: str) -> str:
    """Текст ячейки без разметки Markdown."""
    return re.sub(r"[*`_]", "", cell).strip()


def finding(rule: str, *, criterion: str, kind: str, severity: str, description: str,
            quotes: list[Quote], fix: str, status: str = "confirmed",
            standard: str = STD_REQ) -> Finding:
    return Finding(
        fid="", scope="set", requirement_ids=[], criterion=criterion, standard=standard,
        kind=kind, severity=severity, status=status, description=description,
        quotes=quotes, fix=fix, origin_pass=f"rules.{rule}",
    )

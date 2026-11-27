"""Чтение документа: файл -> Document с блоками и позициями.

Блок — минимальная единица, на которую ссылается цитата: строка таблицы, пункт списка,
абзац, заголовок, блок кода. Document.text[block.start:block.end] == block.text.
"""
from __future__ import annotations

import re
import statistics
from pathlib import Path

from .schemas import Block, Document

SEP = " › "
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST_RE = re.compile(r"^\s*([*+-]|\d+[.)])\s+")
_DOCX_HEADING_RE = re.compile(r"^(heading|заголовок)\s*(\d)", re.I)


def read_document(path: str | Path) -> Document:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Файл не найден: {p}")
    ext = p.suffix.lower()
    if ext in (".md", ".markdown"):
        return _read_text(p, "md")
    if ext == ".txt":
        return _read_text(p, "txt")
    if ext == ".pdf":
        return _read_pdf(p)
    if ext == ".docx":
        return _read_docx(p)
    raise ValueError(f"Неподдерживаемый формат {ext}: нужен .pdf, .md, .txt или .docx")


def render_for_llm(doc: Document) -> str:
    """Текст документа для модели: каждый блок с меткой [B-012]."""
    out = []
    for b in doc.blocks:
        prefix = "\n" if b.kind == "heading" else ""
        out.append(f"{prefix}[{b.block_id}] {b.text}")
    return "\n".join(out).strip()


# ---------- общее ----------


class _Builder:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.pos = 0
        self.blocks: list[Block] = []
        self.stack: list[tuple[int, str]] = []
        self.drop_top = False   # не включать в путь единственный заголовок первого уровня

    def heading(self, level: int, title: str) -> None:
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.stack.append((level, title))

    def section(self) -> str:
        items = [t for lvl, t in self.stack if not (self.drop_top and lvl == 1)]
        return SEP.join(items)

    def location(self, where: str) -> str:
        sec = self.section()
        return f"{sec}, {where}" if sec else where

    def add_block(self, text: str, start: int, kind: str, where: str,
                  line: int | None = None, page: int | None = None) -> None:
        self.blocks.append(Block(
            block_id=f"B-{len(self.blocks) + 1:03d}", section=self.section(), kind=kind,
            text=text, start=start, end=start + len(text), line=line, page=page,
            location=self.location(where)))

    def append(self, text: str, kind: str, where_fn, **kw) -> None:
        """Для форматов без исходного текста (PDF, DOCX): текст собирается из блоков."""
        if self.parts:
            self.parts.append("\n\n")
            self.pos += 2
        self.add_block(text, self.pos, kind, where_fn(), **kw)
        self.parts.append(text)
        self.pos += len(text)

    def text(self) -> str:
        return "".join(self.parts)


def _clean_heading(s: str) -> str:
    return re.sub(r"[*_`]", "", s).strip()


# ---------- Markdown / TXT ----------


def _read_text(p: Path, fmt: str) -> Document:
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1251")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    b = _Builder()
    lines = text.split("\n")
    b.drop_top = fmt == "md" and sum(1 for ln in lines if re.match(r"^#\s", ln)) == 1

    offsets, acc = [], 0
    for ln in lines:
        offsets.append(acc)
        acc += len(ln) + 1

    i = 0
    while i < len(lines):
        ln = lines[i]
        stripped = ln.strip()
        start = offsets[i] + (len(ln) - len(ln.lstrip()))
        if not stripped or (fmt == "md" and re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped)):
            i += 1
            continue
        if fmt == "md" and stripped.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            end_line = min(j, len(lines) - 1)
            end = offsets[end_line] + len(lines[end_line])
            b.add_block(text[start:end], start, "code", f"строки {i + 1}–{end_line + 1}", line=i + 1)
            i = end_line + 1
            continue
        m = _HEADING_RE.match(stripped) if fmt == "md" else None
        if m:
            b.heading(len(m.group(1)), _clean_heading(m.group(2)))
            kind = "heading"
        elif stripped.startswith("|"):
            if _TABLE_SEP_RE.match(stripped):
                i += 1
                continue
            kind = "table_row"
        elif _LIST_RE.match(stripped):
            kind = "list_item"
        else:
            kind = "paragraph"
        b.add_block(stripped, start, kind, f"строка {i + 1}", line=i + 1)
        i += 1
    return Document(path=str(p), format=fmt, text=text, blocks=b.blocks)


# ---------- PDF ----------

# Лигатуры Unicode (ﬁ, ﬂ, ﬀ…) -> обычные буквы: модель цитирует «field», а не «ﬁeld».
_LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
              "\ufb05": "st", "\ufb06": "st"}
# Разрыв на месте лигатуры: глиф «fi» извлекается как «f», буква после лигатуры, затем «i» и лишний пробел
# («field» -> «fei ld», «flow» -> «fol w»). Возвращаем вторую букву лигатуры на место и убираем пробел.
_LIG_SPLIT_RE = re.compile(r"(?<![A-Za-z])(f)([a-z])([il]) (?=[a-z])")
_PAGE_NO_RE = re.compile(r"^(стр\.?|страница|page)?\s*\d+(\s*(из|/|of)\s*\d+)?$", re.I)


def fix_pdf_text(s: str) -> str:
    """Нормализация текста, извлечённого из PDF: лигатуры, разрывы на их месте, неразрывные пробелы."""
    for k, v in _LIGATURES.items():
        s = s.replace(k, v)
    s = s.replace("\u00ad", "").replace("\u00a0", " ")
    return _LIG_SPLIT_RE.sub(lambda m: m.group(1) + m.group(3) + m.group(2), s)


def _join_lines(lines: list[str]) -> str:
    """Склейка строк переноса: «чек-» + «лист» -> «чек-лист», иначе через пробел."""
    out = ""
    for ln in (x.strip() for x in lines):
        if not ln:
            continue
        if out.endswith("-") and not out.endswith(" -") and ln[:1].isalnum():
            out += ln
        else:
            out = f"{out} {ln}" if out else ln
    return out


def _cell(c) -> str:
    return fix_pdf_text(_join_lines(str(c or "").split("\n")))


def _inside(bbox, rect, tol: float = 2.0) -> bool:
    """Центр текстового блока внутри таблицы: рамка find_tables бывает уже текста ячеек на пару пунктов."""
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return rect[0] - tol <= cx <= rect[2] + tol and rect[1] - tol <= cy <= rect[3] + tol


def _text_items(blk: dict) -> list[dict]:
    """Блок PyMuPDF -> абзацы. Новый абзац начинается со строки с жирной меткой («Ф-1.», «200.») или
    с маркера списка: вёрстка часто кладёт несколько пунктов в один блок."""
    lines = []
    for line in blk["lines"]:
        spans = [s for s in line["spans"] if s["text"].strip()]
        if spans:
            lines.append((line, spans))
    if not lines:
        return []
    # строки на одной высоте, разнесённые по ширине, — строка таблицы без сетки (например, шапка без данных)
    xs = sorted((l["bbox"][0], l["bbox"][2]) for l, _ in lines)
    wide_gaps = all(b[0] - a[1] > 20 for a, b in zip(xs, xs[1:]))      # колонки, а не разрыв внутри строки
    if len(lines) > 1 and wide_gaps and max(l["bbox"][1] for l, _ in lines) - min(l["bbox"][1] for l, _ in lines) < 2:
        cells = [fix_pdf_text("".join(s["text"] for s in sp).strip()) for _, sp in lines]
        size = max(s["size"] for _, sp in lines for s in sp)
        return [{"kind": "row", "cells": cells, "size": size, "bold": False, "y": blk["bbox"][1]}]
    paras: list[list] = []
    for line, spans in lines:
        txt = "".join(s["text"] for s in line["spans"]).strip()
        all_bold = all(s["flags"] & 16 for s in spans)
        lead_bold = bool(spans[0]["flags"] & 16) and not all_bold
        prev_bold = bool(paras) and all(s["flags"] & 16 for s in paras[-1][-1][1])
        if paras and (lead_bold or _LIST_RE.match(txt) or all_bold != prev_bold):
            paras.append([])
        if not paras:
            paras.append([])
        paras[-1].append((txt, spans, line["bbox"][1]))
    items = []
    for para in paras:
        spans = [s for _, sp, _ in para for s in sp]
        items.append({"kind": "text", "text": fix_pdf_text(_join_lines([t for t, _, _ in para])),
                      "size": max(s["size"] for s in spans), "bold": all(s["flags"] & 16 for s in spans),
                      # размер на каждый символ: основной текст — медиана по объёму текста, а не по числу фрагментов
                      "sizes": [s["size"] for s in spans for _ in s["text"].strip()], "y": para[0][2]})
    return items


def _columns(t) -> list[float]:
    row = t.rows[0] if t.rows else None
    return [round(c[0], 0) for c in (row.cells if row else []) if c]


def _same_columns(a: list[float], b: list[float]) -> bool:
    """Те же столбцы: то же число и те же внутренние границы (левая граница рамки у продолжения бывает сдвинута)."""
    return (len(a) == len(b) and bool(a) and abs(a[0] - b[0]) <= 10
            and all(abs(x - y) <= 3 for x, y in zip(a[1:], b[1:])))


def _read_pdf(p: Path) -> Document:
    """Текст и таблицы по страницам. Таблица (find_tables) даёт по блоку на строку, как в Markdown:
    «| ячейка | ячейка |». Таблица, разорванная переносом страницы, склеивается в одну, если у продолжения
    те же столбцы и между частями нет заголовка раздела; повтор шапки на новой странице отбрасывается.
    Заголовки — по размеру шрифта крупнее основного текста; жирная строка размером с текст — заголовок
    нижнего уровня, но только вне таблиц и не мельче основного текста (шапки таблиц не заголовки)."""
    import pymupdf

    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_LIGATURES   # MuPDF раскрывает лигатуры сам
    seq: list[dict] = []          # элементы документа в порядке чтения
    sizes: list[float] = []
    with pymupdf.open(p) as pdf:
        for pno, page in enumerate(pdf, start=1):
            tables = []
            try:
                found = page.find_tables().tables
            except Exception:  # noqa: BLE001 — нет таблиц лучше, чем упавшее чтение
                found = []
            for t in found:
                rows = [[_cell(c) for c in r] for r in t.extract()]
                rows = [r for r in rows if any(r)]
                if rows:
                    tables.append({"kind": "table", "rows": rows, "cols": _columns(t), "y": t.bbox[1],
                                   "bbox": tuple(t.bbox), "page": pno})
            items: list[dict] = []
            for blk in page.get_text("dict", flags=flags)["blocks"]:
                if blk.get("type") != 0 or any(_inside(blk["bbox"], t["bbox"]) for t in tables):
                    continue
                for it in _text_items(blk):
                    in_margin = it["y"] < page.rect.height * 0.05 or it["y"] > page.rect.height * 0.95
                    if it["kind"] == "text" and in_margin and _PAGE_NO_RE.match(it["text"]):
                        continue      # колонтитул с номером страницы
                    it["page"] = pno
                    sizes += it.get("sizes", [])
                    items.append(it)
            for t in tables:          # таблица встаёт перед первым элементом ниже её верхней границы
                at = next((i for i, it in enumerate(items) if it["y"] > t["y"]), len(items))
                items.insert(at, t)
            seq += items

    body = statistics.median(sizes) if sizes else 10.0
    heading_sizes = sorted({round(it["size"], 1) for it in seq if it["kind"] == "text" and it["size"] > body * 1.15},
                           reverse=True)
    for it in seq:
        if it["kind"] == "text":
            it["level"] = None
            if len(it["text"]) <= 120 and round(it["size"], 1) in heading_sizes:
                it["level"] = heading_sizes.index(round(it["size"], 1)) + 1
            elif len(it["text"]) <= 80 and it["bold"] and it["size"] >= body - 0.25 and not it["text"].endswith("."):
                it["level"] = len(heading_sizes) + 1

    # склейка таблиц через перенос страницы
    merged: list[dict] = []
    for it in seq:
        prev = merged[-1] if merged else None
        if (it["kind"] == "table" and prev and prev["kind"] == "table" and it["page"] == prev["last_page"] + 1
                and _same_columns(it["cols"], prev["cols"])):
            rows = it["rows"][1:] if it["rows"][0] == prev["rows"][0][0] else it["rows"]   # повтор шапки
            prev["rows"] += [(r, it["page"]) for r in rows]
            prev["last_page"] = it["page"]
            continue
        if it["kind"] == "table":
            it = {**it, "rows": [(r, it["page"]) for r in it["rows"]], "last_page": it["page"]}
        merged.append(it)

    b = _Builder()
    b.drop_top = sum(1 for it in merged if it["kind"] == "text" and it["level"] == 1) == 1   # как в Markdown
    table_no = 0
    for it in merged:
        pno = it["page"]
        if it["kind"] == "table":
            table_no += 1
            for r_no, (cells, rp) in enumerate(it["rows"], start=1):
                b.append("| " + " | ".join(cells) + " |", "table_row",
                         lambda rp=rp, t=table_no, r=r_no: f"стр. {rp}, таблица {t}, строка {r}", page=rp)
        elif it["kind"] == "row":
            b.append("| " + " | ".join(it["cells"]) + " |", "table_row", lambda pno=pno: f"стр. {pno}", page=pno)
        else:
            txt, level = it["text"], it["level"]
            if level:
                b.heading(level, txt)
                kind = "heading"
            elif _LIST_RE.match(txt):
                kind = "list_item"
            else:
                kind = "paragraph"
            b.append(txt, kind, lambda pno=pno: f"стр. {pno}", page=pno)
    return Document(path=str(p), format="pdf", text=b.text(), blocks=b.blocks)


# ---------- DOCX ----------


def _read_docx(p: Path) -> Document:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(str(p))
    b = _Builder()
    para_no = table_no = 0
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(el, d)
            txt = para.text.strip()
            if not txt:
                continue
            para_no += 1
            style = (para.style.name if para.style is not None else "") or ""
            m = _DOCX_HEADING_RE.match(style)
            if m or style.lower() == "title":
                b.heading(int(m.group(2)) if m else 1, txt)
                kind = "heading"
            elif "list" in style.lower() or "спис" in style.lower() or _LIST_RE.match(txt):
                kind = "list_item"
            else:
                kind = "paragraph"
            b.append(txt, kind, lambda n=para_no: f"абзац {n}")
        elif tag == "tbl":
            table_no += 1
            for r_no, row in enumerate(Table(el, d).rows, start=1):
                cells, prev = [], None
                for c in row.cells:
                    if c._tc is prev:  # объединённые ячейки
                        continue
                    prev = c._tc
                    cells.append(" ".join(c.text.split()))
                if not any(cells):
                    continue
                txt = "| " + " | ".join(cells) + " |"
                b.append(txt, "table_row", lambda t=table_no, r=r_no: f"таблица {t}, строка {r}")
    return Document(path=str(p), format="docx", text=b.text(), blocks=b.blocks)

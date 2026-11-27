"""Чтение свёрстанного PDF: заголовки по размеру, таблицы через перенос страницы, шапки таблиц, лигатуры."""
from pathlib import Path

import pymupdf
import pytest

from agent.reader import fix_pdf_text, read_document

COLS = (50, 200, 350, 540)


def grid(page, y, rows, bold_header=True, size=9.5, h=22):
    """Таблица с сеткой: шапка жирная и размером почти как текст."""
    for i, row in enumerate(rows):
        top = y + i * h
        for x0, x1, cell in zip(COLS, COLS[1:], row):
            page.draw_rect(pymupdf.Rect(x0, top, x1, top + h), color=(0, 0, 0), width=0.5)
            font = "hebo" if (i == 0 and bold_header) else "helv"
            page.insert_text((x0 + 4, top + 15), cell, fontsize=size, fontname=font)
    return y + len(rows) * h


def text(page, y, s, size=10.5, bold=False):
    page.insert_text((50, y), s, fontsize=size, fontname="hebo" if bold else "helv")


HEAD = ["Conditions", "Action", "Expected"]


def make_pdf(path, heading_between=False, other_columns=False):
    pdf = pymupdf.open()
    p1 = pdf.new_page()
    text(p1, 60, "Card spending alert", 20, True)
    text(p1, 100, "Part A. New feature", 16, True)
    text(p1, 130, "Goals", 13, True)
    text(p1, 155, "The client learns about the budget at the moment of purchase.")
    text(p1, 185, "Business rules", 11.5, True)
    text(p1, 210, "The limit is an integer from 1000 to 1000000 inclusive.")
    text(p1, 250, "Acceptance criteria", 13, True)
    grid(p1, 700, [HEAD, ["Spent 48500", "Buy for 1500", "One push"], ["Spent 100", "Refund 50", "No push"]])
    p2 = pdf.new_page()
    y = 50
    if heading_between:
        text(p2, 60, "Review checklist", 13, True)
        y = 80
    if other_columns:
        global COLS
        saved, COLS = COLS, (50, 120, 400, 540)
        grid(p2, y, [HEAD, ["Spent 19900", "Transfer 500", "Counter 19900"]])
        COLS = saved
    else:
        grid(p2, y, [HEAD, ["Spent 19900", "Transfer 500", "Counter 19900"]])
    text(p2, 200, "Data requirements", 13, True)
    text(p2, 225, "Store the card id and the amount.")
    pdf.save(path)


def rows(doc):
    return [b for b in doc.blocks if b.kind == "table_row"]


def test_headings_by_size_and_table_header_is_not_heading(tmp_path):
    make_pdf(tmp_path / "a.pdf")
    doc = read_document(tmp_path / "a.pdf")
    heads = [b.text for b in doc.blocks if b.kind == "heading"]
    assert heads == ["Card spending alert", "Part A. New feature", "Goals", "Business rules",
                     "Acceptance criteria", "Data requirements"]
    assert "Conditions" not in " ".join(heads)                       # жирная шапка таблицы 9.5 pt — не заголовок
    assert rows(doc)[0].text == "| Conditions | Action | Expected |"
    assert rows(doc)[0].section.endswith("Acceptance criteria")
    assert all(doc.text[b.start:b.end] == b.text for b in doc.blocks)


def test_table_split_by_page_break_is_merged(tmp_path):
    make_pdf(tmp_path / "a.pdf")
    r = rows(read_document(tmp_path / "a.pdf"))
    assert [b.text for b in r] == ["| Conditions | Action | Expected |", "| Spent 48500 | Buy for 1500 | One push |",
                                   "| Spent 100 | Refund 50 | No push |",
                                   "| Spent 19900 | Transfer 500 | Counter 19900 |"]   # повтор шапки отброшен
    assert r[-1].page == 2 and r[-1].location.endswith("стр. 2, таблица 1, строка 4")
    assert r[-1].section.endswith("Acceptance criteria")             # продолжение в том же разделе


@pytest.mark.parametrize("kw", [{"heading_between": True}, {"other_columns": True}])
def test_not_merged_with_heading_between_or_other_columns(tmp_path, kw):
    make_pdf(tmp_path / "a.pdf", **kw)
    r = rows(read_document(tmp_path / "a.pdf"))
    assert sum(1 for b in r if b.text == "| Conditions | Action | Expected |") == 2
    assert "таблица 2, строка 2" in r[-1].location


def test_ligatures_expanded_in_real_shaped_pdf(tmp_path):
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_htmlbox(pymupdf.Rect(50, 50, 500, 200),        # вёрстка через HarfBuzz ставит лигатуры ﬁ, ﬂ, ﬃ
                        '<p style="font-family:serif;font-size:14px">The field flow office must be filled.</p>')
    pdf.save(tmp_path / "lig.pdf")
    assert "ﬁ" in pymupdf.open(tmp_path / "lig.pdf")[0].get_text()   # лигатуры в файле действительно есть
    doc = read_document(tmp_path / "lig.pdf")
    assert doc.blocks[0].text == "The field flow office must be filled."


def test_fix_pdf_text_ligature_chars_and_split_words():
    assert fix_pdf_text("ﬁeld ﬂow oﬃce aﬀine") == "field flow office affine"
    assert fix_pdf_text("The fei ld is required") == "The field is required"
    assert fix_pdf_text("data fol w and fei lds") == "data flow and fields"
    assert fix_pdf_text("Поле fei ld обязательно") == "Поле field обязательно"
    # обычный текст не трогаем
    for s in ("fix it", "fail later", "self in", "a fee is due", "Ф-1. Порог 1 000 ₸", "file list"):
        assert fix_pdf_text(s) == s


def test_hyphen_wrap_in_cells_and_soft_hyphen():
    assert fix_pdf_text("ин­формация") == "информация"
    from agent.reader import _cell
    assert _cell("Для архитектуры — чек-\nлист") == "Для архитектуры — чек-лист"
    assert _cell("порог 50\n000 ₸") == "порог 50 000 ₸"


RENDERED = Path("docs/requirements.pdf")


@pytest.mark.skipif(not RENDERED.is_file(), reason="свёрстанный PDF лежит только локально")
def test_rendered_requirements_pdf():
    doc = read_document(RENDERED)
    heads = [b.text for b in doc.blocks if b.kind == "heading"]
    tables = {b.location.split("таблица ")[1].split(",")[0] for b in doc.blocks if "таблица " in b.location}
    assert len(tables) == 6                                          # 9 таблиц find_tables, три разорваны переносом
    crit = [b for b in rows(doc) if b.section.endswith("Критерии приемки")]
    assert len(crit) == 4 and crit[0].text.startswith("| Исходные условия")   # 3 + 2 строки, шапка одна
    check = [b for b in rows(doc) if b.section.endswith("Ревью требований")]
    assert len(check) == 15                                          # 11 + 5, шапка одна
    assert not {"Поле", "Значение", "Проверка", "Parameter", "Подразделение"} & set(heads)
    assert any(b.text.startswith("Ф-3.") for b in doc.blocks)       # пункты одного блока разделены
    assert all(doc.text[b.start:b.end] == b.text for b in doc.blocks)

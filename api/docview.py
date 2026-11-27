"""Документ и результат прогона в виде, удобном фронту.

Позиции не пересчитываются: start/end блоков, ячеек и цитат — смещения в Document.text,
те же, что в RunResult. Фронт режет текст по этим смещениям и подсвечивает цитаты.
"""
from __future__ import annotations

import re
from collections import Counter

from agent.passes.calibrate import pick_main
from agent.pipeline import finding_sort_key, run_totals
from agent.report import CRITICAL_KINDS, KIND_RU, SEVERITY_RU, STATUS_RU, VERDICT_RU, humanize_result
from agent.schemas import Document, RunResult

_MD_HEADING = re.compile(r"^(#{1,6})\s")
SEP = " › "


def _cells(text: str, start: int) -> list[dict]:
    """Ячейки строки таблицы «| a | b |» — абсолютные смещения текста ячейки без пробелов по краям."""
    bars = [i for i, ch in enumerate(text) if ch == "|" and (i == 0 or text[i - 1] != "\\")]
    if len(bars) < 2:
        return [{"start": start, "end": start + len(text)}]
    out = []
    for a, b in zip(bars, bars[1:]):
        s, e = a + 1, b
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        out.append({"start": start + s, "end": start + e})
    return out


def document_view(doc: Document) -> dict:
    blocks = []
    for b in doc.blocks:
        item = {"id": b.block_id, "kind": b.kind, "start": b.start, "end": b.end, "location": b.location}
        if b.kind == "heading":
            m = _MD_HEADING.match(b.text)
            if m:
                item["level"] = len(m.group(1))
                item["text_start"] = b.start + m.end()   # без «## »
            else:
                item["level"] = min(len(b.section.split(SEP)) + 1, 6) if b.section else 1
        elif b.kind == "table_row":
            item["cells"] = _cells(b.text, b.start)
        blocks.append(item)
    return {"format": doc.format, "text": doc.text, "blocks": blocks}


def check_positions(result: RunResult, doc: Document) -> int:
    """Число цитат, текст которых не совпадает с документом по позициям (0 — всё сходится)."""
    quotes = [q for f in result.findings for q in f.quotes] + [r.quote for r in result.requirements]
    return sum(1 for q in quotes if doc.text[q.start:q.end] != q.text)


def result_view(result: RunResult) -> dict:
    """Числа сводки и «Главное» — теми же функциями ядра, что и в Markdown-отчёте."""
    findings = sorted(result.findings, key=finding_sort_key)
    confirmed = [f for f in findings if f.status == "confirmed"]
    totals = run_totals(result.stages)
    wall = result.wall_seconds if result.wall_seconds is not None else round(totals["seconds"], 1)
    return {
        "main_fids": [f.fid for f in pick_main(findings)],
        "summary": {
            "requirements": len(result.requirements),
            "findings": len(findings),
            "confirmed": len(confirmed),
            "potential": len(findings) - len(confirmed),
            "by_severity": dict(Counter(f.severity for f in confirmed)),
            "by_kind": dict(Counter(f.kind for f in confirmed)),
            "key_fids": [f.fid for f in confirmed if f.kind in CRITICAL_KINDS],   # ключевые дефекты по типам
        },
        "totals": {"calls": totals["calls"], "cache_hits": totals["cache_hits"], "seconds": wall,
                   "tokens": totals["tokens_total"]},
        "verdict_text": VERDICT_RU.get(result.verdict, result.verdict),
        "failed_stages": [{"name": s["name"], "status": s["status"], "error": s.get("error")}
                          for s in result.stages if s.get("status") != "ok"],
        "labels": {"severity": SEVERITY_RU, "kind": KIND_RU, "status": STATUS_RU},
    }


def result_payload(result: RunResult, doc: Document) -> dict:
    """Тексты замечаний — без служебных номеров блоков, как в Markdown-отчёте; цитаты и позиции те же."""
    result = humanize_result(result)
    return {"result": result.model_dump(mode="json"), "view": result_view(result), "document": document_view(doc)}

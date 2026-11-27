"""Правила кода для степени важности и блока «Главное». Модель их обойти не может.

floor_severity / apply_floors — нижние границы степени по виду дефекта:
  * подтверждённое противоречие с цитатами обеих сторон — не ниже high;
  * противоречие между требованием и критерием приёмки либо между нумерованными требованиями
    одного раздела (одного сценария) — critical;
  * непроверяемое нефункциональное требование — не ниже high;
  * замечание правила единиц (rules.units) — не ниже high и не оспаривается.
pick_main — отбор замечаний в блок «Главное».
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from agent.schemas import Finding, Quote, Requirement

SEVERITIES = ("critical", "high", "medium", "low")          # от сильной к слабой
MAIN_KIND_ORDER = {"contradiction": 0, "unverifiable": 1}   # остальные виды — после них
TEMPLATE_ORIGINS = ("rules.template", "rules.checklist")
# Правила, которые сами проверили весь документ и не нашли определения (например, правила пересчёта единиц).
# Их замечания не оспариваются доводом «очевидно из контекста» и не опускаются ниже high.
PROTECTED_ORIGINS = ("rules.units",)

_ACCEPTANCE = re.compile(r"критери\w*\s+при[её]мк|при[её]мочн|acceptance|тестов\w*\s+сценари|тест-кейс", re.I)
_NONFUNCTIONAL = re.compile(r"нефункциональн|non-?functional|\bнфт\b", re.I)


def stronger(a: str, b: str) -> str:
    return a if SEVERITIES.index(a) <= SEVERITIES.index(b) else b


def _where(q: Quote, requirements: Sequence[Requirement]) -> tuple[str, list[Requirement]]:
    """Текст о месте цитаты (место + разделы требований) и требования, с которыми она пересекается."""
    reqs = [r for r in requirements if q.start < r.quote.end and r.quote.start < q.end]
    return " ".join([q.location] + [r.section for r in reqs]), reqs


def is_protected(f: Finding) -> bool:
    return any(part in PROTECTED_ORIGINS for part in f.origin_pass.split("+"))


def floor_severity(f: Finding, requirements: Sequence[Requirement] = ()) -> tuple[str | None, str]:
    """Нижняя граница степени для замечания и её основание; (None, "") — границы нет."""
    if f.status != "confirmed":
        return None, ""
    if is_protected(f):
        return "high", "разные единицы или точность без правила пересчёта (проверено по всему документу)"
    places = [_where(q, requirements) for q in f.quotes]
    if f.kind == "unverifiable" and any(_NONFUNCTIONAL.search(text) for text, _ in places):
        return "high", "непроверяемое нефункциональное требование"
    if f.kind != "contradiction" or len(f.quotes) < 2:
        return None, ""
    in_acceptance = [bool(_ACCEPTANCE.search(text)) for text, _ in places]
    if any(in_acceptance) and not all(in_acceptance):
        return "critical", "противоречие между требованием и критерием приёмки"
    numbered = {}
    for _, reqs in places:
        for r in reqs:
            if r.source_id:
                numbered[r.rid] = r
    sections = [r.section for r in numbered.values()]
    if len(numbered) >= 2 and any(sections.count(s) >= 2 for s in sections):
        return "critical", "противоречие между требованиями одного сценария"
    return "high", "противоречие с цитатами обеих сторон"


def apply_floors(findings: Sequence[Finding], requirements: Sequence[Requirement] = ()) -> list[dict]:
    """Поднять степень замечаний до нижней границы (на месте). Возвращает журнал повышений."""
    raised = []
    for f in findings:
        floor, why = floor_severity(f, requirements)
        if floor and stronger(floor, f.severity) != f.severity:
            raised.append({"fid": f.fid, "from": f.severity, "to": floor, "reason": why})
            f.severity = floor
    return raised


def is_template_blank(f: Finding) -> bool:
    return any(part.startswith(TEMPLATE_ORIGINS) for part in f.origin_pass.split("+"))


def pick_main(findings: Sequence[Finding], limit: int = 5) -> list[Finding]:
    """Блок «Главное»: до limit подтверждённых замечаний степени critical и high.

    Порядок: сначала critical, затем high; внутри степени противоречия, затем непроверяемые, затем
    остальные. Незаполненные места шаблона не попадают никогда. Замечание, цитаты которого
    пересекаются с уже выбранным, пропускается. Если подходящих меньше limit, блок короче:
    слабыми замечаниями он не добивается.
    """
    pool = [f for f in findings
            if f.status == "confirmed" and f.severity in ("critical", "high") and not is_template_blank(f)]
    pool.sort(key=lambda f: (SEVERITIES.index(f.severity), MAIN_KIND_ORDER.get(f.kind, 2), f.fid))
    chosen: list[Finding] = []
    spans: list[tuple[int, int]] = []
    for f in pool:
        fs = [(q.start, q.end) for q in f.quotes]
        if any(a < d and c < b for a, b in fs for c, d in spans):
            continue
        chosen.append(f)
        spans.extend(fs)
        if len(chosen) == limit:
            break
    return chosen

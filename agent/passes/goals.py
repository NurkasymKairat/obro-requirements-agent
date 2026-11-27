"""Прослеживаемость целей: таблица «цель -> реализующие требования -> соответствие» от модели,
замечания по строкам «нет» и «частично» собирает код.

Шаг нельзя пропустить молча: если в документе есть раздел целей, а таблица пуста, проход
завершается ошибкой.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from agent.schemas import RawFinding, RawQuote

# Название раздела, где документ объясняет, зачем нужна система.
_GOAL_WORDS = (r"\b(цел[ьией]\w*|назначени\w*|бизнес-требовани\w*|бизнес-цел\w*|"
               r"ожидаем\w+ результат\w*|goals?|objectives?|purpose|business requirements?)\b")
_GOAL_HEADING = re.compile(r"^\[B-\d+\]\s*#{1,6}\s*[^\n]*" + _GOAL_WORDS, re.I | re.M)
_GOAL_SECTION = re.compile(_GOAL_WORDS, re.I)


def is_goal_section(section: str) -> bool:
    """Раздел требования — цели или бизнес-требования (по последнему уровню пути раздела)."""
    return bool(_GOAL_SECTION.search(section.split("›")[-1]))


class GoalRow(BaseModel):
    goal: RawQuote = Field(description="Дословная цитата одного утверждения из целей или бизнес-требований")
    implemented_by: list[str] = Field(description="ID требований, которые реализуют утверждение; пустой список, если таких нет")
    verdict: Literal["yes", "partial", "no"] = Field(
        description="yes — требования дают ровно обещанное; partial — дают с оговорками или при других "
                    "условиях; no — не дают или дают противоположное")
    evidence: RawQuote | None = Field(
        default=None, description="Для partial и no: дословная цитата требования, которое расходится с обещанием; "
                                  "null, если реализующих требований нет")
    gap: str | None = Field(default=None, description="Для partial и no: в чём именно реализация расходится с обещанием")
    fix: str | None = Field(default=None, description="Для partial и no: как согласовать цель и требования")


class GoalsOutput(BaseModel):
    findings: list[RawFinding] = Field(description="Замечания остальных шагов; пустой список, если дефектов нет")
    goals: list[GoalRow] = Field(description="По одной строке на каждое утверждение из целей и бизнес-требований")


def has_goals_section(document_text: str) -> bool:
    return bool(_GOAL_HEADING.search(document_text))


def goal_findings(rows: Sequence[GoalRow], document_text: str, known_rids: set[str]) -> list[RawFinding]:
    """Замечания по строкам таблицы целей с вердиктом «нет» или «частично»."""
    if not rows and has_goals_section(document_text):
        raise ValueError("таблица целей пуста, хотя в документе есть раздел целей")
    out = []
    for row in rows:
        if row.verdict == "yes":
            continue
        rids = [r for r in dict.fromkeys(row.implemented_by) if r in known_rids]
        gap = (row.gap or "").strip() or "реализация не соответствует обещанию"
        fix = (row.fix or "").strip() or ("Привести цель и реализующие её требования к одному обещанию: изменить "
                                          "формулировку цели либо требования.")
        if row.evidence is not None:
            how = "не выполняют" if row.verdict == "no" else "выполняют только частично"
            out.append(RawFinding(
                scope="set", requirement_ids=rids, criterion="согласованность (consistent)",
                standard="ISO/IEC/IEEE 29148 п. 5.2.6", kind="contradiction", severity="high",
                description=f"Требования {how} обещание из целей документа: {gap}",
                quotes=[row.goal, row.evidence], fix=fix))
        else:
            out.append(RawFinding(
                scope="set", requirement_ids=rids, criterion="прослеживаемость (traceable)",
                standard="IEEE 830 п. 4.3", kind="incomplete", severity="high",
                description=f"Цель документа не прослеживается к требованиям: {gap}",
                quotes=[row.goal], fix=fix))
    return out

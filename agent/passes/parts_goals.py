"""Сверка частей документа с обязательной таблицей целей (заменяет cross_parts).

Шаги сверки частей те же, что были в cross_parts; добавлена таблица «цель -> требования -> соответствие»,
замечания по которой собирает код (goals.goal_findings).
"""
from __future__ import annotations

from collections.abc import Sequence

from agent.schemas import RawFinding, Requirement

from .base import Client, load_prompt, render_requirements
from .goals import GoalsOutput, goal_findings

NAME = "parts_goals"
THINKING = "high"
# Рассуждение входит в лимит выхода: при общем лимите 32768 ответ обрезается.
MAX_OUTPUT_TOKENS = 65536


def run(client: Client, document_text: str, requirements: Sequence[Requirement]) -> list[RawFinding]:
    system, user = load_prompt(NAME, document=document_text,
                               requirements=render_requirements(requirements, document_text))
    out = client.complete(system, user, GoalsOutput, NAME)
    return list(out.findings) + goal_findings(out.goals, document_text, {r.rid for r in requirements})

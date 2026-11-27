"""Проход 3: требования против критериев приёмки и проверяемость количественных требований.

Количественные требования отбирает код (measurable.select), модель заполняет по ним таблицу,
замечания «непроверяемо» собирает код (measurable.measurable_findings).
"""
from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from agent.schemas import RawFinding, Requirement

from .base import Client, load_prompt, render_requirements
from .measurable import MeasurableRow, measurable_findings, select

NAME = "cross_acceptance"
THINKING = "high"
# Рассуждение входит в лимит выхода: при общем лимите 32768 ответ обрезался в половине вызовов.
MAX_OUTPUT_TOKENS = 65536


class AcceptanceOutput(BaseModel):
    findings: list[RawFinding] = Field(description="Замечания шагов 1–3; пустой список, если дефектов нет")
    measurable: list[MeasurableRow] = Field(description="По одной строке на каждое количественное требование из списка")


def run(client: Client, document_text: str, requirements: Sequence[Requirement]) -> list[RawFinding]:
    candidates = select(requirements)
    listed = render_requirements(candidates, document_text) if candidates else "(таких требований нет; верни пустую таблицу)"
    system, user = load_prompt(NAME, document=document_text, measurable=listed,
                               requirements=render_requirements(requirements, document_text))
    out = client.complete(system, user, AcceptanceOutput, NAME)
    return list(out.findings) + measurable_findings(out.measurable, candidates)

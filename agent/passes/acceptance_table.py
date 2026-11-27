"""Вариант cross_acceptance: модель возвращает таблицу покрытия, общее замечание о покрытии собирает код."""
from __future__ import annotations

from collections.abc import Sequence

from agent.schemas import RawFinding, Requirement

from .base import Client, load_prompt, render_requirements
from .coverage import CoverageOutput, coverage_finding

NAME = "acceptance_table"


def run(client: Client, document_text: str, requirements: Sequence[Requirement]) -> list[RawFinding]:
    system, user = load_prompt(NAME, document=document_text,
                               requirements=render_requirements(requirements, document_text))
    out = client.complete(system, user, CoverageOutput, NAME)
    findings = list(out.findings)
    gap = coverage_finding(out.coverage, requirements)
    if gap:
        findings.append(gap)
    return findings

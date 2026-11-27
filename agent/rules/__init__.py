"""Детерминированные проверки без модели.

    from agent.rules import run_rules
    findings = run_rules(document, requirements)

Цитаты правил берутся прямо из Document.text и сверки не требуют. Замечание привязывается
к требованиям, чьи цитаты пересекаются с его цитатами; без привязки оно относится к набору.
fid временный (D-001…): окончательную нумерацию назначает pipeline.
"""
from __future__ import annotations

from collections.abc import Sequence

from agent.schemas import Document, Finding, Requirement

from . import checklist, fields, http, template, units, vague
from .base import Doc

RULES = (template, checklist, http, fields, units, vague)


def run_rules(document: Document, requirements: Sequence[Requirement] = ()) -> list[Finding]:
    doc = Doc(document)
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule.check(doc))
    for i, f in enumerate(findings, start=1):
        f.fid = f"D-{i:03d}"
        f.requirement_ids = [
            r.rid for r in requirements
            if any(q.start < r.quote.end and r.quote.start < q.end for q in f.quotes)
        ]
        f.scope = "requirement" if f.requirement_ids else "set"
    return findings

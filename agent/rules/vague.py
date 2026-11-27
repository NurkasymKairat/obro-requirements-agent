"""Размытые слова без чисел и открытые перечисления."""
from __future__ import annotations

import re

from agent.schemas import Finding

from .base import Doc, finding

# Открытые перечисления: неполны при любом окружении.
_OPEN = re.compile(r"\bи\s+т\.\s?[дп]\.|\bи\s+др\.|\bи\s+(?:так\s+)?далее\b|\bи\s+проч(?:ее|ие|\.)|\betc\b\.?", re.I)
# Оценочные слова: замечание только если в строке нет числа.
_VAGUE = re.compile(
    r"\b(?:быстр\w*|оперативн\w*|своевременн\w*|незамедлительн\w*|мгновенн\w*|"
    r"удобн\w*|интуитивн\w*|дружественн\w*|понятн\w*|просто[йе]?|"
    r"надежн\w*|эффективн\w*|оптимальн\w*|гибк\w*|современн\w*|качественн\w*|"
    r"достаточн\w*|приемлем\w*|адекватн\w*|разумн\w*|минимальн\w*|максимальн\w*|"
    r"несколько|некотор\w+|обычно|как правило|примерно|около|приблизительно|"
    r"при необходимости|по возможности|при возможности|желательно|"
    r"в кратчайшие сроки|в ближайшее время|периодически|регулярно|часто|редко)\b",
    re.I,
)
_NUMBER = re.compile(r"\d")
# Номера требований и пунктов числовым критерием не считаются.
_LABEL = re.compile(r"\b[a-zа-я]{1,6}[-.]?\d+(?:\.\d+)*\b\.?|^\s*\d+[.)]\s")


def check(doc: Doc) -> list[Finding]:
    out: list[Finding] = []
    for line in doc.lines:
        if line.in_code or line.heading() or line.is_separator or not line.text.strip():
            continue
        text = line.text.lower().replace("ё", "е")
        m = _OPEN.search(text)
        if m:
            out.append(finding(
                "vague", criterion="однозначность (unambiguous)", kind="ambiguous", severity="low",
                status="potential",
                description=f"Открытое перечисление «{line.text[m.start():m.end()]}»: состав значений не закрыт, "
                            "проверить полноту реализации нельзя.",
                quotes=[doc.quote_line(line)],
                fix="Перечислить все значения явно или задать правило, по которому значение относится к перечню.",
            ))
            continue
        m = _VAGUE.search(text)
        if m and not _NUMBER.search(_LABEL.sub(" ", text)):
            out.append(finding(
                "vague", criterion="проверяемость (verifiable)", kind="unverifiable", severity="low",
                status="potential",
                description=f"Оценочное слово «{line.text[m.start():m.end()]}» без числового критерия: "
                            "нельзя объективно проверить выполнение.",
                quotes=[doc.quote_line(line)],
                fix="Заменить оценочное слово измеримым критерием: число, единица измерения, условия проверки.",
            ))
    return out

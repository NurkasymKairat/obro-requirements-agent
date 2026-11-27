"""Покрытие требований критериями приёмки: таблица от модели, общее замечание собирает код.

Модель обязана вернуть строку на каждое требование, поэтому шаг нельзя пропустить молча:
если таблица пуста или в ней меньше половины требований, проход завершается ошибкой.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from agent.schemas import RawFinding, RawQuote, Requirement

MAX_QUOTES = 8
MAX_HINTS = 8


class CoverageRow(BaseModel):
    requirement_id: str = Field(description="ID требования из списка, например R-007")
    status: Literal["covered", "partial", "not_covered", "not_applicable"]
    criterion_block: str | None = Field(
        default=None, description="Метка блока критерия, который проверяет требование; null, если критерия нет")
    missing: str | None = Field(
        default=None, description="Какого сценария не хватает: условия, действие, ожидаемый результат; null для covered")


class CoverageOutput(BaseModel):
    findings: list[RawFinding] = Field(description="Замечания шагов 1 и 2; пустой список, если дефектов нет")
    coverage: list[CoverageRow] = Field(description="По одной строке на каждое требование из списка")


def _label(r: Requirement) -> str:
    return f"{r.rid} ({r.source_id})" if r.source_id else r.rid


def coverage_finding(rows: Sequence[CoverageRow], requirements: Sequence[Requirement]) -> RawFinding | None:
    """Одно замечание о неполноте покрытия по таблице; None, если всё покрыто."""
    by_id = {r.rid: r for r in requirements}
    seen: dict[str, CoverageRow] = {}
    for row in rows:
        if row.requirement_id in by_id:
            seen.setdefault(row.requirement_id, row)
    if requirements and len(seen) * 2 < len(requirements):
        raise ValueError(f"таблица покрытия неполна: {len(seen)} строк на {len(requirements)} требований")

    testable = [rid for rid, row in seen.items() if row.status != "not_applicable"]
    none = [by_id[rid] for rid in testable if seen[rid].status == "not_covered"]
    part = [by_id[rid] for rid in testable if seen[rid].status == "partial"]
    if not none and not part:
        return None

    parts = [f"Критерии приёмки не покрывают требования полностью: из {len(testable)} проверяемых требований "
             f"не покрыто {len(none)}, покрыто частично {len(part)}."]
    if none:
        parts.append("Нет ни одного критерия: " + ", ".join(_label(r) for r in none) + ".")
    if part:
        parts.append("Проверена только часть требования: " + ", ".join(_label(r) for r in part) + ".")
    parts.append("По непокрытым требованиям нельзя принять результат разработки: тестировщик не знает ожидаемого результата.")

    hints = [f"{_label(r)}: {seen[r.rid].missing.strip()}" for r in none + part if (seen[r.rid].missing or "").strip()]
    fix = "Добавить критерии приёмки с исходными условиями, действием и ожидаемым результатом. "
    if hints:
        fix += "Недостающие сценарии: " + " ".join(f"{i}) {h.rstrip('.')}." for i, h in enumerate(hints[:MAX_HINTS], 1))
        if len(hints) > MAX_HINTS:
            fix += f" И ещё {len(hints) - MAX_HINTS}."

    # Сначала требования с номером из документа: они важнее строк таблиц и шагов.
    cited = sorted(none + part, key=lambda r: (r.source_id is None, r.rid))[:MAX_QUOTES]
    return RawFinding(
        scope="set", requirement_ids=[r.rid for r in none + part],
        criterion="проверяемость набора (able to be validated)",
        standard="ISO/IEC/IEEE 29148 п. 5.2.6", kind="incomplete",
        severity="high", description=" ".join(parts),
        quotes=[RawQuote(text=r.quote.text, block_id=r.quote.block_id) for r in cited], fix=fix,
    )

"""Проверяемость количественных требований: отбор кодом, таблица от модели, замечания кодом.

Код отбирает требования с числом и единицей или со словами срока (до вызова модели).
Модель для каждого заполняет: что измеряется, от какого события до какого, при каких условиях,
как проверяется. Поле, которое по документу заполнить нельзя, превращается в замечание «непроверяемо».
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from agent.schemas import RawFinding, RawQuote, Requirement

from .calibrate import _ACCEPTANCE
from .goals import is_goal_section

_UNIT = (r"мс|миллисекунд\w*|с|сек\w*|мин\w*|ч|час\w*|сут\w*|дн\w*|день|дня|недел\w*|месяц\w*|мес|год\w*|лет|"
         r"%|процент\w*|раз\w*|шт\w*|запрос\w*|операци\w*|транзакци\w*|пользовател\w*|клиент\w*|"
         r"байт\w*|[кмгт]б|rps|tps|ms|sec\w*|min\w*|hours?|days?|"
         r"₸|тенге|тг|тиын\w*|руб\w*|₽|\$|€|usd|eur|kzt")
_NUMBER_UNIT = re.compile(rf"(?<![\w.,-])\d[\d  .,]*\s?(?:{_UNIT})(?![\w])", re.I)
_TIME_WORD = re.compile(
    r"\bв течение\b|\bне позднее\b|\bне дольше\b|\bне более\b|\bне менее\b|\bне реже\b|\bне чаще\b|"
    r"\bежедневн\w*|\bежемесячн\w*|\bеженедельн\w*|\bнемедленн\w*|\bмгновенн\w*|\bсразу\b|\bкруглосуточн\w*|"
    r"\bв реальном времени\b|\bв момент\b|\bминут\w*|\bсекунд\w*|\bчас(?:а|ов|ы)?\b|\bсут(?:ки|ок)\b", re.I)

ASPECTS = (("metric", "что именно измеряется"), ("interval", "от какого события до какого идёт отсчёт"),
           ("conditions", "при каких условиях и нагрузке должно выполняться"),
           ("method", "как это проверяется"))


def is_measurable(r: Requirement) -> bool:
    """Требование с числом и единицей или со словами срока.

    Не отбираются критерии приёмки (они сами тесты) и утверждения из разделов целей: цель проверяется
    таблицей целей, второе замечание «непроверяемо» по ней было бы дублем."""
    if _ACCEPTANCE.search(r.section) or is_goal_section(r.section):
        return False
    return bool(_NUMBER_UNIT.search(r.quote.text) or _TIME_WORD.search(r.quote.text.lower().replace("ё", "е")))


def select(requirements: Sequence[Requirement]) -> list[Requirement]:
    return [r for r in requirements if is_measurable(r)]


class Aspect(BaseModel):
    status: Literal["defined", "not_defined", "not_applicable"] = Field(
        description="defined — документ это определяет; not_defined — должен определять, но не определяет; "
                    "not_applicable — для этого требования не имеет смысла")
    note: str = Field(description="defined: что сказано в документе и где; not_defined: чего именно не хватает; "
                                  "not_applicable: почему")


class MeasurableRow(BaseModel):
    requirement_id: str = Field(description="ID требования из списка количественных требований")
    metric: Aspect = Field(description="Что именно измеряется или ограничивается")
    interval: Aspect = Field(description="От какого события до какого идёт отсчёт; для периода — его начало и конец")
    conditions: Aspect = Field(description="При каких условиях, нагрузке или для какой доли случаев должно выполняться")
    method: Aspect = Field(description="Как проверить выполнение: что наблюдается и по какому признаку тест пройден")
    fix: str | None = Field(default=None, description="Если есть not_defined: исправленная формулировка требования")


def measurable_findings(rows: Sequence[MeasurableRow], candidates: Sequence[Requirement]) -> list[RawFinding]:
    """Замечание «непроверяемо» на каждое количественное требование с незаполнимым полем."""
    by_id = {r.rid: r for r in candidates}
    seen: dict[str, MeasurableRow] = {}
    for row in rows:
        if row.requirement_id in by_id:
            seen.setdefault(row.requirement_id, row)
    if candidates and len(seen) * 2 < len(candidates):
        raise ValueError(f"таблица количественных требований неполна: {len(seen)} строк на {len(candidates)} требований")
    out = []
    for rid, row in seen.items():
        missing = [(title, getattr(row, key).note.strip()) for key, title in ASPECTS
                   if getattr(row, key).status == "not_defined"]
        if not missing:
            continue
        r = by_id[rid]
        label = f"{r.rid} ({r.source_id})" if r.source_id else r.rid
        details = " ".join(f"{i}) {title}: {note.rstrip('.')}." for i, (title, note) in enumerate(missing, 1))
        out.append(RawFinding(
            scope="requirement", requirement_ids=[rid], criterion="проверяемость (verifiable)",
            standard="ISO/IEC/IEEE 29148 п. 5.2.5", kind="unverifiable",
            severity="high" if len(missing) > 1 else "medium",
            description=f"Количественное требование {label} нельзя проверить измерением. Не определено: {details}",
            quotes=[RawQuote(text=r.quote.text, block_id=r.quote.block_id)],
            fix=(row.fix or "").strip() or "Дополнить требование: " + "; ".join(t for t, _ in missing) + "."))
    return out

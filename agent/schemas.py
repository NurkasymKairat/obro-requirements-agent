"""Контракт результата прогона. Меняется только в сессии ядра (A).

Поток данных:
  reader      -> Document(blocks)
  проходы     -> RawRequirement / RawFinding   (ответ модели, цитаты не сверены)
  verify      -> Requirement / Finding         (цитаты найдены в тексте, есть позиции)
  pipeline    -> RunResult                     (пишется в report.json рядом с отчётом)
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["ambiguous", "unverifiable", "contradiction", "incomplete",
               "design_in_requirement", "other"]
Severity = Literal["critical", "high", "medium", "low"]

# ---------- Вход: документ, нарезанный reader ----------


class Block(BaseModel):
    block_id: str               # B-001, сквозная нумерация по документу
    section: str                # путь раздела: «Часть A › Функциональные требования»
    kind: Literal["heading", "paragraph", "list_item", "table_row", "code", "other"]
    text: str                   # исходный текст блока (== Document.text[start:end])
    start: int                  # смещение в Document.text
    end: int
    line: int | None = None     # MD/TXT: номер строки (с 1)
    page: int | None = None     # PDF: номер страницы (с 1)
    location: str               # человекочитаемо: «Часть A › Критерии приемки, строка 61»


class Document(BaseModel):
    path: str
    format: Literal["md", "txt", "pdf", "docx"]
    text: str                   # полный текст, на который ссылаются start/end
    blocks: list[Block]


# ---------- Ответ модели (до сверки цитат) ----------


class RawQuote(BaseModel):
    text: str = Field(description="Дословный фрагмент документа, без пересказа и сокращений")
    block_id: str = Field(description="ID блока, из которого взят фрагмент, например B-012")


class RawRequirement(BaseModel):
    source_id: str | None = Field(
        description="Номер или идентификатор требования, как он написан в документе; null, если номера нет")
    section: str = Field(description="Раздел документа, где находится требование")
    summary: str = Field(description="Краткая формулировка требования на русском, до 15 слов")
    quote: RawQuote


class RawFinding(BaseModel):
    scope: Literal["requirement", "set"]
    requirement_ids: list[str] = Field(description="ID требований из нарезки, например R-001")
    criterion: str = Field(description="Нарушенное свойство, например «однозначность (unambiguous)»")
    standard: str = Field(description="Пункт стандарта, например «ISO/IEC/IEEE 29148 п. 5.2.5»")
    kind: Kind
    severity: Severity
    description: str
    quotes: list[RawQuote] = Field(description="Цитаты места нарушения; для противоречия — обе стороны")
    fix: str = Field(description="Как переписать требование или какой вопрос задать владельцу")


class PassResult(BaseModel):
    """Расширенный результат прохода. Обычный проход возвращает просто list[RawFinding]."""
    findings: list[RawFinding] = Field(default_factory=list)
    potential_fids: list[str] = Field(default_factory=list)  # F-xxx, которые проход оспорил: статус -> potential


# ---------- Результат после сверки ----------


class Quote(BaseModel):
    text: str                   # дословно из документа
    block_id: str
    start: int                  # позиция в тексте документа после сверки
    end: int
    location: str               # «Часть A, Ф-2» или «строка 38»


class Requirement(BaseModel):
    rid: str                    # R-001
    source_id: str | None       # «Ф-1» или None
    section: str
    summary: str
    quote: Quote


class Finding(BaseModel):
    fid: str
    scope: Literal["requirement", "set"]
    requirement_ids: list[str]
    criterion: str              # «однозначность (unambiguous)», «согласованность (consistent)» и т.д.
    standard: str               # «ISO 29148 п. 5.2.5»
    kind: Kind
    severity: Severity
    status: Literal["confirmed", "potential"]
    description: str
    quotes: list[Quote]         # для противоречия — обе стороны
    fix: str                    # как переписать или какой вопрос задать владельцу
    origin_pass: str


class RunResult(BaseModel):
    document: str
    date: str
    provider: str
    model: str
    agent_version: str          # git-коммит
    requirements: list[Requirement]
    findings: list[Finding]
    verdict: Literal["ready", "ready_with_fixes", "not_ready", "incomplete"]  # incomplete — прогон оборван, вердикт не выносится
    first_fixes: list[str]
    stages: list[dict]          # {"name","status": ok|failed|skipped,"seconds","calls","retries","cache_hits",
                                #  "tokens_in","tokens_out","tokens_thinking","call_log":[...],"models",...}
    incomplete_reason: str | None = None   # почему прогон неполный; None — все проходы выполнены
    wall_seconds: float | None = None      # время прогона по часам: от старта до записи отчёта
    calls_seconds: float | None = None     # сумма длительностей вызовов модели; при параллельных вызовах больше wall_seconds
